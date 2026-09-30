"""Bounded saved-task views; execution and delivery remain with tasks.py."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re


class TaskViewError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TaskRunDigest:
    status: str
    started_at: str


@dataclass(frozen=True)
class TaskActiveRun:
    id: str
    status: str
    started_at: str
    steps_done: int
    steps_total: int


@dataclass(frozen=True)
class TaskSummary:
    id: str
    name: str
    description: str
    icon: str
    enabled: bool
    notify_only: bool
    step_count: int
    schedule: str | None
    at: str | None
    last_run: str | None
    last_status: str | None
    conversation_id: str | None
    # What a run starts with: the saved profile (or the default one) and the
    # workflow's approval mode (or the global default); a profile can only
    # make approvals stricter.
    agent_profile_id: str
    approval_mode: str
    # Saved run history and schedule, never live probes. `next_run` is derived
    # from the saved schedule for the returned page only.
    recent_runs: tuple[TaskRunDigest, ...] = ()
    active_run: TaskActiveRun | None = None
    next_run: str | None = None


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


def _recent_runs(value: object) -> tuple[TaskRunDigest, ...]:
    try:
        rows = json.loads(value) if isinstance(value, str) else []
    except ValueError:
        return ()
    if not isinstance(rows, list):
        return ()
    return tuple(
        TaskRunDigest(str(row["status"])[:80], str(row["started_at"])[:80])
        for row in rows[:10]
        if isinstance(row, dict)
        and isinstance(row.get("status"), str)
        and isinstance(row.get("started_at"), str)
    )


def _active_run(value: object) -> TaskActiveRun | None:
    try:
        row = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if not isinstance(row, dict) or _identity(row.get("id")) is None:
        return None
    done, total = row.get("steps_done"), row.get("steps_total")
    return TaskActiveRun(
        row["id"],
        str(row.get("status") or "")[:80],
        str(row.get("started_at") or "")[:80],
        done if type(done) is int and done >= 0 else 0,
        total if type(total) is int and total >= 0 else 0,
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
    default_approval = tasks.get_global_approval_mode()
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
                row["step_count"],
                row["schedule"] or None,
                row["at"] or None,
                row["last_run"] or None,
                row["last_status"] or None,
                _identity(row["persistent_thread_id"]),
                row["agent_profile_id"] or tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
                tasks.legacy_safety_mode_to_approval_mode(row["safety_mode"])
                if row["safety_mode"]
                else default_approval,
                _recent_runs(row.get("recent_runs_json")),
                _active_run(row.get("active_run_json")),
            )
            # The page revision covers saved metadata and run history; the
            # clock-derived next run is left out so paging stays stable.
            stable = asdict(item)
            del stable["next_run"]
            digest.update(
                json.dumps(stable, sort_keys=True, ensure_ascii=True).encode()
            )
            digest.update(b"\n")
            if enabled is not None and item.enabled != enabled:
                continue
            if term and term not in (item.name + " " + item.description).casefold():
                continue
            if offset <= total < offset + limit:
                items.append(
                    replace(item, next_run=tasks.estimate_next_run(row))
                )
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
