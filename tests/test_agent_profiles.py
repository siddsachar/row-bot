from __future__ import annotations

import importlib
import sys

import pytest


def _fresh_agent_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.agent_profiles as agent_profiles

    tasks = importlib.reload(tasks)
    threads = importlib.reload(threads)
    agent_profiles = importlib.reload(agent_profiles)
    return agent_profiles, tasks, threads


def test_builtin_agent_profile_registry(tmp_path, monkeypatch):
    agent_profiles, _tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    profiles = agent_profiles.list_agent_profiles()
    slugs = {profile["slug"] for profile in profiles}

    assert {
        "row_bot_default",
        "planner",
        "explorer",
        "researcher",
        "docs_researcher",
        "reviewer",
        "tester",
        "worker",
        "browser_debugger",
        "synthesizer",
    } <= slugs
    reviewer = agent_profiles.require_agent_profile("Reviewer")
    tester = agent_profiles.require_agent_profile("tester")
    assert reviewer["tool_policy_json"]["capability"] == "read_only"
    assert tester["tool_policy_json"]["capability"] == "write_capable"


def test_db_backed_profile_crud_and_duplicate_builtin(tmp_path, monkeypatch):
    agent_profiles, _tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    saved = agent_profiles.save_agent_profile(
        slug="release_reviewer",
        display_name="Release Reviewer",
        description="Review release notes and packaging risk.",
        instructions="Review release assets and summarize risk.",
        tool_policy_json={"capability": "read_only"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
        approval_policy_json={"mode": "inherit"},
    )

    assert saved["scope"] == "user"
    assert agent_profiles.require_agent_profile("release_reviewer")["id"] == saved["id"]

    duplicated = agent_profiles.duplicate_agent_profile(
        "reviewer",
        {"slug": "custom_reviewer", "display_name": "Custom Reviewer"},
    )

    assert duplicated["slug"] == "custom_reviewer"
    assert duplicated["source"] == "user_created"
    assert duplicated["provenance_json"]["duplicated_from_profile_slug"] == "reviewer"

    assert agent_profiles.delete_agent_profile(saved["id"]) is True
    assert agent_profiles.get_agent_profile(saved["id"]) is None
    with pytest.raises(agent_profiles.AgentProfileError):
        agent_profiles.save_agent_profile(slug="reviewer", display_name="Nope")


def test_profile_snapshot_is_immutable_after_edit(tmp_path, monkeypatch):
    agent_profiles, _tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    saved = agent_profiles.save_agent_profile(
        slug="triage_helper",
        display_name="Triage Helper",
        description="Triage bugs.",
        instructions="Version one.",
        tool_policy_json={"capability": "read_only"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    snapshot = agent_profiles.snapshot_agent_profile(saved["id"])

    updated = agent_profiles.save_agent_profile(
        {
            **saved,
            "instructions": "Version two.",
            "description": "Triage bugs quickly.",
        }
    )

    assert snapshot["instructions"] == "Version one."
    assert updated["instructions"] == "Version two."
    assert updated["revision"] == saved["revision"] + 1
    assert snapshot["snapshot_profile_id"] == saved["id"]


def test_profile_resolution_cannot_broaden_parent_approval(tmp_path, monkeypatch):
    agent_profiles, _tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    saved = agent_profiles.save_agent_profile(
        slug="too_open",
        display_name="Too Open",
        description="Requests broader approval.",
        instructions="Try to run broadly.",
        tool_policy_json={"capability": "write_capable"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "single_writer"},
        approval_policy_json={"mode": "allow_all"},
    )

    resolved = agent_profiles.resolve_profile_for_run(
        saved["id"],
        parent_approval_mode="block",
    )

    assert resolved["effective_approval_mode"] == "block"
    assert resolved["warnings"]


def test_thread_profile_pointer_helpers(tmp_path, monkeypatch):
    agent_profiles, _tasks, threads = _fresh_agent_modules(tmp_path, monkeypatch)

    tid = threads.create_thread("Profiled thread")
    stored = threads._set_thread_agent_profile(tid, "reviewer")

    assert stored == {"id": "builtin:reviewer", "slug": "reviewer"}
    assert threads._get_thread_agent_profile(tid) == stored
    detailed = threads._list_threads(include_details=True)
    row = next(item for item in detailed if item[0] == tid)
    assert row[10] == "builtin:reviewer"
    assert row[11] == "reviewer"

    threads._clear_thread_agent_profile(tid)
    assert threads._get_thread_agent_profile(tid) == {"id": "", "slug": ""}
    with pytest.raises(agent_profiles.AgentProfileError):
        threads._set_thread_agent_profile(tid, "missing_profile")


def test_workflow_profile_fields_and_step_resolution(tmp_path, monkeypatch):
    agent_profiles, tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    task_id = tasks.create_task(
        "Review workflow",
        steps=[
            {
                "type": "prompt",
                "prompt": "Review this.",
                "agent_profile_id": "tester",
            }
        ],
        agent_profile_id="reviewer",
    )
    task = tasks.get_task(task_id)

    assert task["agent_profile_id"] == "builtin:reviewer"
    assert task["steps"][0]["agent_profile_id"] == "builtin:tester"
    with pytest.raises(agent_profiles.AgentProfileError):
        tasks.create_task("Bad workflow", prompts=["hi"], agent_profile_id="missing")

