"""What Learning may record (D27): the owner's own chats, their own direct
channel chats, workflows and goals; never a conversation someone else started
or shares; unknown sources denied; the per-chat opt-out purges."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from row_bot import learning, tasks, threads
from row_bot.learning import scope
from row_bot.learning.scope import SourceFacts, ThreadInfo
from row_bot.learning.store import MARK_CHAT_EXCLUDED, LearningStore
from tests.fixtures.learning import decision, episode, lesson, save_learning_settings, signal

pytestmark = pytest.mark.subsystem

ON = replace(learning.DEFAULTS, enabled=True)


def _decide(facts: SourceFacts, **settings) -> scope.ScopeDecision:
    return scope.decide(facts, replace(ON, **settings))


@pytest.mark.parametrize(
    ("facts", "allowed", "reason"),
    [
        (SourceFacts("app", "chat-1"), True, scope.OWNER_CHAT),
        (SourceFacts("channel", "tg_1", channel="telegram", shared=False, with_owner=True), True,
         scope.OWNER_CHANNEL_CHAT),
        (SourceFacts("workflow", "run-1"), True, scope.WORKFLOW),
        (SourceFacts("app", "chat-1", goal=True), True, scope.GOAL),
        (SourceFacts("channel", "tg_1", channel="telegram", shared=False, with_owner=True, goal=True), True,
         scope.GOAL),
        # Someone else's conversation, or one other people share, is never recorded.
        (SourceFacts("channel", "slack_C1", channel="slack", shared=True, with_owner=True), False,
         scope.SHARED_CONVERSATION),
        (SourceFacts("channel", "sms_1", channel="sms", shared=False, with_owner=False), False, scope.NOT_OWNER),
        (SourceFacts("channel", "discord_1", channel="discord", shared=False, with_owner=None), False,
         scope.OWNER_UNKNOWN),
        (SourceFacts("channel", "x_1", channel="plugin_chat", shared=None, with_owner=True), False,
         scope.UNKNOWN_SOURCE),
        (SourceFacts("channel", "x_1", channel=None, shared=False, with_owner=True), False, scope.UNKNOWN_SOURCE),
        (SourceFacts("unknown", "who-knows"), False, scope.UNKNOWN_SOURCE),
        (SourceFacts("email", "x"), False, scope.UNKNOWN_SOURCE),  # type: ignore[arg-type]
    ],
)
def test_the_decision_for_each_kind_of_source(facts, allowed, reason):
    assert _decide(facts) == scope.ScopeDecision(allowed, reason)


def test_learning_off_and_the_opt_out_deny_everything():
    owner_chat = SourceFacts("app", "chat-1")
    assert scope.decide(owner_chat, learning.DEFAULTS) == scope.ScopeDecision(False, scope.LEARNING_OFF)
    assert scope.decide(owner_chat, ON, opted_out=True) == scope.ScopeDecision(False, scope.CHAT_OPTED_OUT)


def test_the_source_toggles_feed_the_same_decision():
    chat = SourceFacts("app", "chat-1")
    channel = SourceFacts("channel", "tg_1", channel="telegram", shared=False, with_owner=True)
    workflow = SourceFacts("workflow", "run-1")
    goal = SourceFacts("app", "chat-1", goal=True)
    past = SourceFacts("app", "chat-1", historical=True)

    assert _decide(chat, my_chats=False).reason == scope.MY_CHATS_OFF
    assert _decide(channel, my_chats=False).reason == scope.MY_CHATS_OFF
    assert _decide(workflow, my_chats=False).allowed is True
    assert _decide(goal, my_chats=False).allowed is True
    assert _decide(workflow, workflows_and_goals=False).reason == scope.WORKFLOWS_AND_GOALS_OFF
    assert _decide(goal, workflows_and_goals=False).reason == scope.WORKFLOWS_AND_GOALS_OFF
    assert _decide(chat, workflows_and_goals=False).allowed is True
    assert _decide(past, past_chats=False).reason == scope.PAST_CHATS_OFF
    assert _decide(past).allowed is True
    assert _decide(chat, past_chats=False).allowed is True  # only past chats need that box
    # The never-rules come before the toggles.
    shared = SourceFacts("channel", "slack_C1", channel="slack", shared=True, with_owner=False)
    assert _decide(shared, my_chats=False).reason == scope.SHARED_CONVERSATION


class FakeThreads:
    """What the stores know about conversations, and the channels' owner ids."""

    def __init__(self, threads: dict[str, ThreadInfo], owners: dict[str, str] | None = None) -> None:
        self.threads = threads
        self.owners = owners or {}

    def thread(self, thread_id: str) -> ThreadInfo:
        return self.threads.get(thread_id, ThreadInfo(exists=False))

    def owner_identity(self, channel: str) -> str:
        return self.owners.get(channel, "")


def _channel(channel: str, target: str, *, shared: bool = False) -> ThreadInfo:
    return ThreadInfo(exists=True, channel=channel, channel_target=target, shared=shared)


OWNERS = {"telegram": "4242", "sms": "+44 7700-900001", "whatsapp": "447700900002",
          "slack": "U_OWNER", "discord": "1001"}


@pytest.mark.parametrize(
    ("info", "sender", "conversation", "with_owner"),
    [
        (ThreadInfo(exists=True), None, "app", None),
        (ThreadInfo(exists=True, is_workflow=True), None, "workflow", None),
        # A private Telegram chat's id is the person's id.
        (_channel("telegram", "4242"), None, "channel", True),
        (_channel("telegram", "5555"), None, "channel", False),
        # SMS and WhatsApp chats are addressed by the person's number.
        (_channel("sms", "+447700900001"), None, "channel", True),
        (_channel("sms", "+447700900009"), None, "channel", False),
        (_channel("whatsapp", "447700900002@c.us"), None, "channel", True),
        # A Slack or Discord DM names its channel, not the person: only the sender tells.
        (_channel("slack", "D123"), None, "channel", None),
        (_channel("slack", "D123"), "U_OWNER", "channel", True),
        (_channel("discord", "777"), "2002", "channel", False),
        # A plugin channel has no known owner.
        (_channel("plugin_chat", "anyone"), "anyone", "channel", None),
    ],
)
def test_facts_come_from_the_conversation_and_the_channels_owner_id(info, sender, conversation, with_owner):
    facts = scope.describe_thread("thread-1", sender_id=sender, lookup=FakeThreads({"thread-1": info}, OWNERS))
    assert facts.conversation == conversation
    assert facts.with_owner == with_owner
    assert facts.root_thread_id == "thread-1"


def test_an_owner_id_that_is_not_configured_is_ambiguous_and_denied():
    lookup = FakeThreads({"sms_1": _channel("sms", "+447700900001")}, owners={})
    facts = scope.describe_thread("sms_1", lookup=lookup)
    assert facts.with_owner is None
    assert scope.decide(facts, ON).reason == scope.OWNER_UNKNOWN


def test_a_delegated_agents_thread_answers_for_the_conversation_that_started_it():
    lookup = FakeThreads(
        {
            "child-2": ThreadInfo(exists=True, thread_type="agent_child", parent_thread_id="child-1"),
            "child-1": ThreadInfo(exists=True, thread_type="agent_child", parent_thread_id="slack_C1"),
            "slack_C1": _channel("slack", "C1:1700.1", shared=True),
            "orphan": ThreadInfo(exists=True, thread_type="agent_child"),
            "app-child": ThreadInfo(exists=True, thread_type="agent_child", parent_thread_id="chat-1"),
            "chat-1": ThreadInfo(exists=True),
        },
        OWNERS,
    )
    shared = scope.describe_thread("child-2", lookup=lookup)
    assert (shared.conversation, shared.root_thread_id, shared.shared) == ("channel", "slack_C1", True)
    assert scope.decide(shared, ON).reason == scope.SHARED_CONVERSATION
    assert scope.describe_thread("orphan", lookup=lookup).conversation == "unknown"
    owned = scope.describe_thread("app-child", lookup=lookup)
    assert (owned.conversation, owned.thread_id, owned.root_thread_id) == ("app", "app-child", "chat-1")


@pytest.mark.parametrize("thread_id", ["missing", "", "../escape", "x" * 600])
def test_unknown_or_unclassifiable_conversations_are_denied(thread_id):
    facts = scope.describe_thread(thread_id, lookup=FakeThreads({}))
    assert facts.conversation == "unknown"
    assert scope.decide(facts, ON) == scope.ScopeDecision(False, scope.UNKNOWN_SOURCE)


def test_a_lookup_that_fails_denies():
    class Broken(FakeThreads):
        def thread(self, thread_id):
            raise OSError("database is locked")

    assert scope.describe_thread("chat-1", lookup=Broken({})).conversation == "unknown"


def test_check_is_off_without_reading_anything(data_dir):
    class Unreachable(FakeThreads):
        def thread(self, thread_id):
            raise AssertionError("Learning off must not look anything up")

    assert scope.check("chat-1", lookup=Unreachable({})) == scope.ScopeDecision(False, scope.LEARNING_OFF)
    assert list(data_dir.iterdir()) == []


def test_check_reads_real_conversations_and_channel_refs(learning_on):
    class Owners(scope.SavedThreads):
        def owner_identity(self, channel):
            return OWNERS.get(channel, "")

    suffix = uuid.uuid4().hex[:8]
    chat = threads.create_thread("Scope chat", thread_id=f"scope-chat-{suffix}", seed_default_skills=False)
    owner_dm = threads.create_thread("Owner DM", thread_id=f"tg_4242_{suffix}", seed_default_skills=False)
    group = threads.create_thread("Group", thread_id=f"tg_-100_{suffix}", seed_default_skills=False)
    stranger = threads.create_thread("Stranger", thread_id=f"sms_447700900009_{suffix}", seed_default_skills=False)
    tasks.record_thread_channel_ref(owner_dm, channel="telegram", target=4242, shared=False)
    tasks.record_thread_channel_ref(group, channel="telegram", target=-100, shared=True)
    tasks.record_thread_channel_ref(stranger, channel="sms", target="+447700900009", shared=False)

    lookup = Owners()
    assert scope.check(chat, lookup=lookup) == scope.ScopeDecision(True, scope.OWNER_CHAT)
    assert scope.check(owner_dm, lookup=lookup) == scope.ScopeDecision(True, scope.OWNER_CHANNEL_CHAT)
    assert scope.check(group, lookup=lookup) == scope.ScopeDecision(False, scope.SHARED_CONVERSATION)
    assert scope.check(stranger, lookup=lookup) == scope.ScopeDecision(False, scope.NOT_OWNER)
    assert scope.check(f"never-created-{suffix}", lookup=lookup).reason == scope.UNKNOWN_SOURCE

    save_learning_settings(learning_on, sources={"my_chats": False})
    assert scope.check(chat, lookup=lookup).reason == scope.MY_CHATS_OFF
    assert scope.check(chat, goal=True, lookup=lookup).reason == scope.GOAL


def test_opting_a_chat_out_purges_it_and_opting_back_in_restores_nothing(learning_on, clock):
    store = LearningStore(clock=clock)
    blob = store.put_blob(b"what the model saw in chat-1")
    store.put_many([
        episode("chat-1"),
        decision("chat-1", request_blob=blob),
        signal("chat-1"),
        episode("child-1", "ep_child", conversation_id="chat-1"),
        episode("chat-2", "ep_2"),
        lesson(source_thread_ids=("chat-1",)),
    ])
    lookup = FakeThreads({
        "chat-1": ThreadInfo(exists=True),
        "chat-2": ThreadInfo(exists=True),
        "child-1": ThreadInfo(exists=True, thread_type="agent_child", parent_thread_id="chat-1"),
    })
    assert scope.check("chat-1", lookup=lookup, store=store).allowed is True

    assert scope.set_chat_opt_out("chat-1", True, store=store) == 4
    assert store.owned_by("chat-1") == []
    assert store.read_blob(blob) is None
    assert [item.id for item in store.find("episode")] == ["ep_2"]
    assert [(mark.id, mark.mark) for mark in store.marks()] == [("les_1", MARK_CHAT_EXCLUDED)]
    assert scope.check("chat-1", lookup=lookup, store=store).reason == scope.CHAT_OPTED_OUT
    # A delegated Agent's thread from that chat is out too, and the store refuses its records.
    assert scope.check("child-1", lookup=lookup, store=store).reason == scope.CHAT_OPTED_OUT
    assert store.put(episode("chat-1", "ep_late")) is False
    assert store.put(episode("child-1", "ep_late_child", conversation_id="chat-1")) is False

    assert scope.set_chat_opt_out("chat-1", False, store=store) == 0
    assert scope.check("chat-1", lookup=lookup, store=store).allowed is True
    assert store.owned_by("chat-1") == []  # nothing came back
