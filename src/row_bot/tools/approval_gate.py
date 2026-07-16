from __future__ import annotations

from typing import Any, Callable, Literal

from row_bot.approval_policy import decision_for_action, normalize_approval_mode


GateOutcome = Literal["allow", "block", "deny", "take_over"]


def current_approval_mode() -> str:
    """Return the active thread approval mode, defaulting to Ask."""

    try:
        from row_bot.agent import get_approval_mode

        return get_approval_mode()
    except Exception:
        from row_bot.approval_policy import DEFAULT_APPROVAL_MODE

        return DEFAULT_APPROVAL_MODE


def resolve_approval(
    payload: dict[str, Any],
    *,
    approval_mode: object,
    read_only: bool = False,
    approval_callback: Callable[[dict[str, Any]], bool | str] | None = None,
) -> GateOutcome:
    """Resolve one action through the shared Block/Ask/Auto policy.

    The optional callback keeps deterministic subsystem tests independent from
    LangGraph. Shipped callers omit it and use the normal graph interrupt.
    """

    decision = decision_for_action(
        normalize_approval_mode(approval_mode),
        read_only=read_only,
    )
    if decision == "allow":
        return "allow"
    if decision == "block":
        return "block"
    if approval_callback is None:
        from langgraph.types import interrupt

        response: bool | str = interrupt(payload)
    else:
        response = approval_callback(payload)
    if response is True:
        return "allow"
    if str(response or "").strip().casefold() == "take over":
        return "take_over"
    return "deny"


def gate_action(
    payload: dict[str, Any],
    *,
    read_only: bool = False,
    blocked_message: str | None = None,
    cancelled_message: str = "Action cancelled by user.",
) -> str | None:
    """Return None when the action may run, otherwise a user-facing refusal."""

    mode = current_approval_mode()
    decision = resolve_approval(
        payload,
        approval_mode=mode,
        read_only=read_only,
    )
    if decision == "allow":
        return None
    label = str(payload.get("label") or payload.get("tool") or "Action")
    if decision == "block":
        return blocked_message or (
            f"BLOCKED: {label} is unavailable while this thread is in Block approval mode."
        )
    return cancelled_message
