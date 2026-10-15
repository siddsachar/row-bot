from __future__ import annotations

import json
from pathlib import Path

import pytest


def _reload_skill_pinning_modules(tmp_path: Path, reload_for_data_dir):
    skills, activation, threads, tasks, _storage = reload_for_data_dir(
        tmp_path,
        "row_bot.skills",
        "row_bot.skills_activation",
        "row_bot.threads",
        "row_bot.tasks",
        "row_bot.developer.storage",
    )
    return skills, activation, threads, tasks


def _reload_status_tool_module(reload_for_data_dir):
    (status_tool,) = reload_for_data_dir(None, "row_bot.tools.row_bot_status_tool")
    return status_tool


def test_default_pin_is_persisted_and_enabled(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)

    skills.load_skills()

    assert skills.is_enabled("proactive_agent") is True
    assert skills.is_pinned("proactive_agent") is True
    assert skills.get_default_active_skill_names("chat") == ["proactive_agent"]
    assert skills.get_default_active_skill_names("task") == ["proactive_agent"]

    saved = json.loads((tmp_path / "skills_config.json").read_text(encoding="utf-8"))
    assert saved["pinned"] == ["proactive_agent"]
    assert saved[skills.SKILL_PINS_CONFIG_KEY] is True


def test_pin_auto_enables_and_disabling_clears_pin(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    skills.set_enabled("meeting_notes", False)
    assert skills.is_enabled("meeting_notes") is False

    skills.set_pinned("meeting_notes", True)
    assert skills.is_enabled("meeting_notes") is True
    assert skills.is_pinned("meeting_notes") is True

    skills.set_enabled("meeting_notes", False)
    assert skills.is_enabled("meeting_notes") is False
    assert skills.is_pinned("meeting_notes") is False
    assert "meeting_notes" not in skills.get_pinned_skill_names()


def test_tool_guides_cannot_be_pinned(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    guide = next(skill for skill in skills.get_all_skills() if skills.is_tool_guide(skill))
    with pytest.raises(ValueError):
        skills.set_pinned(guide.name, True)
    assert guide.name not in skills.get_pinned_skill_names()


def test_new_threads_snapshot_pins_without_retroactive_resolution(tmp_path, reload_for_data_dir):
    skills, activation, threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    legacy_id = threads.create_thread(
        "Legacy thread",
        thread_id="legacy-thread",
        seed_default_skills=False,
    )
    assert activation.resolve_active_skill_names(legacy_id) == []

    new_id = threads.create_thread("New thread", thread_id="new-thread")
    assert activation.resolve_active_skill_names(new_id) == ["proactive_agent"]

    skills.set_pinned("meeting_notes", True)
    assert activation.resolve_active_skill_names(new_id) == ["proactive_agent"]
    assert activation.resolve_active_skill_names(legacy_id) == []

    activation.reset_thread(new_id)
    assert activation.resolve_active_skill_names(new_id) == [
        "proactive_agent",
        "meeting_notes",
    ]


def test_profile_based_tasks_do_not_snapshot_pinned_skills(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    task_id = tasks.create_task(name="Pinned task", prompts=["say hi"])
    task = tasks.get_task(task_id)
    assert task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert task["skills_override"] is None
    assert task["tools_override"] is None

    no_skills_id = tasks.create_task(
        name="No skills task",
        prompts=["say hi"],
        skills_override=[],
    )
    no_skills_task = tasks.get_task(no_skills_id)
    assert no_skills_task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert no_skills_task["skills_override"] is None
    assert no_skills_task["tools_override"] is None

    notify_id = tasks.create_task(
        name="Notify only",
        prompts=[],
        notify_only=True,
    )
    assert tasks.get_task(notify_id)["skills_override"] is None


def test_surface_defaults_are_additive_for_designer_and_developer(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    assert skills.get_default_active_skill_names("designer") == [
        "proactive_agent",
        "design_creator",
    ]
    assert skills.get_default_active_skill_names("developer") == ["proactive_agent"]

    skills.set_pinned("meeting_notes", True)
    assert skills.get_default_active_skill_names("designer") == [
        "proactive_agent",
        "meeting_notes",
        "design_creator",
    ]
    assert skills.get_default_active_skill_names("developer") == [
        "proactive_agent",
        "meeting_notes",
    ]


def test_task_advanced_mode_is_persisted(tmp_path, reload_for_data_dir):
    _skills, _activation, _threads, tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)

    advanced_id = tasks.create_task(
        name="Prompt-only advanced task",
        prompts=["say hi"],
        advanced_mode=True,
    )
    assert tasks.get_task(advanced_id)["advanced_mode"] is True

    tasks.update_task(advanced_id, advanced_mode=False)
    assert tasks.get_task(advanced_id)["advanced_mode"] is False

    stepped_id = tasks.create_task(
        name="Stepped task",
        steps=[{"type": "prompt", "prompt": "say hi"}],
    )
    assert tasks.get_task(stepped_id)["advanced_mode"] is True


def test_custom_tool_builder_uses_tool_guide_not_manual_skill(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    guide = skills.get_skill("custom_tool_builder_guide")
    assert guide is not None
    assert skills.is_tool_guide(guide)
    assert guide.name not in {skill.name for skill in skills.get_manual_skills()}

    with pytest.raises(ValueError):
        skills.set_pinned(guide.name, True)

    assert "Custom Tool Builder Guide" not in skills.get_skills_prompt(
        [],
        active_tool_names=[],
    )
    assert "Custom Tool Builder Guide" in skills.get_skills_prompt(
        [],
        active_tool_names=["custom_tool_builder"],
    )


def test_developer_guidance_is_tool_bound_not_extra_manual_injection(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()
    from row_bot.skill_discovery import tool_skill_records

    regular_prompt = skills.get_skills_prompt([], active_tool_names=[])
    developer_prompt = skills.get_skills_prompt([], active_tool_names=["developer"])

    # F34: the Developer workflows are tool-linked skills that are off by default. They are offered
    # to skill_load while Developer tools are bound, and never injected as tool guides.
    for heading in ("# Developer Coding", "# Developer Review", "# Developer PR Prep", "# Developer Custom Tools"):
        assert heading not in regular_prompt and heading not in developer_prompt
    assert developer_prompt.count("# Developer Tool Guide") == 1
    offered = {record.canonical_id for record in tool_skill_records(["developer"])}
    assert offered == {"developer_coding", "developer_review", "developer_pr_prep", "developer_custom_tools"}
    assert tool_skill_records([]) == []
    assert {skill.name for skill in skills.get_manual_skills()}.isdisjoint(offered)  # Not a manual skill to pin.


def test_row_bot_status_skill_query_reports_pins_and_surface_defaults(tmp_path, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()
    status_mod = _reload_status_tool_module(reload_for_data_dir)

    block = status_mod._row_bot_status("skills")

    assert "**Skill Library**" in block
    assert "available" in block
    assert "pinned" in block
    assert "Default chat skills: Proactive Agent" in block
    assert "Default task skills: Proactive Agent" in block
    assert "Default developer skills: Proactive Agent" in block
    assert "Default designer skills: Proactive Agent, Design Creator" in block
    assert "Proactive Agent: Available (Pinned default)" in block
    assert "Design Creator: Available (Designer default)" in block
    assert "Tool guides are separate tool instructions" in block


def test_row_bot_status_tool_can_pin_and_unpin_skills(tmp_path, monkeypatch, reload_for_data_dir):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()
    status_mod = _reload_status_tool_module(reload_for_data_dir)

    import row_bot.tools.approval_gate as approval_gate

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "full")
    tools = {tool.name: tool for tool in status_mod.RowBotStatusTool().as_langchain_tools()}
    update_tool = tools["row_bot_update_setting"]
    query_tool = tools["row_bot_status"]

    pin_result = update_tool.invoke({"setting": "skill_pin", "value": "Meeting Notes:on"})
    assert "Meeting Notes" in pin_result
    assert "now pinned" in pin_result
    assert skills.is_enabled("meeting_notes") is True
    assert skills.is_pinned("meeting_notes") is True

    query_result = query_tool.invoke({"category": "skills"})
    assert "Default chat skills: Proactive Agent, Meeting Notes" in query_result
    assert "Meeting Notes: Available (Pinned default)" in query_result

    unpin_result = update_tool.invoke({"setting": "skill_pin", "value": "meeting_notes:off"})
    assert "no longer pinned" in unpin_result
    assert skills.is_enabled("meeting_notes") is True
    assert skills.is_pinned("meeting_notes") is False

    skills.set_pinned("meeting_notes", True)
    off_result = update_tool.invoke({"setting": "skill_toggle", "value": "meeting_notes:off"})
    assert "Off and no longer pinned" in off_result
    assert skills.is_enabled("meeting_notes") is False
    assert skills.is_pinned("meeting_notes") is False
