"""Conversation exports saved on this computer without a Save dialog (B238).

When the desktop window cannot show its Save dialog (its native bridge is
reconnecting), the local owner's export is written into the workspace
folder's ``Exports`` folder (by default Documents/Row-Bot/Exports). The name
is made safe, the file never leaves that folder, never replaces another file
and is readable by its owner only.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

_UNSAFE = re.compile(r"[^\w .-]+")
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                       *(f"LPT{n}" for n in range(1, 10))})
_MAX_NAME = 120


def exports_folder() -> Path:
    """``<workspace folder>/Exports``, created on first use; never a link."""
    from row_bot.application.conversation_creation import configured_workspace_root
    try:
        root = configured_workspace_root()
        folder = root / "Exports"
        if folder.is_symlink() or folder.is_junction():
            raise ValueError("export_storage_unavailable")
        folder.mkdir(exist_ok=True)
        if not folder.is_dir() or folder.resolve(strict=True).parent != root.resolve(strict=True):
            raise ValueError("export_storage_unavailable")
    except (OSError, ValueError):
        raise ValueError("export_storage_unavailable") from None
    return folder


def safe_export_name(name: str) -> str:
    """One plain file name: no folders, control characters or device names."""
    base = _UNSAFE.sub("-", Path(str(name).replace("\\", "/")).name).strip(" .-")
    stem, dot, extension = base.rpartition(".")
    if not dot:
        stem, extension = base, ""
    extension = extension[:16]
    stem = stem.strip(" .-")[:_MAX_NAME - len(extension) - 1] or "export"
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "export-" + stem
    return f"{stem}.{extension}" if extension else stem


def save_export(name: str, data: bytes, *, validate: Callable[[], None]) -> dict[str, str]:
    """Write one new file into Exports and say which, never where on disk."""
    folder = exports_folder()
    file_name = safe_export_name(name)
    stem, dot, extension = file_name.rpartition(".")
    if not dot:
        stem, extension = file_name, ""
    validate()
    for attempt in range(1, 1000):
        candidate = file_name if attempt == 1 else f"{stem} {attempt}{dot}{extension}"
        target = folder / candidate
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            continue
        except OSError:
            raise ValueError("export_storage_unavailable") from None
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
        except OSError:
            target.unlink(missing_ok=True)
            raise ValueError("export_storage_unavailable") from None
        return {"file_name": candidate, "folder": f"{folder.parent.name} › {folder.name}"}
    raise ValueError("export_capacity_reached")


def _show_in_folder(path: Path) -> None:
    """Show a file in the file manager; argv only, never a shell."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"], close_fds=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)], close_fds=True, start_new_session=True)
    else:
        subprocess.Popen(["xdg-open", str(path.parent)], close_fds=True, start_new_session=True)


def reveal_export(file_name: str) -> dict[str, str]:
    """Show a saved export in its folder; only a file directly inside Exports."""
    if safe_export_name(file_name) != file_name:
        return {"status": "not_found"}
    folder = exports_folder()
    path = folder / file_name
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != folder.resolve(strict=True):
        return {"status": "not_found"}
    try:
        _show_in_folder(path)
    except OSError:
        return {"status": "unavailable"}
    return {"status": "opened"}
