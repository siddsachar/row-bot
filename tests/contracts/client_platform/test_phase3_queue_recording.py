"""F-P11 records Phase 3 queue events from authenticated routes and real owners."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from langchain_core.messages import AIMessage
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform as platform
from tests.contracts.client_platform.test_protocol_boundaries import client as client, protocol_clock as protocol_clock
from tests.contracts.client_platform.test_recordings import post_command
from tests.helpers.client_platform_fakes import CheckpointCommit, RecordedProtocolTrace, ScriptedAgentStream, StreamBarrier, fixture_id

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.contract


class QueueTrace(RecordedProtocolTrace):
    def normalize(self, value, *, key=""):
        normalized = super().normalize(value, key=key)
        if isinstance(normalized, str):
            # Existing orchestration IDs are short opaque identities embedded
            # in native event references; preserve their equality with the DTO.
            return re.sub(r"(?<=orchestration:)[0-9a-f]{12}(?=:)", lambda match: self._identity(match.group()), normalized)
        return normalized


def test_record_f_p11_ordinary_and_parent_queue_receipts(platform, client, monkeypatch):
    from row_bot import agent_orchestrator, agent_runs
    from row_bot.application.client_platform import _COMMAND_LOCK
    trace = QueueTrace("F-P11")
    subscriptions = {}
    for conversation in ("conversation-a", "conversation-b"):
        response = client.post(f"/api/v1/conversations/{conversation}/subscriptions")
        assert response.status_code == 200, response.text
        subscriptions[conversation] = response.json()
    def wire_events(conversation):
        subscription = subscriptions[conversation]
        response = client.get("/api/v1/events/poll", params={"subscription_id": subscription["subscription_id"], "cursor": subscription["cursor"]})
        assert response.status_code == 200, response.text
        assert not response.json()["snapshot_required"]
        return [item["event"] for item in response.json()["events"]]
    barrier, pending = StreamBarrier(), StreamBarrier()
    def script(label, gate):
        native = fixture_id(label + ":native")
        return (gate, CheckpointCommit((AIMessage(content="Synthetic answer", id=native),), native), ("done", "Synthetic answer"))
    fake = ScriptedAgentStream(script("p11-initial", barrier), script("p11-pending", pending))
    platform.stream_factory = fake.stream
    initial = post_command(client, trace, "conversation.submit", "p11-initial", {
        "submission_id": fixture_id("p11-initial-input"), "text": "Synthetic first input", "attachment_refs": [],
        "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}})
    assert initial.status_code == 202, initial.text
    assert barrier.entered.wait(10)
    try:
        queued = post_command(client, trace, "conversation.steer", "p11-ordinary-queued", {
            "steering_id": fixture_id("p11-pending-input"), "text": "Synthetic queued input"})
        assert queued.status_code == 202, queued.text
        def ordinary_view():
            response = client.get("/api/v1/conversations/conversation-a/queue", params={"generation_id": initial.json()["generation_id"]})
            assert response.status_code == 200, response.text
            trace.record("ClientQueueView", response.json())
            return response.json()
        assert ordinary_view()["items"][0]["state"] == "queued"
        barrier.release.set()
        assert pending.entered.wait(10)
        handle = platform.registry.active("conversation-a")[0]
        assert ordinary_view()["items"][0]["state"] == "dispatching"
        pending.release.set()
        assert handle.producer_done.wait(10)
        with _COMMAND_LOCK:
            assert ordinary_view()["items"][0]["state"] == "consumed"
        ordinary = [event for event in wire_events("conversation-a") if event["type"] == "queue.changed"]
        assert len(ordinary) == 3
        for event in ordinary:
            trace.record("Event", event)
        assert fake.external_call_count == 0 and len(fake.calls) == 2
    finally:
        platform.registry.stop("conversation-a")
        barrier.release.set()
        pending.release.set()
        for handle in platform.registry.active("conversation-a"):
            assert handle.producer_done.wait(10)

    agent_runs.ensure_agent_run_schema(force=True)
    monkeypatch.setattr(agent_orchestrator, "_schedule_parent_runner", lambda _: None)
    orchestration = agent_orchestrator.create_or_get_orchestration(
        parent_thread_id="conversation-b", parent_generation_id=fixture_id("p11-parent-generation"),
        root_objective="Synthetic delegated task", model_ref="fixture/model", approval_mode="block",
        runtime_surface="normal_chat", orchestration_version=2)
    child = agent_runs.create_agent_run(run_id=fixture_id("p11-child"), status="running",
        parent_thread_id="conversation-b", thread_id="synthetic-child-thread", prompt="Synthetic child objective",
        display_name="Synthetic child", model_override="fixture/model")
    agent_orchestrator.register_member(orchestration["id"], child["id"], required=True)
    parent_calls = []
    def parent_executor(_row, _context, _tools, config):
        parent_calls.append([item for item in config["configurable"]["thread_event_messages"] if item["role"] == "human"])
        return "Applied the synthetic guidance."
    agent_orchestrator.set_test_executors(parent=parent_executor, delivery=lambda *_: True)
    try:
        agent_orchestrator.complete_parent_pass(orchestration["id"], "Initial delegation", foreground=True)
        for index, text in enumerate(["Synthetic A", "Synthetic B", "Synthetic A"]):
            response = post_command(client, trace, "conversation.steer", f"p11-parent-{index}", {
                "steering_id": fixture_id(f"p11-parent-{index}"), "text": text}, target="conversation-b")
            assert response.status_code == 202, response.text
        def parent_view():
            response = client.get("/api/v1/conversations/conversation-b/steering", params={"generation_id": orchestration["parent_generation_id"]})
            assert response.status_code == 200, response.text
            trace.record("ParentSteeringView", response.json())
            return response.json()
        assert [item["state"] for item in parent_view()["items"]] == ["queued"] * 3
        agent_orchestrator._run_parent_thread(orchestration["id"])
        assert [item["state"] for item in parent_view()["items"]] == ["consumed"] * 3
        assert len(parent_calls) == 1 and len(parent_calls[0]) == 3
        events = [event for event in wire_events("conversation-b") if event["type"].startswith("steering.")]
        assert [event["type"] for event in events] == ["steering.queued"] * 3 + ["steering.consumed"]
        for event in events:
            trace.record("Event", event)
    finally:
        agent_orchestrator.set_test_executors()
    trace.barriers = ["ordinary producer active before enqueue", "pending input admitted before fake provider output", "parent batch captured by actual orchestration consumer"]
    trace.assertions = ["Ordinary queued identity survives dispatch and becomes consumed only at effect proof", "Parent A/B/A inputs retain three exact identities", "Only actual parent consumer acknowledges its captured batch", "All calls use synthetic local stores and fake external execution"]
    document = trace.document(ROOT)
    document["source"]["base_commit"] = "5c0f2217074d5ca940d8443d8c05a83329bc9dc1"
    for relative in ("src/row_bot/agent_orchestrator.py", "src/row_bot/agent.py", "tests/contracts/client_platform/test_phase3_queue_recording.py"):
        document["source"]["files"][relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    document["source"]["sha256"] = hashlib.sha256(json.dumps(document["source"]["files"], sort_keys=True).encode()).hexdigest()
    trace.validate(ROOT, document)
    if os.environ.get("ROW_BOT_RECORD_PROTOCOL_FIXTURES") == "1":
        trace.write(ROOT, document)
    saved = json.loads((ROOT / "contracts/client-platform/v1/fixtures/F-P11.json").read_text(encoding="utf-8"))
    trace.validate(ROOT, saved)
    for index, (old, current) in enumerate(zip(saved["records"], document["records"], strict=True)):
        assert old == current, f"record {index}: {old!r} != {current!r}"
    assert {key: value for key, value in saved.items() if key not in {"source", "records"}} == {key: value for key, value in document.items() if key not in {"source", "records"}}
