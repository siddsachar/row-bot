"""Local-owner Custom Tool routes bind every request to one workspace."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_empty_workspace_setup import (
    _command,
    _grant,
    _payload,
    _public,
    service,
    workspace_api,
)

pytestmark = pytest.mark.subsystem


def test_custom_tool_routes_are_bound_and_idempotent(workspace_api, monkeypatch):
    _, _, parent, client, headers, _, _ = workspace_api
    created = _public(_command(client, headers, _payload(_grant(client, headers))), parent)
    from row_bot.developer import client_custom_tools as owner

    calls = []
    phase = ["completed"]
    snapshot = {
        "schema_version": 1,
        "resource_id": created["resource_id"],
        "conversation_id": created["conversation_id"],
        "binding_id": created["binding_id"],
        "workspace_name": "synthetic-workspace",
        "source_is_repository": False,
        "drafts": [],
        "tools": [],
        "revision": "a" * 64,
    }

    def read(resource_id, conversation_id, *, validate):
        validate()
        assert resource_id == created["resource_id"]
        assert conversation_id == created["conversation_id"]
        return snapshot

    def execute(resource_id, conversation_id, command, *, owner_id, validate):
        read(resource_id, conversation_id, validate=validate)
        assert owner_id
        calls.append(command)
        return {
            "command_id": command["command_id"],
            "status": phase[0],
            "summary": "Inspected synthetic workspace." if phase[0] == "completed" else "Inspection failed before a draft was saved. Try again.",
            "snapshot": snapshot,
        }

    monkeypatch.setattr(owner, "read_custom_tools", read)
    monkeypatch.setattr(owner, "execute_custom_tool", execute)
    base = (
        f"/api/v1/conversations/{created['conversation_id']}"
        f"/workspaces/{created['binding_id']}/custom-tools"
    )
    response = client.get(base, headers=headers)
    assert response.status_code == 200, response.text
    command_id = str(uuid4())
    command = {
        "command_id": command_id,
        "client_session_id": headers["X-Client-Session"],
        "revision": "a" * 64,
        "action": "inspect",
        "payload": {},
    }
    denied = client.post(base + "/commands", headers=headers, json=command)
    assert denied.status_code == 409
    assert not calls
    accepted = client.post(
        base + "/commands",
        headers={**headers, "idempotency-key": command_id},
        json=command,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "completed"
    assert len(calls) == 1
    phase[0] = "failed"
    retry_id = str(uuid4())
    retry = client.post(
        base + "/commands",
        headers={**headers, "idempotency-key": retry_id},
        json={**command, "command_id": retry_id},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "failed"
    assert "before a draft was saved" in retry.json()["summary"]
    assert len(calls) == 2
