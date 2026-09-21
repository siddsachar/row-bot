"""Authenticated composer reads and durable per-conversation skill commands."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from row_bot import skills, skills_activation
from row_bot.api.v1.schemas import ConversationComposer, SlashCommandResult
from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,  # noqa: F401
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def composer_library(tmp_path, monkeypatch):
    from row_bot.plugins import state as plugin_state

    item = skills.Skill(
        name="deep_research",
        display_name="Deep Research",
        icon="search",
        description="Research sources and summarize evidence.",
        instructions="Research sources and summarize the supplied evidence.",
        tags=["research", "sources", "evidence"],
        activation={"keywords": ["research", "sources", "evidence"]},
        enabled_by_default=False,
        source="user",
    )
    snapshot = {
        "revision": "library-api-1",
        "items": {item.name: {"skill": item, "revision": "skill-api-1"}},
        "enabled": {item.name: True},
        "pinned": [],
    }
    monkeypatch.setattr(skills, "read_client_skills", lambda: deepcopy(snapshot))
    # Composer reads are passive: make this fixture independent of plugin
    # enablement caches intentionally exercised by earlier protocol modules.
    monkeypatch.setattr(plugin_state, "get_cached_plugin_enablement", lambda: None)
    monkeypatch.setattr(skills_activation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        skills_activation,
        "STATE_PATH",
        tmp_path / "skills_activation.json",
    )
    return snapshot


def _create(client, headers):
    response = _command(
        client,
        headers,
        "conversation.create",
        {"title": "Composer API fixture"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _query(client, headers, conversation, **body):
    return client.post(
        f"/api/v1/conversations/{conversation}/composer/query",
        headers=headers,
        json=body,
    )


def _skill_command(
    client,
    headers,
    conversation,
    *,
    revision,
    composer_revision,
    command_id=None,
    key=None,
):
    return _command(
        client,
        headers,
        "conversation.skills",
        {
            "action": "activate",
            "composer_revision": composer_revision,
            "skill_id": "deep_research",
            "draft": "Research sources and summarize the evidence.",
        },
        target=conversation,
        revision=revision,
        command_id=command_id,
        key=key,
    )


def test_composer_read_is_bounded_passive_and_authenticated(
    service, composer_library
):
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _create(client, headers)
        conversation = created["conversation_id"]
        activation_before = (
            skills_activation.STATE_PATH.read_bytes()
            if skills_activation.STATE_PATH.exists()
            else b""
        )

        denied = _query(client, {}, conversation, draft="review")
        assert denied.status_code in {401, 403}
        bad_origin = _query(
            client,
            {**headers, "Origin": "https://example.invalid"},
            conversation,
            draft="review",
        )
        assert bad_origin.status_code == 403
        oversized = _query(client, headers, conversation, draft="x" * 16001)
        assert oversized.status_code == 422

        response = _query(
            client,
            headers,
            conversation,
            draft="Research sources and summarize the evidence.",
            command_query="research",
            command_limit=256,
        )
        assert response.status_code == 200, response.text
        view = ConversationComposer.model_validate_json(response.text)
        assert view.library.revision == "library-api-1"
        assert [item.skill_id for item in view.suggestions] == ["deep_research"]
        assert any(item.id == "skill:deep_research" for item in view.commands)
        assert len(response.content) < 256 * 1024
        assert skills_activation.STATE_PATH.read_bytes() == activation_before


def test_skill_command_replays_exactly_and_stale_clients_refresh_from_truth(
    service, composer_library
):
    with _client(service) as client:
        _, first_headers = bootstrap(client)
        _, second_headers = bootstrap(client)
        created = _create(client, first_headers)
        conversation = created["conversation_id"]
        first = _query(client, first_headers, conversation).json()
        second = _query(client, second_headers, conversation).json()
        command_id, key = str(uuid4()), str(uuid4())

        accepted = _skill_command(
            client,
            first_headers,
            conversation,
            revision=created["revision"],
            composer_revision=first["composer_revision"],
            command_id=command_id,
            key=key,
        )
        assert accepted.status_code == 200, accepted.text
        replay = _skill_command(
            client,
            first_headers,
            conversation,
            revision=created["revision"],
            composer_revision=first["composer_revision"],
            command_id=command_id,
            key=key,
        )
        assert replay.status_code == 200
        assert replay.json() == accepted.json()

        receipt = client.get(
            f"/api/v1/commands/{command_id}", headers=first_headers
        )
        assert receipt.status_code == 200 and receipt.json() == accepted.json()
        # Local-owner clients share one authenticated owner receipt namespace,
        # allowing a second observing client to reconcile response loss.
        assert client.get(
            f"/api/v1/commands/{command_id}", headers=second_headers
        ).json() == accepted.json()
        current = _query(client, first_headers, conversation).json()
        assert [(item["id"], item["source"]) for item in current["active_skills"]] == [
            ("deep_research", "pinned")
        ]

        stale = _skill_command(
            client,
            second_headers,
            conversation,
            revision=second["conversation_revision"],
            composer_revision=second["composer_revision"],
        )
        assert stale.status_code == 409
        assert stale.json()["code"] in {"revision_conflict", "skill_revision_conflict"}
        assert _query(client, second_headers, conversation).json() == current


def test_slash_read_is_allowlisted_bounded_and_cannot_execute_arbitrary_text(
    service, composer_library
):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _create(client, headers)["conversation_id"]
        path = f"/api/v1/conversations/{conversation}/composer/command"
        rejected = client.post(path, headers=headers, json={"command_id": "shell"})
        assert rejected.status_code == 422
        response = client.post(path, headers=headers, json={"command_id": "help"})
        assert response.status_code == 200, response.text
        value = SlashCommandResult.model_validate_json(response.text)
        assert value.command_id == "help"
        assert "/status" in value.text
        assert len(response.content) < 20 * 1024
