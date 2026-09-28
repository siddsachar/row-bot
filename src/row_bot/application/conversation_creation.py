"""Helpers for work a conversation creates: the draft folder and media tools.

Designs and code folders are created by the assistant's tools when the work
needs one (``tools/conversation_setup_tool.py``); a message's wording never
creates or binds anything by itself (B113).
"""

from __future__ import annotations

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
