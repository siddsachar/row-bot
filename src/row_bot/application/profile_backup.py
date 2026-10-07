"""Back up the local profile and prepare a restore (Settings › Data, decision 21).

A backup is one zip of the profile without caches, logs, runtimes or
secrets. Secrets never enter an archive: API keys, sign-in tokens, the
browser profile, the WhatsApp session, device access and launch secrets are
left out (``profile_restore.SECRET_ENTRIES``), credential-looking files are
skipped anywhere in the tree, MCP header and environment values are blanked
in the archived copy and webhook secrets are removed from the archived
workflows. The manifest lists what will need signing in again.

A restore validates the archive (a Row-Bot backup, not newer than this app,
safe paths, no secrets) before anything is staged, and only a confirmed
command stages it; it is applied on the next start with the current profile
kept aside (``profile_restore.apply_pending``).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath

from row_bot import profile_restore
from row_bot.profile_restore import SECRET_ENTRIES, SECRET_NAMES, kept_in_place

FORMAT = "row-bot-backup"
FORMAT_VERSION = 1
MANIFEST = "row-bot-backup.json"
STATE_FILE = "backup_state.json"
MAX_ENTRIES = 500_000
MAX_BYTES = 256 * 1024 ** 3
# An attachment upload's staging file while it is kept for retries (attachments.py: a Windows TemporaryFile
# named upload_ plus 8 random characters, no extension). It is never user data, and on Windows it can't be
# read while open.
_UPLOAD_STAGING = re.compile(r"upload_[a-z0-9_]{8}")
SKIPPED_SHOWN = 20
logger = logging.getLogger(__name__)
LEFT_OUT = (
    "API keys, sign-in tokens and passwords",
    "Browser, WhatsApp and device sessions",
    "Caches, logs and downloaded runtimes",
)


class BackupError(Exception):
    """A backup or restore refusal with a public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _version(value: str) -> tuple[int, ...]:
    parts = []
    for part in str(value or "").split(".")[:4]:
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _app_version() -> str:
    from row_bot.version import __version__

    return __version__


def _files(data_dir: Path) -> list[Path]:
    """Every file a backup holds, in a stable order."""
    chosen: list[Path] = []
    for entry in sorted(data_dir.iterdir(), key=lambda item: item.name):
        if kept_in_place(entry.name) or entry.is_symlink():
            continue
        if entry.is_file():
            if not SECRET_NAMES.fullmatch(entry.name):
                chosen.append(entry)
            continue
        for path in sorted(entry.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(data_dir)
            if any(SECRET_NAMES.fullmatch(part) for part in relative.parts):
                continue
            if any(part.endswith(("-wal", "-shm")) for part in relative.parts):
                continue
            if relative.parts[0] == "media" and _UPLOAD_STAGING.fullmatch(path.name):
                continue
            chosen.append(path)
    return chosen


def _sqlite_copy(source: Path, target: Path) -> None:
    """A consistent copy of a live database (its WAL included)."""
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as live,             closing(sqlite3.connect(target)) as copy:
        live.backup(copy)


def _scrub_workflows(database: Path) -> int:
    """Remove webhook secrets from the archived copy of the workflows."""
    scrubbed = 0
    with closing(sqlite3.connect(database)) as conn, conn:
        try:
            rows = conn.execute("SELECT id, trigger FROM tasks WHERE trigger IS NOT NULL").fetchall()
        except sqlite3.Error:
            return 0
        for task_id, trigger in rows:
            try:
                value = json.loads(trigger)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("secret"):
                value["secret"] = ""
                conn.execute("UPDATE tasks SET trigger=? WHERE id=?", (json.dumps(value), task_id))
                scrubbed += 1
    return scrubbed


def _scrub_mcp(text: str) -> tuple[str, list[str]]:
    """Blank MCP header and environment values (they can hold keys)."""
    try:
        document = json.loads(text)
    except ValueError:
        return text, []
    touched: list[str] = []
    servers = document.get("servers") if isinstance(document, dict) else None
    if isinstance(servers, dict):
        for name, server in servers.items():
            if not isinstance(server, dict):
                continue
            for key in ("headers", "env"):
                values = server.get(key)
                if isinstance(values, dict) and any(values.values()):
                    server[key] = {item: "" for item in values}
                    if name not in touched:
                        touched.append(str(name))
    return json.dumps(document, indent=2), touched


def _sign_in_again(data_dir: Path, mcp: list[str], webhooks: int) -> list[dict]:
    """What will need signing in again after restoring (names only)."""
    items: list[dict] = []
    try:
        providers = json.loads((data_dir / "providers.json").read_text(encoding="utf-8")).get("providers", {})
    except (OSError, ValueError, AttributeError):
        providers = {}
    for provider_id, config in sorted(providers.items()) if isinstance(providers, dict) else []:
        if isinstance(config, dict) and any(key.endswith("_ref") and value for key, value in config.items()):
            items.append({"kind": "provider", "name": str(provider_id)})
    for folder, name in (("gmail", "Gmail"), ("calendar", "Google Calendar"), ("x", "X")):
        if (data_dir / folder).exists():
            items.append({"kind": "account", "name": name})
    if (data_dir / "whatsapp_session").exists():
        items.append({"kind": "channel", "name": "WhatsApp"})
    items.extend({"kind": "mcp", "name": name} for name in mcp)
    if webhooks:
        items.append({"kind": "webhooks", "name": f"{webhooks} webhook workflow{'s' if webhooks != 1 else ''}"})
    return items


def backup_state(data_dir: Path) -> dict:
    try:
        value = json.loads((data_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def create_backup(data_dir: Path, destination: Path, *, now: datetime | None = None) -> dict:
    """Write one archive of the profile into ``destination`` and return its facts."""
    moment = now or datetime.now()
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"Row-Bot backup {moment:%Y-%m-%d %H%M}"
    target = destination / f"{stem}.zip"
    counter = 2
    while target.exists():
        target = destination / f"{stem} {counter}.zip"
        counter += 1
    partial = target.with_suffix(".zip.partial")
    files = 0
    total = 0
    mcp: list[str] = []
    skipped: list[str] = []
    webhooks = 0
    try:
        with tempfile.TemporaryDirectory() as scratch, zipfile.ZipFile(
            partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for path in _files(data_dir):
                arcname = path.relative_to(data_dir).as_posix()
                if path.suffix in {".db", ".sqlite", ".sqlite3"}:
                    copy = Path(scratch) / f"{files}.db"
                    _sqlite_copy(path, copy)
                    if arcname == "tasks.db":
                        webhooks += _scrub_workflows(copy)
                    archive.write(copy, arcname)
                    total += copy.stat().st_size
                    copy.unlink()
                elif arcname == "mcp_servers.json":
                    text, mcp = _scrub_mcp(path.read_text(encoding="utf-8"))
                    archive.writestr(arcname, text)
                    total += len(text.encode())
                else:
                    try:
                        archive.write(path, arcname)
                    except OSError as error:
                        # One unreadable file (locked or vanished) is named, not a failed backup.
                        logger.warning("Backup left out %s: %s", arcname, error)
                        skipped.append(arcname)
                        continue
                    total += path.stat().st_size
                files += 1
            manifest = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "app_version": _app_version(),
                "created_at": moment.isoformat(timespec="seconds"),
                "files": files,
                "bytes": total,
                "left_out": list(LEFT_OUT),
                "sign_in_again": _sign_in_again(data_dir, mcp, webhooks),
                "skipped": skipped,
            }
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    size = target.stat().st_size
    state = {"last_backup_at": manifest["created_at"], "name": target.name, "bytes": size}
    (data_dir / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {**manifest, "name": target.name, "path": str(target), "size_bytes": size}


def _safe_member(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or ":" in name:
        return False
    parts = PurePosixPath(name).parts
    return bool(parts) and ".." not in parts and "." not in parts


def inspect_backup(archive: Path, *, app_version: str | None = None) -> dict:
    """Validate an archive; nothing is changed. Raises ``BackupError``."""
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise BackupError("backup_not_row_bot")
    running = app_version or _app_version()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_ENTRIES:
                raise BackupError("backup_too_large")
            try:
                manifest = json.loads(bundle.read(MANIFEST).decode("utf-8"))
            except (KeyError, ValueError, UnicodeDecodeError):
                raise BackupError("backup_not_row_bot") from None
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                raise BackupError("backup_not_row_bot")
            if (type(manifest.get("format_version")) is not int
                    or manifest["format_version"] > FORMAT_VERSION
                    or _version(manifest.get("app_version", "")) > _version(running)):
                raise BackupError("backup_newer")
            size = 0
            for member in members:
                name = member.filename
                if member.is_dir():
                    continue
                if not _safe_member(name):
                    raise BackupError("backup_invalid")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise BackupError("backup_invalid")  # a symbolic link
                if name == MANIFEST:
                    continue
                parts = PurePosixPath(name).parts
                # A backup never holds credentials: one that does is refused.
                if parts[0] in SECRET_ENTRIES or any(SECRET_NAMES.fullmatch(part) for part in parts):
                    raise BackupError("backup_invalid")
                # Machine-local files (caches, logs, locks) that another version
                # still wrote are skipped, never restored (B181).
                if kept_in_place(parts[0]):
                    continue
                size += member.file_size
            if size > MAX_BYTES:
                raise BackupError("backup_too_large")
    except zipfile.BadZipFile:
        raise BackupError("backup_not_row_bot") from None
    return {
        "created_at": str(manifest.get("created_at", "")),
        "app_version": str(manifest.get("app_version", "")),
        "files": int(manifest.get("files", 0) or 0),
        "bytes": size,
        "left_out": [str(item) for item in manifest.get("left_out", [])][:10],
        "sign_in_again": [
            {"kind": str(item.get("kind", "")), "name": str(item.get("name", ""))}
            for item in manifest.get("sign_in_again", [])
            if isinstance(item, dict)
        ][:100],
    }


def stage_restore(archive: Path, data_dir: Path, *, source_name: str,
                  app_version: str | None = None) -> dict:
    """Unpack a validated archive for the next start; the profile is untouched."""
    info = inspect_backup(archive, app_version=app_version)
    profile_restore.cancel(data_dir)
    staged = data_dir / profile_restore.PENDING_DIR / "files"
    staged.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or member.filename == MANIFEST:
                    continue
                if kept_in_place(PurePosixPath(member.filename).parts[0]):
                    continue  # machine-local: never restored (B181)
                target = staged.joinpath(*PurePosixPath(member.filename).parts)
                if not target.resolve().is_relative_to(staged.resolve()):
                    raise BackupError("backup_invalid")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink)
    except BaseException:
        profile_restore.cancel(data_dir)
        raise
    marker = {
        "staged_at": datetime.now().isoformat(timespec="seconds"),
        "created_at": info["created_at"],
        "source_name": source_name[:200],
        "sign_in_again": info["sign_in_again"],
    }
    (data_dir / profile_restore.PENDING_MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return {**info, "staged": True}
