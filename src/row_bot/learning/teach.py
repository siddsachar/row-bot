"""Teach Row-Bot: the person's own correction of a reply or a step, saved as a Signal.

A Teach is evidence for later, never a change now: the improvement loop (L3a) reads it during
quiet hours. It is recorded only while Learning is on (not paused) and only in a conversation
Learning may use (scope, D27), so never in a conversation someone else started.

It is linked to what it is about when that was captured:

- a reply: the model call that produced it. This process's capture remembers the replies it
  wrote; otherwise the conversation's saved replies are searched for the message id;
- a failed step: the tool call with that call id.

A Teach about something Learning never captured (a reply from before Learning was on) is
still saved, on the conversation. Undo removes it (nothing is kept). The note is redacted the
way captured text is.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from row_bot import learning as gate
from row_bot.learning import redaction, scope
from row_bot.learning.contracts import Decision, Signal, new_id, valid_thread_id
from row_bot.learning.store import LearningStore

logger = logging.getLogger(__name__)

CATEGORIES = ("wrong_answer", "wrong_tool_or_step", "ignored_instructions", "other")
MAX_NOTE = 2000
# How many of a conversation's newest replies or steps a Teach looks through.
_SEARCH_LIMIT = 200


class TeachError(Exception):
    """Why a Teach can't be saved: ``learning_off``, ``learning_paused``,
    ``learning_chat_not_used`` or ``invalid_learning_command``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Taught:
    signal: Signal
    # Whether it was linked to a captured reply or step.
    linked: bool


def _message_id(store: LearningStore, sha: str | None) -> str:
    if not sha:
        return ""
    data = store.read_blob(sha)
    if data is None:
        return ""
    try:
        message = json.loads(data.decode("utf-8"))
    except ValueError:
        return ""
    if isinstance(message, dict):
        inner = message.get("data")
        if isinstance(inner, dict):
            return str(inner.get("id") or "")
        return str(message.get("id") or "")
    return ""


def find_reply(store: LearningStore, thread_id: str, message_id: str) -> Decision | None:
    """The captured model call that produced a reply, by the reply's message id."""

    if not message_id:
        return None
    capture = sys.modules.get("row_bot.learning.capture")
    if capture is not None:
        try:
            capture.flush(2.0)
            remembered = capture.decision_for_message(message_id)
        except Exception:
            remembered = None
        if remembered is not None:
            found = store.get("decision", remembered[1])
            if isinstance(found, Decision):
                return found
    for decision in store.find("decision", thread_id=thread_id, subkind="model_call",
                               newest_first=True, limit=_SEARCH_LIMIT):
        if not isinstance(decision, Decision):
            continue
        if message_id in {_message_id(store, decision.repaired_output_blob),
                          _message_id(store, decision.raw_output_blob)}:
            return decision
    return None


def find_step(store: LearningStore, thread_id: str, tool_call_id: str) -> Decision | None:
    """The captured tool call of a step, by its call id."""

    if not tool_call_id:
        return None
    for decision in store.find("decision", thread_id=thread_id, subkind="tool_call",
                               newest_first=True, limit=_SEARCH_LIMIT):
        if isinstance(decision, Decision) and decision.tool_call_id == tool_call_id:
            return decision
    return None


def teach(
    thread_id: str,
    *,
    message_id: str = "",
    tool_call_id: str = "",
    category: str | None = None,
    note: str = "",
    store: LearningStore | None = None,
    lookup: scope.ThreadLookup | None = None,
    clock: Callable[[], float] = time.time,
) -> Taught:
    """Save a Teach on a reply (``message_id``) or a failed step (``tool_call_id``)."""

    clean = str(thread_id or "")
    if not valid_thread_id(clean) or (category is not None and category not in CATEGORIES):
        raise TeachError("invalid_learning_command")
    text = str(note or "").strip()
    if len(text) > MAX_NOTE:
        raise TeachError("invalid_learning_command")
    settings = gate.read_settings()
    if not settings.enabled:
        raise TeachError("learning_off")
    if settings.paused:
        raise TeachError("learning_paused")
    saved = store if store is not None else LearningStore()
    decision, facts = scope.resolve(clean, lookup=lookup, store=saved)
    if not decision.allowed:
        raise TeachError("learning_chat_not_used")
    target = find_step(saved, clean, tool_call_id) if tool_call_id else find_reply(saved, clean, message_id)
    if text:
        text, _ = redaction.redact_text(text)
    conversation = facts.root_thread_id if valid_thread_id(facts.root_thread_id) else clean
    signal = Signal(
        id=new_id("sig"),
        kind="teach",
        thread_id=clean,
        created_at=float(clock()),
        conversation_id=conversation,
        episode_id=target.episode_id if target else None,
        decision_id=target.id if target else None,
        category=category,  # type: ignore[arg-type]
        note=text[:MAX_NOTE] or None,
    )
    if not saved.put(signal):
        raise TeachError("learning_chat_not_used")
    return Taught(signal, target is not None)


def undo(signal_id: str, *, store: LearningStore | None = None) -> bool:
    """Undo a Teach: remove it. False when it is already gone."""

    saved = store if store is not None else LearningStore()
    found = saved.get("signal", str(signal_id or ""))
    if not isinstance(found, Signal) or found.kind != "teach":
        return False
    return saved.remove("signal", found.id)


__all__ = ["CATEGORIES", "MAX_NOTE", "TeachError", "Taught", "find_reply", "find_step", "teach", "undo"]
