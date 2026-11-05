"""Trusted sources: R and D count a change only when its new value came from the person or the world (on only).

A memory can be written from what the person said, from the world's own sources (documents, the inbox, app data), or
from the agent's own actions and replies: the 9B booked a deep clean at the wrong time, and that evening's extraction
saved the booking as a memory, which reconciliation then drafted into the skill (smoke R, 26 Oct). So a change's new
value counts only when it comes from a trusted source, judged by where the write came from (its ``origin``, kept on
each validity row: :func:`row_bot.stale_skills.current_origin`):

- ``document:<id>``: document knowledge, trusted;
- "" (no conversation and no document: the person's own edit, on the Memory page say), trusted;
- ``thread:<id>``: a conversation (the turn the agent saved it in, or the one extraction read). A value is trusted
  when the conversation has it in **a person's message**, or in **a tool's result from the world** (any tool but
  Row-Bot's own memory, conversation, skill, wiki and status readers, :data:`OWN_TOOLS`) that the agent never put
  there itself: the value is in none of the agent's replies or tool calls before that result, in this conversation
  or in any conversation that started before it (a calendar's "event created at 18:00" echoes the agent's own
  booking, in this chat or an earlier one; the user's decision, 26 Oct). Row-Bot's turn context (the recalled
  memories, the date) and its own events never count.

A value that comes from nowhere trusted is the agent's own: the change is logged (``untrusted_source``) and R and D
leave it alone.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from row_bot.stale_skills import links

logger = logging.getLogger(__name__)
_agent_cache: dict[str, tuple[str, frozenset[str]]] = {}

#: Row-Bot's own readers: what they return is the agent's memory, conversations, skills or notes, not the world.
OWN_TOOLS = frozenset({
    "save_memory", "search_memory", "list_memories", "update_memory", "delete_memory", "link_memories",
    "explore_connections", "search_conversations", "list_conversations", "skill_search", "skill_load", "tool_search",
})
OWN_PREFIXES = ("wiki_", "row_bot_")


def _own(tool: str) -> bool:
    return tool in OWN_TOOLS or tool.startswith(OWN_PREFIXES)


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(block.get("text") or "") if isinstance(block, dict) else str(block) for block in content)
    return str(content or "")


def _app_event(message: Any) -> bool:
    """A message Row-Bot wrote into the conversation itself (a goal or orchestration event)."""

    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, dict):
        return False
    ui = kwargs.get("row_bot_ui")
    return bool(kwargs.get("row_bot_internal_event")) or (isinstance(ui, dict) and bool(ui.get("thread_event")))


def _agent_wrote(message: Any) -> set[str]:
    """The values in one of the agent's messages: its reply and its tool calls' arguments."""

    found = set(links.value_tokens(_text(getattr(message, "content", ""))))
    for call in getattr(message, "tool_calls", None) or ():
        found |= set(links.value_tokens(json.dumps(call.get("args") or {}, ensure_ascii=False, default=str)))
    return found


def _messages(thread_id: str) -> list[Any]:
    from row_bot import threads
    from row_bot.turn_context import is_turn_context_message

    return [message for message in threads.get_latest_checkpoint_messages(thread_id)
            if not is_turn_context_message(message) and not _app_event(message)]


def _agent_values(thread_id: str) -> frozenset[str]:
    """Every value the agent wrote in a conversation (cached until the conversation changes)."""

    from row_bot import threads

    revision = threads.get_latest_checkpoint_revision(thread_id)
    cached = _agent_cache.get(thread_id)
    if cached is not None and cached[0] == revision:
        return cached[1]
    found: set[str] = set()
    for message in _messages(thread_id):
        if getattr(message, "type", "") == "ai":
            found |= _agent_wrote(message)
    _agent_cache[thread_id] = (revision, frozenset(found))
    return _agent_cache[thread_id][1]


def _earlier_agent_values(thread_id: str) -> set[str]:
    """What the agent wrote in every other conversation that started before this one (or at the same time; all of
    them when this one's start isn't known)."""

    from row_bot import threads

    started = {str(row[0]): str(row[2] or "") for row in threads._list_threads()}  # noqa: SLF001 - its metadata
    mine = started.get(thread_id)
    found: set[str] = set()
    for other, when in started.items():
        if other != thread_id and (mine is None or when <= mine):
            found |= _agent_values(other)
    return found


def conversation_values(thread_id: str) -> frozenset[str]:
    """The values (:func:`links.value_tokens`) a conversation got from the person or the world (see the module)."""

    from row_bot.turn_context import is_person_message

    agent = _earlier_agent_values(thread_id)
    found: set[str] = set()
    for message in _messages(thread_id):
        values = set(links.value_tokens(_text(getattr(message, "content", ""))))
        kind = getattr(message, "type", "")
        if is_person_message(message):
            found |= values
        elif kind == "ai":
            agent |= _agent_wrote(message)
        elif kind == "tool" and not _own(str(getattr(message, "name", "") or "")):
            found |= values - agent
    return frozenset(found)


def trusted(values: list[str], origin: str) -> list[str]:
    """Those of ``values`` (normalised, as :func:`links.value_tokens` gives them) that came from a trusted source."""

    if not origin.startswith("thread:"):
        return list(values)
    try:
        said = conversation_values(origin.removeprefix("thread:"))
    except Exception:
        logger.warning("Stale skills: conversation %s unreadable; its values aren't trusted", origin, exc_info=True)
        return []
    return [value for value in values if value in said]


__all__ = ["OWN_TOOLS", "conversation_values", "trusted"]
