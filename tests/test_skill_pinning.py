from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _reload_skill_pinning_modules(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    modules = (
        "row_bot.skills",
        "row_bot.skills_activation",
        "row_bot.threads",
        "row_bot.tasks",
        "row_bot.developer.storage",
    )
    for name in modules:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)

    import row_bot.skills as skills
    import row_bot.skills_activation as activation
    import row_bot.threads as threads
    import row_bot.tasks as tasks

    return skills, activation, threads, tasks


def _reload_status_tool_module():
    name = "row_bot.tools.row_bot_status_tool"
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def test_default_pin_is_persisted_and_enabled(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)

    skills.load_skills()

    assert skills.is_enabled("proactive_agent") is True
    assert skills.is_pinned("proactive_agent") is True
    assert skills.get_default_active_skill_names("chat") == ["proactive_agent"]
    assert skills.get_default_active_skill_names("task") == ["proactive_agent"]

    saved = json.loads((tmp_path / "skills_config.json").read_text(encoding="utf-8"))
    assert saved["pinned"] == ["proactive_agent"]
    assert saved[skills.SKILL_PINS_CONFIG_KEY] is True


def test_pin_auto_enables_and_disabling_clears_pin(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
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


def test_tool_guides_cannot_be_pinned(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
    skills.load_skills()

    guide = next(skill for skill in skills.get_all_skills() if skills.is_tool_guide(skill))
    with pytest.raises(ValueError):
        skills.set_pinned(guide.name, True)
    assert guide.name not in skills.get_pinned_skill_names()


def test_new_threads_snapshot_pins_without_retroactive_resolution(tmp_path, monkeypatch):
    skills, activation, threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
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


def test_profile_based_tasks_do_not_snapshot_pinned_skills(tmp_path, monkeypatch):
    skills, _activation, _threads, tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
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


def test_surface_defaults_are_additive_for_designer_and_developer(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
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

    home_source = Path("src/row_bot/ui/home.py").read_text(encoding="utf-8")
    assert 'get_default_active_skill_names("designer")' in home_source
    assert "set_thread_skills_override(tid, default_designer_skills)" in home_source


def test_task_advanced_mode_is_persisted(tmp_path, monkeypatch):
    _skills, _activation, _threads, tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)

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

    dialog_source = Path("src/row_bot/ui/task_dialog.py").read_text(encoding="utf-8")
    assert "task.get(\"advanced_mode\")" in dialog_source
    assert "advanced_mode=is_advanced" in dialog_source


def test_workflow_dialog_is_profile_first_source_contract() -> None:
    dialog_source = Path("src/row_bot/ui/task_dialog.py").read_text(encoding="utf-8")

    assert "Agent Profile" in dialog_source
    assert "agent_profile_id=cur_agent_profile_id" in dialog_source
    assert "apply_default_skills=False" in dialog_source
    assert "Tools override" not in dialog_source
    assert "Skills override" not in dialog_source
    assert "tools_override" not in dialog_source
    assert "skills_override" not in dialog_source


def test_developer_threads_snapshot_pinned_skills(tmp_path, monkeypatch):
    skills, _activation, threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
    skills.load_skills()

    from row_bot.developer import storage

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    workspace = storage.add_or_update_local_workspace(str(workspace_dir))

    thread_id = storage.create_workspace_thread(workspace.id, name="Dev thread")

    assert threads.get_thread_skills_override(thread_id) == ["proactive_agent"]


def test_custom_tool_builder_uses_tool_guide_not_manual_skill(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
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


def test_developer_guidance_is_tool_bound_not_extra_manual_injection(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
    skills.load_skills()

    regular_prompt = skills.get_skills_prompt([], active_tool_names=[])
    developer_prompt = skills.get_skills_prompt([], active_tool_names=["developer"])

    assert "Developer Coding" not in regular_prompt
    assert developer_prompt.count("# Developer Coding") == 1
    assert developer_prompt.count("# Developer Review") == 1
    assert developer_prompt.count("# Developer PR Prep") == 1
    assert developer_prompt.count("# Developer Custom Tools") == 1

    agent_source = Path("src/row_bot/agent.py").read_text(encoding="utf-8")
    assert "DEVELOPER_AUTO_SKILLS" not in agent_source
    assert "extra_skill_names=DEVELOPER_AUTO_SKILLS" not in agent_source


def test_row_bot_status_skill_query_reports_pins_and_surface_defaults(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
    skills.load_skills()
    status_mod = _reload_status_tool_module()

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


def test_row_bot_status_tool_can_pin_and_unpin_skills(tmp_path, monkeypatch):
    skills, _activation, _threads, _tasks = _reload_skill_pinning_modules(tmp_path, monkeypatch)
    skills.load_skills()
    status_mod = _reload_status_tool_module()

    import row_bot.tools.approval_gate as approval_gate

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "allow_all")
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


def test_channel_thread_creation_opts_into_default_skill_seeding():
    channel_paths = [
        Path("src/row_bot/channels/discord_channel.py"),
        Path("src/row_bot/channels/sms.py"),
        Path("src/row_bot/channels/slack.py"),
        Path("src/row_bot/channels/telegram.py"),
        Path("src/row_bot/channels/whatsapp.py"),
    ]

    for path in channel_paths:
        source = path.read_text(encoding="utf-8")
        assert "seed_default_skills=True" in source, path
