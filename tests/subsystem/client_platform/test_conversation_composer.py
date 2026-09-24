from __future__ import annotations

from copy import deepcopy
import sys
from types import SimpleNamespace

import pytest

from row_bot import skills, skills_activation, slash_commands, threads
from row_bot.api.v1.schemas import ConversationComposer
from row_bot.application import conversation_composer
from row_bot.application.client_platform import ClientPlatformError
from row_bot.skill_discovery import SkillRecord


def _skill(
    name: str,
    *,
    display_name: str | None = None,
    description: str = "Test skill",
    tags: list[str] | None = None,
    activation: dict[str, list[str]] | None = None,
) -> skills.Skill:
    return skills.Skill(
        name=name,
        display_name=display_name or name.replace("_", " ").title(),
        icon="✨",
        description=description,
        instructions=f"Instructions for {name}.",
        tags=tags or [],
        activation=activation or {},
        enabled_by_default=True,
        source="user",
    )


@pytest.fixture
def composer_state(tmp_path, monkeypatch):
    current_threads = conversation_composer._thread_domain()
    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    if current_threads is not threads:
        monkeypatch.setattr(current_threads, "DB_PATH", str(tmp_path / "threads.db"))
    monkeypatch.setattr(skills_activation, "DATA_DIR", tmp_path)
    monkeypatch.setattr(skills_activation, "STATE_PATH", tmp_path / "skills_activation.json")
    current_threads.create_thread(
        "Composer", thread_id="conversation-a", seed_default_skills=False
    )

    alpha = _skill("alpha_skill", description="Alpha planning workflow")
    beta = _skill("beta_skill", description="Beta writing workflow")
    research = _skill(
        "deep_research",
        display_name="Deep Research",
        description="Research sources and summarize evidence",
        tags=["research", "sources", "evidence"],
        activation={"keywords": ["research", "sources", "evidence"]},
    )
    colliding = _skill("status", description="Must not shadow built-in status")
    values = [alpha, beta, research, colliding]
    snapshot = {
        "revision": "library-r1",
        "items": {
            item.name: {"skill": item, "revision": f"rev-{item.name}"}
            for item in values
        },
        "enabled": {item.name: True for item in values},
        "pinned": [alpha.name],
    }
    monkeypatch.setattr(skills, "read_client_skills", lambda: deepcopy(snapshot))
    monkeypatch.setattr(
        skills,
        "load_skills",
        lambda: pytest.fail("composer reads must not load or migrate the skill registry"),
    )
    monkeypatch.setattr(conversation_composer, "_passive_plugin_records", lambda: ([], {}))
    skills_activation.reset_thread_to_defaults("conversation-a", snapshot["pinned"])
    return snapshot


def test_snapshot_is_passive_bounded_and_uses_canonical_command_registry(
    composer_state,
    monkeypatch,
):
    plugin = SkillRecord(
        canonical_id="plugin:local:formatter",
        alias="formatter",
        display_name="Local Formatter",
        icon="🔌",
        description="Format local text",
        tags=("format",),
        activation={},
        instructions="Format the supplied text.",
        source="plugin:local",
        root=None,
        plugin_name="Local",
    )
    monkeypatch.setattr(
        conversation_composer,
        "_passive_plugin_records",
        lambda: (
            [plugin],
            {
                plugin.canonical_id: {
                    "id": plugin.canonical_id,
                    "display_name": plugin.display_name,
                    "icon": plugin.icon,
                    "description": plugin.description,
                    "library_source": "plugin",
                }
            },
        ),
    )
    skills_activation.load_auto_skill(
        "conversation-a",
        plugin.canonical_id,
        available_ids=[plugin.canonical_id],
    )
    state_before = skills_activation.STATE_PATH.read_bytes()

    result = conversation_composer.read_conversation_composer(
        "conversation-a",
        draft="Research sources and summarize the evidence",
    )
    ConversationComposer.model_validate(result)

    assert result["library"] == {"availability": "available", "revision": "library-r1"}
    assert [(item["id"], item["source"], item["removable"]) for item in result["active_skills"]] == [
        ("alpha_skill", "default", True),
        (plugin.canonical_id, "auto", True),
    ]
    assert result["suggestions"][0]["id"] == "skill:deep_research"
    assert skills_activation.STATE_PATH.read_bytes() == state_before

    commands = {item["id"]: item for item in result["commands"]}
    assert commands["reasoning"]["argument_mode"] == "prefix"
    assert commands["reasoning"]["argument_hint"] == "Type details after the command"
    assert commands["reasoning"]["handler_kind"] == "reasoning"
    assert commands["skill:deep_research"]["token"] == "/deep-research"
    assert "skill:status" not in commands
    assert all(item.get("skill_id") != plugin.canonical_id for item in result["commands"])
    assert result["commands_truncated"] is False

    specs = slash_commands.get_command_specs()
    assert any(spec.id == "skill:deep_research" for spec in specs)


def test_revision_checked_commands_are_thread_local_and_advance_client_revision(composer_state):
    initial = conversation_composer.read_conversation_composer(
        "conversation-a",
        draft="Research sources and summarize the evidence",
    )
    unchanged = conversation_composer.apply_conversation_skill_command(
        "conversation-a",
        "activate",
        composer_revision=initial["composer_revision"],
        skill_id="alpha_skill",
        draft="Research sources and summarize the evidence",
    )
    assert unchanged["changed"] is False
    assert unchanged["conversation_revision"] == initial["conversation_revision"]
    assert unchanged["snapshot"]["composer_revision"] == initial["composer_revision"]
    assert unchanged["snapshot"]["active_skills"][0]["source"] == "default"
    dismissed = conversation_composer.apply_conversation_skill_command(
        "conversation-a",
        "dismiss",
        composer_revision=unchanged["snapshot"]["composer_revision"],
        skill_id="deep_research",
        draft="Research sources and summarize the evidence",
    )
    assert int(dismissed["conversation_revision"]) == int(initial["conversation_revision"]) + 1
    assert dismissed["snapshot"]["suggestions"] == []

    activated = conversation_composer.apply_conversation_skill_command(
        "conversation-a",
        "activate",
        composer_revision=dismissed["snapshot"]["composer_revision"],
        skill_id="beta_skill",
    )
    assert int(activated["conversation_revision"]) == int(dismissed["conversation_revision"]) + 1
    assert any(item["id"] == "beta_skill" for item in activated["snapshot"]["active_skills"])
    assert composer_state["pinned"] == ["alpha_skill"]

    with pytest.raises(ClientPlatformError) as stale:
        conversation_composer.apply_conversation_skill_command(
            "conversation-a",
            "remove",
            composer_revision=initial["composer_revision"],
            skill_id="beta_skill",
        )
    assert stale.value.code == "skill_revision_conflict"
    assert stale.value.current_revision == activated["snapshot"]["composer_revision"]

    removed = conversation_composer.apply_conversation_skill_command(
        "conversation-a",
        "remove",
        composer_revision=activated["snapshot"]["composer_revision"],
        skill_id="beta_skill",
    )
    assert not any(item["id"] == "beta_skill" for item in removed["snapshot"]["active_skills"])
    reset = conversation_composer.apply_conversation_skill_command(
        "conversation-a",
        "reset",
        composer_revision=removed["snapshot"]["composer_revision"],
    )
    assert [(item["id"], item["source"]) for item in reset["snapshot"]["active_skills"]] == [
        ("alpha_skill", "default")
    ]
    assert composer_state["pinned"] == ["alpha_skill"]


def test_profile_skill_policy_is_runtime_effective_and_not_removable(composer_state, monkeypatch):
    monkeypatch.setattr(
        conversation_composer,
        "_selected_profile",
        lambda _context: {
            "id": "profile-a",
            "enabled": True,
            "skill_policy_json": {"skills_override": ["beta_skill"]},
        },
    )
    result = conversation_composer.read_conversation_composer("conversation-a")

    assert [(item["id"], item["source"], item["removable"]) for item in result["active_skills"]] == [
        ("beta_skill", "thread", False)
    ]
    with pytest.raises(ClientPlatformError) as blocked:
        conversation_composer.apply_conversation_skill_command(
            "conversation-a",
            "remove",
            composer_revision=result["composer_revision"],
            skill_id="beta_skill",
        )
    assert blocked.value.code == "skill_not_removable"


def test_unavailable_library_degrades_reads_and_blocks_mutation(composer_state, monkeypatch):
    monkeypatch.setattr(
        skills,
        "read_client_skills",
        lambda: (_ for _ in ()).throw(ValueError("unavailable")),
    )

    result = conversation_composer.read_conversation_composer("conversation-a")

    assert result["library"]["availability"] == "unavailable"
    assert result["active_skills"] == []
    assert all(not item["id"].startswith("skill:") for item in result["commands"])
    with pytest.raises(ClientPlatformError) as blocked:
        conversation_composer.apply_conversation_skill_command(
            "conversation-a",
            "reset",
            composer_revision=result["composer_revision"],
        )
    assert blocked.value.code == "skills_unavailable"


def test_thread_override_changes_both_composer_and_client_revisions(composer_state):
    before = conversation_composer.read_conversation_composer("conversation-a")

    threads.set_thread_skills_override("conversation-a", ["beta_skill"])
    after = conversation_composer.read_conversation_composer("conversation-a")

    assert after["composer_revision"] != before["composer_revision"]
    assert int(after["conversation_revision"]) == int(before["conversation_revision"]) + 1
    assert [(item["id"], item["source"]) for item in after["active_skills"]] == [
        ("beta_skill", "thread"),
        ("alpha_skill", "default"),
    ]
    with pytest.raises(ClientPlatformError) as stale:
        conversation_composer.apply_conversation_skill_command(
            "conversation-a",
            "activate",
            composer_revision=before["composer_revision"],
            skill_id="deep_research",
        )
    assert stale.value.code == "skill_revision_conflict"


def test_thread_owner_is_resolved_after_supported_module_replacement(monkeypatch):
    replacement = SimpleNamespace(marker="current")
    monkeypatch.setitem(sys.modules, "row_bot.threads", replacement)

    assert conversation_composer._thread_domain() is replacement
