"""The shared approval levels: Block, Ask, Auto (reviewed) and Hands-off.

An action that needs approval is decided by the level the person chose:

- **Block** (``block``): refused.
- **Ask** (``approve``, the default): the person approves each one.
- **Auto** (``auto``): a reviewer model approves what clearly matches the person's
  request and asks the person about everything else (``approval_review``).
- **Hands-off** (``full``): approved without asking, and still recorded.

``decision_for_action`` says only whether an action *needs* an approval ("ask");
who gives it is the level's business, resolved in one place
(``tools.approval_gate.request_approval``). An action that is not routed through
there is asked of the person, so a missed call site asks rather than runs.

Legacy values (``allow_all``, ``auto_edit``, ``agent_run``…) are the old
"Auto" and map to reviewed ``auto``. Nothing maps to ``full``: a person turns it
on, never a migration.
"""

from __future__ import annotations

from typing import Literal


ApprovalMode = Literal["block", "approve", "auto", "full"]
ApprovalDecision = Literal["block", "ask", "allow"]

DEFAULT_APPROVAL_MODE: ApprovalMode = "approve"
APPROVAL_MODES: tuple[ApprovalMode, ...] = ("block", "approve", "auto", "full")

APPROVAL_MODE_LABELS: dict[ApprovalMode, str] = {
    "block": "Block",
    "approve": "Ask",
    "auto": "Auto",
    "full": "Hands-off",
}

# How much each level lets run without the person: a profile or helper may
# narrow its parent's level, never widen it.
_APPROVAL_RANKS: dict[ApprovalMode, int] = {"block": 0, "approve": 1, "auto": 2, "full": 3}

_VALID_APPROVAL_MODES = frozenset(APPROVAL_MODE_LABELS)

_LEGACY_DEVELOPER_MODE_MAP: dict[str, ApprovalMode] = {
    "read_only": "block",
    "ask": "approve",
    "auto_edit": "auto",
    "agent_run": "auto",
}

_LEGACY_APPROVAL_ALIASES: dict[str, ApprovalMode] = {
    "block": "block",
    "blocked": "block",
    "read_only": "block",
    "readonly": "block",
    "approve": "approve",
    "ask": "approve",
    "approval": "approve",
    # The old "Auto" (allow almost everything) is reviewed Auto now.
    "auto": "auto",
    "allow_all": "auto",
    "allow all": "auto",
    "auto_edit": "auto",
    "agent_run": "auto",
    # Only ever chosen by a person.
    "full": "full",
    "hands_off": "full",
    "hands off": "full",
    "full_autonomy": "full",
    "full autonomy": "full",
}


def normalize_approval_mode(
    value: object,
    default: ApprovalMode = DEFAULT_APPROVAL_MODE,
) -> ApprovalMode:
    """Return a valid shared approval level for user, legacy, or stored input."""

    default_mode = default if default in _VALID_APPROVAL_MODES else DEFAULT_APPROVAL_MODE
    text = str(value or "").strip().lower()
    if not text:
        return default_mode
    text = text.replace("-", "_")
    return _LEGACY_APPROVAL_ALIASES.get(text, default_mode)


def known_approval_mode(value: object) -> ApprovalMode | None:
    """The level a known value (today's or a legacy alias) names, or None for anything else."""

    text = str(value or "").strip().lower().replace("-", "_")
    return _LEGACY_APPROVAL_ALIASES.get(text)


def approval_label(mode: object) -> str:
    """Return the user-facing label for an approval level."""

    return APPROVAL_MODE_LABELS[normalize_approval_mode(mode)]


def approval_rank(mode: object) -> int:
    """How much a level lets run without the person (Block lowest, Hands-off highest)."""

    return _APPROVAL_RANKS[normalize_approval_mode(mode)]


def stricter_approval_mode(first: object, second: object) -> ApprovalMode:
    """The stricter of two levels: what a narrowed run (a profile, a shared conversation) uses."""

    left, right = normalize_approval_mode(first), normalize_approval_mode(second)
    return left if _APPROVAL_RANKS[left] <= _APPROVAL_RANKS[right] else right


def decision_for_action(
    mode: object,
    *,
    read_only: bool = False,
    by_person: bool = False,
) -> ApprovalDecision:
    """Whether an action may run, is refused, or needs an approval under ``mode``.

    "ask" means it needs an approval. Who gives it depends on the level: the
    person under Ask, the reviewer (then the person when it asks) under Auto,
    the level itself under Hands-off. Agent-driven callers resolve "ask" through
    ``tools.approval_gate``. ``by_person``: the person started this action
    themselves (a button in the app), so under Auto or Hands-off it needs no
    further approval; under Ask it still does.
    """

    if read_only:
        return "allow"
    normalized = normalize_approval_mode(mode)
    if normalized == "block":
        return "block"
    if by_person and normalized in {"auto", "full"}:
        return "allow"
    return "ask"


def legacy_developer_mode_to_approval_mode(value: object) -> ApprovalMode:
    """Map Developer Studio's retired four-mode model to the shared levels."""

    text = str(value or "").strip().lower().replace("-", "_")
    return _LEGACY_DEVELOPER_MODE_MAP.get(
        text,
        normalize_approval_mode(text, DEFAULT_APPROVAL_MODE),
    )


def legacy_safety_mode_to_approval_mode(value: object) -> ApprovalMode:
    """Normalize legacy workflow safety values into the shared approval levels."""

    return normalize_approval_mode(value, DEFAULT_APPROVAL_MODE)
