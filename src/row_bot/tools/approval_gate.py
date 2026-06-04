from __future__ import annotations

from typing import Any

from row_bot.approval_policy import decision_for_action


def current_approval_mode() -> str:
    """Return the active thread approval mode, defaulting to Ask."""

    try:
        from row_bot.agent import get_approval_mode

        return get_approval_mode()
    except Exception:
        from row_bot.approval_policy import DEFAULT_APPROVAL_MODE

        return DEFAULT_APPROVAL_MODE


def gate_action(
    payload: dict[str, Any],
    *,
    read_only: bool = False,
    blocked_message: str | None = None,
    cancelled_message: str = "Action cancelled by user.",
) -> str | None:
    """Return None when the action may run, otherwise a user-facing refusal."""

    mode = current_approval_mode()
    decision = decision_for_action(mode, read_only=read_only)
    if decision == "allow":
        return None
    label = str(payload.get("label") or payload.get("tool") or "Action")
    if decision == "block":
        return blocked_message or (
            f"BLOCKED: {label} is unavailable while this thread is in Block approval mode."
        )
    from langgraph.types import interrupt

    approval = interrupt(payload)
    if not approval:
        return cancelled_message
    return None
