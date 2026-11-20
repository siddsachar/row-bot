"""Always allow (D1): the person's standing rules, for their own app only.

A rule is made only from a real approval card: the client names the approval, and the rule is the
scope the gate kept on that stored card, checked again (``approval_rules.from_interrupt``). Nothing a
client sends says what a rule covers. Editing switches a rule on or off, deletes it, or changes a command
rule's exact command (checked like a new one). "Would this have asked?" reads the activity log only.
No tool calls any of this.
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from typing import Any

from row_bot import approval_rules
from row_bot.approval_rules import Rule, RuleError, Scope


def _when(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec="seconds")


def _draft(scope: Scope) -> dict[str, Any]:
    return {"kind": scope.kind, "words": scope.words()[:800], "app_name": scope.app_name[:128],
            "tool_title": scope.tool_title[:96], "command": scope.command[:500],
            "folder_name": scope.folder_name[:260]}


def _view(rule: Rule) -> dict[str, Any]:
    return {**_draft(rule.scope), "rule_id": rule.rule_id, "enabled": rule.enabled,
            "created_at": _when(rule.created_at), "last_used_at": _when(rule.last_used_at),
            "uses": max(0, min(rule.uses, 2**31 - 1))}


def read_rules() -> dict[str, Any]:
    return {"schema_version": 1, "items": [_view(rule) for rule in approval_rules.list_rules()],
            "max_rules": approval_rules.MAX_RULES}


def stored_scope(approval_id: str) -> Scope:
    """The rule a waiting approval offers: the scope its card was raised with, checked again now."""

    from row_bot.tasks import _get_conn

    with closing(_get_conn()) as conn:
        row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
    if row is None:
        raise RuleError("not_found")
    if row["status"] != "pending":
        raise RuleError("approval_already_resolved")
    try:
        context = json.loads(str(row["approval_payload_json"] or "{}"))
    except (TypeError, ValueError):
        context = {}
    context = context if isinstance(context, dict) else {}
    interrupt = context.get("interrupts") if row["resume_kind"] == "agent_run" else context.get("interrupt")
    scope = approval_rules.from_interrupt(interrupt)
    if scope is None:
        raise RuleError("rule_not_eligible")
    return scope


def create_from_approval(approval_id: str) -> dict[str, Any]:
    """Always allow… on a card: the rule its stored approval offers (the card itself is approved after)."""

    return _view(approval_rules.create(stored_scope(approval_id), created_from="card"))


def update_rule(rule_id: str, *, enabled: bool | None = None, command: str | None = None) -> dict[str, Any]:
    return _view(approval_rules.update(rule_id, enabled=enabled, command=command))


def delete_rule(rule_id: str) -> dict[str, Any]:
    approval_rules.delete(rule_id)
    return read_rules()


def dry_run(*, approval_id: str | None = None, rule_id: str | None = None,
            command: str | None = None) -> dict[str, Any]:
    """Would this have asked? The draft from a card, or a rule with its command changed, over 30 days."""

    if approval_id:
        scope = stored_scope(approval_id)
    else:
        rule = approval_rules.get_rule(str(rule_id or ""))
        scope = approval_rules.narrowed(rule, command) if command is not None else rule.scope
    result = approval_rules.dry_run(scope)
    from row_bot.application.client_activity import _titles

    titles = _titles({str(row["conversation_id"] or "") for row in result["examples"]})
    return {
        "schema_version": 1, "rule": _draft(scope), "days": approval_rules.DRY_RUN_DAYS,
        "total": result["total"], "counts": result["counts"],
        "examples": [{"at": _when(row["at"]), "conversation_title": titles.get(str(row["conversation_id"] or ""),
                                                                               "")[:160],
                      "title": str(row["title"] or "")[:160], "summary": str(row["summary"] or "")[:300],
                      "outcome": str(row["outcome"])} for row in result["examples"]],
    }
