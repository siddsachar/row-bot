"""Buddy pack discovery and validation.

A living pack (``runtime: living``) holds the cut-out art, its rig hints, an
optional atlas of expression patches and a still. Packs hatched before 5.3.4
(``generated_still`` / ``generated_motion_pack``) keep their files untouched:
their still becomes a living Buddy through a derived cut-out and rig
(``client_service``); their motion clips are no longer read.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import stat
from dataclasses import dataclass, replace
from itertools import islice
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime_paths import static_dir

logger = logging.getLogger(__name__)

_STATIC_DIR = static_dir()
_BUILTIN_PACKS_DIR = _STATIC_DIR / "buddy" / "builtins"
_DATA_DIR = get_row_bot_data_dir()
_BUDDY_STATIC_DIR = _DATA_DIR / "buddy"
_USER_PACKS_DIR = _BUDDY_STATIC_DIR / "packs"

LIVING_RUNTIME = "living"
LEGACY_RUNTIMES = frozenset({"generated_still", "generated_motion_pack"})
RUNTIMES = frozenset({LIVING_RUNTIME, *LEGACY_RUNTIMES})
_ID_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


@dataclass(frozen=True)
class BuddyPack:
    id: str
    name: str
    preview_path: pathlib.Path
    version: str = "1.0.0"
    status: str = "available"
    message: str = ""
    #: What is on disk: ``living``, or a legacy still/motion pack shown through a derived cut-out.
    runtime: str = LIVING_RUNTIME
    art_path: pathlib.Path | None = None
    rig_path: pathlib.Path | None = None
    patches_path: pathlib.Path | None = None

    @property
    def derived(self) -> bool:
        return self.runtime != LIVING_RUNTIME


def _load_manifest(path: pathlib.Path, *, strict_root: pathlib.Path | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(read_buddy_asset(path, root=strict_root, limit=65536).decode("utf-8")
                         if strict_root is not None else path.read_text(encoding="utf-8"))
        if strict_root is not None and not isinstance(raw, dict):
            raise ValueError("buddy_pack_unavailable")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        if strict_root is not None:
            raise ValueError("buddy_pack_unavailable") from None
        logger.warning("Failed to load Buddy pack manifest %s", path, exc_info=True)
        return {}


def buddy_static_dir() -> pathlib.Path:
    """Return the user's Buddy data directory (hatched packs live in ``packs``)."""

    return _BUDDY_STATIC_DIR


def _pack_dir_for(pack_id: str) -> pathlib.Path:
    user_pack = _USER_PACKS_DIR / pack_id
    if (user_pack / "manifest.json").exists():
        return user_pack
    return _BUILTIN_PACKS_DIR / pack_id


def _leaf(value: Any, default: str, *, pack_dir: pathlib.Path, strict: bool) -> pathlib.Path:
    """A pack file named by its manifest; strict loading keeps the name as given (no resolving)."""

    candidate = pathlib.Path(str(value or default)).expanduser()
    if strict:
        # Keep the named path intact so the no-follow ownership checks see any
        # symbolic link; never resolve away that evidence.
        return candidate if candidate.is_absolute() else pack_dir / candidate
    if not candidate.is_absolute():
        return (pack_dir / candidate).resolve()
    resolved = candidate.resolve(strict=False)
    if resolved.exists():
        return resolved
    # A legacy absolute preview from another data folder: map it into this pack.
    mapped = (pack_dir / resolved.name).resolve()
    return mapped if mapped.exists() else resolved


def _runtime_of(manifest: dict[str, Any], pack_dir: pathlib.Path) -> str:
    runtime = str(manifest.get("runtime") or "")
    if runtime in RUNTIMES:
        return runtime
    if manifest.get("rig") or (pack_dir / "rig.json").exists():
        return LIVING_RUNTIME
    if isinstance(manifest.get("clips"), dict) or (pack_dir / "motions" / "manifest.json").exists():
        return "generated_motion_pack"
    return "generated_still"


def load_buddy_pack(pack_id: str = "glyph", *, strict: bool = False) -> BuddyPack:
    if strict and (not isinstance(pack_id, str) or not 1 <= len(pack_id) <= 128
                   or any(ch not in _ID_CHARACTERS for ch in pack_id)):
        raise ValueError("buddy_pack_unavailable")
    pack_dir = _pack_dir_for(pack_id)
    manifest = _load_manifest(pack_dir / "manifest.json", strict_root=pack_dir if strict else None)
    if strict and manifest.get("runtime") not in (None, *RUNTIMES):
        raise ValueError("buddy_pack_unavailable")
    runtime = _runtime_of(manifest, pack_dir)
    leaf = lambda key, default: _leaf(manifest.get(key), default, pack_dir=pack_dir, strict=strict)  # noqa: E731
    if runtime == LIVING_RUNTIME:
        art = leaf("art", "art.png")
        pack = BuddyPack(
            id=str(manifest.get("id") or pack_id),
            name=str(manifest.get("name") or "Buddy"),
            version=str(manifest.get("version") or "1.0.0"),
            runtime=runtime,
            art_path=art,
            rig_path=leaf("rig", "rig.json"),
            patches_path=leaf("patches", "patches.png") if manifest.get("patches") else None,
            preview_path=leaf("preview", str(manifest.get("art") or "art.png")),
        )
    else:
        pack = BuddyPack(
            id=str(manifest.get("id") or pack_id),
            name=str(manifest.get("name") or "Buddy"),
            version=str(manifest.get("version") or "1.0.0"),
            runtime=runtime,
            preview_path=_leaf(manifest.get("preview") or manifest.get("preview_path"), "preview.png",
                               pack_dir=pack_dir, strict=strict),
        )
    if strict:
        if pack.id != pack_id or len(pack.name) > 256 or len(pack.version) > 128:
            raise ValueError("buddy_pack_unavailable")
        for path in pack_files(pack).values():
            _buddy_asset_identity(path, root=pack_dir)
    return validate_buddy_pack(pack)


def pack_files(pack: BuddyPack) -> dict[str, pathlib.Path]:
    """The pack's own files by asset id (a legacy pack has only its still)."""

    if pack.derived:
        return {"preview": pack.preview_path}
    files = {"art": pack.art_path, "rig": pack.rig_path, "preview": pack.preview_path}
    if pack.patches_path is not None:
        files["patches"] = pack.patches_path
    return {key: path for key, path in files.items() if path is not None}


def list_buddy_packs(*, strict: bool = False) -> list[BuddyPack]:
    pack_ids: set[str] = set()
    for base in (_BUILTIN_PACKS_DIR, _USER_PACKS_DIR):
        if base.exists():
            entries = list(islice(base.iterdir(), 4097)) if strict else base.iterdir()
            if strict and len(entries) > 4096:
                raise ValueError("buddy_pack_limit")
            pack_ids.update(path.name for path in entries if path.is_dir())
    return [load_buddy_pack(pack_id, strict=strict) for pack_id in sorted(pack_ids)]


def _buddy_asset_identity(path: pathlib.Path, *, root: pathlib.Path) -> tuple[int, int, int, int]:
    """Validate a contained regular leaf without following a substituted ancestor."""
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    try:
        if ".." in path.parts or ".." in root.parts:
            raise ValueError
        path.absolute().relative_to(root.absolute())
        with _empty_parent_guard(path.parent, _directory_identity(path.parent, parent=True)) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False) if parent is not None else path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0) & 0x400 or not 0 < info.st_size <= 64 * 1024 * 1024):
                raise ValueError
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    except (OSError, ValueError):
        raise ValueError("buddy_asset_unavailable") from None


def read_buddy_asset(path: pathlib.Path, *, root: pathlib.Path, limit: int = 64 * 1024 * 1024) -> bytes:
    """Opened-handle bounded read; never serve arbitrary manifest paths."""
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    try:
        before = _buddy_asset_identity(path, root=root)
        if before[2] > limit:
            raise ValueError
        with _empty_parent_guard(path.parent, _directory_identity(path.parent, parent=True)) as parent:
            fd = os.open(path.name if parent is not None else path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != before or opened.st_nlink != 1:
                    raise ValueError
                data = handle.read(limit + 1)
                finished = os.fstat(handle.fileno())
            if (len(data) > limit or (finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns) != before
                    or _buddy_asset_identity(path, root=root) != before):
                raise ValueError
            return data
    except (OSError, ValueError):
        raise ValueError("buddy_asset_unavailable") from None


def _usable(path: pathlib.Path | None) -> bool:
    return bool(path) and path.exists() and path.is_file() and path.stat().st_size > 0


def validate_buddy_pack(pack: BuddyPack) -> BuddyPack:
    problems: list[str] = []
    if not _usable(pack.preview_path):
        problems.append("still image is missing or empty")
    if not pack.derived:
        if not _usable(pack.art_path):
            problems.append("art is missing or empty")
        if not _usable(pack.rig_path):
            problems.append("rig is missing or empty")
        if pack.patches_path is not None and not _usable(pack.patches_path):
            problems.append("expression patches are missing or empty")
    if problems:
        return replace(pack, status="unavailable", message="; ".join(problems))
    return replace(pack, status="available", message="Ready")
