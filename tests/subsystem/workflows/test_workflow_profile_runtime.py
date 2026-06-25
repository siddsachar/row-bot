from __future__ import annotations

import contextvars
import importlib
import sys
import types
from pathlib import Path

import pytest


pytestmark = pytest.mark.subsystem


def _fresh_modules(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
        "row_bot.agent_runs",
        "row_bot.skills",
        "row_bot.agent",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.agent_profiles as agent_profiles
    import row_bot.agent_runs as agent_runs
    import row_bot.skills as skills

    tasks = importlib.reload(tasks)
    threads = importlib.reload(threads)
    agent_profiles = importlib.reload(agent_profiles)
    agent_runs = importlib.reload(agent_runs)
    skills = importlib.reload(skills)
    return tasks, threads, agent_profiles, agent_runs, skills


def _install_fake_agent(monkeypatch, calls: list[dict]) -> None:
    fake_agent = types.ModuleType("row_bot.agent")

    class TaskStoppedError(Exception):
        pass

    def invoke_agent(prompt, enabled_tool_names, config, stop_event=None):
        calls.append(
            {
                "prompt": prompt,
                "tools": list(enabled_tool_names),
                "config": config,
                "stop_event": stop_event,
            }
        )
        return "workflow result"

    fake_agent.TaskStoppedError = TaskStoppedError
    fake_agent.RECURSION_LIMIT_TASK = 12
    fake_agent.invoke_agent = invoke_agent
    fake_agent.resume_invoke_agent = lambda *args, **kwargs: "resumed"
    fake_agent.repair_orphaned_tool_calls = lambda *args, **kwargs: None
    fake_agent._approval_mode_var = contextvars.ContextVar("approval_mode", default="approve")
    fake_agent._background_workflow_var = contextvars.ContextVar(
        "background_workflow",
        default=False,
    )
    fake_agent._persistent_thread_var = contextvars.ContextVar(
        "persistent_thread",
        default=False,
    )
    monkeypatch.setitem(sys.modules, "row_bot.agent", fake_agent)


def _run_workflow_synchronously(monkeypatch, tasks) -> None:
    class ImmediateThread:
        def __init__(self, target, *args, **kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(tasks.threading, "Thread", ImmediateThread)


def test_workflow_run_applies_profile_policy_and_mirrors_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, threads, agent_profiles, agent_runs, _skills = _fresh_modules(
        tmp_path,
        monkeypatch,
    )
    calls: list[dict] = []
    _install_fake_agent(monkeypatch, calls)
    _run_workflow_synchronously(monkeypatch, tasks)
    profile = agent_profiles.save_agent_profile(
        slug="workflow_file_reader",
        display_name="Workflow File Reader",
        description="Read files in workflow runs.",
        instructions="Use only the file reader tools.",
        tool_policy_json={
            "capability": "read_only",
            "allow_tools": ["filesystem"],
        },
        skill_policy_json={"skills_override": ["meeting_notes"]},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    task_id = tasks.create_task(
        "Profile runtime",
        prompts=["summarize files"],
        model_override="model:openai:gpt-4o-mini",
        channels=[],
        agent_profile_id=profile["id"],
        apply_default_skills=False,
    )
    thread_id = threads.create_thread(
        "Workflow run",
        thread_id="workflow-profile-run",
        seed_default_skills=False,
    )

    tasks.run_task_background(
        task_id,
        thread_id,
        ["filesystem", "shell", "row_bot_status"],
        notification=False,
    )

    history = tasks.get_run_history(task_id, limit=1)
    mirrored = agent_runs.get_agent_run(history[0]["id"])

    assert len(calls) == 1
    assert calls[0]["tools"] == ["filesystem"]
    assert calls[0]["config"]["configurable"]["agent_profile_id"] == profile["id"]
    assert calls[0]["config"]["configurable"]["agent_profile_snapshot"]["slug"] == (
        "workflow_file_reader"
    )
    assert calls[0]["config"]["configurable"]["tool_allowlist"] == ["filesystem"]
    assert calls[0]["config"]["configurable"]["model_override"] == (
        "model:openai:gpt-4o-mini"
    )
    assert threads._get_thread_agent_profile(thread_id) == {
        "id": profile["id"],
        "slug": "workflow_file_reader",
    }
    assert threads.get_thread_skills_override(thread_id) == ["meeting_notes"]
    assert history[0]["status"] == "completed"
    assert mirrored["kind"] == "workflow"
    assert mirrored["status"] == "completed"
    assert mirrored["profile_id"] == profile["id"]
    assert mirrored["profile_snapshot_json"]["slug"] == "workflow_file_reader"
    assert mirrored["tools_override"] == ["filesystem"]
    assert mirrored["skills_override"] == ["meeting_notes"]


def test_default_workflow_profile_uses_task_default_skills_without_task_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    tasks, threads, _profiles, _agent_runs, skills = _fresh_modules(
        tmp_path,
        monkeypatch,
    )
    skills.load_skills()
    calls: list[dict] = []
    _install_fake_agent(monkeypatch, calls)
    _run_workflow_synchronously(monkeypatch, tasks)
    task_id = tasks.create_task(
        "Default profile runtime",
        prompts=["use defaults"],
        channels=[],
    )
    task = tasks.get_task(task_id)
    thread_id = threads.create_thread(
        "Default workflow run",
        thread_id="workflow-default-run",
        seed_default_skills=False,
    )

    tasks.run_task_background(
        task_id,
        thread_id,
        ["filesystem", "shell"],
        notification=False,
    )

    assert task["agent_profile_id"] == tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    assert task["skills_override"] is None
    assert calls[0]["config"]["configurable"]["agent_profile_id"] == (
        tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
    )
    assert threads.get_thread_skills_override(thread_id) == ["proactive_agent"]
