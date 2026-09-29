"""Stage a validated backup and apply it when Row-Bot next starts (decision 21).

Standard library only: the server applies a pending restore at the very top
of ``row_bot.app``, before anything opens a database. The current profile is
kept aside in ``before-restore-<time>`` inside the data folder; what a backup
never holds (secrets, runtimes, caches and logs) stays where it is, so a
restore on the same computer keeps working sign-ins and downloaded runtimes.
A restore is only ever staged by an explicit, confirmed command.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PENDING_DIR = ".restore-pending"
PENDING_MARKER = "restore-pending.json"
RESULT_FILE = "restore-result.json"
ASIDE_PREFIX = "before-restore-"

# Never in a backup: secrets and sign-ins that only work on this computer.
SECRET_ENTRIES = frozenset({
    "api_keys.json", "plugin_secrets.json", "secure-secrets", "whatsapp_session",
    "browser_profile", "mobile.db", "gmail", "calendar", "x", "runtime",
    "tailscale_serve_ownership.json",
})
# Rebuildable or machine-local: caches, runtimes, logs and earlier backups.
LOCAL_ENTRIES = frozenset({
    "cache", "font_cache", "vector_store", "memory_vectors", "document_index", "node",
    "runtimes", "piper", "kokoro", "logs", "plugin_logs", "crashes", "feedback_reports",
    "recovery", "evolution_backups", "migration-backups", "migration_reports", "backups",
    "doc_staging", "stale_plugins", "document_ingestion", PENDING_DIR,
    "backup_state.json", RESULT_FILE,
})
LOCAL_NAMES = re.compile(
    r"(.*_cache\.json|.*\.log|.*\.log\.prev|.*\.lock|launcher-.*|tmp.*|.*-wal|.*-shm"
    r"|mobile\.db\..*|.*-backup-\d{8}-\d{6}\.json|" + re.escape(ASIDE_PREFIX) + r".*)",
    re.IGNORECASE,
)
# Anywhere in the tree: files that hold credentials.
SECRET_NAMES = re.compile(
    r"(token.*\.json|credentials.*\.json|.*\.pem|.*\.key|.*\.p12|.*secret.*|\.env|.*\.env)",
    re.IGNORECASE,
)


def kept_in_place(name: str) -> bool:
    """A top-level entry a backup never holds and a restore never touches."""
    return name in SECRET_ENTRIES or name in LOCAL_ENTRIES or bool(LOCAL_NAMES.fullmatch(name))


def pending(data_dir: Path) -> dict | None:
    marker = data_dir / PENDING_MARKER
    if not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def cancel(data_dir: Path) -> bool:
    """Drop a staged restore; the current profile is untouched."""
    had = (data_dir / PENDING_MARKER).exists() or (data_dir / PENDING_DIR).exists()
    (data_dir / PENDING_MARKER).unlink(missing_ok=True)
    shutil.rmtree(data_dir / PENDING_DIR, ignore_errors=True)
    return had


def result(data_dir: Path) -> dict | None:
    path = data_dir / RESULT_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def dismiss_result(data_dir: Path) -> None:
    (data_dir / RESULT_FILE).unlink(missing_ok=True)


def _write_result(data_dir: Path, value: dict) -> None:
    (data_dir / RESULT_FILE).write_text(json.dumps(value, indent=2), encoding="utf-8")


def apply_pending(data_dir: Path, *, now: datetime | None = None) -> dict | None:
    """Apply a staged restore; called once at start, before any database opens.

    Everything a backup can hold is moved aside first, then the staged files
    move in. Any failure puts the kept-aside profile back and says so.
    """
    marker = pending(data_dir)
    staged = data_dir / PENDING_DIR / "files"
    if marker is None:
        return None
    if not staged.is_dir():
        cancel(data_dir)
        return None
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    aside = data_dir / f"{ASIDE_PREFIX}{stamp}"
    aside.mkdir(parents=True, exist_ok=False)
    moved_aside: list[str] = []
    moved_in: list[str] = []
    try:
        for entry in sorted(data_dir.iterdir(), key=lambda item: item.name):
            if entry.name == aside.name or entry.name == PENDING_MARKER or kept_in_place(entry.name):
                continue
            shutil.move(str(entry), str(aside / entry.name))
            moved_aside.append(entry.name)
        for entry in sorted(staged.iterdir(), key=lambda item: item.name):
            if kept_in_place(entry.name):
                continue  # Never let an archive replace what a backup cannot hold.
            shutil.move(str(entry), str(data_dir / entry.name))
            moved_in.append(entry.name)
    except OSError as exc:
        logger.error("Restore could not be applied; keeping the current profile", exc_info=True)
        for name in moved_in:
            target = data_dir / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for name in moved_aside:
            shutil.move(str(aside / name), str(data_dir / name))
        shutil.rmtree(aside, ignore_errors=True)
        cancel(data_dir)
        value = {"status": "failed", "reason": type(exc).__name__,
                 "source_created_at": marker.get("created_at", "")}
        _write_result(data_dir, value)
        return value
    cancel(data_dir)
    value = {
        "status": "applied",
        "applied_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "source_created_at": marker.get("created_at", ""),
        "source_name": marker.get("source_name", ""),
        "kept_aside": aside.name,
        "sign_in_again": marker.get("sign_in_again", []),
    }
    _write_result(data_dir, value)
    logger.info("Restored the profile from a backup; the previous one is in %s", aside.name)
    return value


def apply_on_start() -> None:
    """The server's first step: apply a staged restore, never raising."""
    try:
        from row_bot.data_paths import get_row_bot_data_dir

        apply_pending(get_row_bot_data_dir())
    except Exception:  # pragma: no cover - start must go on regardless
        logger.error("Pending restore check failed", exc_info=True)
