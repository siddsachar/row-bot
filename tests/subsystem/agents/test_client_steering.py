"""Public steering receipts come from the existing parent's actual input batch."""
from __future__ import annotations

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import importlib
import json

import pytest

from tests.subsystem.agents.test_agent_orchestration import _fresh_modules, _orchestration, _run


pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    _tasks, runs, orchestrator = _fresh_modules(tmp_path, monkeypatch)
    from row_bot.projection import conversation

    projection = conversation.ConversationProjection("steering-test-epoch")
    monkeypatch.setattr(conversation, "conversation_projection", projection)
    monkeypatch.setattr(orchestrator, "_schedule_parent_runner", lambda _: None)
    orchestration = _orchestration(orchestrator, version=2)
    child = _run(runs, "child-active")
    orchestrator.register_member(orchestration["id"], child["id"], required=True)
    orchestrator.set_test_executors(delivery=lambda *_: True)
    orchestrator.complete_parent_pass(orchestration["id"], "Initial delegated work", foreground=True)
    return orchestrator, orchestration, projection


def enqueue(orchestrator, index, *, thread="parent-thread", text="repeat A"):
    return orchestrator.route_parent_steering(parent_thread_id=thread,
        incoming_generation_id=f"steering-{index}", content=text)


def receipts(projection, kind):
    return [event["payload"] for event in projection.events_since("parent-thread", "0")["events"]
            if event["type"] == kind]


def test_seven_messages_acknowledge_only_actual_consumed_batch(owner):
    orchestrator, row, projection = owner
    for index, text in enumerate(["A", "B", "A", "four", "five"]):
        assert enqueue(orchestrator, index, text=text)
    orchestrator.record_thread_event(row["id"], kind="child_terminal", content="private child body",
                                    source_event_id="child-secret", payload={"private": "secret"}, request_wake=False)
    calls = []

    def execute(_row, _context, _tools, config):
        messages = config["configurable"]["thread_event_messages"]
        humans = [message for message in messages if message["role"] == "human"]
        calls.append(humans)
        if len(calls) == 1:
            enqueue(orchestrator, 5, text="six")
            enqueue(orchestrator, 6, text="seven")
        return "Applied the captured guidance while the child remains active."

    orchestrator.set_test_executors(parent=execute, delivery=lambda *_: True)
    orchestrator._run_parent_thread(row["id"])
    view = orchestrator.read_parent_steering("parent-thread")
    assert [item.text for item in view.items] == ["A", "B", "A", "four", "five", "six", "seven"]
    assert [item.state for item in view.items] == ["consumed"] * 5 + ["queued"] * 2
    assert all(not item.editable for item in view.items)
    assert receipts(projection, "steering.consumed") == [{
        "generation_id": "generation-1", "steering_ids": [f"steering-{index}" for index in range(5)]}]
    assert "secret" not in json.dumps(asdict(view))
    assert "private" not in json.dumps(receipts(projection, "steering.consumed"))

    # Idempotent response-loss retry cannot turn a consumed item back into queued.
    enqueue(orchestrator, 0, text="replacement text must not overwrite accepted input")
    assert len(receipts(projection, "steering.queued")) == 7
    orchestrator._run_parent_thread(row["id"])
    assert len(calls) == 2 and [len(batch) for batch in calls] == [5, 2]
    assert [item.state for item in orchestrator.read_parent_steering("parent-thread").items] == ["consumed"] * 7
    assert receipts(projection, "steering.consumed")[-1]["steering_ids"] == ["steering-5", "steering-6"]


def test_failed_parent_invocation_leaves_guidance_queued(owner):
    orchestrator, row, projection = owner
    enqueue(orchestrator, 1)

    def failing(*_):
        raise RuntimeError("synthetic provider failure")

    orchestrator.set_test_executors(parent=failing, delivery=lambda *_: True)
    orchestrator._run_parent_thread(row["id"])
    assert orchestrator.read_parent_steering("parent-thread").items[0].state == "queued"
    assert receipts(projection, "steering.consumed") == []


def test_receipt_is_idempotent_and_scoped_to_parent_orchestration(owner):
    orchestrator, row, projection = owner
    enqueue(orchestrator, 1)
    own = orchestrator.pending_thread_events(row["id"])[0]
    other = orchestrator.create_or_get_orchestration(parent_thread_id="other-thread",
        parent_generation_id="other-generation", root_objective="other", model_ref="provider:model",
        approval_mode="block", runtime_surface="normal_chat", orchestration_version=2)
    foreign = orchestrator.record_thread_event(other["id"], kind="parent_steering", content="other private input",
        source_event_id="steering:foreign", request_wake=False)
    orchestrator.complete_parent_pass(row["id"], "Progress", foreground=True,
                                     consumed_event_ids=[own.id, foreign.id, own.id])
    orchestrator.complete_parent_pass(row["id"], "Progress", foreground=True,
                                     consumed_event_ids=[own.id])
    assert receipts(projection, "steering.consumed") == [{"generation_id": "generation-1", "steering_ids": ["steering-1"]}]
    assert orchestrator.read_parent_steering("other-thread", generation_id="other-generation").items[0].state == "queued"
    assert projection.events_since("other-thread", "0")["events"] == []


def test_pages_are_complete_ordered_and_scoped_across_restart(owner):
    orchestrator, row, _projection = owner
    for index in range(107):
        orchestrator.record_thread_event(row["id"], kind="parent_steering", content=f"item {index}",
            source_event_id=f"steering:id-{index}", request_wake=False)
    first = orchestrator.read_parent_steering("parent-thread", limit=50)
    assert len(first.items) == 50 and first.has_more
    orchestrator = importlib.reload(orchestrator)
    second = orchestrator.read_parent_steering("parent-thread", generation_id="generation-1",
                                              cursor=first.next_cursor, limit=50)
    final = orchestrator.read_parent_steering("parent-thread", cursor=second.next_cursor, limit=50)
    assert [item.id for item in (*first.items, *second.items, *final.items)] == [f"id-{index}" for index in range(107)]
    assert final.next_cursor is None and not final.has_more
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.read_parent_steering("wrong-thread", cursor=first.next_cursor)
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.read_parent_steering("parent-thread", generation_id="wrong-generation", cursor=first.next_cursor)


@pytest.mark.parametrize("cursor", ["bad!", "e30=", "W10=", "WzEsLTJd"])
def test_invalid_cursor_fails_without_selecting_other_history(owner, cursor):
    orchestrator, _row, _projection = owner
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.read_parent_steering("parent-thread", cursor=cursor)


def test_empty_conversation_never_reads_another_threads_latest_queue(owner):
    orchestrator, _row, _projection = owner
    enqueue(orchestrator, 1)
    with pytest.raises(orchestrator.OrchestrationError):
        orchestrator.read_parent_steering("")


def test_concurrent_receipt_retries_publish_only_once(owner):
    orchestrator, row, projection = owner
    enqueue(orchestrator, 1)
    event_id = orchestrator.pending_thread_events(row["id"])[0].id
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _: orchestrator._mark_events_consumed(row["id"], [event_id]), range(8)))
    assert receipts(projection, "steering.consumed") == [{"generation_id": "generation-1", "steering_ids": ["steering-1"]}]


def test_unavailable_observer_does_not_undo_durable_consumption(owner, monkeypatch):
    orchestrator, row, projection = owner
    enqueue(orchestrator, 1)
    event_id = orchestrator.pending_thread_events(row["id"])[0].id

    def unavailable(*_):
        raise RuntimeError("synthetic observer unavailable")

    monkeypatch.setattr(projection, "publish", unavailable)
    orchestrator.complete_parent_pass(row["id"], "Applied", foreground=True, consumed_event_ids=[event_id])
    assert orchestrator.read_parent_steering("parent-thread").items[0].state == "consumed"
