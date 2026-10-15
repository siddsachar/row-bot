from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from row_bot.approval_policy import ApprovalMode, approval_label, decision_for_action, normalize_approval_mode


DeveloperAction = Literal[
    "read",
    "edit",
    "run_safe_command",
    "run_command",
    "run_install",
    "run_network",
    "start_server",
    "delete",
    "git_branch",
    "git_worktree",
    "git_commit",
    "git_push",
    "git_pr",
]

PolicyDecision = Literal["allow", "ask", "block"]


@dataclass(frozen=True)
class ApprovalDecision:
    decision: PolicyDecision
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def requires_approval(self) -> bool:
        return self.decision == "ask"


_ACTION_LABELS = {
    "read": "read workspace files",
    "edit": "edit workspace files",
    "run_safe_command": "run a read-only command",
    "run_command": "run a command",
    "run_install": "install dependencies",
    "run_network": "use the network",
    "start_server": "start a local server",
    "delete": "delete files",
    "git_branch": "create or switch branches",
    "git_worktree": "create a Git worktree",
    "git_commit": "create a commit",
    "git_push": "push to a remote",
    "git_pr": "open a pull request",
}


def decide_action(mode: ApprovalMode, action: DeveloperAction, *, by_person: bool = False) -> ApprovalDecision:
    """Return the policy decision for a Developer action.

    This is intentionally conservative.  The UI can still perform explicit
    user-triggered actions, but agent-driven actions must pass through this
    function so the same policy holds everywhere.

    "ask" means the action needs an approval; an agent-driven caller gets it
    through ``tools.approval_gate.request_approval``, which resolves it by the
    level (the person under Ask, the reviewer first under Auto, the level
    itself under Hands-off). ``by_person``: the person started it in the app, so Auto and
    Hands-off need nothing more (Ask still confirms).
    """
    label = _ACTION_LABELS[action]
    normalized = normalize_approval_mode(mode)
    decision = decision_for_action(
        normalized, read_only=action in {"read", "run_safe_command"}, by_person=by_person,
    )
    mode_label = approval_label(normalized)
    if decision == "allow":
        return ApprovalDecision("allow", f"{mode_label} allows {label}.")
    if decision == "block":
        return ApprovalDecision("block", f"{mode_label} blocks attempts to {label}.")
    if normalized == "auto":
        return ApprovalDecision("ask", f"Auto asks the reviewer before it will {label}.")
    if normalized == "full":
        return ApprovalDecision("ask", f"Hands-off approves attempts to {label}.")
    return ApprovalDecision("ask", f"{mode_label} requires approval to {label}.")


def action_needs_explicit_user_intent(action: DeveloperAction) -> bool:
    """Actions that should never be hidden behind a generic approval."""
    return action in {"delete", "run_install", "run_network", "git_commit", "git_push", "git_pr"}
