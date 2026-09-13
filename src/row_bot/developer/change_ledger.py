from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import time
import uuid
import threading
from functools import wraps
from dataclasses import asdict, dataclass
from collections.abc import Callable

from row_bot.developer.storage import DEVELOPER_DIR


LEDGER_PATH = DEVELOPER_DIR / "change_ledger.json"
_LEDGER_LOCK = threading.RLock()


def _ledger_mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with _LEDGER_LOCK:
            _load_client_ledger()
            return function(*args, **kwargs)
    return guarded


def validate_client_ledger() -> None:
    """Fail closed before a client write if retained ledger data is malformed."""
    with _LEDGER_LOCK:
        _load_client_ledger()


def _load_client_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return {"change_sets": []}
    if LEDGER_PATH.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("change_ledger_unavailable")
    try:
        value = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if type(value) is not dict or type(value.get("change_sets")) is not list:
            raise ValueError
        for row in value["change_sets"]:
            if type(row) is not dict:
                raise ValueError
            _from_dict(row)
        return value
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError("change_ledger_unavailable") from None


@dataclass(frozen=True)
class FileChange:
    path: str
    action: str
    before_hash: str
    after_hash: str
    before_text: str | None = None
    patch: str = ""


@dataclass(frozen=True)
class ChangeSet:
    id: str
    workspace_id: str
    thread_id: str
    created_at: float
    summary: str
    files: list[FileChange]
    reverted: bool = False
    reviewed: bool = False
    guarded_import: dict | None = None
    undo_command_id: str | None = None


def _sha(text: str | None) -> str:
    if text is None:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def text_hash(text: str | None) -> str:
    return _sha(text)


def _load() -> dict:
    if not LEDGER_PATH.exists():
        return {"change_sets": []}
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"change_sets": []}
    if not isinstance(data, dict):
        return {"change_sets": []}
    data.setdefault("change_sets", [])
    return data


def _save(data: dict) -> None:
    DEVELOPER_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=LEDGER_PATH.name, suffix=".tmp", dir=str(LEDGER_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp_name, LEDGER_PATH)
    finally:
        try:
            pathlib.Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass


def _from_dict(raw: dict) -> ChangeSet:
    files = [FileChange(**item) for item in raw.get("files", []) if isinstance(item, dict)]
    return ChangeSet(
        id=str(raw.get("id", "")),
        workspace_id=str(raw.get("workspace_id", "")),
        thread_id=str(raw.get("thread_id", "")),
        created_at=float(raw.get("created_at", 0) or 0),
        summary=str(raw.get("summary", "")),
        files=files,
        reverted=bool(raw.get("reverted", False)),
        reviewed=bool(raw.get("reviewed", False)),
        guarded_import=raw.get("guarded_import"),
        undo_command_id=raw.get("undo_command_id"),
    )


@_ledger_mutation
def record_change_set(
    *,
    workspace_id: str,
    thread_id: str,
    summary: str,
    files: list[FileChange],
    command_id: str | None = None,
    guarded_import: dict | None = None,
) -> ChangeSet:
    identity = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:client-edit:" + command_id).hex if command_id else uuid.uuid4().hex[:12]
    data = _load_client_ledger() if command_id else _load()
    if command_id:
        matches = [row for row in data["change_sets"] if row.get("id") == identity]
        if matches:
            existing = _from_dict(matches[0])
            if len(matches) != 1 or (existing.workspace_id, existing.thread_id, existing.summary, existing.files, existing.guarded_import) != (workspace_id, thread_id, summary, files, guarded_import):
                raise ValueError("change_ledger_identity_conflict")
            return existing
    change_set = ChangeSet(
        id=identity,
        workspace_id=workspace_id,
        thread_id=thread_id,
        created_at=time.time(),
        summary=summary,
        files=files,
        guarded_import=guarded_import,
    )
    rows = data.get("change_sets", [])
    rows.append(asdict(change_set))
    data["change_sets"] = rows if command_id else rows[-500:]
    _save(data)
    return change_set


def list_change_sets(
    *,
    workspace_id: str | None = None,
    thread_id: str | None = None,
    include_reverted: bool = False,
) -> list[ChangeSet]:
    rows: list[ChangeSet] = []
    for raw in _load().get("change_sets", []):
        if not isinstance(raw, dict):
            continue
        change_set = _from_dict(raw)
        if workspace_id and change_set.workspace_id != workspace_id:
            continue
        if thread_id and change_set.thread_id != thread_id:
            continue
        if change_set.reverted and not include_reverted:
            continue
        rows.append(change_set)
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return rows


@_ledger_mutation
def mark_reverted(change_set_id: str, *, expected_revision: str | None = None,
                  command_id: str | None = None, validate: Callable[[], None] | None = None) -> None:
    data = _load()
    if expected_revision is not None:
        matches = [row for row in data["change_sets"] if row.get("id") == change_set_id]
        if len(matches) != 1 or change_set_revision(matches[0]) != expected_revision:
            raise ValueError("change_set_revision_conflict")
    if validate:
        validate()
    for raw in data.get("change_sets", []):
        if isinstance(raw, dict) and raw.get("id") == change_set_id:
            raw["reverted"] = True
            if command_id is not None:
                raw["undo_command_id"] = command_id
    _save(data)


def change_set_revision(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def read_change_set(change_set_id: str) -> tuple[ChangeSet, str]:
    """Capture the complete canonical row and its revision without initializing storage."""
    with _LEDGER_LOCK:
        matches = [row for row in _load_client_ledger()["change_sets"] if row.get("id") == change_set_id]
        if len(matches) != 1:
            raise ValueError("change_set_unavailable")
        return _from_dict(matches[0]), change_set_revision(matches[0])


def requires_guarded_undo(change: ChangeSet) -> bool:
    """A lost proof cannot turn a canonically imported command into legacy text Undo."""
    if change.guarded_import is not None:
        return True
    if len(change.id) != 32:
        return False
    from row_bot.developer.sandbox_runtime import read_pending_import_rows
    rows = read_pending_import_rows(change.workspace_id, change.thread_id)
    for row in rows:
        command = row.get("import_command_id")
        if isinstance(command, str) and uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:client-edit:" + command).hex == change.id:
            return True
    return False


@_ledger_mutation
def delete_thread_change_sets(thread_id: str) -> int:
    """Remove obsolete per-thread Developer ledger records, not file changes."""

    clean = str(thread_id or "").strip()
    if not clean:
        return 0
    data = _load()
    existing = list(data.get("change_sets", []))
    retained = [
        raw
        for raw in existing
        if not (isinstance(raw, dict) and str(raw.get("thread_id") or "") == clean)
    ]
    removed = len(existing) - len(retained)
    if removed:
        data["change_sets"] = retained
        _save(data)
    return removed
