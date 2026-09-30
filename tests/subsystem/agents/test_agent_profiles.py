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
    agent_profiles, tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    profiles = agent_profiles.list_agent_profiles()
    slugs = {profile["slug"] for profile in profiles}

    expected = {
        "row_bot_default",
        "plan",
        "research",
        "write",
        "ideas",
        "knowledge",
        "data",
        "automate",
        "review",
        "design",
        "develop",
        "code_review",
        "ui_check",
        "worker",
        "synthesize",
        "verify",
    }
    old_visible = {
        "planner",
        "researcher",
        "writer_editor",
        "learning_coach",
        "brainstormer",
        "career_guide",
        "life_admin",
        "meeting_followup",
        "knowledge_librarian",
        "data_analyst",
        "creative_designer",
        "automation_builder",
        "quality_reviewer",
        "synthesizer",
        "verifier",
        "code_reviewer",
        "web_ui_checker",
        "explorer",
        "docs_researcher",
        "tester",
        "browser_debugger",
        "reviewer",
    }
    assert slugs == expected
    assert old_visible.isdisjoint(slugs)

    quality_reviewer = agent_profiles.require_agent_profile("Quality Reviewer")
    verifier = agent_profiles.require_agent_profile("verifier")
    researcher = agent_profiles.require_agent_profile("researcher")
    librarian = agent_profiles.require_agent_profile("knowledge_librarian")
    assert quality_reviewer["slug"] == "review"
    assert verifier["slug"] == "verify"
    assert researcher["slug"] == "research"
    assert librarian["slug"] == "knowledge"
    assert quality_reviewer["tool_policy_json"]["capability"] == "read_only"
    assert verifier["tool_policy_json"]["capability"] == "write_capable"
    assert "memory" in researcher["tool_policy_json"]["allow_tools"]
    assert "memory" in librarian["tool_policy_json"]["allow_tools"]
    assert agent_profiles.require_agent_profile("default")["slug"] == "row_bot_default"

    inherited = {
        profile["slug"]
        for profile in profiles
        if not profile["tool_policy_json"].get("allow_tools")
    }
    assert inherited == {"row_bot_default", "worker"}
    common_tools = {
        "memory",
        "row_bot_status",
        "conversation_search",
        "duckduckgo",
        "web_search",
        "url_reader",
        "filesystem",
        "shell",
    }
    explicit_profiles = [
        profile
        for profile in profiles
        if profile["slug"] not in {"row_bot_default", "worker"}
    ]
    for profile in explicit_profiles:
        allow_tools = set(profile["tool_policy_json"].get("allow_tools") or [])
        assert common_tools <= allow_tools, profile["slug"]
    system_info_profiles = {
        profile["slug"]
        for profile in explicit_profiles
        if "system_info" in (profile["tool_policy_json"].get("allow_tools") or [])
    }
    assert system_info_profiles == {"develop", "code_review", "ui_check", "verify"}
    data_tools = {profile["slug"]: profile for profile in profiles}
    assert "wolfram_alpha" in data_tools["data"]["tool_policy_json"]["allow_tools"]
    assert "wolfram" not in data_tools["data"]["tool_policy_json"]["allow_tools"]
    expected_skills = {
        "row_bot_default": [],
        "plan": ["brain_dump", "task_automation"],
        "research": ["deep_research", "web_navigator"],
        "write": ["humanizer", "meeting_notes"],
        "ideas": [],
        "knowledge": ["knowledge_base", "self_reflection", "brain_dump"],
        "data": ["data_analyst"],
        "automate": ["task_automation"],
        "review": [],
        "design": ["design_creator"],
        "develop": [],
        "code_review": [],
        "ui_check": ["web_navigator"],
        "worker": [],
        "synthesize": [],
        "verify": [],
    }
    assert {
        profile["slug"]: profile["skill_policy_json"].get("skills_override") or []
        for profile in profiles
    } == expected_skills
    for profile in profiles:
        assert "memory_policy_json" not in profile
        assert "deny_memory_write" not in profile["tool_policy_json"]
        assert "allow_tool_groups" not in profile["tool_policy_json"]
        assert "include_memory" not in profile["context_policy_json"]
    groups = {profile["slug"]: profile["ui_json"].get("group") for profile in profiles}
    assert groups["plan"] == "Everyday"
    assert groups["review"] == "Work"
    assert groups["design"] == "Creative"
    assert {slug for slug, group in groups.items() if group == "Developer"} == {
        "develop",
        "code_review",
        "ui_check",
    }
    assert {slug for slug, group in groups.items() if group == "Advanced/Internal"} == {
        "worker",
        "synthesize",
        "verify",
    }

    conn = tasks._get_conn()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_profiles)").fetchall()}
    finally:
        conn.close()
    assert "memory_policy_json" not in columns


def test_db_backed_profile_crud_and_duplicate_builtin(tmp_path, monkeypatch):
    agent_profiles, _tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    saved = agent_profiles.save_agent_profile(
        slug="release_reviewer",
        display_name="Release Reviewer",
        description="Review release notes and packaging risk.",
        instructions="Review release assets and summarize risk.",
        tool_policy_json={
            "capability": "read_only",
            "allow_tools": ["filesystem", "filesystem"],
            "allow_tool_groups": ["core"],
            "deny_memory_write": True,
        },
        context_policy_json={"default_context_mode": "focused", "include_memory": True},
        workspace_policy_json={"workspace_mode_default": "read_only"},
        approval_policy_json={"mode": "inherit"},
        memory_policy_json={"mode": "none", "deny_memory_write": True},
    )

    assert saved["scope"] == "user"
    assert agent_profiles.require_agent_profile("release_reviewer")["id"] == saved["id"]
    assert saved["tool_policy_json"]["allow_tools"] == ["filesystem"]
    assert "deny_memory_write" not in saved["tool_policy_json"]
    assert "allow_tool_groups" not in saved["tool_policy_json"]
    assert "include_memory" not in saved["context_policy_json"]
    assert "memory_policy_json" not in saved

    duplicated = agent_profiles.duplicate_agent_profile(
        "quality_reviewer",
        {"slug": "custom_reviewer", "display_name": "Custom Reviewer"},
    )

    assert duplicated["slug"] == "custom_reviewer"
    assert duplicated["source"] == "user_created"
    assert duplicated["provenance_json"]["duplicated_from_profile_slug"] == "review"

    assert agent_profiles.delete_agent_profile(saved["id"]) is True
    assert agent_profiles.get_agent_profile(saved["id"]) is None
    with pytest.raises(agent_profiles.AgentProfileError):
        agent_profiles.save_agent_profile(slug="review", display_name="Nope")

    alias_override = agent_profiles.save_agent_profile(
        slug="quality_reviewer",
        display_name="Alias Override",
        description="DB exact slug should win over folded built-in aliases.",
        instructions="Use the user-created profile.",
        tool_policy_json={"capability": "read_only"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    assert agent_profiles.require_agent_profile("quality_reviewer")["id"] == alias_override["id"]


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
    stored = threads._set_thread_agent_profile(tid, "quality_reviewer")

    assert stored == {"id": "builtin:review", "slug": "review"}
    assert threads._get_thread_agent_profile(tid) == stored
    detailed = threads._list_threads(include_details=True)
    row = next(item for item in detailed if item[0] == tid)
    assert row[10] == "builtin:review"
    assert row[11] == "review"

    threads._clear_thread_agent_profile(tid)
    assert threads._get_thread_agent_profile(tid) == {"id": "", "slug": ""}
    with pytest.raises(agent_profiles.AgentProfileError):
        threads._set_thread_agent_profile(tid, "missing_profile")


def test_workflow_profile_fields_strip_step_profile_policy(tmp_path, monkeypatch):
    agent_profiles, tasks, _threads = _fresh_agent_modules(tmp_path, monkeypatch)

    task_id = tasks.create_task(
        "Review workflow",
        steps=[
            {
                "type": "prompt",
                "prompt": "Review this.",
                "agent_profile_id": "verifier",
            }
        ],
        agent_profile_id="quality_reviewer",
    )
    task = tasks.get_task(task_id)

    assert task["agent_profile_id"] == "builtin:review"
    assert "agent_profile_id" not in task["steps"][0]
    with pytest.raises(agent_profiles.AgentProfileError):
        tasks.create_task("Bad workflow", prompts=["hi"], agent_profile_id="missing")

