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
