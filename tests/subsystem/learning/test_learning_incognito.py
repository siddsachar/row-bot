"""Learning skips incognito chats (N11): nothing of a chat that is or was incognito is recorded or
imported (no decisions, episodes, signals or blobs), a chat that turns incognito while its turn runs
records nothing more of it, and what Learning recorded before the chat turned incognito stays and
still trains, as memories saved before stay. Privacy: these stay in the pull request lane."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from row_bot import threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.api.v1.schemas import LearningConversation
from row_bot.learning import past_chats, scope, teach
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes
from tests.fixtures.learning import CapturedWork, FakeThreads, all_blob_text, all_record_text, save_learning_settings
from tests.subsystem.client_protocol.test_settings_snapshot_api import api

# ruff: noqa: F401, F811 -- the imported pytest fixture is requested by name.

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]  # the capture writer's blob files

SECRET = "My blood test came back with a high cholesterol reading."


def _request(text: str, number: int = 1) -> list:
    return [SystemMessage(content="You are Row-Bot.", id="sys"), HumanMessage(content=text, id=f"h{number}")]


def _nothing_of(root, *threads: str) -> None:
    store = LearningStore(root)
    for thread in threads:
        assert store.owned_by(thread) == [], thread
    assert SECRET not in all_blob_text(root) and SECRET not in all_record_text(root)


def test_an_incognito_chat_and_its_helpers_record_nothing_at_all(writer, chats, learning_on):
    chats.chat("private", incognito=True).child("private-helper", "private").chat("open")
    turn = writer.begin_turn(thread_id="private", surface="normal_chat", person_message=True)
    step = AIMessage(content="", id="a1", tool_calls=[{"id": "c1", "name": "save_memory", "args": {"text": SECRET}}])
    writer.model_call(turn, messages=_request(SECRET), response=step)
    writer.tool_call(turn, call=step.tool_calls[0], result=ToolMessage(content=SECRET, tool_call_id="c1", id="t1"))
    writer.approval(turn, payload={"tool": "save_memory", "description": SECRET}, mode="approve", response=False,
                    line="Approval: asked; denied by you", tool_call_id="c1")
    writer.end_turn(turn, "stopped", stopped_by_person=True)
    helper = writer.begin_turn(thread_id="private-helper", surface="agent")
    writer.model_call(helper, messages=_request(SECRET, 2), response=AIMessage(content=SECRET, id="a2"))
    writer.end_turn(helper, "completed")
    writer.retry("private", ["a1"])
    kept = writer.begin_turn(thread_id="open", surface="normal_chat")
    writer.model_call(kept, messages=_request("What day is it?", 3), response=AIMessage(content="Friday.", id="a3"))
    writer.end_turn(kept, "completed")
    writer.drain()

    _nothing_of(learning_on, "private", "private-helper")
    store = LearningStore(learning_on)
    assert {item.thread_id for item in store.find("episode")} == {"open"}
    assert store.find("signal") == []  # no stop, denial or retry from the incognito chat
    assert scope.check("private", lookup=chats, store=store) == scope.ScopeDecision(False, scope.INCOGNITO_CHAT)
    assert scope.check("private-helper", lookup=chats, store=store).reason == scope.INCOGNITO_CHAT


def test_a_chat_that_turns_incognito_midway_records_nothing_more_and_keeps_what_came_before(
        writer, chats, clock, learning_on):
    work = CapturedWork(writer, chats, clock)
    before = work.turn("chat-1", "What is six times seven?", "It is 42.")
    # The next turn's first call is written, then the person turns the chat incognito while it runs.
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    step = AIMessage(content="", id="a-mid", tool_calls=[{"id": "c-mid", "name": "calculate", "args": {}}])
    first = writer.model_call(turn, messages=_request("And six times eight?", 2), response=step)
    writer.drain()
    chats.went_incognito("chat-1")
    writer.tool_call(turn, call=step.tool_calls[0], result=ToolMessage(content=SECRET, tool_call_id="c-mid"))
    writer.model_call(turn, messages=[*_request("And six times eight?", 2), step], response=AIMessage(content=SECRET))
    writer.end_turn(turn, "completed")
    writer.drain()
    # Turning incognito off again doesn't bring it back: every later request carries the incognito part.
    after = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    writer.model_call(after, messages=_request(SECRET, 3), response=AIMessage(content="Noted.", id="a-after"))
    writer.end_turn(after, "completed")
    writer.drain()

    store = LearningStore(learning_on)
    assert sorted(item.id for item in store.find("decision", subkind="model_call")) == sorted([*before, first])
    [tool] = store.find("decision", subkind="tool_call")
    assert tool.tool_call_id == "call_1"  # the first turn's
    assert SECRET not in all_blob_text(learning_on) and SECRET not in all_record_text(learning_on)
    statuses = sorted(item.status for item in store.find("episode"))
    assert statuses == ["completed", "running"]  # the cut turn never settles, so it never trains

    # What was recorded before the chat turned incognito still trains.
    compiled = dataset.compile_dataset(store, recipes.load("16gb"), lookup=chats, refresh_vault=False)
    assert compiled.conversations == ("chat-1",)
    assert "not_in_scope" not in compiled.left_out


def test_a_queued_turn_whose_chat_turns_incognito_before_it_is_written_records_nothing(writer, chats, learning_on):
    chats.chat("chat-2")
    turn = writer.begin_turn(thread_id="chat-2", surface="normal_chat", person_message=True)
    writer.model_call(turn, messages=_request(SECRET), response=AIMessage(content="Noted.", id="a1"))
    writer.end_turn(turn, "completed")
    chats.went_incognito("chat-2")  # before the writer reached it
    writer.drain()

    _nothing_of(learning_on, "chat-2")


class _PastChats:
    """Saved conversations for the import: each created ten days ago, read whole."""

    def __init__(self, now: float, chats: dict[str, list]) -> None:
        self.now, self.chats = now, chats

    def conversations(self, since: float) -> list[tuple[str, float]]:
        return [(thread, self.now - 10 * 86_400) for thread in self.chats]

    def messages(self, thread_id: str) -> list:
        return list(self.chats[thread_id])

    def window_start(self, thread_id: str, since: float, created_at: float) -> tuple[int, float]:
        return 0, created_at


def test_the_past_chat_import_reads_no_chat_that_is_or_was_incognito(learning_on, clock, chats):
    save_learning_settings(learning_on, enabled=True, sources={"past_chats": True})
    said = [HumanMessage(content=SECRET, id="h1"), AIMessage(content="Thanks for telling me.", id="a1")]
    plain = [HumanMessage(content="What day is it?", id="h2"), AIMessage(content="Friday.", id="a2")]
    chats.chat("private", incognito=True).chat("was-private").chat("open")
    chats.went_incognito("was-private")
    source = _PastChats(clock(), {"private": said, "was-private": said, "open": plain})
    store = LearningStore(learning_on, clock=clock)

    progress = past_chats.run_import(store=store, source=source, lookup=chats, clock=clock,
                                     busy=lambda: False, wait=lambda _seconds: None)

    assert progress == past_chats.ImportProgress(total=3, done=3, state="ready")
    assert {item.thread_id for item in store.find("episode")} == {"open"}
    _nothing_of(learning_on, "private", "was-private")


def test_teach_is_refused_in_an_incognito_chat(learning_on, chats):
    chats.chat("private", incognito=True)

    with pytest.raises(teach.TeachError) as refused:
        teach.teach("private", note=SECRET, lookup=chats, store=LearningStore(learning_on))

    assert refused.value.code == "learning_chat_not_used"
    _nothing_of(learning_on, "private")


def test_a_chat_that_is_or_was_incognito_offers_no_teach_and_says_why(api, monkeypatch):
    client, headers, data, _ = api
    chats = FakeThreads().chat("private", incognito=True).chat("was-private").chat("open")
    chats.went_incognito("was-private")
    monkeypatch.setattr(scope, "SavedThreads", lambda: chats)
    save_learning_settings(data, enabled=True)

    def read(conversation: str) -> dict:
        response = client.get(f"/api/v1/learning/conversations/{conversation}", headers=headers)
        assert response.status_code == 200, response.text
        LearningConversation.model_validate_json(response.text)
        return response.json()

    for conversation in ("private", "was-private"):
        assert read(conversation) == {"conversation_id": conversation, "state": "not_used",
                                      "reason": "Learning doesn’t use chats that are or were incognito."}
    assert read("open")["state"] == "learning"
