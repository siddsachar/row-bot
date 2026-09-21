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


def project_approval_context(value: Any, *, fallback_reason: str = "") -> dict[str, str]:
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
    return {
        "action_label": action_label,
        "reason": reason,
        "risk_class": risk,
        "scope": scope,
        "safe_argument_summary": safe_tool_input(item.get("args")),
        "requesting_trace_id": requesting_trace_id,
    }
