"""What Learning may record (D27).

Learning records only the owner's own work: their chats in Row-Bot (every
signed-in device is the owner), their own direct chats on channels, and their
workflows and goals. It never records a conversation someone else started or
shares: a group chat, a Slack or Discord channel thread, or a direct chat with
a paired person who is not the owner. Anything it can't classify is denied.

- :func:`decide` is the pure rule: facts plus settings in, allowed or denied with
  a reason code out. The source toggles (my chats; workflows and goals; the last
  30 days of past chats) and the per-chat opt-out feed it.
- :func:`describe_thread` gathers the facts about a conversation from the
  stores that already know them: ``thread_meta`` (delegated Agent threads),
  workflow runs, and ``channel_thread_refs`` (which channel a conversation lives
  on and whether other people share it, the same fact Hands-off is capped by).
  Who the owner is on a channel comes from that channel's configured owner id
  (``TELEGRAM_USER_ID``, ``DISCORD_USER_ID``, ``SLACK_USER_ID``,
  ``SMS_USER_PHONE``, ``WHATSAPP_USER_PHONE``). People paired by code are
  approved to talk to Row-Bot, but they are not the owner.
- :func:`check` is what the capture seams call (after :func:`row_bot.learning.is_enabled`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from row_bot import learning as gate
from row_bot.learning.contracts import valid_thread_id

if TYPE_CHECKING:
    from row_bot.learning.store import LearningStore

logger = logging.getLogger(__name__)

Conversation = Literal["app", "channel", "workflow", "unknown"]

# Reason codes. Allowed:
OWNER_CHAT = "owner_chat"
OWNER_CHANNEL_CHAT = "owner_channel_chat"
WORKFLOW = "workflow"
GOAL = "goal"
# Denied:
LEARNING_OFF = "learning_off"
CHAT_OPTED_OUT = "chat_opted_out"
UNKNOWN_SOURCE = "unknown_source"
SHARED_CONVERSATION = "shared_conversation"
NOT_OWNER = "not_owner"
OWNER_UNKNOWN = "owner_unknown"
MY_CHATS_OFF = "my_chats_off"
WORKFLOWS_AND_GOALS_OFF = "workflows_and_goals_off"
PAST_CHATS_OFF = "past_chats_off"

_MAX_AGENT_DEPTH = 8
# Channels whose configured owner id names the owner; a channel not listed
# (a plugin channel, say) has no known owner, so its chats are denied.
OWNER_SECRETS = {
    "telegram": "TELEGRAM_USER_ID",
    "discord": "DISCORD_USER_ID",
    "slack": "SLACK_USER_ID",
    "sms": "SMS_USER_PHONE",
    "whatsapp": "WHATSAPP_USER_PHONE",
}
_PHONE_CHANNELS = {"sms", "whatsapp"}


@dataclass(frozen=True)
class SourceFacts:
    """What is known about where a piece of work comes from.

    ``shared`` and ``with_owner`` are None when unknown, which denies.
    """

    conversation: Conversation
    thread_id: str = ""
    # The conversation a delegated Agent's thread belongs to (its own id otherwise).
    root_thread_id: str = ""
    channel: str | None = None
    shared: bool | None = None
    with_owner: bool | None = None
    goal: bool = False
    historical: bool = False


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    reason: str


class ScopeSettings(Protocol):
    enabled: bool
    my_chats: bool
    workflows_and_goals: bool
    past_chats: bool


def _deny(reason: str) -> ScopeDecision:
    return ScopeDecision(False, reason)


def decide(facts: SourceFacts, settings: ScopeSettings, *, opted_out: bool = False) -> ScopeDecision:
    """Whether Learning may record this work, and why. Pure: no I/O."""

    if not settings.enabled:
        return _deny(LEARNING_OFF)
    if opted_out:
        return _deny(CHAT_OPTED_OUT)
    if facts.conversation not in ("app", "channel", "workflow"):
        return _deny(UNKNOWN_SOURCE)
    if facts.conversation == "channel":
        # Never a conversation others share or someone else started.
        if facts.shared is None or not facts.channel:
            return _deny(UNKNOWN_SOURCE)
        if facts.shared:
            return _deny(SHARED_CONVERSATION)
        if facts.with_owner is None:
            return _deny(OWNER_UNKNOWN)
        if not facts.with_owner:
            return _deny(NOT_OWNER)
    if facts.goal or facts.conversation == "workflow":
        if not settings.workflows_and_goals:
            return _deny(WORKFLOWS_AND_GOALS_OFF)
    elif not settings.my_chats:
        return _deny(MY_CHATS_OFF)
    if facts.historical and not settings.past_chats:
        return _deny(PAST_CHATS_OFF)
    if facts.conversation == "workflow":
        return ScopeDecision(True, WORKFLOW)
    if facts.goal:
        return ScopeDecision(True, GOAL)
    return ScopeDecision(True, OWNER_CHANNEL_CHAT if facts.conversation == "channel" else OWNER_CHAT)


# ── Facts from the stores that know them ────────────────────────────────────


@dataclass(frozen=True)
class ThreadInfo:
    """One conversation as the other stores record it."""

    exists: bool
    thread_type: str = ""
    # For a delegated Agent's thread: the conversation that started it.
    parent_thread_id: str = ""
    is_workflow: bool = False
    channel: str | None = None
    channel_target: str = ""
    shared: bool | None = None


class ThreadLookup(Protocol):
    def thread(self, thread_id: str) -> ThreadInfo: ...

    def owner_identity(self, channel: str) -> str: ...


class SavedThreads:
    """Reads ``thread_meta``, workflow runs, Agent runs, channel refs and the
    channels' configured owner ids. Imports those owners only when asked."""

    def thread(self, thread_id: str) -> ThreadInfo:
        from row_bot import threads

        context = threads._get_thread_cleanup_context(thread_id)  # noqa: SLF001 - one read of the row
        if not context.get("exists"):
            return ThreadInfo(exists=False)
        thread_type = str(context.get("thread_type") or "")
        parent = ""
        if thread_type == "agent_child":
            try:
                from row_bot.agent_runs import get_agent_run_for_thread

                run = get_agent_run_for_thread(thread_id) or {}
                parent = str(run.get("parent_thread_id") or "")
            except Exception:
                logger.debug("Learning could not read an Agent thread's parent", exc_info=True)
        channel: str | None = None
        target = ""
        shared: bool | None = None
        try:
            from row_bot.tasks import get_thread_channel_ref

            ref = get_thread_channel_ref(thread_id)
        except Exception:
            logger.debug("Learning could not read a conversation's channel", exc_info=True)
            # Unknown origin: treat it as a channel conversation we can't classify.
            return ThreadInfo(exists=True, thread_type=thread_type, parent_thread_id=parent,
                              channel="", shared=None)
        if ref:
            channel = str(ref.get("channel") or "")
            target = str(ref.get("target") or "")
            shared = bool(int(ref.get("shared") or 0))
        is_workflow = False
        if channel is None:
            is_workflow = thread_id in threads.get_workflow_thread_ids()
        return ThreadInfo(
            exists=True,
            thread_type=thread_type,
            parent_thread_id=parent,
            is_workflow=is_workflow,
            channel=channel,
            channel_target=target,
            shared=shared,
        )

    def owner_identity(self, channel: str) -> str:
        key = OWNER_SECRETS.get(channel)
        if key is None:
            return ""
        try:
            from row_bot.channels.auth_store import get_channel_secret

            return str(get_channel_secret(channel, key) or "").strip()
        except Exception:
            logger.debug("Learning could not read the %s owner id", channel, exc_info=True)
            return ""


def _identity(channel: str, value: str) -> str:
    text = str(value or "").strip()
    if channel == "whatsapp":
        text = text.split("@", 1)[0]
    if channel in _PHONE_CHANNELS:
        text = text.replace("+", "").replace("-", "").replace(" ", "")
    return text


def _with_owner(channel: str, target: str, sender_id: str | None, owner: str) -> bool | None:
    """Whether the person in a direct channel chat is the owner (None: can't tell)."""

    owner_id = _identity(channel, owner)
    if not owner_id:
        return None  # no owner configured: paired people only, so it is ambiguous
    if sender_id:
        candidate = _identity(channel, sender_id)
    elif channel in {"telegram", *_PHONE_CHANNELS}:
        # A private Telegram chat's id is the person's id; SMS and WhatsApp chats
        # are addressed by the person's number.
        candidate = _identity(channel, target)
    else:
        return None  # a Slack or Discord DM is addressed by its channel, not the person
    return candidate == owner_id if candidate else None


def describe_thread(
    thread_id: str,
    *,
    goal: bool = False,
    historical: bool = False,
    sender_id: str | None = None,
    lookup: ThreadLookup | None = None,
) -> SourceFacts:
    """The facts about a conversation (a delegated Agent's thread answers for the
    conversation that started it)."""

    clean = str(thread_id or "")
    if not valid_thread_id(clean):
        return SourceFacts("unknown", goal=goal, historical=historical)
    source = lookup or SavedThreads()
    try:
        current = clean
        info = source.thread(current)
        for _ in range(_MAX_AGENT_DEPTH):
            if not info.exists or info.thread_type != "agent_child":
                break
            if not info.parent_thread_id or not valid_thread_id(info.parent_thread_id):
                return SourceFacts("unknown", thread_id=clean, goal=goal, historical=historical)
            current = info.parent_thread_id
            info = source.thread(current)
        else:
            return SourceFacts("unknown", thread_id=clean, goal=goal, historical=historical)
        if not info.exists or info.thread_type == "agent_child":
            return SourceFacts("unknown", thread_id=clean, root_thread_id=current, goal=goal,
                               historical=historical)
        if info.channel is not None:
            channel = info.channel
            with_owner = (
                _with_owner(channel, info.channel_target, sender_id, source.owner_identity(channel))
                if channel
                else None
            )
            return SourceFacts(
                "channel",
                thread_id=clean,
                root_thread_id=current,
                channel=channel or None,
                shared=info.shared,
                with_owner=with_owner,
                goal=goal,
                historical=historical,
            )
        conversation: Conversation = "workflow" if info.is_workflow else "app"
        return SourceFacts(conversation, thread_id=clean, root_thread_id=current, goal=goal,
                           historical=historical)
    except Exception:
        logger.debug("Learning could not classify a conversation; denying", exc_info=True)
        return SourceFacts("unknown", thread_id=clean, goal=goal, historical=historical)


def check(
    thread_id: str,
    *,
    goal: bool = False,
    historical: bool = False,
    sender_id: str | None = None,
    lookup: ThreadLookup | None = None,
    store: LearningStore | None = None,
) -> ScopeDecision:
    """Whether Learning may record work in this conversation now.

    Off costs one cached settings read. On, it reads the conversation's facts and
    its opt-out (the conversation's own, and the one that started it).
    """

    settings = gate.read_settings()
    if not settings.enabled:
        return _deny(LEARNING_OFF)
    facts = describe_thread(
        thread_id, goal=goal, historical=historical, sender_id=sender_id, lookup=lookup
    )
    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    opted_out = False
    for candidate in dict.fromkeys(filter(None, (facts.thread_id, facts.root_thread_id))):
        if saved.is_chat_opted_out(candidate):
            opted_out = True
            break
    return decide(facts, settings, opted_out=opted_out)


def set_chat_opt_out(thread_id: str, opted_out: bool, *, store: LearningStore | None = None) -> int:
    """"Don't learn from this chat": opting out purges what Learning recorded from
    it; opting back in restores nothing. Returns how many records were purged."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    report = saved.set_chat_opt_out(thread_id, opted_out)
    return report.total if report is not None else 0


__all__ = [
    "CHAT_OPTED_OUT",
    "GOAL",
    "LEARNING_OFF",
    "MY_CHATS_OFF",
    "NOT_OWNER",
    "OWNER_CHANNEL_CHAT",
    "OWNER_CHAT",
    "OWNER_SECRETS",
    "OWNER_UNKNOWN",
    "PAST_CHATS_OFF",
    "SHARED_CONVERSATION",
    "SavedThreads",
    "ScopeDecision",
    "SourceFacts",
    "ThreadInfo",
    "ThreadLookup",
    "UNKNOWN_SOURCE",
    "WORKFLOW",
    "WORKFLOWS_AND_GOALS_OFF",
    "check",
    "decide",
    "describe_thread",
    "set_chat_opt_out",
]
