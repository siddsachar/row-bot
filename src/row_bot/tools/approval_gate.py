from __future__ import annotations

from typing import Any, Callable, Literal

from row_bot.approval_policy import decision_for_action, normalize_approval_mode


GateOutcome = Literal["allow", "block", "deny", "take_over"]

# The first line of every approval-gated result, so the model can say whether
# approval was needed, given or refused instead of guessing (B235).
APPROVAL_NOT_NEEDED_SAFE = "Approval: not needed (safe command)"
APPROVAL_NOT_NEEDED_AUTO = "Approval: not needed (Auto approval mode)"
APPROVAL_NOT_NEEDED_READ_ONLY = "Approval: not needed (read-only)"
APPROVAL_GIVEN = "Approval: asked; approved by you"
APPROVAL_DENIED = "Approval: asked; denied by you — did not run"


def with_approval(line: str, result: Any) -> Any:
    """Lead a plain-text result with its approval line.

    Structured results (JSON, media markers) are left as they are: the
    transcript reads them as data.
    """

    if not isinstance(result, str) or result.lstrip().startswith(("{", "[", "__")):
        return result
    return f"{line}\n{result}"


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


def approval_check(
    payload: dict[str, Any],
    *,
    read_only: bool = False,
    blocked_message: str | None = None,
    cancelled_message: str = "Action cancelled by user.",
) -> tuple[str | None, str]:
    """Resolve one action: its refusal (None when it may run) and approval line.

    A tool that runs the action leads its result with the line; a refusal
    after a denial already carries it.
    """

    mode = current_approval_mode()
    asked = decision_for_action(normalize_approval_mode(mode), read_only=read_only) == "ask"
    decision = resolve_approval(
        payload,
        approval_mode=mode,
        read_only=read_only,
    )
    if decision == "allow":
        if asked:
            return None, APPROVAL_GIVEN
        return None, APPROVAL_NOT_NEEDED_READ_ONLY if read_only else APPROVAL_NOT_NEEDED_AUTO
    label = str(payload.get("label") or payload.get("tool") or "Action")
    if decision == "block":
        return blocked_message or (
            f"BLOCKED: {label} is unavailable while this thread is in Block approval mode."
        ), ""
    return with_approval(APPROVAL_DENIED, cancelled_message), APPROVAL_DENIED


def gate_action(
    payload: dict[str, Any],
    *,
    read_only: bool = False,
    blocked_message: str | None = None,
    cancelled_message: str = "Action cancelled by user.",
) -> str | None:
    """Return None when the action may run, otherwise a user-facing refusal."""

    refusal, _line = approval_check(
        payload,
        read_only=read_only,
        blocked_message=blocked_message,
        cancelled_message=cancelled_message,
    )
    return refusal
