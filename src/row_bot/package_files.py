"""Small, nonexecuting filesystem checks shared by managed package owners."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PurePosixPath


def relative_package_path(value: str) -> str:
    """Reject paths with different or unsafe meanings on supported platforms."""
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("unsafe_package_path")
    parts = value.split("/")
    if any(
        part in {"", ".", ".."} or part[-1:] in {".", " "}
        or re.search(r'[\x00-\x1f:<>"|?*]', part)
        or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise ValueError("unsafe_package_path")
    return str(PurePosixPath(value))


def contained_path(root: Path, relative: str) -> Path:
    """Resolve a package path without following a link or reparse point."""
    relative_package_path(relative)
    root = root.absolute()
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError("package_link_not_allowed")
    path = root
    for part in ("", *PurePosixPath(relative).parts):
        path = path / part if part else path
        if path.is_symlink() or path.is_junction():
            raise ValueError("package_link_not_allowed")
        if path.exists():
            info = path.lstat()
            if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("package_link_not_allowed")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("unsafe_package_path")
    return path


def check_package_tree(root: Path, *, max_files: int = 8192, max_bytes: int = 64 * 1024 * 1024) -> None:
    """Bound reads/copies, refusing links, special files and case collisions."""
    count = total = 0
    contained_path(root, ".package-probe")
    if not root.is_dir():
        raise ValueError("package_root_unavailable")
    seen: set[str] = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            contained_path(root, relative)
            key = relative.casefold()
            if key in seen:
                raise ValueError("package_path_collision")
            seen.add(key)
            count += 1
            info = path.stat()
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("package_file_type_invalid")
            if count > max_files or total > max_bytes:
                raise ValueError("package_capacity_exceeded")
