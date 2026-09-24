"""Conservative, idempotent resource setup for clear first-turn requests."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from row_bot.brand import DEFAULT_WORKSPACE_DIR_NAME
from row_bot.conversation_resources import list_bindings


def requested_resource(text: str) -> tuple[str, str] | None:
    """Route only explicit deliverables; unclear requests remain ordinary chat."""
    request = re.sub(r"\s+", " ", text.casefold()).strip()
    if not request or len(request) > 20000:
        return None
    if re.search(r"\b(repo|repository|checkout|codebase)\b", request) or re.search(
        r"\b(existing|saved|my|this|that)\s+folder\b", request
    ):
        return None
    if re.search(r"\b(generate|make|create)\b.*\b(image|photo|video|clip)\b", request) and not re.search(
        r"\b(design|deck|slide|storyboard|presentation|poster|social post)\b", request
    ):
        return None
    if re.search(r"\b(deck|slides?|presentation)\b", request) and re.search(
        r"\b(create|make|build|design|draft|put together)\b", request
    ):
        return "artifact", "deck"
    if re.search(r"\b(storyboard)\b", request):
        return "artifact", "storyboard"
    if re.search(r"\b(design|mock ?up|wireframe|poster|social post|brochure)\b", request) and re.search(
        r"\b(create|make|build|design|mock ?up|draft|produce)\b", request
    ):
        if re.search(r"\b(document|report|one.pager)\b", request):
            return "artifact", "document"
        if re.search(r"\b(landing page|web page|website)\b", request):
            return "artifact", "landing"
        return "artifact", "app_mockup"
    if re.search(r"\b(polished|formatted)\s+(document|report|file)\b", request) and re.search(
        r"\b(create|make|draft|write|produce)\b", request
    ):
        return "artifact", "document"
    if re.search(r"\b(build|make|implement|code|develop|scaffold|create)\b", request) and re.search(
        r"\b(app|application|website|web ?app|landing page|component|script|program|code|api|feature)\b", request
    ):
        return "workspace", "draft"
    return None


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


def ensure_for_submission(service: Any, conversation_id: str, text: str,
                          submission_command_id: str) -> dict | None:
    """Use the existing setup receipts and owners before this same turn runs."""
    request = requested_resource(text)
    if request is None:
        return None
    kind, mode = request
    if any(binding.kind == kind for binding in list_bindings(conversation_id).bindings):
        return None
    from row_bot.tools import registry
    if not registry.is_enabled("developer" if kind == "workspace" else "designer"):
        return None
    # A new submission may retry after a lost or partial setup response. Keep
    # one setup identity per conversation and kind until its binding is visible.
    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"row-bot:{conversation_id}:auto:{kind}"))
    payload: dict = {"kind": kind, "intent": "create"}
    authorized_folder = None
    if kind == "workspace":
        authorized_folder = _draft_parent()
        payload["empty_workspace"] = {"folder_name": f"Draft-{command_id[:12]}"}
    else:
        payload["artifact"] = {"mode": mode, "name": f"Design {command_id[:8]}", "brief": text[:20000]}
    command = {"type": "resource.setup", "command_id": command_id,
               "expected_revision": str(service._metadata(conversation_id)["client_revision"]),
               "payload": payload}
    return service.execute(owner_id=f"conversation-auto:{conversation_id}",
                           idempotency_key=command_id, command=command,
                           target=conversation_id, authorized_folder=authorized_folder)
