"""Settings › Data: Back up now and Restore from backup for the local owner.

Backups and restores are long, so one runs at a time in the background and
the page reads its progress. A restore is two steps: the picked archive is
checked and described (a review bound to that file), then only an explicit
confirmation of that review stages it for the next start. Nothing here ever
replaces the profile in use, and no secret is ever written to an archive.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from row_bot import profile_restore
from row_bot.application import profile_backup
from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_JOB: dict = {}
_REVIEWS: dict[str, tuple[Path, float, str]] = {}
_KEY = secrets.token_bytes(32)
_REVIEW_SECONDS = 600
_BACKUPS_FOLDER = "Backups"


def _data_dir() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir()


def _backups_folder() -> Path:
    from row_bot.application.conversation_creation import configured_workspace_root

    root = configured_workspace_root()
    folder = root / _BACKUPS_FOLDER
    if folder.is_symlink():
        raise ClientPlatformError("backup_storage_unavailable")
    folder.mkdir(exist_ok=True)
    return folder


def _folder_words() -> str:
    try:
        from row_bot.application.conversation_creation import configured_workspace_root

        return f"{configured_workspace_root().name} › {_BACKUPS_FOLDER}"
    except Exception:
        return _BACKUPS_FOLDER


def _job_view() -> dict | None:
    with _LOCK:
        if not _JOB:
            return None
        return {key: _JOB.get(key) for key in ("kind", "status", "started_at", "finished_at", "code", "name")}


def read_state(*, local_owner: bool) -> dict:
    """What the Data page shows; other devices see only that it is local."""
    if not local_owner:
        return {"local_owner": False, "last_backup_at": None, "last_backup_name": None,
                "folder": None, "job": None, "pending_restore": None, "restore_result": None}
    data = _data_dir()
    state = profile_backup.backup_state(data)
    pending = profile_restore.pending(data)
    result = profile_restore.result(data)
    return {
        "local_owner": True,
        "last_backup_at": state.get("last_backup_at"),
        "last_backup_name": state.get("name"),
        "folder": _folder_words(),
        "job": _job_view(),
        "pending_restore": None if pending is None else {
            "created_at": str(pending.get("created_at", "")),
            "source_name": str(pending.get("source_name", "")),
            "sign_in_again": _sign_ins(pending.get("sign_in_again")),
        },
        "restore_result": None if result is None else {
            "status": "applied" if result.get("status") == "applied" else "failed",
            "applied_at": result.get("applied_at"),
            "source_created_at": str(result.get("source_created_at", "")),
            "kept_aside": result.get("kept_aside"),
            "sign_in_again": _sign_ins(result.get("sign_in_again")),
        },
    }


def _sign_ins(value) -> list[dict]:
    kinds = {"provider", "account", "channel", "mcp", "webhooks"}
    return [
        {"kind": str(item.get("kind")), "name": str(item.get("name", ""))[:200]}
        for item in (value if isinstance(value, list) else [])
        if isinstance(item, dict) and item.get("kind") in kinds
    ][:100]


def _start(kind: str, work: Callable[[], dict]) -> None:
    with _LOCK:
        if _JOB.get("status") == "running":
            raise ClientPlatformError("data_job_running")
        _JOB.clear()
        _JOB.update(kind=kind, status="running", started_at=datetime.now().isoformat(timespec="seconds"))

    def run() -> None:
        try:
            outcome = work()
            with _LOCK:
                _JOB.update(status="completed", finished_at=datetime.now().isoformat(timespec="seconds"),
                            name=outcome.get("name"))
        except profile_backup.BackupError as exc:
            with _LOCK:
                _JOB.update(status="failed", code=exc.code, finished_at=datetime.now().isoformat(timespec="seconds"))
        except Exception:
            logger.error("Data %s failed", kind, exc_info=True)
            with _LOCK:
                _JOB.update(status="failed", code=f"{kind}_failed",
                            finished_at=datetime.now().isoformat(timespec="seconds"))

    threading.Thread(target=run, name=f"row-bot-data-{kind}", daemon=True).start()


def _review_id(path: Path, info: dict) -> str:
    stat = path.stat()
    material = f"{path}|{stat.st_size}|{stat.st_mtime_ns}|{info['created_at']}"
    return hmac.new(_KEY, material.encode(), hashlib.sha256).hexdigest()


def inspect_restore(path: Path) -> dict:
    """Check a picked archive and remember it for one confirmation."""
    info = profile_backup.inspect_backup(path)
    review = _review_id(path, info)
    with _LOCK:
        now = time.monotonic()
        for key in [key for key, (_, expires, _) in _REVIEWS.items() if expires < now]:
            _REVIEWS.pop(key, None)
        _REVIEWS[review] = (path, now + _REVIEW_SECONDS, path.name)
    return {**info, "review_id": review, "source_name": path.name[:200]}


def restore(review_id: str) -> None:
    """Stage the reviewed archive for the next start (the confirmed step)."""
    with _LOCK:
        entry = _REVIEWS.pop(review_id, None)
    if entry is None or entry[1] < time.monotonic():
        raise ClientPlatformError("backup_review_expired")
    path, _, name = entry
    info = profile_backup.inspect_backup(path)
    if not hmac.compare_digest(_review_id(path, info), review_id):
        raise ClientPlatformError("backup_review_expired")  # the file changed since it was checked
    data = _data_dir()
    _start("restore", lambda: {**profile_backup.stage_restore(path, data, source_name=name), "name": name})


def backup() -> None:
    folder = _backups_folder()
    data = _data_dir()
    _start("backup", lambda: profile_backup.create_backup(data, folder))


def cancel_restore() -> bool:
    return profile_restore.cancel(_data_dir())


def dismiss_result() -> None:
    profile_restore.dismiss_result(_data_dir())


def reveal_last(opener: Callable[[str, Path], bool] | None = None) -> bool:
    """Show the last backup in the file manager (argv only, never a shell)."""
    name = profile_backup.backup_state(_data_dir()).get("name")
    folder = _backups_folder()
    target = folder / str(name or "")
    if not name or not target.is_file() or target.resolve().parent != folder.resolve():
        raise ClientPlatformError("backup_unavailable")
    from row_bot.designer.client_exports import _open_path

    return (opener or _open_path)("reveal", target)
