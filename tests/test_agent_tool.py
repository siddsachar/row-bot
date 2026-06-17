from __future__ import annotations

import importlib
import json
import sys


def _fresh_agent_tool_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
        "row_bot.agent_runs",
        "row_bot.agent_context",
        "row_bot.agent_runner",
        "row_bot.tools.agent_tool",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.agent_runs as agent_runs
    import row_bot.tools.agent_tool as agent_tool

    tasks = importlib.reload(tasks)
    agent_runs = importlib.reload(agent_runs)
    agent_tool = importlib.reload(agent_tool)
    return agent_tool, agent_runs


def test_agents_tool_registers_expected_subtools(tmp_path, monkeypatch):
    agent_tool, _agent_runs = _fresh_agent_tool_modules(tmp_path, monkeypatch)
    from row_bot.tools import registry

    registered = registry.get_tool("agents")
    assert registered is not None
    names = {tool.name for tool in registered.as_langchain_tools()}

    assert {
        "delegate_work",
        "agent_status",
        "agent_wait",
        "agent_stop",
        "agent_profiles",
        "agent_profile_save",
        "agent_message",
        "agent_promote",
    } <= names
    assert registered.destructive_tool_names == {"agent_profile_save", "agent_promote"}
    assert registered.enabled_by_default is True


def test_delegate_work_uses_runner_and_returns_public_run(tmp_path, monkeypatch):
    agent_tool, _agent_runs = _fresh_agent_tool_modules(tmp_path, monkeypatch)
    calls = {}

    def fake_spawn(objective, **kwargs):
        calls["objective"] = objective
        calls["kwargs"] = kwargs
        return {
            "id": "run-1",
            "kind": "subagent",
            "status": "queued",
            "display_name": "Reviewer",
            "thread_id": "child-thread",
            "parent_thread_id": kwargs["parent_thread_id"],
            "profile_id": "builtin:reviewer",
            "profile_slug": "reviewer",
            "profile_display_name": "Reviewer",
        }

    monkeypatch.setattr(agent_tool.agent_runner, "spawn_agent_run", fake_spawn)

    payload = json.loads(agent_tool._delegate_work(
        objective="Review the diff.",
        profile="reviewer",
        context="Changed files: app.py",
        parent_thread_id="parent-thread",
        wait=False,
    ))

    assert payload["ok"] is True
    assert payload["run"]["id"] == "run-1"
    assert payload["run"]["profile"]["slug"] == "reviewer"
    assert calls["objective"] == "Review the diff."
    assert calls["kwargs"]["profile"] == "reviewer"
    assert calls["kwargs"]["context"] == "Changed files: app.py"
    assert calls["kwargs"]["parent_thread_id"] == "parent-thread"


def test_delegate_work_wait_timeout_message_is_explicit(tmp_path, monkeypatch):
    agent_tool, _agent_runs = _fresh_agent_tool_modules(tmp_path, monkeypatch)

    def fake_spawn(objective, **kwargs):
        return {
            "id": "run-timeout",
            "kind": "subagent",
            "status": "running",
            "display_name": "Slow Agent",
        }

    monkeypatch.setattr(agent_tool.agent_runner, "spawn_agent_run", fake_spawn)

    payload = json.loads(agent_tool._delegate_work(
        objective="Do slow work.",
        parent_thread_id="parent-thread",
        wait=True,
        timeout_seconds=0.01,
    ))

    assert payload["message"] == "Child Agent is still running after the wait timeout."


def test_agent_status_profiles_and_profile_save(tmp_path, monkeypatch):
    agent_tool, agent_runs = _fresh_agent_tool_modules(tmp_path, monkeypatch)

    run = agent_runs.create_agent_run(
        run_id="status-run",
        kind="subagent",
        status="completed",
        parent_thread_id="parent-thread",
        thread_id="child-thread",
        display_name="Status Run",
        profile_id="reviewer",
        summary="Looks good.",
    )
    agent_runs.append_agent_event(run["id"], "summary.updated", {"summary": "Looks good."})
    agent_runs.append_agent_parent_message(run["id"], "Prefer concise evidence.")

    status_payload = json.loads(agent_tool._agent_status(
        run_id="status-run",
        include_events=True,
    ))
    assert status_payload["ok"] is True
    assert status_payload["run"]["status"] == "completed"
    assert status_payload["run"]["parent_message_count"] == 1
    assert status_payload["run"]["latest_parent_message"] == "Prefer concise evidence."
    assert any(event["type"] == "summary.updated" for event in status_payload["events"])

    list_payload = json.loads(agent_tool._agent_status(parent_thread_id="parent-thread"))
    assert [item["id"] for item in list_payload["runs"]] == ["status-run"]

    profiles_payload = json.loads(agent_tool._agent_profiles(query="reviewer"))
    assert any(profile["slug"] == "reviewer" for profile in profiles_payload["profiles"])

    saved_payload = json.loads(agent_tool._agent_profile_save(
        slug="release_reviewer",
        display_name="Release Reviewer",
        description="Review releases.",
        when_to_use="Before shipping.",
        instructions="Review release risk.",
        allow_tools=["filesystem"],
        skills=["release_notes"],
    ))
    assert saved_payload["ok"] is True
    assert saved_payload["profile"]["slug"] == "release_reviewer"
    assert saved_payload["profile"]["allow_tools"] == ["filesystem"]
    assert saved_payload["profile"]["skills"] == ["release_notes"]


def test_agent_promote_creates_profile_and_disabled_workflow(tmp_path, monkeypatch):
    agent_tool, agent_runs = _fresh_agent_tool_modules(tmp_path, monkeypatch)
    import row_bot.tasks as tasks

    run = agent_runs.create_agent_run(
        run_id="promote-run",
        kind="subagent",
        status="completed",
        display_name="Release Check",
        prompt="Review the release checklist.",
        context_summary="Changed files: release.py",
        profile_id="reviewer",
        model_override="",
        tools_override=["filesystem"],
        skills_override=["release_notes"],
        approval_mode="approve",
        summary="Release checklist passed.",
    )

    profile_payload = json.loads(agent_tool._agent_promote(run["id"], target="profile"))
    assert profile_payload["ok"] is True
    assert profile_payload["profile"]["slug"] == "promoted_promote_run"

    workflow_payload = json.loads(agent_tool._agent_promote(run["id"], target="workflow"))
    assert workflow_payload["ok"] is True
    workflow = workflow_payload["workflow"]
    task = tasks.get_task(workflow["id"])

    assert workflow["enabled"] is False
    assert task["enabled"] is False
    assert task["advanced_mode"] is True
    assert task["agent_profile_id"] == "builtin:reviewer"
    assert task["tools_override"] == ["filesystem"]
    assert task["skills_override"] == ["release_notes"]
    assert task["safety_mode"] == "approve"
    assert "Review the release checklist." in task["steps"][0]["prompt"]
    assert "Release checklist passed." in task["steps"][0]["prompt"]


def test_agent_message_records_parent_steering_for_nonterminal_run(tmp_path, monkeypatch):
    agent_tool, agent_runs = _fresh_agent_tool_modules(tmp_path, monkeypatch)
    queued = agent_runs.create_agent_run(
        run_id="queued-message",
        kind="subagent",
        status="queued",
        display_name="Queued Message",
    )

    payload = json.loads(agent_tool._agent_message(
        queued["id"],
        "Prefer the smaller refactor.",
    ))

    assert payload["ok"] is True
    assert payload["run"]["status_message"] == "Parent message queued"
    events = agent_runs.get_agent_events(queued["id"])
    assert events[-2]["type"] == "parent.message"
    assert events[-2]["payload_json"]["message"] == "Prefer the smaller refactor."

    agent_runs.finish_agent_run(queued["id"], "completed", summary="Done")
    terminal = json.loads(agent_tool._agent_message(queued["id"], "Too late"))
    assert terminal["ok"] is False
    assert "cannot be steered" in terminal["message"]


def test_agents_guide_is_parent_tool_guide():
    text = open("tool_guides/agents_guide/SKILL.md", encoding="utf-8").read()

    assert "name: agents_guide" in text
    assert "tools:\n  - agents" in text
    assert "delegate_work" in text
    assert "agent_profile_save" in text
    assert "agent_message" in text
    assert "workflow" in text
