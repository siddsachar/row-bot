"""Session-bound process review and durable control over an isolated workspace."""
# ruff: noqa: F811 -- shared isolated fixtures.
from dataclasses import replace
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import pytest

from tests.subsystem.client_protocol.test_workspace_edit_commands import editable, workspace_api, service, send  # noqa: F401

pytestmark = pytest.mark.subsystem


def review(client, headers, created, command_id=None):
    identifier = command_id or str(uuid4())
    response = client.post(f"/api/v1/conversations/{created['conversation_id']}/workspaces/{created['binding_id']}/processes/review",
        headers=headers, json={"command_id": identifier, "command": "python -c pass"})
    assert response.status_code == 200, response.text
    value = response.json()
    return {"command_id": identifier, "client_session_id": headers["X-Client-Session"],
        "type": "workspace.process.start", "expected_revision": value["conversation_revision"], "payload": {
            "target": {"kind": "workspace", **{key: value[key] for key in
                ("resource_id", "resource_revision", "binding_id", "binding_revision")}},
            **{key: value[key] for key in ("command", "policy_revision", "action_digest", "nonce")}}}


@pytest.fixture
def process_api(editable, monkeypatch):
    from row_bot.developer import client_processes
    calls, records = [], {}
    def start(resource, conversation, command, *, command_id, validate, **kwargs):
        validate()
        calls.append(("start", command_id))
        result = client_processes.WorkspaceProcessInfo(command_id, command_id,
            uuid5(NAMESPACE_URL, "row-bot:workspace-process:" + command_id).hex,
            command, "running", None, False)
        records[command_id] = result
        return result
    def stop(resource, conversation, process_id, *, validate):
        validate()
        calls.append(("stop", process_id))
        records[process_id] = replace(records[process_id], state="exited", exit_code=0, quiesced=True)
        return records[process_id]
    monkeypatch.setattr(client_processes, "start_workspace_process", start)
    monkeypatch.setattr(client_processes, "stop_workspace_process", stop)
    return (*editable, calls, records)


def test_review_is_passive_and_exact_start_stop_receipts_replay(process_api):
    _, client, headers, created, root, _, calls, _ = process_api
    body = review(client, headers, created)
    assert not calls
    import json
    from row_bot.api.v1.schemas import Command
    Command.model_validate_json(json.dumps(body))
    response = send(client, headers, created, body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["workspace_process"]["state"] == "running"
    assert not result["workspace_process"]["quiesced"]
    assert send(client, headers, created, body).json() == result
    assert calls == [("start", body["command_id"])]
    public = client.get(f"/api/v1/commands/{body['command_id']}", headers=headers)
    assert public.status_code == 200, public.text
    for text in (body["payload"]["command"], body["payload"]["nonce"], str(root)):
        assert text not in public.text
    stopped = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "type": "workspace.process.stop", "expected_revision": "0",
        "payload": {"target": body["payload"]["target"], "process_id": body["command_id"]}}
    response = send(client, headers, created, stopped)
    assert response.status_code == 200, response.text
    assert response.json()["workspace_process"]["quiesced"] is True
    assert send(client, headers, created, stopped).json() == response.json()
    assert len(calls) == 2


def test_forged_review_and_withdrawn_policy_never_start(process_api):
    from row_bot import threads
    _, client, headers, created, _, _, calls, _ = process_api
    body = review(client, headers, created)
    body["payload"]["nonce"] = "forged"
    rejected = send(client, headers, created, body)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "approval_expired"
    body = review(client, headers, created)
    threads._set_thread_approval_mode(created["conversation_id"], "block")
    rejected = send(client, headers, created, body)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "process_review_stale"
    assert not calls


def test_passive_queries_are_bounded_and_cross_binding_denied(process_api):
    _, client, headers, created, _, _, calls, _ = process_api
    base = f"/api/v1/conversations/{created['conversation_id']}/workspaces/{created['binding_id']}/processes"
    value = client.get(base, headers=headers)
    assert value.status_code == 200, value.text
    assert value.json()["processes"] == []
    value = client.get(base + "/recovery", headers=headers)
    assert value.status_code == 200, value.text
    assert value.json() == {"items": [], "next_cursor": None}
    invalid = client.get(base + "/recovery", headers=headers, params={"cursor": "x" * 65})
    assert invalid.status_code == 422
    other = base.replace(created["binding_id"], str(UUID(int=42)))
    assert client.get(other, headers=headers).status_code == 403
    assert not calls
