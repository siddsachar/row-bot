"""The Developer workspace root's folders, for attaching a code folder from any device (F19).

A browser, a phone or another computer signed in to Row-Bot has no folder picker for this
computer. It can still attach a code folder: Row-Bot lists the folders inside its workspace
root (Settings › Workspace folder), folders only, and the person picks one by clicking through
them. Nothing outside the root can be listed or chosen: every listing and every choice is
checked here, on the server, against the root.

A path is the folder's names under the root joined with ``/`` (``""`` is the root itself), as
the browser built it from names this module listed. Refused: ``..`` and ``.``, empty names,
``.git``, absolute paths, drive letters and other colons, backslashes, control characters,
names Windows would quietly change (a trailing dot or space) or treat as a device, and any
folder reached through a link, a Windows junction or another reparse point, or on another
disk (a mount). Hidden folders (a leading dot) are not listed.

Choosing a folder gives an ``AuthorizedWorkspaceFolder`` scoped to the root, so registration
and cloning check every folder between the root and the choice again, with the same
functions the desktop picker's choice goes through (``register_existing_folder``,
``create_empty_workspace``, ``clone_selected_repository``).
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Folders listed in one place; more are left out (the listing says so).
LIST_LIMIT = 500
# A path no deeper than this, and no longer.
MAX_DEPTH = 24
MAX_PATH_CHARS = 1024
MAX_NAME_BYTES = 255
_RESERVED = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                       *(f"com{n}" for n in "123456789¹²³"), *(f"lpt{n}" for n in "123456789¹²³")})
_FORBIDDEN = frozenset('<>:"/\\|?*')


class FolderRefused(ValueError):
    """A listing or choice outside the workspace root, or not a folder there."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootFolder:
    """A folder inside the workspace root: its path under the root and on disk."""

    path: str
    target: Path
    root: Path


def _is_link(info: os.stat_result) -> bool:
    # Symbolic links, and on Windows every reparse point (junctions, mount points, cloud stubs).
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def name_allowed(name: str) -> bool:
    """A folder name that means only itself on every system Row-Bot runs on."""
    if not isinstance(name, str) or not name or name in {".", ".."} or name.casefold() == ".git":
        return False
    if name != name.strip() or name.endswith("."):
        return False
    if any(ord(char) < 32 or ord(char) == 127 or char in _FORBIDDEN for char in name):
        return False
    if name.split(".", 1)[0].casefold() in _RESERVED:
        return False
    try:
        return len(name.encode("utf-8")) <= MAX_NAME_BYTES
    except UnicodeError:  # A lone surrogate from an undecodable name.
        return False


def clean_path(path: Any) -> str:
    """The path as names under the root, or FolderRefused when it isn't one."""
    if not isinstance(path, str) or len(path) > MAX_PATH_CHARS:
        raise FolderRefused("workspace_folder_refused")
    if path == "":
        return ""
    parts = path.split("/")
    if len(parts) > MAX_DEPTH or not all(name_allowed(part) for part in parts):
        raise FolderRefused("workspace_folder_refused")
    return "/".join(parts)


def workspace_root() -> Path:
    """The Developer workspace root (created when missing), never a link."""
    from row_bot.application.conversation_creation import configured_workspace_root

    try:
        return configured_workspace_root()
    except (OSError, ValueError):
        raise FolderRefused("workspace_folder_unavailable") from None


def resolve(path: Any) -> RootFolder:
    """The folder at *path* under the root, checked folder by folder from the root down."""
    clean = clean_path(path)
    root = workspace_root()
    try:
        root_info = root.lstat()
        if _is_link(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise FolderRefused("workspace_folder_unavailable")
        target = root
        for part in clean.split("/") if clean else []:
            target = target / part
            info = target.lstat()
            # A link, a junction or a mount leads somewhere else: refused, wherever it points.
            if _is_link(info) or info.st_dev != root_info.st_dev:
                raise FolderRefused("workspace_folder_refused")
            if not stat.S_ISDIR(info.st_mode):
                raise FolderRefused("workspace_folder_unavailable")
        # The same folder by its real path, still inside the root.
        target.resolve(strict=True).relative_to(root.resolve(strict=True))
    except FolderRefused:
        raise
    except FileNotFoundError:
        raise FolderRefused("workspace_folder_unavailable") from None
    except (OSError, ValueError, RuntimeError):
        raise FolderRefused("workspace_folder_refused") from None
    return RootFolder(clean, target, root)


def _crumbs(root: Path, path: str) -> list[dict[str, str]]:
    crumbs = [{"name": root.name or str(root), "path": ""}]
    names = path.split("/") if path else []
    for index, name in enumerate(names):
        crumbs.append({"name": name, "path": "/".join(names[: index + 1])})
    return crumbs


def browse(path: Any = "") -> dict[str, Any]:
    """The folders directly inside *path* (under the workspace root), sorted by name."""
    folder = resolve(path)
    root_info = folder.root.lstat()
    names: list[str] = []
    seen = 0
    try:
        with os.scandir(folder.target) as entries:
            for entry in entries:
                seen += 1
                if seen > LIST_LIMIT * 8:
                    break
                if entry.name.startswith(".") or not name_allowed(entry.name):
                    continue
                try:
                    # A full lstat: a directory entry's own stat has no device number on Windows.
                    info = os.lstat(entry.path)
                except OSError:
                    continue
                if _is_link(info) or not stat.S_ISDIR(info.st_mode) or info.st_dev != root_info.st_dev:
                    continue
                names.append(entry.name)
    except OSError:
        raise FolderRefused("workspace_folder_unavailable") from None
    names.sort(key=lambda name: (name.casefold(), name))
    shown = names[:LIST_LIMIT]
    prefix = f"{folder.path}/" if folder.path else ""
    folders = []
    for name in shown:
        try:
            git = (folder.target / name / ".git").lstat()
            is_git = stat.S_ISDIR(git.st_mode) or stat.S_ISREG(git.st_mode)
        except OSError:
            is_git = False
        folders.append({"name": name, "path": prefix + name, "is_git": is_git})
    return {
        "path": folder.path,
        "name": folder.target.name if folder.path else (folder.root.name or str(folder.root)),
        "crumbs": _crumbs(folder.root, folder.path),
        "folders": folders,
        "truncated": len(names) > len(shown) or seen > LIST_LIMIT * 8,
        "is_root": folder.path == "",
    }


def authorize(path: Any, *, parent: bool) -> Any:
    """The chosen folder as an authorized folder scoped to the workspace root.

    *parent*: a folder to create or clone into (the root itself is allowed); otherwise the
    folder to work in, which is never the root itself.
    """
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder

    folder = resolve(path)
    if not parent and not folder.path:
        raise FolderRefused("workspace_folder_root")
    identity = hashlib.sha256(f"{folder.root}\0{folder.path}".encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return AuthorizedWorkspaceFolder(folder.target, folder.root, f"workspace-root:{identity}")


def label(path: Any) -> str:
    """Where a folder is, in words: "Row-Bot › Projects › tide-app"."""
    folder = resolve(path)
    return " › ".join(crumb["name"] for crumb in _crumbs(folder.root, folder.path))
