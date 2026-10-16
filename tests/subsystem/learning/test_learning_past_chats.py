"""Reading the last 30 days of past chats (D26): partial-fidelity historical episodes, only
conversations updated in the window and only their turns inside it (O10, found by bisecting their
checkpoints), resumable, never twice, Slack and Discord DMs denied, stops when Learning is turned off
or paused, and yields while a chat is generating."""

from __future__ import annotations

import threading
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from row_bot import threads
from row_bot.learning import past_chats
from row_bot.learning.contracts import Decision, Episode
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import read_json_blob, save_learning_settings

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]  # a background thread and its stop

DAY = 86400.0
FAKE_KEY = "-".join(("sk", "abcdefghij0123456789"))  # made up, built at run time


class FakeChats:
    """Saved conversations: when each was created, its messages and its checkpoints
    ((saved at, how many messages), oldest first)."""

    def __init__(self) -> None:
        self.created: dict[str, float] = {}
        self.saved: dict[str, list] = {}
        self.checkpoints: dict[str, list[tuple[float, int]]] = {}
        self.reads: list[str] = []
        self.checkpoint_reads: list[int] = []

    def add(self, thread_id: str, created_at: float, messages: list,
            checkpoints: list[tuple[float, int]] | None = None) -> None:
        self.created[thread_id] = created_at
        self.saved[thread_id] = list(messages)
        self.checkpoints[thread_id] = list(checkpoints or [(created_at, len(messages))])

    def conversations(self, since: float) -> list[tuple[str, float]]:
        updated = {thread: max([created, *(at for at, _ in self.checkpoints[thread])])
                   for thread, created in self.created.items()}
        return sorted(((t, c) for t, c in self.created.items() if updated[t] >= since), key=lambda item: item[1])

    def messages(self, thread_id: str) -> list:
        self.reads.append(thread_id)
        return list(self.saved[thread_id])

    def window_start(self, thread_id: str, since: float, created_at: float) -> tuple[int, float]:
        if created_at >= since:
            return 0, created_at
        saved = self.checkpoints[thread_id]

        def read(checkpoint_id: str) -> tuple[float | None, int]:
            self.checkpoint_reads.append(int(checkpoint_id))
            return saved[int(checkpoint_id)]

        return past_chats.checkpoint_window([str(index) for index in range(len(saved))], since, read)


def _chat(question: str = "What's in notes.txt?") -> list:
    return [
        HumanMessage(content=question, id=f"h-{uuid.uuid4().hex[:6]}"),
        AIMessage(content="", id=f"a-{uuid.uuid4().hex[:6]}",
                  tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "notes.txt", "api_key": FAKE_KEY}}]),
        ToolMessage(content="three tasks", tool_call_id="c1", name="read_file", id=f"t-{uuid.uuid4().hex[:6]}",
                    additional_kwargs={"row_bot_untrusted": {"version": 1, "source": "read_file", "injection": []}}),
        AIMessage(content="It lists three tasks.", id=f"b-{uuid.uuid4().hex[:6]}"),
        HumanMessage(content="Thanks, and the second?", id=f"h2-{uuid.uuid4().hex[:6]}"),
        AIMessage(content="Buy milk.", id=f"c-{uuid.uuid4().hex[:6]}"),
    ]


@pytest.fixture
def source(clock):
    return FakeChats()


def _run(store, source, chats, clock, **kwargs):
    kwargs.setdefault("busy", lambda: False)
    kwargs.setdefault("wait", lambda seconds: None)
    return past_chats.run_import(store=store, source=source, lookup=chats, clock=clock, **kwargs)


def test_past_chats_in_the_window_become_partial_historical_episodes(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("recent").chat("old")
    source.add("recent", clock() - 10 * DAY, _chat())
    source.add("old", clock() - 40 * DAY, _chat())

    result = _run(store, source, chats, clock)

    assert result == past_chats.ImportProgress(total=1, done=1, state="ready")
    episodes = store.find("episode")
    assert {item.thread_id for item in episodes} == {"recent"}
    assert all(isinstance(item, Episode) and item.historical and item.fidelity == "partial" for item in episodes)
    first, second = sorted(episodes, key=lambda item: item.started_at)
    decisions = sorted(store.find("decision"), key=lambda item: (item.created_at, item.sequence))
    assert all(isinstance(item, Decision) and item.fidelity == "partial" for item in decisions)
    replies = [item for item in decisions if item.kind == "model_call"]
    # A past reply is kept as stored, never labelled as the model's own output.
    assert all(item.executed == "repaired" and item.model_generated_blob is None for item in replies)
    request = read_json_blob(store, replies[0].request_blob)
    assert request["reconstructed"] is True and request["prefix"] == 1
    conversation = read_json_blob(store, request["messages_of"])["messages"]
    assert len(conversation) == 6
    # Read back for replay: the conversation as stored, up to that reply.
    from row_bot.learning.capture import read_request

    later = read_request(store, replies[1])
    assert later["reconstructed"] is True and [m["type"] for m in later["messages"]] == ["human", "ai", "tool"]
    # The second turn's person message followed the first turn's reply (a marker only).
    assert second.follows_decision_id == [r for r in replies if r.episode_id == first.id][-1].id
    tool = next(item for item in decisions if item.kind == "tool_call")
    assert tool.untrusted is True and tool.tool_name == "read_file"
    assert FAKE_KEY not in str(read_json_blob(store, tool.arguments_blob))
    assert store.find("signal") == []


def test_the_import_resumes_where_it_stopped_and_never_imports_a_conversation_twice(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    for name in ("a", "b", "c"):
        chats.chat(name)
        source.add(name, clock() - DAY, _chat())
    checks = iter([False, True])  # stop before the second conversation

    stopped = _run(store, source, chats, clock, should_stop=lambda: next(checks, False))
    assert stopped == past_chats.ImportProgress(total=3, done=1, state="stopped")
    assert {item.thread_id for item in store.find("episode")} == {"a"}

    finished = _run(store, source, chats, clock, should_stop=lambda: False)
    assert finished == past_chats.ImportProgress(total=3, done=3, state="ready")
    counts = (len(store.find("episode")), len(store.find("decision")))
    assert counts == (6, 12)  # two turns each: 3 + 1 decisions

    source.reads.clear()
    assert _run(store, source, chats, clock) == finished  # a finished import never runs again
    assert source.reads == [] and (len(store.find("episode")), len(store.find("decision"))) == counts


def test_turns_after_the_import_was_planned_are_not_read(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("a").chat("b")
    source.add("a", clock() - DAY, _chat())
    source.add("b", clock() - DAY, _chat())
    checks = iter([False, True])
    _run(store, source, chats, clock, should_stop=lambda: next(checks, False))
    # A live turn in "b" after the plan: captured live, so never read again.
    source.saved["b"] += [HumanMessage(content="A new question"), AIMessage(content="A new answer")]
    _run(store, source, chats, clock, should_stop=lambda: False)
    assert len(store.find("episode", thread_id="b")) == 2


def test_slack_and_discord_dms_and_shared_or_opted_out_chats_are_never_imported(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.owners.update({"slack": "U_OWNER", "discord": "42", "telegram": "777"})
    chats.channel("slack_D1", "slack", "D1").channel("discord_1", "discord", "1")
    chats.channel("slack_C1", "slack", "C1", shared=True).channel("tg_777", "telegram", "777")
    chats.chat("excluded")
    for name in ("slack_D1", "discord_1", "slack_C1", "tg_777", "excluded"):
        source.add(name, clock() - DAY, _chat())
    store.create()
    store.set_chat_opt_out("excluded", True)

    _run(store, source, chats, clock)

    episodes = store.find("episode")
    assert {item.thread_id for item in episodes} == {"tg_777"}  # the owner's own Telegram chat
    assert {item.source for item in episodes} == {"channel_chat"} and {item.channel for item in episodes} == {"telegram"}


def test_the_import_needs_learning_and_past_chats_on(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("a")
    source.add("a", clock() - DAY, _chat())
    save_learning_settings(learning_on, sources={"past_chats": False})
    assert _run(store, source, chats, clock) is None
    assert store.find("episode") == []


def test_turning_learning_off_stops_the_import(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    for name in ("a", "b", "c"):
        chats.chat(name)
        source.add(name, clock() - DAY, _chat())
    calls = []

    def busy() -> bool:
        calls.append(1)
        if len(calls) == 2:  # while the second conversation is about to be read
            save_learning_settings(learning_on, enabled=False)
        return False

    result = past_chats.run_import(store=store, source=source, lookup=chats, clock=clock, busy=busy,
                                   wait=lambda seconds: None)
    assert result is not None and result.done == 1 and not result.finished
    assert {item.thread_id for item in store.find("episode")} == {"a"}


def test_the_import_yields_while_a_chat_is_generating(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("a")
    source.add("a", clock() - DAY, _chat())
    generating = iter([True, True, True, False])
    waits: list[float] = []
    seen_paused: list[str] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        seen_paused.append(past_chats.progress(store).state)

    result = _run(store, source, chats, clock, busy=lambda: next(generating, False), wait=wait)

    assert len(waits) == 2 and seen_paused == ["paused", "paused"]
    assert result == past_chats.ImportProgress(total=1, done=1, state="ready")


def test_saved_conversations_are_read_from_their_checkpoints(learning_on, clock):
    thread_id = f"past-{uuid.uuid4().hex[:10]}"
    threads.create_thread("A past chat", thread_id=thread_id, seed_default_skills=False)
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(content="Plan my week", id=f"{thread_id}-h"),
        AIMessage(content="Here is a plan.", id=f"{thread_id}-a"),
    ])
    source = past_chats.SavedChats()
    assert thread_id in [item for item, _created in source.conversations(clock() - 365 * DAY)]

    class OnlyThis(past_chats.SavedChats):
        def conversations(self, since):
            return [item for item in super().conversations(since) if item[0] == thread_id]

    store = LearningStore(learning_on)
    result = past_chats.run_import(store=store, source=OnlyThis(), busy=lambda: False, wait=lambda s: None)
    assert result is not None and result.finished
    [episode] = store.find("episode", thread_id=thread_id)
    assert episode.historical and episode.source == "my_chat"


def test_the_background_import_runs_once_at_a_time_and_stops(learning_on, monkeypatch):
    started = threading.Event()
    calls = []

    def fake_run_import(*, should_stop, wait, root):
        calls.append(root)
        started.set()
        while not should_stop():
            wait(0.01)

    monkeypatch.setattr(past_chats, "run_import", fake_run_import)
    assert past_chats.start_background() is True
    assert started.wait(10)
    assert past_chats.start_background() is False  # already reading
    past_chats.stop_background(10)
    assert not [t for t in threading.enumerate() if t.name == "learning-past-chats" and t.is_alive()]
    assert calls == [learning_on]


# ── O10: conversations updated in the window, from their first checkpoint inside it ──


def test_an_older_conversation_updated_in_the_window_gives_only_its_last_30_days(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("long")
    now = clock()
    # Created 60 days ago; its first turn ended 40 days ago, its second 10 days ago.
    source.add("long", now - 60 * DAY, _chat(), checkpoints=[(now - 59 * DAY, 2), (now - 40 * DAY, 4),
                                                              (now - 10 * DAY, 6)])

    result = _run(store, source, chats, clock)

    assert result == past_chats.ImportProgress(total=1, done=1, state="ready")
    [episode] = store.find("episode")
    assert episode.thread_id == "long" and episode.historical
    assert abs(episode.started_at - (now - 10 * DAY)) < 1  # dated from the checkpoint inside the window
    replies = [item for item in store.find("decision") if item.kind == "model_call"]
    assert len(replies) == 1
    request = read_json_blob(store, replies[0].request_blob)
    # The reply's request is still the whole conversation up to it, as the model saw it.
    assert request["prefix"] == 5 and len(read_json_blob(store, request["messages_of"])["messages"]) == 6
    assert episode.follows_decision_id is None  # the turn before it is outside the window


def test_when_older_checkpoints_were_tidied_away_only_what_followed_the_oldest_kept_one_counts(
        learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("long")
    now = clock()
    source.add("long", now - 90 * DAY, _chat(), checkpoints=[(now - 5 * DAY, 4), (now - DAY, 6)])

    _run(store, source, chats, clock)

    assert len(store.find("episode")) == 1  # never a turn that might be older than 30 days


def test_an_older_conversation_not_updated_in_the_window_is_left_out(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("old").chat("recent")
    now = clock()
    source.add("old", now - 60 * DAY, _chat(), checkpoints=[(now - 50 * DAY, 6)])
    source.add("recent", now - DAY, _chat())

    result = _run(store, source, chats, clock)

    assert result == past_chats.ImportProgress(total=1, done=1, state="ready")
    assert {item.thread_id for item in store.find("episode")} == {"recent"}


def test_the_window_is_found_by_bisecting_the_checkpoints(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    chats.chat("busy")
    now = clock()
    messages = _chat() * 32  # 64 turns
    checkpoints = [(now - (64 - index) * DAY, (index + 1) * 3) for index in range(64)]
    source.add("busy", now - 70 * DAY, messages, checkpoints=checkpoints)

    _run(store, source, chats, clock)

    assert len(source.checkpoint_reads) <= 8  # log2(64) + 2, not all 64
    # 30 days at one checkpoint a day: from the 34th checkpoint's 102 messages on.
    assert all(item.started_at >= now - 31 * DAY for item in store.find("episode"))
    assert len(store.find("episode")) == len(past_chats._turns(messages[102:]))


def test_saved_conversations_are_bisected_by_their_real_checkpoint_times(learning_on):
    thread_id = f"past-{uuid.uuid4().hex[:10]}"
    threads.create_thread("A long chat", thread_id=thread_id, seed_default_skills=False)
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(content="Plan my week", id=f"{thread_id}-h1"), AIMessage(content="Done.", id=f"{thread_id}-a1"),
    ])
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(content="And next week?", id=f"{thread_id}-h2"), AIMessage(content="Also done.", id=f"{thread_id}-a2"),
    ])
    source = past_chats.SavedChats()
    with threads.checkpointer.cursor(transaction=False) as cursor:
        ids = [str(row[0]) for row in cursor.execute(
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = '' ORDER BY checkpoint_id",
            (thread_id,))]
    stamps = [source._checkpoint(thread_id, item) for item in ids]
    assert [count for _at, count in stamps] == [2, 4]
    between = (stamps[0][0] + stamps[1][0]) / 2

    # As if the conversation began long before the window and its second turn came inside it.
    assert source.window_start(thread_id, between, created_at=between - 40 * DAY) == (2, stamps[1][0])
    assert source.window_start(thread_id, stamps[0][0] - 1, created_at=0.0) == (2, stamps[0][0])
    assert source.window_start(thread_id, stamps[1][0] + 1, created_at=0.0)[0] == 4

    class ListOnly:
        """A checkpointer without the SQLite cursor: ids come from its listing."""

        def __init__(self, inner):
            self.list = inner.list
            self.get_tuple = inner.get_tuple

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(threads, "checkpointer", ListOnly(threads.checkpointer))
        assert source.window_start(thread_id, between, created_at=between - 40 * DAY) == (2, stamps[1][0])


# ── Pause ────────────────────────────────────────────────────────────────────


def test_a_paused_learning_reads_no_past_chats_and_pausing_stops_the_import(learning_on, clock, chats, source):
    store = LearningStore(learning_on, clock=clock)
    for name in ("a", "b"):
        chats.chat(name)
        source.add(name, clock() - DAY, _chat())
    save_learning_settings(learning_on, paused=True)
    assert _run(store, source, chats, clock) is None and store.find("episode") == []

    save_learning_settings(learning_on, paused=False)
    calls = []

    def busy() -> bool:
        calls.append(1)
        if len(calls) == 2:  # paused while the second conversation is about to be read
            save_learning_settings(learning_on, paused=True)
        return False

    result = past_chats.run_import(store=store, source=source, lookup=chats, clock=clock, busy=busy,
                                   wait=lambda seconds: None)
    assert result == past_chats.ImportProgress(total=2, done=1, state="stopped")
    save_learning_settings(learning_on, paused=False)
    assert _run(store, source, chats, clock) == past_chats.ImportProgress(total=2, done=2, state="ready")
