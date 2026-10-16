"""Capture across real Agent turns (a fake model, fake tools, the real graph and seams): what each
model call was sent, parallel tool calls, an overflow compacted and retried, approvals given and
refused, the person's Stop, Retry in place, a delegated Agent, Chat Only and the usage ledger; and
deleting the conversation afterwards purges everything captured."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import closing

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, message_to_dict
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

import row_bot.agent as agent
from row_bot import agent_runs, thread_cleanup, threads
from row_bot.learning import capture
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import read_json_blob
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, model_call_in_turn, says

pytestmark = [pytest.mark.integration, pytest.mark.platform]  # the writer thread across real turns


@tool
def read_note(name: str) -> str:
    """Read one of the person's notes."""
    return f"The {name} note says: water the plants."


@tool
def delete_file(path: str) -> str:
    """Delete one of the person's files (asks first)."""
    from row_bot.tools import approval_gate

    refusal = approval_gate.gate_action({"tool": "delete_file", "label": "Delete a file",
                                         "description": f"Delete {path}"})
    return refusal or f"Deleted {path}."


def _written(root) -> LearningStore:
    assert capture.flush(10)
    return LearningStore(root)


def _decisions(store: LearningStore, **filters) -> list:
    return sorted(store.find("decision", **filters), key=lambda item: (item.created_at, item.sequence))


def _sent(store: LearningStore, decision) -> list:
    """The messages a captured model call says the model was sent."""
    manifest = read_json_blob(store, decision.request_blob)
    return [read_json_blob(store, sha) for sha in manifest["messages"]]


def test_each_model_call_is_captured_exactly_as_the_model_was_sent_it(learning_dir, monkeypatch):
    model = ScriptedModel(responses=[
        asks(call("read_note", "c1", name="home"), call("read_note", "c2", name="work")),  # a parallel batch
        says("Both notes say: water the plants."),
    ], seen=[])
    from row_bot.providers.usage_ledger import attach_usage_ledger

    attach_usage_ledger(model, provider_id="openai", model_name="gpt-4.1")
    turns = AgentTurns(monkeypatch, model, [read_note])

    events = turns.send("What do my notes say?")

    assert ("done", "Both notes say: water the plants.") in events
    store = _written(learning_dir)
    with closing(sqlite3.connect(learning_dir / "usage.db")) as connection:
        ledger = sorted(row[0] for row in connection.execute("SELECT call_id FROM model_usage"))
    [episode] = store.find("episode")
    calls = _decisions(store, subkind="model_call")
    tools = _decisions(store, subkind="tool_call")
    assert len(calls) == len(model.seen) == 2
    for decision, sent in zip(calls, model.seen):
        # The request after trim and compaction: the prepared prompt the provider got, not the
        # conversation as saved.
        assert _sent(store, decision) == [message_to_dict(message) for message in sent]
        assert sent[0].type == "system"
        assert decision.fidelity == "exact" and decision.executed == "raw" and decision.status == "ok"
        expected_tools = hashlib.sha256(agent.json.dumps(
            turns.canonical_tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
        ).encode("utf-8")).hexdigest()
        assert decision.tool_schema_hash == expected_tools
        assert decision.model_ref == turns.model_ref and decision.ledger_call_id
    # Each call's id is the usage ledger's own call id for it.
    assert sorted(item.ledger_call_id for item in calls) == ledger
    assert {item.tool_call_id for item in tools} == {"c1", "c2"}
    assert {item.tool_name for item in tools} == {"read_note"}
    results = {item.tool_call_id: read_json_blob(store, item.result_blob)["data"]["content"] for item in tools}
    assert results == {"c1": "The home note says: water the plants.", "c2": "The work note says: water the plants."}
    assert read_json_blob(store, tools[0].arguments_blob)["args"] in ({"name": "home"}, {"name": "work"})
    assert (episode.status, episode.source, episode.conversation_id) == ("completed", "my_chat", turns.thread_id)
    assert {item.episode_id for item in calls + tools} == {episode.id}

    # Deleting the conversation purges everything Learning captured from it.
    assert thread_cleanup.delete_thread(turns.thread_id).deleted is True
    assert store.owned_by(turns.thread_id) == [] and store.find("decision") == [] and store.find("episode") == []


def test_the_persons_next_message_follows_the_last_reply(learning_dir, monkeypatch):
    model = ScriptedModel(responses=[says("For when?"), says("Booked for lunch.")], seen=[])
    turns = AgentTurns(monkeypatch, model, [])
    turns.send("Book a table")
    turns.send("No, I meant lunch")
    store = _written(learning_dir)
    first, second = sorted(store.find("episode"), key=lambda item: item.started_at)
    [first_reply] = _decisions(store, thread_id=turns.thread_id)[:1]
    assert first.follows_decision_id is None and second.follows_decision_id == first_reply.id
    assert store.find("signal") == []  # a marker, not a correction


def test_an_overflow_is_compacted_retried_and_both_calls_are_captured(learning_dir, monkeypatch):
    """The Agent's model call over a long conversation: the provider refuses the request as too long,
    it is compacted and sent once more. (Driven at the model call, as the compaction regression tests
    are: a graph's background checkpoint write can race the compaction's own check.)"""
    from tests.helpers.agent_requests import AgentConversation

    # This conversation lives in memory: one checkpoint revision stands for it (as in the
    # compaction regression tests), so the summary is saved against it.
    monkeypatch.setattr(threads, "get_latest_checkpoint_revision", lambda thread_id: "rev-1")
    monkeypatch.setattr(threads, "_latest_checkpoint_revision_conn", lambda conn, thread_id: "rev-1")
    monkeypatch.setattr(agent, "_COMPACTION_FAILURES", {})
    conversation = AgentConversation(agent, monkeypatch, context_window=200_000)
    for number in range(1, 5):
        conversation.say(f"Question {number}: " + "detail " * 800)
        conversation.request()
        conversation.answer(f"Answer {number}.")
    conversation.say("Question 5: and the totals?")
    summary = "\n".join(("## Current Goal", "Report the totals.", "## Constraints and Decisions", "None.",
                         "## Completed Work", "The earlier questions were answered.", "## Current State",
                         "Working.", "## Relevant Details", "None.", "## Next Step", "Give the totals."))
    monkeypatch.setattr(agent, "_invoke_compaction_model",
                        lambda _inputs, *, prior_summary, transcript, output_tokens: summary)
    model = ScriptedModel(responses=[says("The totals are 42.")], seen=[], overflows=1)

    answer = model_call_in_turn(conversation, model)

    assert answer.content == "The totals are 42."
    store = _written(learning_dir)
    refused, retried = _decisions(store, subkind="model_call")
    assert refused.status == "error" and refused.raw_output_blob is None
    assert read_json_blob(store, refused.request_blob)["error"] == "ValueError"
    assert retried.retry_of == refused.id and retried.status == "ok"
    # Each captured request is the one the model got: the full one, then the compacted retry.
    assert _sent(store, refused) == [message_to_dict(message) for message in model.seen[0]]
    assert _sent(store, retried) == [message_to_dict(message) for message in model.seen[1]]
    marker = "The earlier questions were answered."  # a line of the summary
    assert marker not in str(_sent(store, refused)) and marker in str(_sent(store, retried))
    [episode] = store.find("episode")
    assert episode.status == "completed" and episode.thread_id == conversation.thread_id


def test_an_approval_given_and_one_refused_are_captured_with_who_decided(learning_dir, monkeypatch):
    model = ScriptedModel(responses=[
        asks(call("delete_file", "d1", path="old.txt")), says("Deleted old.txt."),
        asks(call("delete_file", "d2", path="notes.txt")), says("I left notes.txt alone."),
    ], seen=[])
    turns = AgentTurns(monkeypatch, model, [delete_file])

    first = turns.send("Delete old.txt")
    assert [kind for kind, _ in first if kind == "interrupt"]
    turns.answer(True, first)
    second = turns.send("Delete notes.txt")
    turns.answer(False, second)

    store = _written(learning_dir)
    approvals = _decisions(store, subkind="approval")
    assert [(item.approval_level, item.decided_by, item.approval_outcome) for item in approvals] == [
        ("ask", "person", "approved"), ("ask", "person", "denied"),
    ]
    assert [item.tool_call_id for item in approvals] == ["d1", "d2"]
    [refused] = store.find("signal")
    assert (refused.kind, refused.decision_id) == ("approval_denied", approvals[1].id)
    episodes = sorted(store.find("episode"), key=lambda item: item.started_at)
    assert len(episodes) == 2 and {item.status for item in episodes} == {"completed"}
    # Each turn is one episode across its pause.
    assert {item.episode_id for item in _decisions(store) if item.tool_call_id == "d1"} == {episodes[0].id}
    results = {item.tool_call_id: read_json_blob(store, item.result_blob)["data"]["content"]
               for item in _decisions(store, subkind="tool_call")}
    assert "Deleted old.txt." in results["d1"] and "Deleted" not in results["d2"]


def test_the_persons_stop_ends_the_episode_and_is_a_signal(learning_dir, monkeypatch):
    from row_bot.runtime.executions import generation_registry

    running: dict = {}

    @tool
    def slow_search(query: str) -> str:
        """Search for flights."""
        generation_registry.cancel(running["handle"], reason="user")  # the person presses Stop meanwhile
        return "partial results"

    model = ScriptedModel(responses=[asks(call("slow_search", "s1", query="flights")), says("unused")], seen=[])
    turns = AgentTurns(monkeypatch, model, [slow_search])
    handle = generation_registry.register(turns.thread_id, generation_id=f"stop-{uuid.uuid4().hex[:6]}")
    running["handle"] = handle
    with generation_registry.ownership(handle, check_entry=False):
        turns.send("Find flights", stop_event=handle.cancel_scope.stop_event)

    store = _written(learning_dir)
    [episode] = store.find("episode")
    [stop] = store.find("signal")
    assert episode.status == "stopped"
    assert (stop.kind, stop.episode_id) == ("stop", episode.id)
    assert len(model.seen) == 1  # the model was not called again after Stop


def test_retry_in_place_is_a_signal_on_the_reply_it_replaced(learning_dir, monkeypatch):
    model = ScriptedModel(responses=[says("Your apps are broken."), says("Your apps are fine.")], seen=[])
    turns = AgentTurns(monkeypatch, model, [])
    turns.send("Check my apps")
    store = _written(learning_dir)
    [reply] = _decisions(store, subkind="model_call")

    # Retry, as the platform admits it: the last turn is replaced in the same write.
    assert threads.append_checkpoint_messages(
        turns.thread_id, [HumanMessage(content="Check my apps", id=f"retry-{uuid.uuid4().hex[:6]}")],
        retry_text="Check my apps")

    store = _written(learning_dir)
    [signal] = store.find("signal")
    assert (signal.kind, signal.episode_id, signal.decision_id) == ("retry", reply.episode_id, reply.id)


def test_a_delegated_agents_turn_belongs_to_the_conversation_that_started_it(learning_dir, monkeypatch):
    parent_model = ScriptedModel(responses=[says("I asked a helper to look.")], seen=[])
    parent = AgentTurns(monkeypatch, parent_model, [])
    parent.send("Research the weather in Leeds")

    child_id = f"child-{uuid.uuid4().hex[:10]}"
    child = AgentTurns(monkeypatch, ScriptedModel(responses=[says("Leeds: mild and dry.")], seen=[]), [],
                       thread_id=child_id)
    threads._set_thread_type(child_id, "agent_child")  # as the Agent runner creates its thread
    run = agent_runs.create_agent_run(thread_id=child_id, parent_thread_id=parent.thread_id, status="running")
    agent.invoke_agent("Find the weather in Leeds", [], {"configurable": {
        "thread_id": child_id, "runtime_surface": "agent_child", "runtime_mode": "agent",
        "agent_run_id": run["id"], "approval_mode": "approve", "model_override": child.model_ref,
    }})

    store = _written(learning_dir)
    episodes = {item.thread_id: item for item in store.find("episode")}
    assert episodes[child_id].conversation_id == parent.thread_id
    assert episodes[child_id].parent_episode_id == episodes[parent.thread_id].id
    assert episodes[child_id].root_episode_id == episodes[parent.thread_id].id
    assert episodes[child_id].agent_run_id == run["id"]
    assert {item.conversation_id for item in store.find("decision")} == {parent.thread_id}

    # Deleting the conversation that started it purges the helper's records too.
    agent_runs.finish_agent_run(run["id"], "completed")
    assert thread_cleanup.delete_thread(parent.thread_id).deleted is True
    assert store.find("episode") == [] and store.find("decision") == []


class _StreamingModel(BaseChatModel):
    """A provider that streams its answer and reports usage; keeps what it was sent."""

    received: list = []

    @property
    def _llm_type(self) -> str:
        return "learning-fixture"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("Chat Only streams")

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        yield ChatGenerationChunk(message=AIMessageChunk(content="Why did the plant "))
        yield ChatGenerationChunk(message=AIMessageChunk(content="go to school?", usage_metadata={
            "input_tokens": 40, "output_tokens": 9, "total_tokens": 49}))


def test_a_chat_only_turn_is_captured_with_its_usage_ledger_call(learning_dir, monkeypatch):
    from row_bot.providers import readiness
    from row_bot.providers.models import TransportMode
    from row_bot.providers.readiness import ChatReadinessResult
    from row_bot.providers.usage_ledger import attach_usage_ledger

    turns = AgentTurns(monkeypatch, ScriptedModel(responses=[], seen=[]), [])
    model = attach_usage_ledger(_StreamingModel(received=[]), provider_id="openai", model_name="gpt-4.1")
    monkeypatch.setattr(readiness, "evaluate_chat_readiness", lambda label: ChatReadinessResult(
        ready=True, provider_id="openai", model_id="gpt-4.1", runtime_model="gpt-4.1",
        selection_ref=turns.model_ref, transport=TransportMode.OPENAI_CHAT, context_window=200_000,
        credential_status="configured"))
    monkeypatch.setattr(agent, "_chat_only_llm", lambda label: model)

    events = list(agent.stream_chat_only("Tell me a joke", {"configurable": {
        "thread_id": turns.thread_id, "generation_id": "chat-only-1", "runtime_surface": "normal_chat",
        "model_override": turns.model_ref}}))

    assert ("done", "Why did the plant go to school?") in events
    store = _written(learning_dir)
    [decision] = _decisions(store)
    [episode] = store.find("episode")
    assert episode.status == "completed" and decision.episode_id == episode.id
    assert read_json_blob(store, decision.request_blob)["purpose"] == "chat_only"
    assert _sent(store, decision) == [message_to_dict(message) for message in model.received[0]]
    assert read_json_blob(store, decision.raw_output_blob)["data"]["content"] == "Why did the plant go to school?"
    with closing(sqlite3.connect(learning_dir / "usage.db")) as connection:
        ledger = [row[0] for row in connection.execute("SELECT call_id FROM model_usage WHERE purpose = 'chat_only'")]
    assert ledger == [decision.ledger_call_id]


def test_under_auto_the_reviewer_decides_from_the_persons_words_never_learnings(learning_dir, monkeypatch):
    """E8 through a real turn: Learning has recorded a tool result and a reply; the reviewer the
    gate consults under Auto is sent neither, and Learning records the reviewer's decision."""
    from row_bot import approval_review

    canary = "CANARY-note-contents-4d2e"

    @tool
    def read_secret_note() -> str:
        """Read the person's note."""
        return canary

    model = ScriptedModel(responses=[
        asks(call("read_secret_note", "n1")), says(f"The note says {canary}."),
        asks(call("delete_file", "d1", path="old.txt")), says("Deleted old.txt."),
    ], seen=[])
    turns = AgentTurns(monkeypatch, model, [read_secret_note, delete_file], approval_mode="auto")
    reviewed: list = []

    def reviewer(messages, runtime):
        reviewed.append(messages)
        return '{"decision": "approve", "reason": "You asked to delete old.txt."}'

    monkeypatch.setattr(approval_review, "_call_reviewer", reviewer)
    monkeypatch.setattr(approval_review, "_model_ref", lambda runtime: "model:openai:gpt-4.1")
    turns.send("What does my note say?")
    assert capture.flush(10)
    events = turns.send("Now delete old.txt")

    assert ("done", "Deleted old.txt.") in events and len(reviewed) == 1
    assert canary not in str(reviewed) and "Now delete old.txt" in str(reviewed)
    store = _written(learning_dir)
    assert canary in "".join((store.read_blob(item.result_blob) or b"").decode()
                             for item in _decisions(store, subkind="tool_call"))
    [approval] = _decisions(store, subkind="approval")
    assert (approval.approval_level, approval.decided_by, approval.approval_outcome) == ("auto", "reviewer", "approved")
    assert read_json_blob(store, approval.result_blob)["reviewer_reason"] == "You asked to delete old.txt"
