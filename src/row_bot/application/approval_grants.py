"""Approve the rest of a turn's actions of one kind (F21).

A person answering an approval card can approve that action and every later
one of the same kind until the turn ends. Only reviewed, local, repeatable
kinds qualify; pushing, opening pull requests, deleting, installing, network
access and anything else always ask each time. Grants live in memory, belong
to one conversation, and end with the turn (or a restart).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

# Each tool's effect is fixed by its name, except the Developer command tool,
# whose kind includes the command's class.
_REPEATABLE_TOOLS = frozenset({
    "developer_import_sandbox_changes",
    "developer_apply_patch",
    "developer_write_file",
    "developer_create_branch",
    "developer_switch_branch",
    "developer_commit_changes",
    "developer_run_detected_test",
})
_REPEATABLE_COMMAND_CLASSES = frozenset({"run_command", "run_safe_command"})

_LOCK = threading.Lock()
_GRANTS: dict[str, set[str]] = {}


def _kind(item: Any) -> str | None:
    if not isinstance(item, Mapping):
        return None
    tool = str(item.get("tool") or "")
    if tool in _REPEATABLE_TOOLS:
        return tool
    if tool == "developer_run_command":
        from row_bot.developer.runtime import classify_command_action

        args = item.get("args") if isinstance(item.get("args"), Mapping) else {}
        action = classify_command_action(str(args.get("command") or ""))
        return f"{tool}:{action}" if action in _REPEATABLE_COMMAND_CLASSES else None
    return None


def approval_kinds(interrupt: Any) -> frozenset[str] | None:
    """The kinds one approval covers, or None when any of its actions must always ask."""
    items = interrupt if isinstance(interrupt, list) else [interrupt]
    kinds = [_kind(item) for item in items]
    if not kinds or any(kind is None for kind in kinds):
        return None
    return frozenset(kind for kind in kinds if kind)


def grant(conversation_id: str, kinds: frozenset[str]) -> None:
    with _LOCK:
        _GRANTS.setdefault(conversation_id, set()).update(kinds)


def granted(conversation_id: str, interrupt: Any) -> bool:
    """True when every action of this approval was approved for the rest of the turn."""
    kinds = approval_kinds(interrupt)
    if not kinds:
        return False
    with _LOCK:
        return kinds <= _GRANTS.get(conversation_id, set())


def end_turn(conversation_id: str) -> None:
    with _LOCK:
        _GRANTS.pop(conversation_id, None)
