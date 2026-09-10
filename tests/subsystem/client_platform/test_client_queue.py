"""Ordinary queued inputs use real checkpoint/admission owners and fake effects."""
from __future__ import annotations

from contextlib import closing

from dataclasses import asdict
import json
import sqlite3
import threading

from langchain_core.messages import AIMessage
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform, command, submit
from tests.contracts.client_platform.test_protocol_boundaries import client as client, protocol_clock as protocol_clock
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id
from row_bot.application import client_queue
from row_bot.runtime import admissions

pytestmark = pytest.mark.subsystem


def execute(service, kind, label, payload):
    return service.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                           target="conversation-a", command=command(kind, label, payload))


def enqueue(service, label, text="Same A"):
    return execute(service, "conversation.steer", label,
                   {"steering_id": fixture_id(label), "text": text})


def completed(label, *before):
    native_id = fixture_id(label + ":assistant")
    return (*before, CheckpointCommit((AIMessage(content=label, id=native_id),), native_id), ("done", label))


def settle(service):
    # Finalization owns this lock through the next admission; this observes the
    # complete handoff, rather than assuming producer_done implies a drain.
    from row_bot.application.client_platform import _COMMAND_LOCK
    with _COMMAND_LOCK:
        return client_queue.read_queue(service, "conversation-a")


def test_seven_exact_inputs_drain_in_order_without_entering_current_prompt(platform, monkeypatch):
    from row_bot import threads
    barriers = [StreamBarrier() for _ in range(8)]
    fake = ScriptedAgentStream(*(completed(str(i), barrier) for i, barrier in enumerate(barriers)))
    receipt = submit(platform, fake, "initial-seven")
    assert barriers[0].entered.wait(10)
    try:
        receipts = [enqueue(platform, f"queued-{i}", ["Same A", "B", "Same A"][i % 3]) for i in range(7)]
        ids = [item["submission_id"] for item in receipts]
        assert set(ids).isdisjoint(message.id for message in threads.get_latest_checkpoint_messages("conversation-a"))
        _, staged = client_queue._staged("conversation-a")
        assert [message.id for message in staged["messages"]] == ids
        with admissions.transaction() as conn:
            records = [dict(row) for row in conn.execute("SELECT * FROM generation_passes WHERE queue_state!=''")]
        assert "Same A" not in json.dumps(records) and "text" not in records[0]
        view = settle(platform)
        assert [item.id for item in view.items] == ids
        assert all(item.state == "queued" and item.editable for item in view.items)
        for i in range(7):
            barriers[i].release.set()
            assert barriers[i + 1].entered.wait(10)
            view = settle(platform)
            assert [item.state for item in view.items] == ["consumed"] * i + ["dispatching"] + ["queued"] * (6 - i)
        final = platform.registry.active("conversation-a")[0]
        barriers[-1].release.set()
        assert final.producer_done.wait(10)
        view = settle(platform)
        assert all(item.state == "consumed" and not item.editable for item in view.items)
        assert [call["submission_id"] for call in fake.calls[1:]] == ids
        assert [item.text for item in view.items] == [["Same A", "B", "Same A"][i % 3] for i in range(7)]
        assert client_queue._staged("conversation-a")[1]["messages"] == []
        assert fake.external_call_count == 0
    finally:
        platform.registry.stop("conversation-a")
        for barrier in barriers:
            barrier.release.set()


def test_edit_remove_exact_revision_and_response_loss(platform):
    from row_bot.application.client_platform import ClientPlatformError
    barrier = StreamBarrier()
    receipt = submit(platform, ScriptedAgentStream((barrier,)), "edit-initial")
    assert barrier.entered.wait(10)
    try:
        first = enqueue(platform, "edit-one")
        assert enqueue(platform, "edit-one") == first
        second = enqueue(platform, "remove-two")
        edited = execute(platform, "conversation.queue.edit", "edit-command", {
            "submission_id": first["submission_id"], "expected_queue_revision": "0", "text": "Changed A"})
        assert edited["revision"] == "1"
        with pytest.raises(ClientPlatformError, match="queue_revision_conflict"):
            execute(platform, "conversation.queue.remove", "stale-edit", {
                "submission_id": first["submission_id"], "expected_queue_revision": "0"})
        execute(platform, "conversation.queue.remove", "remove-command", {
            "submission_id": second["submission_id"], "expected_queue_revision": "0"})
        view = settle(platform)
        assert [(item.text, item.state) for item in view.items] == [("Changed A", "queued"), ("", "cancelled")]
        assert [message.id for message in client_queue._staged("conversation-a")[1]["messages"]] == [first["submission_id"]]
    finally:
        platform.registry.stop("conversation-a")
        barrier.release.set()
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)


def test_stop_pauses_unconsumed_and_explicit_dispatch_uses_frozen_controls(platform):
    from row_bot import threads
    barrier, next_barrier = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream((barrier,), completed("explicit-next", next_barrier))
    captured = []
    original = fake.stream
    def stream(text, enabled, config, **kwargs):
        captured.append((text, dict(config["configurable"])))
        yield from original(text, enabled, config, **kwargs)
    receipt = submit(platform, fake, "stop-initial")
    platform.stream_factory = stream
    assert barrier.entered.wait(10)
    queued = enqueue(platform, "stop-pending", "Frozen input")
    execute(platform, "conversation.stop", "stop-command", {})
    barrier.release.set()
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    view = settle(platform)
    assert view.items[0].state == "paused" and len(fake.calls) == 1
    _, staged = client_queue._staged("conversation-a")
    frozen = staged["messages"][0].additional_kwargs["client_queue"]["context"]["configurable"]
    with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
        conn.execute("UPDATE thread_meta SET client_runtime_mode='chat_only',agent_profile_id='changed-panel' WHERE thread_id='conversation-a'")
    result = execute(platform, "conversation.queue.dispatch", "explicit-dispatch", {
        "submission_id": queued["submission_id"], "expected_queue_revision": view.items[0].revision})
    try:
        assert next_barrier.entered.wait(10)
        assert captured[-1][0] == "Frozen input"
        assert all(captured[-1][1][key] == frozen[key] for key in ("model_override", "approval_mode", "agent_profile_id", "runtime_mode"))
    finally:
        next_barrier.release.set()
        assert platform.registry.get(result["execution_id"]).producer_done.wait(10)
    assert settle(platform).items[0].state == "consumed"


def test_done_without_effect_proof_never_consumes(platform):
    initial, queued_barrier = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream(completed("first", initial), (queued_barrier, ("done", "No provider output")))
    receipt = submit(platform, fake, "done-initial")
    assert initial.entered.wait(10)
    enqueue(platform, "done-only")
    initial.release.set()
    assert queued_barrier.entered.wait(10)
    handle = platform.registry.active("conversation-a")[0]
    queued_barrier.release.set()
    assert handle.producer_done.wait(10)
    item = settle(platform).items[0]
    assert item.state == "paused" and not item.editable
    assert client_queue._staged("conversation-a")[1]["messages"][0].id == item.id


def test_restart_retains_text_pauses_and_does_not_replay(platform):
    barrier = StreamBarrier()
    fake = ScriptedAgentStream((barrier,))
    receipt = submit(platform, fake, "restart-initial")
    assert barrier.entered.wait(10)
    queued = enqueue(platform, "restart-queued", "Survive restart")
    # Simulate loss after accepted input staging, before any provider boundary.
    platform.registry.stop("conversation-a")
    barrier.release.set()
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    settle(platform)
    with admissions.transaction() as conn:
        conn.execute("UPDATE generation_passes SET queue_state='queued',queue_epoch='old-process' WHERE submission_id=?", (queued["submission_id"],))
    client_queue.recover_queue(platform.server_epoch)
    view = settle(platform)
    assert [(item.id, item.text, item.state) for item in view.items] == [(queued["submission_id"], "Survive restart", "paused")]
    assert client_queue.dispatch(platform, "conversation-a", automatic=True) is None
    assert len(fake.calls) == 1


def test_unicode_pages_bound_wire_bytes_and_preserve_all_ids(platform):
    barrier = StreamBarrier()
    receipt = submit(platform, ScriptedAgentStream((barrier,)), "pages-initial")
    assert barrier.entered.wait(10)
    try:
        ids = [enqueue(platform, f"page-{i}", "😀" * 16000)["submission_id"] for i in range(5)]
        seen, cursor = [], None
        while True:
            view = client_queue.read_queue(platform, "conversation-a", cursor=cursor)
            assert len(json.dumps(asdict(view)).encode()) <= 256 * 1024
            seen.extend(item.id for item in view.items)
            if not view.has_more:
                break
            assert view.next_cursor != cursor
            cursor = view.next_cursor
        assert seen == ids
        with pytest.raises(admissions.AdmissionError, match="invalid_queue_cursor"):
            client_queue.read_queue(platform, "conversation-b", cursor=cursor)
    finally:
        platform.registry.stop("conversation-a")
        barrier.release.set()
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)


def test_preparation_failure_has_no_receipt_and_prepared_ids_ack_exactly(platform, monkeypatch):
    from types import SimpleNamespace
    from row_bot import agent
    from row_bot.runtime import executions
    from langchain_core.messages import HumanMessage
    barrier = StreamBarrier()
    receipt = submit(platform, ScriptedAgentStream((barrier,)), "hook-initial")
    assert barrier.entered.wait(10)
    one = enqueue(platform, "hook-one")
    two = enqueue(platform, "hook-two")
    handle = platform.registry.get(receipt["execution_id"])
    try:
        # Model hook receives only current prepared input identities. A future
        # staged input and an unrelated invocation never gain a consumed ack.
        with admissions.transaction() as conn:
            conn.execute("UPDATE generation_passes SET state='interrupted',queue_state='paused' WHERE submission_id=?", (one["submission_id"],))
        monkeypatch.setattr(executions, "current_execution", lambda: handle)
        monkeypatch.setattr(agent, "_collect_agent_preparation_inputs", lambda *a, **k: SimpleNamespace(execution_budget=None))
        monkeypatch.setattr(agent, "_prepare_with_compaction", lambda value: (_ for _ in ()).throw(RuntimeError("preparation rejected")))
        with pytest.raises(RuntimeError, match="preparation rejected"):
            agent._pre_model_trim({"messages": []})
        assert settle(platform).items[0].state == "paused"
        prepared = [HumanMessage(content="Same A", id=one["submission_id"])]
        monkeypatch.setattr(agent, "_prepare_with_compaction", lambda value: SimpleNamespace(messages=prepared))
        agent._pre_model_trim({"messages": []})
        assert [item.state for item in settle(platform).items] == ["consumed", "queued"]
        assert [message.id for message in client_queue._staged("conversation-a")[1]["messages"]] == [two["submission_id"]]
    finally:
        platform.registry.stop("conversation-a")
        barrier.release.set()
        assert handle.producer_done.wait(10)


@pytest.mark.parametrize("preparation_fails", [False, True])
def test_chat_only_actual_prepared_boundary_preserves_native_identity(platform, monkeypatch, preparation_fails):
    from row_bot import agent, threads
    from row_bot.providers import readiness
    from row_bot.runtime import executions
    from langchain_core.messages import HumanMessage, AIMessageChunk
    from tests.test_chat_only_runtime import _chat_ready_result
    barrier = StreamBarrier()
    receipt = submit(platform, ScriptedAgentStream((barrier,)), "chat-hook-initial")
    assert barrier.entered.wait(10)
    queued = enqueue(platform, "chat-hook-input", "Native queued input")
    future = enqueue(platform, "chat-hook-future", "Future input")
    handle = platform.registry.get(receipt["execution_id"])
    calls = []
    class Model:
        def stream(self, messages):
            calls.append(messages)
            assert queued["submission_id"] in [message.id for message in messages]
            assert future["submission_id"] not in [message.id for message in messages]
            assert client_queue._row("conversation-a", queued["submission_id"])["queue_state"] == "consumed"
            yield AIMessageChunk(content="Fake model output")
    try:
        threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="Native queued input", id=queued["submission_id"])])
        with admissions.transaction() as conn:
            conn.execute("UPDATE generation_passes SET state='interrupted',queue_state='paused' WHERE submission_id=?", (queued["submission_id"],))
        monkeypatch.setattr(executions, "current_execution", lambda: handle)
        monkeypatch.setattr(readiness, "evaluate_chat_readiness", lambda _: _chat_ready_result())
        monkeypatch.setattr(agent, "_chat_only_llm", lambda _: Model())
        if preparation_fails:
            monkeypatch.setattr(agent, "_prepare_with_compaction", lambda _: (_ for _ in ()).throw(agent.ContextCompactionError("Synthetic preparation failure")))
        events = list(agent.stream_chat_only("", {"configurable": {
            "thread_id": "conversation-a", "model_override": "model:custom_openai_lab:local-chat",
            "platform_submission_id": queued["submission_id"], "generation_id": handle.generation_id}}))
        assert len(calls) == (0 if preparation_fails else 1)
        assert any(kind == ("error" if preparation_fails else "done") for kind, _ in events)
        assert client_queue._row("conversation-a", queued["submission_id"])["queue_state"] == ("paused" if preparation_fails else "consumed")
        assert client_queue._row("conversation-a", future["submission_id"])["queue_state"] == "queued"
    finally:
        platform.registry.stop("conversation-a")
        barrier.release.set()
        assert handle.producer_done.wait(10)


def test_dispatch_and_edit_race_has_one_exact_revision_winner(platform):
    from concurrent.futures import ThreadPoolExecutor
    from row_bot.application.client_platform import ClientPlatformError
    initial, next_barrier = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream((initial,), completed("race-next", next_barrier))
    receipt = submit(platform, fake, "race-initial")
    assert initial.entered.wait(10)
    queued = enqueue(platform, "race-input")
    platform.registry.stop("conversation-a")
    initial.release.set()
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    item = settle(platform).items[0]
    race = threading.Barrier(2)
    def mutate(kind):
        race.wait(timeout=10)
        try:
            return execute(platform, "conversation.queue." + kind, "race-" + kind, {
                "submission_id": queued["submission_id"], "expected_queue_revision": item.revision,
                **({"text": "Racing edit"} if kind == "edit" else {})})
        except ClientPlatformError as exc:
            return exc.code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(mutate, ["edit", "dispatch"]))
        assert sum(isinstance(result, dict) for result in results) == 1
        assert "queue_revision_conflict" in results
        assert len(fake.calls) <= 2
    finally:
        platform.registry.stop("conversation-a")
        next_barrier.release.set()
        for handle in platform.registry.active("conversation-a"):
            assert handle.producer_done.wait(10)


def test_restart_reconciles_staging_commit_and_edit_revision_without_reexecution(platform):
    barrier = StreamBarrier()
    receipt = submit(platform, ScriptedAgentStream((barrier,)), "crash-initial")
    assert barrier.entered.wait(10)
    accepted = enqueue(platform, "crash-accepted", "Retained after commit")
    removed = enqueue(platform, "crash-removed", "Removed input")
    platform.registry.stop("conversation-a")
    barrier.release.set()
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    settle(platform)
    from row_bot import threads
    with threads.checkpoint_mutation("conversation-a"):
        saved, values = client_queue._staged("conversation-a")
        message = values["messages"][0]
        message.additional_kwargs["client_queue"]["revision"] = 8
        message.content = "Retained edited text"
        values["messages"] = [message]
        client_queue._save("conversation-a", saved, values)
    with admissions.transaction() as conn:
        conn.execute("UPDATE generation_passes SET state='queue_preparing',queue_state='preparing',queue_epoch='lost-process' WHERE submission_id=?", (accepted["submission_id"],))
        conn.execute("UPDATE generation_passes SET state='queued',queue_state='queued',queue_epoch='lost-process' WHERE submission_id=?", (removed["submission_id"],))
    client_queue.recover_queue(platform.server_epoch)
    view = settle(platform)
    assert [(item.text, item.state) for item in view.items] == [("Retained edited text", "paused"), ("", "cancelled")]
    assert view.items[0].revision == "9"
    client_queue.recover_queue(platform.server_epoch)
    assert settle(platform) == view


def test_explicit_resume_can_reach_admitted_input_without_sending_future_queue(platform):
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from langchain_core.messages import HumanMessage
    initial, resumed = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream((initial,), completed("resume-output", resumed))
    receipt = submit(platform, fake, "resume-initial")
    assert initial.entered.wait(10)
    accepted = enqueue(platform, "resume-admitted")
    future = enqueue(platform, "resume-future")
    platform.registry.stop("conversation-a")
    initial.release.set()
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    settle(platform)
    threads.append_checkpoint_messages("conversation-a", [HumanMessage(content="Same A", id=accepted["submission_id"])])
    with admissions.transaction() as conn:
        conn.execute("UPDATE generation_passes SET state='interrupted',queue_state='paused' WHERE submission_id=?", (accepted["submission_id"],))
    with pytest.raises(ClientPlatformError, match="queue_pending"):
        execute(platform, "conversation.submit", "cannot-overtake", {"submission_id": fixture_id("overtake"), "text": "Overtake", "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}})
    result = execute(platform, "conversation.resume", "resume-explicit", {"submission_id": fixture_id("resume-command"), "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}})
    try:
        assert resumed.entered.wait(10)
        assert fake.calls[-1]["kind"] == "resume"
        assert future["submission_id"] not in [message.id for message in threads.get_latest_checkpoint_messages("conversation-a")]
        client_queue.acknowledge_consumed(platform.registry.get(result["execution_id"]), [accepted["submission_id"]])
        assert [item.state for item in settle(platform).items] == ["consumed", "paused"]
    finally:
        resumed.release.set()
        assert platform.registry.get(result["execution_id"]).producer_done.wait(10)


def test_snapshot_capacity_rejection_retains_existing_accepted_inputs(platform, monkeypatch):
    from row_bot.application.client_platform import ClientPlatformError
    barrier = StreamBarrier()
    receipt = submit(platform, ScriptedAgentStream((barrier,)), "capacity-initial")
    assert barrier.entered.wait(10)
    try:
        first = enqueue(platform, "capacity-one", "Retained first input")
        monkeypatch.setattr(client_queue, "_MAX_BYTES", 1)
        with pytest.raises(ClientPlatformError, match="queue_capacity"):
            enqueue(platform, "capacity-two", "Rejected second input")
        assert [message.id for message in client_queue._staged("conversation-a")[1]["messages"]] == [first["submission_id"]]
        assert [item.state for item in settle(platform).items] == ["queued", "cancelled"]
    finally:
        platform.registry.stop("conversation-a")
        barrier.release.set()
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)


@pytest.mark.parametrize("change", ["revoke", "revision"])
def test_frozen_resource_cut_blocks_dispatch_when_source_changes(platform, monkeypatch, change):
    from row_bot import conversation_resources as resources
    revision = ["source-1"]
    monkeypatch.setattr(resources, "describe", lambda binding: resources.ResourceDescriptor(binding, "Synthetic resource", revision[0], True))
    bound = resources.bind("conversation-a", "artifact", fixture_id("queue-artifact"), expected_revision="0", role="primary")
    barrier = StreamBarrier()
    fake = ScriptedAgentStream(completed("resource-initial", barrier))
    platform.stream_factory = fake.stream
    receipt = platform._start("conversation-a", {"submission_id": fixture_id("resource-initial"), "text": "Original target", "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}, resume=False)
    assert barrier.entered.wait(10)
    queued = client_queue.enqueue(platform, "conversation-a", fixture_id("resource-queued"), "Use original resource")
    if change == "revoke":
        resources.unbind("conversation-a", bound.bindings[0].binding_id, expected_revision=bound.revision)
    else:
        revision[0] = "source-2"
    barrier.release.set()
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    item = settle(platform).items[0]
    assert item.id == queued["submission_id"] and item.state == "paused" and item.editable
    assert len(fake.calls) == 1


def test_followup_received_during_queued_run_inherits_that_actual_generation(platform):
    initial, first, second = StreamBarrier(), StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream(completed("handoff-initial", initial), completed("handoff-first", first), completed("handoff-second", second))
    submit(platform, fake, "handoff-initial")
    assert initial.entered.wait(10)
    one = enqueue(platform, "handoff-one")
    try:
        initial.release.set()
        assert first.entered.wait(10)
        two = enqueue(platform, "handoff-two")
        assert client_queue._row("conversation-a", two["submission_id"])["queue_source_generation_id"] == one["generation_id"]
        first.release.set()
        assert second.entered.wait(10)
        handle = platform.registry.active("conversation-a")[0]
        second.release.set()
        assert handle.producer_done.wait(10)
        assert [item.state for item in settle(platform).items] == ["consumed", "consumed"]
        assert [call["submission_id"] for call in fake.calls[1:]] == [one["submission_id"], two["submission_id"]]
    finally:
        platform.registry.stop("conversation-a")
        initial.release.set()
        first.release.set()
        second.release.set()


@pytest.mark.parametrize("exception,status,code", [
    (admissions.AdmissionError("queue_revision_conflict"), 409, "queue_revision_conflict"),
    (admissions.AdmissionError("queue_content_unavailable"), 503, "queue_content_unavailable"),
    (admissions.AdmissionError("invalid_queue_text"), 422, "invalid_queue_text"),
    (ValueError("PRIVATE synthetic owner error"), 503, "dependency_unavailable"),
])
def test_queue_api_errors_are_typed_and_never_leak_private_exception(client, monkeypatch, exception, status, code):
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(client_queue, "read_queue", fail)
    response = client.get("/api/v1/conversations/conversation-a/queue")
    assert response.status_code == status
    assert response.json()["code"] == code
    assert "PRIVATE" not in response.text
