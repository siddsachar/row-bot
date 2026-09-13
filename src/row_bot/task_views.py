"""Bounded saved-task views; execution and delivery remain with tasks.py."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import re


class TaskViewError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TaskSummary:
    id: str
    name: str
    description: str
    icon: str
    enabled: bool
    notify_only: bool
    schedule: str | None
    at: str | None
    last_run: str | None
    last_status: str | None
    conversation_id: str | None


@dataclass(frozen=True)
class TaskSummaryPage:
    schema_version: int
    revision: str
    items: tuple[TaskSummary, ...]
    total: int
    next_cursor: str | None


def _identity(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value)
        else None
    )


def list_saved_tasks(
    *,
    query: str = "",
    enabled: bool | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> TaskSummaryPage:
    """Search all saved metadata while retaining only one bounded response page.

    A cursor is tied to both its filters and exact captured metadata revision.
    Last saved run status is historical; it is never presented as a live probe.
    """
    if (
        not isinstance(query, str)
        or len(query) > 256
        or type(limit) is not int
        or not 1 <= limit <= 100
        or (enabled is not None and type(enabled) is not bool)
    ):
        raise TaskViewError("invalid_task_query")
    term = query.strip().casefold()
    filter_id = hashlib.sha256(json.dumps([term, enabled, limit]).encode()).hexdigest()
    offset = 0
    expected = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not re.fullmatch(
                r"[A-Za-z0-9_=-]{1,1024}", cursor
            ):
                raise ValueError
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (
                not isinstance(value, dict)
                or set(value) != {"v", "filter", "revision", "offset"}
                or type(value["v"]) is not int
                or value["v"] != 1
                or value["filter"] != filter_id
                or type(value["offset"]) is not int
                or not 0 < value["offset"] <= 10**12
                or not isinstance(value["revision"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["revision"])
            ):
                raise ValueError
            offset, expected = value["offset"], value["revision"]
        except (ValueError, TypeError, UnicodeError):
            raise TaskViewError("cursor_expired") from None
    from row_bot import tasks

    digest = hashlib.sha256()
    items = []
    total = 0
    rows = tasks.iter_task_summary_snapshot()
    try:
        for row in rows:
            identity = _identity(row["id"])
            if identity is None:
                raise TaskViewError("task_metadata_unavailable")
            item = TaskSummary(
                identity,
                row["name"] or "Untitled task",
                row["description"] or "",
                row["icon"] or "",
                bool(row["enabled"]),
                bool(row["notify_only"]),
                row["schedule"] or None,
                row["at"] or None,
                row["last_run"] or None,
                row["last_status"] or None,
                _identity(row["persistent_thread_id"]),
            )
            digest.update(
                json.dumps(asdict(item), sort_keys=True, ensure_ascii=True).encode()
            )
            digest.update(b"\n")
            if enabled is not None and item.enabled != enabled:
                continue
            if term and term not in (item.name + " " + item.description).casefold():
                continue
            if offset <= total < offset + limit:
                items.append(item)
            total += 1
    finally:
        rows.close()
    revision = digest.hexdigest()
    if expected is not None and (expected != revision or offset >= total):
        raise TaskViewError("cursor_expired")
    next_cursor = None
    if offset + len(items) < total:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "filter": filter_id,
                    "revision": revision,
                    "offset": offset + len(items),
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
    return TaskSummaryPage(1, revision, tuple(items), total, next_cursor)
