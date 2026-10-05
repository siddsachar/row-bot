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


@pytest.fixture
def pinned_default_library(tmp_path, monkeypatch):
    """A real, isolated skill library whose one skill Settings pins for new chats."""
    from row_bot.plugins import state as plugin_state

    root = tmp_path / "skills-data"
    for name, path in [
        ("DATA_DIR", root),
        ("USER_SKILLS_DIR", root / "skills"),
        ("BUNDLED_SKILLS_DIR", tmp_path / "bundled"),
        ("TOOL_GUIDES_DIR", tmp_path / "guides"),
        ("CONFIG_PATH", root / "skills_config.json"),
    ]:
        monkeypatch.setattr(skills, name, path)
    monkeypatch.setattr(skills, "_skills_cache", {})
    monkeypatch.setattr(skills, "_enabled", {})
    monkeypatch.setattr(skills, "_pinned", [])
    folder = skills.USER_SKILLS_DIR / "meeting_notes"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: meeting_notes\ndisplay_name: Meeting Notes\n"
        "description: Turn a meeting into notes.\n---\n\nWrite tidy notes.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_state, "get_cached_plugin_enablement", lambda: None)
    monkeypatch.setattr(skills_activation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        skills_activation, "STATE_PATH", tmp_path / "skills_activation.json"
    )
    skills.set_pinned("meeting_notes", True)


def test_removing_a_default_skill_changes_only_that_chat(
    service, pinned_default_library
):
    def active(client, headers, conversation):
        view = _query(client, headers, conversation).json()
        return [(item["id"], item["source"], item["removable"]) for item in view["active_skills"]]

    with _client(service) as client:
        _, headers = bootstrap(client)
        first = _create(client, headers)
        other = _create(client, headers)["conversation_id"]
        seeded = [("meeting_notes", "default", True)]
        assert active(client, headers, first["conversation_id"]) == seeded
        assert active(client, headers, other) == seeded

        composer = _query(client, headers, first["conversation_id"]).json()
        removed = _command(
            client,
            headers,
            "conversation.skills",
            {
                "action": "remove",
                "composer_revision": composer["composer_revision"],
                "skill_id": "meeting_notes",
                "draft": "",
            },
            target=first["conversation_id"],
            revision=first["revision"],
        )
        assert removed.status_code == 200, removed.text

        assert active(client, headers, first["conversation_id"]) == []
        assert active(client, headers, other) == seeded
        library = client.get("/api/v1/settings/skills", headers=headers)
        assert library.status_code == 200, library.text
        assert [(item["id"], item["pinned"]) for item in library.json()["items"]] == [
            ("meeting_notes", True)
        ]
        # A chat started afterwards still begins with the Settings default.
        later = _create(client, headers)["conversation_id"]
        assert active(client, headers, later) == seeded


def test_a_chat_switch_and_a_mention_reach_the_turn_and_only_narrow_it(service, composer_library, monkeypatch):
    from langchain_core.messages import AIMessage

    from row_bot.integrations import facts, scope
    from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream

    items = [{"id": f"mcp:{name.lower()}", "kind": "mcp", "server": name, "name": name, "icon": "letter:" + name[0],
              "app": {"id": name.lower(), "name": name}, "lifecycle": "installed", "readiness": "ready",
              "parent_id": None, "children": []} for name in ("Notion", "Linear")]
    monkeypatch.setattr(scope, "_items", lambda: items)
    monkeypatch.setattr(facts, "read", lambda item_id, validate=None: next((i for i in items if i["id"] == item_id), None))
    fake = ScriptedAgentStream(*[(CheckpointCommit((AIMessage(id=f"answer-{n}", content="Done"),), f"answer-{n}"),
                                  ("done", None)) for n in range(2)])
    seen = []

    def stream(text, enabled, config, *, stop_event=None):
        seen.append(config["configurable"].get("app_scope"))
        yield from fake.stream(text, enabled, config, stop_event=stop_event)
    service.stream_factory = stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _create(client, headers)["conversation_id"]
        listed = _query(client, headers, conversation).json()["apps"]
        assert [(a["name"], a["on"]) for a in listed] == [("Linear", True), ("Notion", True)]
        switched = _command(client, headers, "conversation.apps", {"item_id": "mcp:notion", "on": False}, target=conversation)
        assert switched.status_code == 200, switched.text
        assert {a["name"]: a["on"] for a in _query(client, headers, conversation).json()["apps"]} == {"Linear": True,
                                                                                                    "Notion": False}
        unknown = _command(client, headers, "conversation.apps", {"item_id": "mcp:nothing", "on": True}, target=conversation,
                           revision=service.get_conversation(conversation)["revision"])
        assert unknown.json()["code"] == "not_found"

        def submit(text):
            response = _command(client, headers, "conversation.submit", {"submission_id": str(uuid4()), "text": text,
                "attachment_refs": [], "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}},
                target=conversation, revision=service.get_conversation(conversation)["revision"])
            assert response.status_code == 202, response.text
            assert service.registry.get(response.json()["execution_id"]).producer_done.wait(5)
        submit("What changed this week?")
        assert seen[-1]["exclude_servers"] == ["Notion"] and seen[-1]["focus"] == []
        submit("@Notion and @Linear, what changed?")  # Notion stays off; the mention focuses on Linear only.
        assert seen[-1]["exclude_servers"] == ["Notion"] and seen[-1]["focus"] == ["mcp:linear"]
