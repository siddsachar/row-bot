"""Approvals in a Row-Bot Voice conversation (VD4): a spoken summary, and a spoken yes only for what can be undone.

The card on screen always works. A spoken yes or no answers an approval only
when its action can be undone, by the approval system's own classification:
the kinds "Allow in this chat" may cover (``approval_grants.approval_kinds``),
which never include actions that reach other people, cost money, delete,
publish code, read private data, carry a high risk, or that only the person
may approve. Voice narrows that further: exactly one action, and never a
command, an app's tool, the computer, a paid call, a setting, a question or a
setup card, or an action that writes or replaces files, cancels, uploads or
answers for the person. Everything else needs a tap.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

# Kinds voice never answers, beyond what approval_grants already keeps for the card.
_TAP_TOOLS = frozenset({"run_command", "developer_run_command", "start_process", "computer"})
_TAP_WORDS = re.compile(
    r"cancel|respond|answer|replace|write|outside|upload|install|merge|deploy|commit|archive|empty|clear|reset|"
    r"wipe|revoke|transfer|setting",
    re.IGNORECASE,
)
_TAP_FIELDS = ("person_only", "binding", "x_post", "memory_save", "price", "setup", "change", "questions")
_SUMMARY_CHARS = 160


def _items(interrupt: Any) -> list[Any]:
    return interrupt if isinstance(interrupt, list) else [interrupt]


def voice_may_answer(interrupt: Any) -> bool:
    """True only for one action the approval system lets be approved again for the chat, and voice's own narrowing."""
    from row_bot.application.approval_grants import approval_kinds
    from row_bot.questions import question_item

    items = _items(interrupt)
    if len(items) != 1 or not isinstance(items[0], Mapping) or question_item(interrupt) is not None:
        return False
    item = items[0]
    tool = str(item.get("tool") or "")
    if (not tool or tool in _TAP_TOOLS or tool.startswith(("mcp_", "computer")) or any(item.get(key) for key in _TAP_FIELDS)
            or _TAP_WORDS.search(tool) or _TAP_WORDS.search(str(item.get("label") or ""))):
        return False
    return approval_kinds(interrupt) is not None


def _fallback_title(item: Mapping[str, Any]) -> str:
    """The card's title in the tool's own words, said only if the client never sends the title it shows."""
    question = " ".join(str(item.get("question") or "").split())
    if question and len(question) <= _SUMMARY_CHARS:
        return question
    label = " ".join(str(item.get("label") or "").split())[:_SUMMARY_CHARS]
    return f"{label}?" if label else ""


def approval_notice(interrupt: Any, approval_id: str) -> dict[str, Any] | None:
    """The voice bar's approval: what its card shows (the client words its title as the card does), whether a
    spoken answer may answer it, and a fallback title. None for a question card or no approval."""
    from row_bot.application.approval_projection import project_approval_context
    from row_bot.questions import question_item

    items = [item for item in _items(interrupt) if isinstance(item, Mapping)]
    if not approval_id or not items or question_item(interrupt) is not None:
        return None
    card = project_approval_context(interrupt)
    app = card.get("app") if isinstance(card.get("app"), Mapping) else None
    return {"approval_id": approval_id, "voice": voice_may_answer(interrupt), "fallback": _fallback_title(items[0]),
            "card": {"action_label": str(card.get("action_label") or ""), "question": str(card.get("question") or ""),
                     **({"app": {"name": str(app.get("name") or ""), "tool": str(app.get("tool") or "")}}
                        if app else {})}}
