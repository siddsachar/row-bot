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


def _action(item: Mapping[str, Any]) -> str:
    """What the card asks, in the tool's own words (never the model's)."""
    question = " ".join(str(item.get("question") or "").split())
    if question and len(question) <= _SUMMARY_CHARS:
        return question
    label = " ".join(str(item.get("label") or "").split())[:_SUMMARY_CHARS]
    return f"{label}?" if label else ""


def approval_notice(interrupt: Any, approval_id: str) -> dict[str, Any] | None:
    """The voice bar's approval and its spoken summary; None for a question card or no approval."""
    from row_bot.questions import question_item

    items = [item for item in _items(interrupt) if isinstance(item, Mapping)]
    if not approval_id or not items or question_item(interrupt) is not None:
        return None
    voice = voice_may_answer(interrupt)
    ask = "Say yes, or tap Approve." if voice else "Tap Approve on the screen."
    if len(items) > 1:
        action = f"{len(items)} actions"
        summary = f"I need your OK for {action}. {ask}"
    else:
        action = _action(items[0])
        summary = f"I need your OK. {action} {ask}" if action else f"I need your OK. {ask}"
    return {"approval_id": approval_id, "action": action, "voice": voice, "summary": summary}
