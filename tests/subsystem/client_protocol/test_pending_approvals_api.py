"""Every pending approval in one list, answered in place (B255).

Approvals from a workflow, another conversation or a delegated agent used to
show only where they were raised, so work in the background waited unseen.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def _pending(client, headers) -> dict[str, dict]:
    response = client.get("/api/v1/monitor/approvals", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == 1 and body["total"] == len(body["items"])
    return {item["id"]: item for item in body["items"]}


def test_pending_approvals_list_every_source_without_secrets(service, monkeypatch):  # noqa: F811
    from row_bot import agent_runs, tasks, threads

    resumed: list[tuple[str, bool]] = []
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda token, approved=True: resumed.append((token, approved)))
    task_id = tasks.create_task("Daily News", steps=[
        {"id": "review", "type": "approval", "message": "Send today's news?"}], channels=[], enabled=False)
    workflow_token, workflow = tasks.create_approval_request("run-1", task_id, "review", "Send today's news?")
    trip = threads.create_thread("Trip planning", seed_default_skills=False)
    _, conversation = tasks.create_approval_request(
        "pass-1", "", "conversation", "Row-Bot wants to run a command.", resume_kind="conversation",
        source_thread_id=trip, parent_thread_id=trip, approval_payload_json={"args": "private-argument"})
    parent = threads.create_thread("Parent", seed_default_skills=False)
    agent_thread = threads.create_thread("Research", seed_default_skills=False)
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=agent_thread, display_name="Research",
                                      status="waiting_approval", prompt="Synthetic task")
    _, agent = tasks.create_approval_request(
        run["id"], "", "agent_interrupt", "Research wants to search the web.", agent_run_id=run["id"],
        resume_kind="agent_run", source_label="Research", source_thread_id=agent_thread,
        parent_thread_id=parent)
    answered_token, _answered = tasks.create_approval_request("run-2", task_id, "review", "Answered already")
    assert tasks.respond_to_approval(answered_token, False)
    _, expired = tasks.create_approval_request("run-3", task_id, "review", "Too late", timeout_minutes=1)
    conn = tasks._get_conn()
    conn.execute("UPDATE approval_requests SET timeout_at=? WHERE id=?",
                 ((datetime.now() - timedelta(minutes=1)).isoformat(), expired))
    conn.commit()
    conn.close()

    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.get("/api/v1/monitor/approvals", headers=headers)
        items = _pending(client, headers)

        assert workflow_token not in response.text and "private-argument" not in response.text
        assert set(items) == {workflow, conversation, agent}
        assert {key: items[workflow][key] for key in ("source", "title", "what", "task_id", "conversation_id")} == {
            "source": "workflow", "title": "Daily News", "what": "Send today's news?",
            "task_id": task_id, "conversation_id": None}
        assert {key: items[conversation][key] for key in ("source", "title", "conversation_id", "task_id")} == {
            "source": "conversation", "title": "Trip planning", "conversation_id": trip, "task_id": None}
        assert {key: items[agent][key] for key in ("source", "title", "what", "conversation_id")} == {
            "source": "agent", "title": "Research", "what": "Research wants to search the web.",
            "conversation_id": agent_thread}
        # Each one says since when it waits, and none of them times out.
        assert all(datetime.fromisoformat(item["requested_at"]) for item in items.values())
        assert {item["expires_at"] for item in items.values()} == {None}

        # Approve in place through the same reviewed command the cards use.
        view = client.get(f"/api/v1/approvals/{workflow}", headers=headers)
        assert view.status_code == 200, view.text
        resolved = client.post(f"/api/v1/approvals/{workflow}/commands", headers={
            **headers, "Idempotency-Key": str(uuid4())}, json={
            "command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": "approval.resolve", "expected_revision": view.json()["revision"],
            "payload": {"decision": "approve", "nonce": view.json()["nonce"]}})
        assert resolved.status_code in {200, 202}, resolved.text
        assert resumed[-1] == (workflow_token, True)
        assert set(_pending(client, headers)) == {conversation, agent}


def test_pending_approvals_need_a_signed_in_owner():
    client, _service, active = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        active["value"] = False
        response = client.get("/api/v1/monitor/approvals", headers=headers)
    assert response.status_code == 401
    assert "items" not in response.json()
