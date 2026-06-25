from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Iterable

import pytest


pytestmark = pytest.mark.subsystem


def _fresh_modules(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "row_bot.tasks",
        "row_bot.agent_profiles",
        "row_bot.skills",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.agent_profiles as agent_profiles
    import row_bot.skills as skills

    tasks = importlib.reload(tasks)
    agent_profiles = importlib.reload(agent_profiles)
    skills = importlib.reload(skills)
    return tasks, agent_profiles, skills


def _json_list(value: list[str] | None) -> str | None:
    return json.dumps(value) if value is not None else None


def _make_legacy_task(
    tasks,
    *,
    name: str,
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    profile: str = "",
    model_override: str = "",
    enabled: bool = True,
) -> str:
    task_id = tasks.create_task(
        name=name,
        prompts=["run the legacy workflow"],
        apply_default_skills=False,
        channels=[],
    )
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET agent_profile_id = ?, tools_override = ?, "
            "skills_override = ?, model_override = ?, enabled = ?, "
            "profile_migration_status = '', profile_migration_note = '', "
            "profile_migration_snapshot_json = '{}' WHERE id = ?",
            (
                profile,
                _json_list(tools),
                _json_list(skills),
                model_override,
                1 if enabled else 0,
                task_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return task_id


def _known_policy(monkeypatch, tasks, *, tools: Iterable[str], skills: Iterable[str]) -> None:
    monkeypatch.setattr(tasks, "_known_workflow_tool_ids", lambda: set(tools))
    monkeypatch.setattr(tasks, "_known_workflow_skill_names", lambda: set(skills))


def test_migration_assigns_default_profile_and_preserves_model_override(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, _profiles, _skills = _fresh_modules(tmp_path, monkeypatch)
    task_id = _make_legacy_task(
        tasks,
        name="No custom policy",
        model_override="model:openai:gpt-4o-mini",
        enabled=True,
    )

    result = tasks.migrate_workflow_profile_policies()
    task = tasks.get_task(task_id)

    assert result["migrated"] == 1
    assert task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert task["tools_override"] is None
    assert task["skills_override"] is None
    assert task["model_override"] == "model:openai:gpt-4o-mini"
    assert task["enabled"] is True
    assert task["profile_migration_status"] == "not_needed"


def test_migration_ignores_old_default_skill_snapshot_but_marks_review(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, _profiles, skills = _fresh_modules(tmp_path, monkeypatch)
    skills.load_skills()
    task_id = _make_legacy_task(
        tasks,
        name="Default skill snapshot",
        skills=["proactive_agent"],
        enabled=True,
    )

    result = tasks.migrate_workflow_profile_policies()
    task = tasks.get_task(task_id)

    assert result["needs_review"] == 1
    assert task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert task["tools_override"] is None
    assert task["skills_override"] is None
    assert task["enabled"] is True
    assert task["profile_migration_status"] == "needs_review"
    assert "legacy task default skill snapshot" in task["profile_migration_note"]


def test_migration_maps_exact_builtin_profile_policy(tmp_path, monkeypatch) -> None:
    tasks, agent_profiles, _skills = _fresh_modules(tmp_path, monkeypatch)
    research = agent_profiles.require_agent_profile("research")
    tools = list(research["tool_policy_json"]["allow_tools"])
    skills = list(research["skill_policy_json"]["skills_override"])
    _known_policy(monkeypatch, tasks, tools=tools, skills=skills)
    task_id = _make_legacy_task(
        tasks,
        name="Research legacy policy",
        tools=tools,
        skills=skills,
        enabled=True,
    )

    result = tasks.migrate_workflow_profile_policies()
    task = tasks.get_task(task_id)

    assert result["migrated"] == 1
    assert task["agent_profile_id"] == "builtin:research"
    assert task["tools_override"] is None
    assert task["skills_override"] is None
    assert task["enabled"] is True
    assert task["profile_migration_status"] == "exact_profile"
    assert task["profile_migration_snapshot_json"]["selected_profile_slug"] == "research"


def test_migration_reuses_one_profile_for_identical_custom_policy(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, agent_profiles, _skills = _fresh_modules(tmp_path, monkeypatch)
    _known_policy(
        monkeypatch,
        tasks,
        tools={"filesystem"},
        skills={"meeting_notes", "proactive_agent"},
    )
    first_id = _make_legacy_task(
        tasks,
        name="Custom policy one",
        tools=["filesystem"],
        skills=["meeting_notes"],
        model_override="model:openai:gpt-4o-mini",
    )
    second_id = _make_legacy_task(
        tasks,
        name="Custom policy two",
        tools=["filesystem"],
        skills=["meeting_notes"],
    )

    result = tasks.migrate_workflow_profile_policies()
    first = tasks.get_task(first_id)
    second = tasks.get_task(second_id)
    migrated_profiles = [
        profile
        for profile in agent_profiles.list_agent_profiles(
            enabled_only=False,
            include_builtins=False,
        )
        if profile["source"] == "workflow_created"
    ]

    assert result["created_or_reused_profiles"] == 2
    assert first["agent_profile_id"] == second["agent_profile_id"]
    assert first["profile_migration_status"] == "created_profile"
    assert second["profile_migration_status"] == "created_profile"
    assert first["tools_override"] is None
    assert second["skills_override"] is None
    assert first["model_override"] == "model:openai:gpt-4o-mini"
    assert len(migrated_profiles) == 1
    profile = migrated_profiles[0]
    assert profile["id"] == first["agent_profile_id"]
    assert profile["tool_policy_json"]["allow_tools"] == ["filesystem"]
    assert profile["skill_policy_json"]["skills_override"] == ["meeting_notes"]
    assert set(profile["provenance_json"]["source_task_ids"]) == {first_id, second_id}


def test_migration_blocks_missing_policy_items_and_preserves_legacy_fields(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, _profiles, _skills = _fresh_modules(tmp_path, monkeypatch)
    _known_policy(monkeypatch, tasks, tools={"filesystem"}, skills={"meeting_notes"})
    task_id = _make_legacy_task(
        tasks,
        name="Missing legacy policy",
        tools=["missing_tool"],
        skills=["meeting_notes"],
        enabled=True,
    )

    result = tasks.migrate_workflow_profile_policies()
    task = tasks.get_task(task_id)

    assert result["blocked"] == 1
    assert task["enabled"] is False
    assert task["profile_migration_status"] == "blocked"
    assert task["tools_override"] == ["missing_tool"]
    assert task["skills_override"] == ["meeting_notes"]
    assert task["profile_migration_snapshot_json"]["missing_policy_items"] == {
        "tools": ["missing_tool"]
    }


def test_migration_clears_legacy_fields_when_existing_profile_is_valid(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, _profiles, _skills = _fresh_modules(tmp_path, monkeypatch)
    task_id = _make_legacy_task(
        tasks,
        name="Already profiled",
        tools=["filesystem"],
        skills=["meeting_notes"],
        profile="builtin:review",
        enabled=True,
    )

    result = tasks.migrate_workflow_profile_policies()
    task = tasks.get_task(task_id)

    assert result["migrated"] == 1
    assert task["agent_profile_id"] == "builtin:review"
    assert task["tools_override"] is None
    assert task["skills_override"] is None
    assert task["enabled"] is True
    assert task["profile_migration_status"] == "not_needed"
