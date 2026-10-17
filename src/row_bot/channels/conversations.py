"""Which Row-Bot conversation a channel chat or thread talks to (decision D6).

- Slack and Discord threads are each their own conversation. A mention in a
  channel starts one, and replies in that thread continue it.
- A DM, an SMS contact or a WhatsApp chat has one rolling conversation, and
  ``/new`` moves it to a fresh one.

A pointer per chat or thread (``channel_conversations`` in tasks.db) records
which conversation is current, so a restart keeps it. Chats that talked to
Row-Bot before the pointer existed keep their conversation: with no pointer,
the chat's fixed thread (``slack_D…``, ``sms_…``) is adopted. A chat whose
current conversation was deleted in the app starts a fresh one; it never falls
back to a conversation it moved away from.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

# Channel handlers run concurrently (one task or thread per event). Resolving
# and creating a pointer is a few quick SQLite statements, so one lock keeps
# two first messages of a chat from starting two conversations.
_LOCK = threading.RLock()

_NAME_WORDS_LIMIT = 48


@dataclass(frozen=True)
class ChannelConversation:
    """One chat or thread on a channel, and how its conversations are made."""

    channel: str
    """Channel registry name, e.g. ``"slack"``."""
    key: str
    """The chat or thread, e.g. ``"D123"`` or ``"C123:1700000000.000100"``."""
    default_thread_id: str
    """The Row-Bot thread id this chat has always used, or would use first."""
    name: str
    """Name for a conversation started here."""
    target: str
    """Where late messages for its conversation are delivered."""
    shared: bool = False
    """Other people share it (a channel thread or a group chat): it runs on Auto at most, never Full
    autonomy (``full_autonomy``)."""


def first_words(text: str, limit: int = _NAME_WORDS_LIMIT) -> str:
    """The start of a message for a conversation name, cut after a whole word."""
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    head = collapsed[: limit + 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else collapsed[:limit]
    return cut.rstrip(" ,;:-") + "…"


def fresh_conversation_name(base: str) -> str:
    """Name for a conversation ``/new`` starts, told apart from the earlier ones."""
    return f"{base} · {datetime.now():%d %b %H:%M}"


def _thread_exists(thread_id: str) -> bool:
    from row_bot.threads import _thread_exists as exists

    return bool(thread_id) and exists(thread_id)


def _record_ref(conversation: ChannelConversation, thread_id: str) -> None:
    try:
        from row_bot.tasks import record_thread_channel_ref

        record_thread_channel_ref(
            thread_id,
            channel=conversation.channel,
            target=conversation.target,
            external_conversation_id=conversation.key,
            shared=conversation.shared,
        )
    except Exception:
        log.debug("%s thread channel ref skipped", conversation.channel, exc_info=True)


def _point(conversation: ChannelConversation, thread_id: str) -> None:
    from row_bot.tasks import set_channel_conversation

    set_channel_conversation(conversation.channel, conversation.key, thread_id)


def _fresh_thread_id(conversation: ChannelConversation) -> str:
    base = conversation.default_thread_id
    if not _thread_exists(base):
        return base
    while True:
        candidate = f"{base}_{secrets.token_hex(3)}"
        if not _thread_exists(candidate):
            return candidate


def _start(conversation: ChannelConversation, name: str) -> str:
    from row_bot.threads import _save_thread_meta

    thread_id = _fresh_thread_id(conversation)
    _save_thread_meta(
        thread_id,
        name,
        seed_default_skills=True,
        allow_recreate=True,
    )
    _record_ref(conversation, thread_id)
    _point(conversation, thread_id)
    log.debug("%s conversation started for a chat", conversation.channel)
    return thread_id


def _continue(conversation: ChannelConversation, thread_id: str) -> str:
    from row_bot.threads import touch_thread

    touch_thread(thread_id)  # recency only; a name the person gave stays
    _record_ref(conversation, thread_id)
    return thread_id


def current_thread(conversation: ChannelConversation) -> str:
    """Return the conversation this chat or thread continues, starting one if needed."""
    with _LOCK:
        from row_bot.tasks import get_channel_conversation

        pointer = get_channel_conversation(conversation.channel, conversation.key)
        if pointer is not None:
            thread_id = str(pointer.get("thread_id") or "")
            if _thread_exists(thread_id):
                return _continue(conversation, thread_id)
            return _start(conversation, conversation.name)
        if _thread_exists(conversation.default_thread_id):
            # A chat from before pointers: its fixed thread stays current.
            _point(conversation, conversation.default_thread_id)
            return _continue(conversation, conversation.default_thread_id)
        return _start(conversation, conversation.name)


def start_new_thread(conversation: ChannelConversation, *, name: str | None = None) -> str:
    """Move this chat or thread to a fresh conversation (``/new``, stuck-thread recovery).

    The earlier conversation stays in Row-Bot, unchanged.
    """
    with _LOCK:
        return _start(conversation, name or fresh_conversation_name(conversation.name))


def known_thread(channel: str, key: str) -> str:
    """The live conversation a chat or thread already has, or ``""``.

    Used to continue a Slack or Discord thread that a reply arrives in without
    mentioning Row-Bot: only threads Row-Bot is already part of are continued.
    """
    from row_bot.tasks import get_channel_conversation

    pointer = get_channel_conversation(channel, key)
    thread_id = str((pointer or {}).get("thread_id") or "")
    return thread_id if _thread_exists(thread_id) else ""
