"""Authenticated Goal and Agent Profile capability routes."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def _conversation(client, headers) -> str:
    response = _command(client, headers, "conversation.create", {"title": "Goal owner"})
    assert response.status_code == 200, response.text
    return response.json()["conversation_id"]


def test_goal_route_reviews_executes_and_replays_one_scoped_action(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        payload = {
            "conversation_id": conversation,
            "goal_id": None,
            "revision": "none",
            "operation": "start",
            "objective": "Finish the isolated capability",
            "max_turns": 12,
            "reason": None,
        }
        review = client.post(
            f"/api/v1/conversations/{conversation}/goals/review",
            headers=headers,
            json=payload,
        )
        assert review.status_code == 200, review.text
        reviewed = review.json()
        command_id = str(uuid4())
        command = {
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": "goal.control",
            "expected_revision": "0",
            "payload": {**payload, "review_id": reviewed["review_id"]},
        }
        url = f"/api/v1/conversations/{conversation}/goals/commands"
        result = client.post(
            url,
            headers={**headers, "Idempotency-Key": command_id},
            json=command,
        )
        assert result.status_code == 200, result.text
        assert result.json()["goal"]["status"] == "active"
        assert client.post(
            url,
            headers={**headers, "Idempotency-Key": command_id},
            json=command,
        ).json() == result.json()
        page = client.get(
            f"/api/v1/conversations/{conversation}/goals",
            headers=headers,
        )
        assert page.status_code == 200, page.text
        assert page.json()["current_goal_id"] == result.json()["goal"]["id"]


def test_profile_route_keeps_private_instructions_out_of_reads(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        fields = {
            "slug": "browser_writer",
            "display_name": "Browser Writer",
            "description": "A reviewed local profile.",
            "when_to_use": "Use for local browser work.",
            "instructions": "Private profile instructions",
            "capability": "write_capable",
            "allow_tools": ["read_file"],
            "skills": [],
            "context_mode": "focused",
            "workspace_mode": "single_writer",
            "approval_mode": "inherit",
            "enabled": True,
        }
        payload = {
            "profile_id": None,
            "revision": "none",
            "operation": "create",
            "fields": fields,
            "target_slug": None,
            "target_name": None,
        }
        review = client.post(
            "/api/v1/settings/profiles/review",
            headers=headers,
            json=payload,
        )
        assert review.status_code == 200, review.text
        command_id = str(uuid4())
        command = {
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": "profile.mutate",
            "expected_revision": "0",
            "payload": {**payload, "review_id": review.json()["review_id"]},
        }
        response = client.post(
            "/api/v1/settings/profiles/commands",
            headers={**headers, "Idempotency-Key": command_id},
            json=command,
        )
        assert response.status_code == 200, response.text
        profile_id = response.json()["profile_id"]
        detail = client.get(
            f"/api/v1/settings/profiles/items/{profile_id}",
            headers=headers,
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["profile"]["instruction_edit"] == {
            "mode": "replace_only",
            "stored": True,
        }
        assert "Private profile instructions" not in detail.text
