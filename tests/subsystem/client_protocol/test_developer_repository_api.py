"""Authenticated, binding-scoped Developer repository capability routes."""

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


def _snapshot(created: dict) -> dict:
    return {
        "schema_version": 1,
        "resource_id": created["resource_id"],
        "conversation_id": created["conversation_id"],
        "binding_id": created["binding_id"],
        "binding_revision": created["binding_revision"],
        "resource_revision": created["resource_revision"],
        "revision": "a" * 64,
        "workspace_name": "new-project",
        "trusted": False,
        "repository": {
            "state": "ready",
            "is_git": True,
            "is_root": True,
            "branch": "main",
            "detached": False,
            "dirty": False,
            "remote_configured": True,
            "tracking_summary": "up to date",
        },
        "worktrees": [],
        "sandbox": {
            "execution_mode": "local",
            "network": "off",
            "image": "row-bot-sandbox:latest",
            "pending_imports": 0,
            "owned_processes": 0,
            "runtime_status": "not_probed",
        },
        "availability": {
            "developer.repository.push": {"available": True, "code": None}
        },
    }


def test_repository_routes_bind_review_and_command_to_one_workspace(
    workspace_api, monkeypatch
):
    _, _, parent, client, headers, _, _ = workspace_api
    created = _public(
        _command(client, headers, _payload(_grant(client, headers))), parent
    )
    from row_bot.conversation_resources import list_bindings

    created["binding_revision"] = list_bindings(
        created["conversation_id"]
    ).bindings[0].revision
    snapshot = _snapshot(created)
    calls: list[str] = []

    from row_bot.application import developer_repository_commands as commands

    def read(resource_id, conversation_id, *, validate):
        validate()
        assert (resource_id, conversation_id) == (
            created["resource_id"],
            created["conversation_id"],
        )
        return snapshot

    def review(action, payload, resource_id, conversation_id, *, validate):
        validate()
        assert action == "developer.repository.push"
        assert payload == {"revision": "a" * 64}
        return {
            "schema_version": 1,
            "action": action,
            "resource_id": resource_id,
            "conversation_id": conversation_id,
            "binding_id": created["binding_id"],
            "binding_revision": created["binding_revision"],
            "resource_revision": created["resource_revision"],
            "revision": "a" * 64,
            "policy_action": "git_push",
            "policy_decision": "ask",
            "approval_required": True,
            "disclosures": ["The configured remote will receive this branch."],
            "action_digest": "b" * 64,
        }

    def execute(
        command,
        resource_id,
        conversation_id,
        *,
        validate,
        validate_action,
        validate_review,
        **_kwargs,
    ):
        validate()
        validate_action("git_push")
        reviewed = review(
            command["type"],
            {"revision": command["payload"]["revision"]},
            resource_id,
            conversation_id,
            validate=validate,
        )
        validate_review(command, reviewed)
        calls.append(command["command_id"])
        return {
            "schema_version": 1,
            "command_id": command["command_id"],
            "action": command["type"],
            "resource_id": resource_id,
            "conversation_id": conversation_id,
            "status": "completed",
            "code": None,
            "revision": "c" * 64,
            "worktree_id": None,
            "external_url": None,
        }

    monkeypatch.setattr(commands, "read_developer_repository", read)
    monkeypatch.setattr(commands, "review_developer_repository_command", review)
    monkeypatch.setattr(commands, "execute_developer_repository_command", execute)
    base = (
        f"/api/v1/conversations/{created['conversation_id']}"
        f"/workspaces/{created['binding_id']}/repository"
    )
    response = client.get(base, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == snapshot
    reviewed = client.post(
        base + "/review",
        headers=headers,
        json={
            "action": "developer.repository.push",
            "payload": {"revision": "a" * 64},
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["review_id"]
    command_id = str(uuid4())
    response = client.post(
        base + "/commands",
        headers={**headers, "Idempotency-Key": command_id},
        json={
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": "developer.repository.push",
            "expected_revision": "0",
            "payload": {
                "revision": "a" * 64,
                "nonce": reviewed.json()["review_id"],
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert calls == [command_id]


def test_repository_routes_reject_missing_or_foreign_binding(workspace_api):
    _, _, parent, client, headers, _, _ = workspace_api
    created = _public(
        _command(client, headers, _payload(_grant(client, headers))), parent
    )
    response = client.get(
        f"/api/v1/conversations/{created['conversation_id']}"
        "/workspaces/missing/repository",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "resource_binding_revoked"
