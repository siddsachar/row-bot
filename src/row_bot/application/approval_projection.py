"""Bounded public context for conversation approval prompts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from row_bot.application.conversation_traces import (
    canonical_tool_name,
    safe_tool_input,
)


_RISK_CLASSES = {"low", "medium", "high", "critical"}


def _text(value: Any, maximum: int) -> str:
    text = str(value or "").strip()[:maximum]
    return "".join(
        character
        for character in text
        if character in "\n\t" or ord(character) >= 32
    )


def project_approval_context(value: Any, *, fallback_reason: str = "") -> dict[str, Any]:
    """Expose only reviewed, size-bounded interrupt metadata."""

    items = value if isinstance(value, list) else [value]
    item = next((entry for entry in items if isinstance(entry, Mapping)), {})
    tool_name = _text(item.get("tool") or item.get("name"), 180)
    action_label = canonical_tool_name(tool_name) if tool_name else "Requested action"
    reason = _text(
        item.get("description")
        or item.get("reason")
        or fallback_reason
        or "This action requires approval under the current policy.",
        1024,
    )
    risk = _text(item.get("risk_class") or item.get("risk"), 32).casefold()
    if risk not in _RISK_CLASSES:
        risk = "unknown"
    scope = _text(item.get("scope") or item.get("consequence"), 1024)
    if not scope:
        scope = (
            f"{len(items)} requested actions will be resolved together."
            if len(items) > 1
            else "Only this requested action will be resolved."
        )
    requesting_trace_id = _text(
        item.get("tool_call_id")
        or item.get("call_id")
        or item.get("id")
        or item.get("__interrupt_id"),
        256,
    )
    projected: dict[str, Any] = {
        "action_label": action_label,
        "reason": reason,
        "risk_class": risk,
        "scope": scope,
        "safe_argument_summary": safe_tool_input(item.get("args")),
        "requesting_trace_id": requesting_trace_id,
    }
    # Turning on a tool the work needs reads as a setup card ("Turn on …"),
    # and a code folder to use or a repository to clone as a folder card
    # where the person picks the folder (B277).
    from row_bot.application.conversation_traces import app_of_tool
    app = app_of_tool(tool_name) if len(items) == 1 else None
    if app:  # Shown with its logo and name, so the person knows which app is asking.
        projected["app"] = app
    setup = item.get("setup")
    if isinstance(setup, Mapping) and len(items) == 1:
        projected_setup = _setup_card(setup)
        if projected_setup:
            projected["setup"] = projected_setup
    return projected


def _setup_card(setup: Mapping[str, Any]) -> dict[str, Any] | None:
    kind = setup.get("kind")
    label = _text(setup.get("label"), 120)
    if not label or kind not in {"tool", "folder", "clone"}:
        return None
    if kind == "tool":
        return {"kind": "tool", "label": label}
    if kind == "clone":
        repo_url = _text(setup.get("repo_url"), 2048)
        return {"kind": "clone", "label": label, "repo_url": repo_url} if repo_url else None
    raw = setup.get("folders")
    folders = []
    for entry in raw if isinstance(raw, list) else []:
        if len(folders) == 8:
            break
        if not isinstance(entry, Mapping):
            continue
        folder = {"resource_id": _text(entry.get("resource_id"), 256), "name": _text(entry.get("name"), 120),
                  "revision": _text(entry.get("revision"), 128)}
        if folder["resource_id"] and folder["name"]:
            folders.append(folder)
    return {"kind": "folder", "label": label, "folders": folders}
