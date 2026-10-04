"""Files a person picks to add: a skill or package archive, staged privately and only read.

Nothing in a staged file runs. A skill is read into memory and checked like any other
skill; a package is inspected by its owner from the staged copy after consent. A
``.mcpb`` bundle is kept for the plan to report as not available yet.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import time
from uuid import uuid4
import zipfile

from row_bot.integrations.safe import write_atomic

MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 200
MAX_UNPACKED = 5 * 1024 * 1024
KEEP_SECONDS = 24 * 3600
_ID = re.compile(r"[0-9a-f]{32}\.(zip|mcpb)")
_MANIFESTS = {"plugin.json", ".claude-plugin/plugin.json", "row-bot-plugin.json"}


def _root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir
    return get_row_bot_data_dir() / "integration_uploads"


def path(upload: str) -> Path:
    if not _ID.fullmatch(upload):
        raise ValueError("invalid_upload")
    return _root() / upload


def _members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Regular files with safe relative paths, within the count and size bounds."""
    found = [info for info in archive.infolist() if not info.is_dir()]
    if len(found) > MAX_FILES or sum(info.file_size for info in found) > MAX_UNPACKED:
        raise ValueError("upload_too_large")
    for info in found:
        name = PurePosixPath(info.filename)
        if (name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename
                or (info.external_attr >> 16) & 0o170000 == 0o120000):  # No links, drive names or escapes.
            raise ValueError("unsafe_upload")
    return found


def _top(names: list[str]) -> str:
    """The single folder an archive wraps everything in, if any."""
    tops = {name.split("/", 1)[0] for name in names}
    return next(iter(tops)) + "/" if len(tops) == 1 and all("/" in name for name in names) else ""


def stage(data: bytes, filename: str) -> dict:
    """Keep one picked file and say what it holds: ``skill``, ``plugin`` or ``mcpb``."""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in {".zip", ".skill", ".mcpb"} or not data or len(data) > MAX_BYTES:
        raise ValueError("invalid_upload")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob("*"):  # Picked files are kept a day at most.
        if old.is_file() and time.time() - old.stat().st_mtime > KEEP_SECONDS:
            old.unlink(missing_ok=True)
    name = uuid4().hex + (".mcpb" if suffix == ".mcpb" else ".zip")
    write_atomic(root / name, data)
    title = PurePosixPath(filename.replace("\\", "/")).stem[:128] or "Added file"
    if suffix == ".mcpb":
        return {"upload": name, "kind": "mcpb", "name": title}
    try:
        with zipfile.ZipFile(root / name) as archive:
            names = [info.filename for info in _members(archive)]
    except zipfile.BadZipFile:
        raise ValueError("invalid_upload") from None
    top = _top(names)
    inner = {n.removeprefix(top) for n in names}
    kind = "plugin" if inner & _MANIFESTS else "skill" if "SKILL.md" in inner else ""
    if not kind:
        raise ValueError("unsupported_upload")
    return {"upload": name, "kind": kind, "name": top.rstrip("/")[:128] or title}


def skill_files(upload: str) -> tuple[list[tuple[str, bytes]], str]:
    """The files of a staged skill, relative to its SKILL.md folder, and the folder's name."""
    with zipfile.ZipFile(path(upload)) as archive:
        members = _members(archive)
        top = _top([info.filename for info in members])
        files = [(info.filename.removeprefix(top), archive.read(info)) for info in members]
    if sum(len(data) for _name, data in files) > MAX_UNPACKED:
        raise ValueError("upload_too_large")
    return files, top.rstrip("/")
