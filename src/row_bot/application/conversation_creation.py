"""Helpers for work a conversation creates: the draft folder and media tools.

Designs and code folders are created by the assistant's tools when the work
needs one (``tools/conversation_setup_tool.py``); a message's wording never
creates or binds anything by itself (B113).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from row_bot.brand import DEFAULT_WORKSPACE_DIR_NAME


def media_tool_selection(text: str, enabled: list[str], *, has_design: bool) -> list[str]:
    """Expose one media generator family for an explicit media request."""
    request = text.casefold()
    media = re.search(r"\b(images?|photos?|pictures?|illustrations?|videos?|clips?)\b", request)
    if not media:
        return enabled
    video = re.search(r"\b(videos?|clips?)\b", request)
    design_destination = has_design and re.search(
        r"\b(design|deck|slides?|presentation|storyboard|poster|social post)\b", request,
    )
    excluded = {"image_gen", "video_gen"} if design_destination else {"designer"}
    if not video:
        excluded.add("video_gen")
    return [name for name in enabled if name not in excluded]


def configured_workspace_root() -> Path:
    from row_bot.tools import registry

    filesystem = registry.get_tool("filesystem")
    configured = filesystem.get_config("workspace_root", "") if filesystem else ""
    root = Path(configured).expanduser() if configured else Path.home() / "Documents" / DEFAULT_WORKSPACE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("workspace_root_unavailable")
    root = root.resolve(strict=True)
    return root


def _draft_parent() -> Any:
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder

    root = configured_workspace_root()
    drafts = root / "Drafts"
    if drafts.is_symlink():
        raise ValueError("workspace_root_unavailable")
    drafts.mkdir(exist_ok=True)
    if not drafts.is_dir() or drafts.resolve(strict=True).parent != root:
        raise ValueError("workspace_root_unavailable")
    return AuthorizedWorkspaceFolder(drafts, root, "configured-drafts")


_RESERVED_NAMES = frozenset({"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
                             *(f"lpt{n}" for n in range(1, 10))})


def code_folder_name(value: str) -> str:
    """A portable folder name from the person's words ("Tiny date app").

    Shared by Add resource › New draft and the create_code_folder tool; an
    empty or unusable name becomes "Code folder".
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()[:120]
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text.split(".", 1)[0].casefold() in _RESERVED_NAMES:
        text = "Code folder"
    return text[:60].rstrip(" .") or "Code folder"


def free_folder_name(parent: Path, name: str) -> str:
    """name, else "name 2", "name 3"…: the first neither on disk nor saved.

    A saved code folder whose files were removed keeps its identity, so its
    name is not handed out again.
    """
    from row_bot.developer import storage

    candidate, index = name, 2
    while os.path.lexists(parent / candidate) or storage.get_workspace(
            storage._workspace_id_for_path(parent / candidate)) is not None:
        candidate = f"{name} {index}"
        index += 1
    return candidate
