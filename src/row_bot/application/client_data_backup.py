"""Settings › Data: Back up now and Restore from backup, from any signed-in device.

Backups and restores are long, so one runs at a time in the background and
the page reads its progress. A restore is two steps: the archive is checked
and described (a review bound to that file), then only an explicit
confirmation of that review stages it for the next start. Nothing here ever
replaces the profile in use, and no secret is ever written to an archive.

The desktop app shows a backup in its folder and picks an archive with its
own file picker. Any other device downloads the last backup, and sends an
archive to restore in bounded chunks. That archive waits in a private staging
file in the data folder's kept-in-place area (never in a backup, never
touched by a restore) until it is checked, then staged or cancelled, or it
expires; it is deleted afterwards either way.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable
from uuid import uuid4

from row_bot import profile_restore
from row_bot.application import profile_backup
from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_JOB: dict = {}
# Review id: (archive, expiry, name shown, the upload it came from).
_REVIEWS: dict[str, tuple[Path, float, str, str | None]] = {}
_KEY = secrets.token_bytes(32)
_REVIEW_SECONDS = 600
_BACKUPS_FOLDER = "Backups"
# Downloads and uploads move at most this much per read or request.
CHUNK_BYTES = 1024 * 1024
_UPLOAD_SECONDS = 1800
_UPLOADS_HELD = 4
_UPLOAD_FILE = re.compile(r"upload-[a-z0-9_]{8}\.zip")
_now = time.monotonic


@dataclass(eq=False)
class _Upload:
    id: str
    session_id: str
    name: str
    size: int
    path: Path
    expires: float
    received: int = 0
    # The last chunk written: a retry of it (its answer was lost) is answered, not refused.
    last: tuple[int, str] | None = None
    # Checks and stagings reading the file; it is deleted after the last one.
    users: int = 0
    gone: bool = False


# Taken before _LOCK, never while holding it.
_UPLOADS_LOCK = threading.RLock()
_UPLOADS: dict[str, _Upload] = {}


def _data_dir() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir()


def _workspace() -> Path | None:
    """The workspace: never part of a backup, and a restore leaves it alone."""
    from row_bot.application.conversation_creation import configured_workspace_root

    try:
        return configured_workspace_root()
    except (OSError, ValueError):
        return None


def _backups_folder() -> Path:
    root = _workspace()
    if root is None:
        raise ClientPlatformError("backup_storage_unavailable")
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
        return {key: _JOB.get(key) for key in ("kind", "status", "started_at", "finished_at", "code", "name",
                                               "skipped", "left_out", "sign_in_again")}


def read_state() -> dict:
    """What the Data page shows, the same on every signed-in device."""
    data = _data_dir()
    state = profile_backup.backup_state(data)
    pending = profile_restore.pending(data)
    result = profile_restore.result(data)
    return {
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
                            name=outcome.get("name"),
                            skipped=list(outcome.get("skipped") or [])[:profile_backup.SKIPPED_SHOWN],
                            # The archive's own manifest says what moves with it (U9).
                            left_out=[str(item)[:200] for item in outcome.get("left_out") or []][:10],
                            sign_in_again=_sign_ins(outcome.get("sign_in_again")))
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


def inspect_restore(path: Path, *, name: str | None = None, upload_id: str | None = None) -> dict:
    """Check an archive and remember it for one confirmation."""
    info = profile_backup.inspect_backup(path)
    review = _review_id(path, info)
    shown = (name or path.name)[:200]
    with _LOCK:
        now = _now()
        for key in [key for key, entry in _REVIEWS.items() if entry[1] < now]:
            _REVIEWS.pop(key, None)
        _REVIEWS[review] = (path, now + _REVIEW_SECONDS, shown, upload_id)
    return {**info, "review_id": review, "source_name": shown}


def restore(review_id: str) -> None:
    """Stage the reviewed archive for the next start (the confirmed step)."""
    with _LOCK:
        if _JOB.get("status") == "running":
            raise ClientPlatformError("data_job_running")  # the review stays for afterwards
        entry = _REVIEWS.pop(review_id, None)
    if entry is None or entry[1] < _now():
        raise ClientPlatformError("backup_review_expired")
    path, _, name, upload_id = entry
    # An uploaded archive is used once: staged or not, it is deleted afterwards.
    upload = _use(upload_id) if upload_id else None
    if upload_id and upload is None:
        raise ClientPlatformError("backup_review_expired")
    try:
        info = profile_backup.inspect_backup(path)
        if not hmac.compare_digest(_review_id(path, info), review_id):
            raise ClientPlatformError("backup_review_expired")  # the file changed since it was checked
        data = _data_dir()
        outside = tuple(folder for folder in (_workspace(),) if folder is not None)

        def work() -> dict:
            try:
                return {**profile_backup.stage_restore(path, data, source_name=name, outside=outside),
                        "name": name}
            finally:
                _used_up(upload)

        _start("restore", work)
    except BaseException:
        _used_up(upload)
        raise


def backup() -> None:
    folder = _backups_folder()
    data = _data_dir()
    _start("backup", lambda: profile_backup.create_backup(data, folder, outside=(folder.parent,)))


def cancel_restore() -> bool:
    return profile_restore.cancel(_data_dir())


def dismiss_result() -> None:
    profile_restore.dismiss_result(_data_dir())


def _last_backup() -> Path:
    """The last backup: only ever a file directly in the Backups folder."""
    name = profile_backup.backup_state(_data_dir()).get("name")
    folder = _backups_folder()
    target = folder / str(name or "")
    if (not name or target.is_symlink() or not target.is_file()
            or target.resolve().parent != folder.resolve()):
        raise ClientPlatformError("backup_unavailable")
    return target


def reveal_last(opener: Callable[[str, Path], bool] | None = None) -> bool:
    """Show the last backup in the file manager (argv only, never a shell)."""
    target = _last_backup()
    from row_bot.designer.client_exports import _open_path

    return (opener or _open_path)("reveal", target)


def open_last_backup(expected: str | None = None) -> tuple[BinaryIO, int, str]:
    """Open the last backup to download it: (file, size, name).

    ``expected`` is the name the page showed; a newer backup since then is
    refused rather than sent under the older name.
    """
    target = _last_backup()
    if expected is not None and expected != target.name:
        raise ClientPlatformError("backup_unavailable")
    try:
        source = open(target, "rb", opener=lambda path, flags: os.open(path, flags | getattr(os, "O_NOFOLLOW", 0)))
    except OSError:
        raise ClientPlatformError("backup_unavailable") from None
    try:
        opened = os.fstat(source.fileno())
        # The file sent is the one checked, never one swapped in since.
        if not os.path.samestat(opened, os.lstat(target)):
            raise ClientPlatformError("backup_unavailable")
    except BaseException:
        source.close()
        raise
    return source, opened.st_size, target.name


# Restore from another device: the archive arrives in chunks first.

def _uploads_folder() -> Path:
    """Private and kept in place: never in a backup, and a restore leaves it alone."""
    folder = _data_dir() / profile_restore.UPLOADS_DIR
    if folder.is_symlink():
        raise ClientPlatformError("backup_storage_unavailable")
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or not folder.is_dir():
        raise ClientPlatformError("backup_storage_unavailable")
    return folder


def _view(upload: _Upload) -> dict:
    return {"upload_id": upload.id, "size_bytes": upload.size, "received_bytes": upload.received,
            "chunk_bytes": CHUNK_BYTES,
            "expires_in_seconds": max(0, min(_UPLOAD_SECONDS, int(upload.expires - _now())))}


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("A restore upload stays until the next sweep", exc_info=True)


def _retire(upload: _Upload) -> None:
    """Cancelled, expired or used: never usable again, deleted once unread. Holds _UPLOADS_LOCK."""
    upload.gone = True
    with _LOCK:
        for key in [key for key, entry in _REVIEWS.items() if entry[3] == upload.id]:
            _REVIEWS.pop(key, None)
    if not upload.users:
        _UPLOADS.pop(upload.id, None)
        _unlink(upload.path)


def _release(upload: _Upload) -> None:
    with _UPLOADS_LOCK:
        upload.users -= 1
        if upload.gone and not upload.users:
            _UPLOADS.pop(upload.id, None)
            _unlink(upload.path)


def _use(upload_id: str) -> _Upload | None:
    with _UPLOADS_LOCK:
        upload = _UPLOADS.get(upload_id)
        if upload is None or upload.gone:
            return None
        upload.users += 1
        return upload


def _used_up(upload: _Upload | None) -> None:
    if upload is not None:
        with _UPLOADS_LOCK:
            _retire(upload)
        _release(upload)


def _expire() -> None:
    now = _now()
    for upload in list(_UPLOADS.values()):
        if not upload.gone and not upload.users and upload.expires <= now:
            _retire(upload)


def _sweep() -> None:
    """Expired uploads, and staging files no upload owns (an earlier run's). Holds _UPLOADS_LOCK."""
    _expire()
    folder = _data_dir() / profile_restore.UPLOADS_DIR
    if folder.is_symlink() or not folder.is_dir():
        return
    owned = {upload.path.name for upload in _UPLOADS.values()}
    for entry in folder.iterdir():
        if _UPLOAD_FILE.fullmatch(entry.name) and entry.name not in owned and entry.is_file():
            _unlink(entry)


def _upload(session_id: str, upload_id: str) -> _Upload:
    _expire()
    upload = _UPLOADS.get(upload_id)
    if upload is None or upload.gone:
        raise ClientPlatformError("upload_expired")
    if upload.session_id != session_id:
        raise ClientPlatformError("not_found")
    return upload


def begin_upload(session_id: str, *, name: str, size_bytes: int) -> dict:
    """Make room for an archive another device sends to restore from."""
    if (not name or len(name) > 200 or any(char in name for char in "/\\:")
            or any(ord(char) < 32 for char in name) or size_bytes < 1):
        raise ClientPlatformError("invalid_command")
    if size_bytes > profile_backup.MAX_BYTES:
        raise ClientPlatformError("backup_too_large")
    with _UPLOADS_LOCK:
        # A page sends one archive at a time: starting again lets the earlier one go.
        for upload in [item for item in _UPLOADS.values() if item.session_id == session_id and not item.gone]:
            _retire(upload)
        _sweep()
        held = [item for item in _UPLOADS.values() if not item.gone]
        if len(held) >= _UPLOADS_HELD:
            raise ClientPlatformError("rate_limited")
        folder = _uploads_folder()
        if shutil.disk_usage(folder).free < size_bytes + sum(item.size - item.received for item in held):
            raise ClientPlatformError("backup_storage_full")
        handle, path = tempfile.mkstemp(prefix="upload-", suffix=".zip", dir=folder)
        os.close(handle)
        upload = _Upload(str(uuid4()), session_id, name, size_bytes, Path(path), _now() + _UPLOAD_SECONDS)
        _UPLOADS[upload.id] = upload
        return _view(upload)


def upload_status(session_id: str, upload_id: str) -> dict:
    with _UPLOADS_LOCK:
        return _view(_upload(session_id, upload_id))


def write_upload(session_id: str, upload_id: str, offset: int, data: bytes) -> dict:
    """Write the next chunk, in order; the declared size bounds the file."""
    digest = hashlib.sha256(data).hexdigest()
    with _UPLOADS_LOCK:
        upload = _upload(session_id, upload_id)
        if upload.last is not None and upload.last[0] == offset and offset + len(data) == upload.received:
            if upload.last[1] != digest:
                raise ClientPlatformError("revision_conflict")
        elif offset != upload.received or offset >= upload.size or len(data) != min(CHUNK_BYTES,
                                                                                      upload.size - offset):
            raise ClientPlatformError("invalid_command")
        else:
            with open(upload.path, "r+b") as sink:
                sink.seek(offset)
                sink.write(data)
            upload.received += len(data)
            upload.last = (offset, digest)
            upload.expires = _now() + _UPLOAD_SECONDS
        return _view(upload)


def cancel_upload(session_id: str, upload_id: str) -> None:
    with _UPLOADS_LOCK:
        _retire(_upload(session_id, upload_id))


def inspect_upload(session_id: str, upload_id: str) -> dict:
    """Check an archive this session sent, as a picked one is checked."""
    with _UPLOADS_LOCK:
        upload = _upload(session_id, upload_id)
        if upload.received != upload.size:
            raise ClientPlatformError("upload_incomplete")
        upload.users += 1
    try:
        review = inspect_restore(upload.path, name=upload.name, upload_id=upload.id)
    except profile_backup.BackupError:
        with _UPLOADS_LOCK:
            _retire(upload)  # not a backup this Row-Bot restores: nothing else to do with it
        raise
    finally:
        _release(upload)
    with _UPLOADS_LOCK:
        if upload.gone:  # cancelled while it was checked
            _retire(upload)
            raise ClientPlatformError("upload_expired")
        # The review and its upload expire together.
        upload.expires = _now() + _REVIEW_SECONDS
    return review


def expire_uploads() -> None:
    """Delete uploads past their time, and files an earlier run left."""
    try:
        with _UPLOADS_LOCK:
            _sweep()
    except OSError:
        logger.warning("Restore uploads could not be swept", exc_info=True)


def close_uploads() -> None:
    """At shutdown: upload ids never outlive the server, so neither do their files."""
    with _UPLOADS_LOCK:
        for upload in list(_UPLOADS.values()):
            _retire(upload)
