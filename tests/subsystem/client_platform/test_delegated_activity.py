"""Public child navigation retains owner relationships without private context."""
from __future__ import annotations

import json

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service, _client  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def test_public_child_detail_parent_return_and_foreign_run_denial(service):
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity, read_run
    from row_bot.application.client_platform import ClientPlatformError
    parent, child, foreign = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child", "Foreign")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Research", status="completed",
                                      summary="Public result", prompt="PRIVATE-PROMPT", context_summary="PRIVATE-CONTEXT",
                                      error="PRIVATE-ERROR", workspace_path="PRIVATE-PATH", result_json={"private": "PRIVATE-RESULT"})
    detail = read_run(service, parent, run["id"])
    assert detail["child_conversation_id"] == child and detail["summary"] == "Public result"
    assert "PRIVATE" not in json.dumps(detail)
    assert read_activity(service, child)["parent_conversation_id"] == parent
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_run(service, foreign, run["id"])
    client = _client(service)
    _, headers = bootstrap(client)
    response = client.get(f"/api/v1/conversations/{parent}/delegated/{run['id']}", headers=headers)
    assert response.status_code == 200 and "PRIVATE" not in response.text
    assert client.get(f"/api/v1/conversations/{foreign}/delegated/{run['id']}", headers=headers).status_code == 404


def test_deleting_parent_denied_and_deleted_child_history_unavailable(service, monkeypatch):
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity, read_run
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.runtime import admissions
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child)
    admissions.close_admission(child)
    assert read_run(service, parent, run["id"])["child_conversation_id"] is None
    original = agent_runs.get_agent_run
    def deleting_read(run_id):
        result = original(run_id)
        admissions.close_admission(parent)
        return result
    monkeypatch.setattr(agent_runs, "get_agent_run", deleting_read)
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_run(service, parent, run["id"])
    with pytest.raises(ClientPlatformError, match="not_found"):
        read_activity(service, parent)


def test_child_pages_complete_and_cursor_cannot_cross_parent(service):
    from row_bot import threads, agent_runs
    from row_bot.application.delegated_activity import read_activity
    from row_bot.application.client_platform import ClientPlatformError
    parent, foreign = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Foreign")]
    ids = {agent_runs.create_agent_run(parent_thread_id=parent, display_name=f"Child {index}")["id"] for index in range(53)}
    first = read_activity(service, parent)
    assert len(first["items"]) == 50 and first["has_more"]
    second = read_activity(service, parent, cursor=first["next_cursor"])
    assert len(second["items"]) == 3 and not second["has_more"]
    assert {item["run_id"] for item in first["items"] + second["items"]} == ids
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        read_activity(service, foreign, cursor=first["next_cursor"])


def _agent_command(service, target, kind, payload):
    from uuid import uuid4
    identity = str(uuid4())
    return service.execute(owner_id="fixture", idempotency_key=identity, command={
        "type": kind, "command_id": identity,
        "expected_revision": str(service._metadata(target)["client_revision"]),
        "payload": payload,
    }, target=target)


def test_stop_and_message_a_running_agent_from_the_parent_and_its_own_thread(service):
    """Parity row 9: Stop and Message from Agents and from the child thread."""
    from uuid import uuid4
    from row_bot import threads, agent_runs
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.delegated_activity import read_activity
    parent, child, foreign = [threads.create_thread(name, seed_default_skills=False)
                              for name in ("Parent", "Child", "Foreign")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Research",
                                      status="running", prompt="Synthetic task")
    own = read_activity(service, child)["own_run"]
    assert own["run_id"] == run["id"] and own["status"] == "running"
    assert read_activity(service, parent)["own_run"] is None
    receipt = _agent_command(service, parent, "agent.message",
                             {"run_id": run["id"], "message_id": str(uuid4()), "text": "Also cover tides."})
    assert receipt["status"] == "completed"
    _agent_command(service, child, "agent.message",
                   {"run_id": run["id"], "message_id": str(uuid4()), "text": "And keep it short."})
    pending = [record["content"] for record in agent_runs.pending_parent_message_records(run["id"])]
    assert pending == ["Also cover tides.", "And keep it short."]
    with pytest.raises(ClientPlatformError, match="not_found"):
        _agent_command(service, foreign, "agent.stop", {"run_id": run["id"]})
    _agent_command(service, child, "agent.stop", {"run_id": run["id"]})
    stopped = agent_runs.get_agent_run(run["id"])
    # Nothing runs it in this test, so the stop settles at once.
    assert stopped["stop_requested"] and stopped["status"] == "stopped"
    with pytest.raises(ClientPlatformError, match="agent_run_finished"):
        _agent_command(service, parent, "agent.message",
                       {"run_id": run["id"], "message_id": str(uuid4()), "text": "Too late."})


def test_stopping_a_finished_agent_is_a_quiet_no_op(service):
    from row_bot import threads, agent_runs
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, status="completed",
                                      summary="Done")
    assert _agent_command(service, parent, "agent.stop", {"run_id": run["id"]})["status"] == "completed"
    assert agent_runs.get_agent_run(run["id"])["status"] == "completed"


def test_agent_start_spawns_a_delegated_agent_from_the_composer(service, monkeypatch):
    """/agent [profile] <task> starts a child run of this conversation (B112)."""
    from row_bot import agent_commands, threads
    from row_bot.application.client_platform import ClientPlatformError
    parent = threads.create_thread("Parent", seed_default_skills=False)
    spawned = []

    def spawn(thread_id, request, **kwargs):
        spawned.append((thread_id, request.objective, request.profile))
        return {"id": "run-started", "parent_thread_id": thread_id, "thread_id": "",
                "display_name": "Agent: Summarise tide tables", "status": "queued", "summary": ""}

    monkeypatch.setattr(agent_commands, "spawn_agent_from_request", spawn)
    receipt = _agent_command(service, parent, "agent.start", {"text": "Summarise tide tables"})
    assert receipt["status"] == "completed"
    assert spawned and spawned[0][:2] == (parent, "Summarise tide tables")
    with pytest.raises(ClientPlatformError, match="invalid_command"):
        _agent_command(service, parent, "agent.start", {"text": "--model="})


def test_stopping_an_agent_that_waits_for_approval_withdraws_the_approval(service):
    """B165: Stop left the agent's approval pending, so "Needs approval" stayed
    on the Buddy and in Home until it timed out."""
    from row_bot import threads, agent_runs, tasks
    parent, child = [threads.create_thread(name, seed_default_skills=False) for name in ("Parent", "Child")]
    run = agent_runs.create_agent_run(parent_thread_id=parent, thread_id=child, display_name="Research",
                                      status="waiting_approval", prompt="Synthetic task")
    other = agent_runs.create_agent_run(parent_thread_id=parent, display_name="Other",
                                        status="waiting_approval", prompt="Another task")
    _token, approval_id = tasks.create_approval_request(
        run_id=run["id"], task_id="", step_id="agent_interrupt", message="Run a synthetic command?",
        agent_run_id=run["id"], resume_kind="agent_run", parent_thread_id=parent)
    _other_token, other_approval = tasks.create_approval_request(
        run_id=other["id"], task_id="", step_id="agent_interrupt", message="Another question?",
        agent_run_id=other["id"], resume_kind="agent_run", parent_thread_id=parent)
    _agent_command(service, parent, "agent.stop", {"run_id": run["id"]})
    assert agent_runs.get_agent_run(run["id"])["status"] == "stopped"
    statuses = tasks.get_approval_request_statuses([approval_id, other_approval])
    assert statuses == {approval_id: "cancelled", other_approval: "pending"}
    assert [item["id"] for item in tasks.get_pending_approvals()] == [other_approval]
