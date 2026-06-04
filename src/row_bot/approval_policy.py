from __future__ import annotations

from typing import Literal


ApprovalMode = Literal["block", "approve", "allow_all"]
ApprovalDecision = Literal["block", "ask", "allow"]

DEFAULT_APPROVAL_MODE: ApprovalMode = "approve"

APPROVAL_MODE_LABELS: dict[ApprovalMode, str] = {
    "block": "Block",
    "approve": "Ask",
    "allow_all": "Auto",
}

_VALID_APPROVAL_MODES = frozenset(APPROVAL_MODE_LABELS)

_LEGACY_DEVELOPER_MODE_MAP: dict[str, ApprovalMode] = {
    "read_only": "block",
    "ask": "approve",
    "auto_edit": "allow_all",
    "agent_run": "allow_all",
}

_LEGACY_APPROVAL_ALIASES: dict[str, ApprovalMode] = {
    "block": "block",
    "blocked": "block",
    "read_only": "block",
    "readonly": "block",
    "approve": "approve",
    "ask": "approve",
    "approval": "approve",
    "allow_all": "allow_all",
    "allow-all": "allow_all",
    "allow all": "allow_all",
    "auto": "allow_all",
    "auto_edit": "allow_all",
    "agent_run": "allow_all",
}


def normalize_approval_mode(
    value: object,
    default: ApprovalMode = DEFAULT_APPROVAL_MODE,
) -> ApprovalMode:
    """Return a valid shared approval mode for user, legacy, or stored input."""

    default_mode = default if default in _VALID_APPROVAL_MODES else DEFAULT_APPROVAL_MODE
    text = str(value or "").strip().lower()
    if not text:
        return default_mode
    text = text.replace("-", "_")
    return _LEGACY_APPROVAL_ALIASES.get(text, default_mode)


def approval_label(mode: object) -> str:
    """Return the user-facing label for an approval mode."""

    return APPROVAL_MODE_LABELS[normalize_approval_mode(mode)]


def decision_for_action(mode: object, *, read_only: bool = False) -> ApprovalDecision:
    """Return how an action should proceed under the shared approval mode."""

    if read_only:
        return "allow"
    normalized = normalize_approval_mode(mode)
    if normalized == "block":
        return "block"
    if normalized == "allow_all":
        return "allow"
    return "ask"


def legacy_developer_mode_to_approval_mode(value: object) -> ApprovalMode:
    """Map Developer Studio's retired four-mode model to the shared modes."""

    text = str(value or "").strip().lower().replace("-", "_")
    return _LEGACY_DEVELOPER_MODE_MAP.get(
        text,
        normalize_approval_mode(text, DEFAULT_APPROVAL_MODE),
    )


def legacy_safety_mode_to_approval_mode(value: object) -> ApprovalMode:
    """Normalize legacy workflow safety values into the shared approval model."""

    return normalize_approval_mode(value, DEFAULT_APPROVAL_MODE)
