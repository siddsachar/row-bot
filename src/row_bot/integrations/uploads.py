"""Files a person picks to add: a skill or package archive or an MCP bundle, staged privately and only read.

Nothing in a staged file runs. A skill is read into memory and checked like any other
skill; a package is inspected by its owner from the staged copy after consent. A
``.mcpb`` bundle's manifest, paths and signature are checked when it is picked, and it is
unpacked only after the person reviews it in a plan.
"""
from __future__ import annotations

import io
from pathlib import Path, PurePosixPath
import re
import time
from uuid import uuid4
import zipfile

from row_bot.integrations.safe import write_atomic

MAX_BYTES = 128 * 1024 * 1024  # A bundle carries its own dependencies.
MAX_ARCHIVE = 20 * 1024 * 1024  # A skill or a package.
MAX_FILES = 200
MAX_UNPACKED = 5 * 1024 * 1024
KEEP_SECONDS = 24 * 3600
MAX_KEPT = 16
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
    """Check one picked file in memory, then keep it and say what it holds: ``skill``, ``plugin`` or ``mcpb``."""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in {".zip", ".skill", ".mcpb"} or not data or len(data) > (MAX_BYTES if suffix == ".mcpb" else MAX_ARCHIVE):
        raise ValueError("invalid_upload")
    title = PurePosixPath(filename.replace("\\", "/")).stem[:128] or "Added file"
    kind, name, bundle = "mcpb", title, None
    if suffix == ".mcpb":
        from row_bot.mcp_client import bundles
        bundle = bundles.read(data)  # Manifest, paths, platform and signature; nothing is unpacked or run.
        name = (bundle.display_name or bundle.name)[:128]
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = [info.filename for info in _members(archive)]
        except zipfile.BadZipFile:
            raise ValueError("invalid_upload") from None
        top = _top(names)
        inner = {n.removeprefix(top) for n in names}
        kind = "plugin" if inner & _MANIFESTS else "skill" if "SKILL.md" in inner else ""
        if not kind:
            raise ValueError("unsupported_upload")
        name = top.rstrip("/")[:128] or title
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    kept = sorted((p for p in root.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    for index, old in enumerate(kept):  # Picked files are kept a day at most, and only the latest few.
        if index >= MAX_KEPT - 1 or time.time() - old.stat().st_mtime > KEEP_SECONDS:
            old.unlink(missing_ok=True)
    upload = uuid4().hex + (".mcpb" if kind == "mcpb" else ".zip")
    write_atomic(root / upload, data)
    return {"upload": upload, "kind": kind, "name": name, "bundle": bundle}


def skill_files(upload: str) -> tuple[list[tuple[str, bytes]], str]:
    """The files of a staged skill, relative to its SKILL.md folder, and the folder's name."""
    with zipfile.ZipFile(path(upload)) as archive:
        members = _members(archive)
        top = _top([info.filename for info in members])
        files = [(info.filename.removeprefix(top), archive.read(info)) for info in members]
    if sum(len(data) for _name, data in files) > MAX_UNPACKED:
        raise ValueError("upload_too_large")
    return files, top.rstrip("/")
