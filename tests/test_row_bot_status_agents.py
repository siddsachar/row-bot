from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _fresh_status_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
        "row_bot.agent_runs",
        "row_bot.goals",
        "row_bot.agent",
        "row_bot.tools.row_bot_status_tool",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.agent_profiles as agent_profiles
    import row_bot.agent_runs as agent_runs
    import row_bot.goals as goals
    import row_bot.agent as agent
    import row_bot.tools.row_bot_status_tool as row_bot_status_tool

    tasks = importlib.reload(tasks)
    threads = importlib.reload(threads)
    agent_profiles = importlib.reload(agent_profiles)
    agent_runs = importlib.reload(agent_runs)
    goals = importlib.reload(goals)
    agent = importlib.reload(agent)
    row_bot_status_tool = importlib.reload(row_bot_status_tool)
    return threads, agent_profiles, agent_runs, goals, agent, row_bot_status_tool


def test_row_bot_status_reports_agents_profiles_and_goals(tmp_path, monkeypatch):
    threads, profiles, agent_runs, goals, agent, status_tool = _fresh_status_modules(
        tmp_path,
        monkeypatch,
    )
    thread_id = threads.create_thread("Status thread")
    threads._set_thread_agent_profile(thread_id, "reviewer")
    profiles.save_agent_profile(
        slug="disabled_release_reviewer",
        display_name="Disabled Release Reviewer",
        description="Review releases when enabled.",
        instructions="Review release risks.",
        tool_policy_json={"capability": "read_only"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
        enabled=False,
    )
    agent_runs.create_agent_run(
        run_id="status-run-1",
        kind="subagent",
        status="running",
        status_message="Working through a delegated task",
        parent_thread_id=thread_id,
        thread_id="child-thread",
        profile_id="reviewer",
        display_name="Status child",
        max_turns=3,
        turns_used=1,
    )
    assert agent_runs.acquire_agent_write_lock(
        "thread:status",
        "status-run-1",
        thread_id="child-thread",
        workspace_path=str(tmp_path),
    )
    goal = goals.start_goal(thread_id, "ship status coverage", max_turns=5)
    goals.update_goal_progress(
        goal_id=goal["id"],
        progress="Seeded the status checks",
        evidence=["tests/test_row_bot_status_agents.py"],
        next_step="Run focused pytest",
    )
    goals.pause_goal(thread_id, reason="Waiting on status smoke")
    agent._set_active_runtime_context(
        thread_id=thread_id,
        enabled_tool_names=[],
        tool_allowlist=["conversation_search", "filesystem", "memory", "row_bot_status", "system_info"],
        agent_profile_id="reviewer",
    )

    agents = status_tool._row_bot_status("agents")
    assert "**Agents**" in agents
    assert "Current runs:" in agents
    assert "Recent runs:" not in agents
    assert "Status child" in agents
    assert "subagent/running" in agents
    assert "thread:status" in agents
    assert "max concurrent 3" in agents

    agent_profiles = status_tool._row_bot_status("agent_profiles")
    assert "**Agent Profiles**" in agent_profiles
    assert "Active thread profile: Reviewer" in agent_profiles
    assert "builtin " in agent_profiles
    assert "user_created 1" in agent_profiles
    assert "enabled /" in agent_profiles
    assert "total" in agent_profiles
    assert "Tool modes:" in agent_profiles
    assert "selected tools" in agent_profiles
    assert "Selected tool sources:" in agent_profiles
    assert "core=" in agent_profiles
    assert "Active profile tool mode: selected tools" in agent_profiles
    assert "Active profile selected tools:" in agent_profiles
    assert "(filesystem)" in agent_profiles
    assert "(row_bot_status)" in agent_profiles
    assert "runtime-bound" in agent_profiles
    assert "other global tools are not bound" in agent_profiles

    goal_status = status_tool._row_bot_status("goals")
    assert "**Goals**" in goal_status
    assert "Current goals:" in goal_status
    assert "Recent goals:" not in goal_status
    assert "ship status coverage" in goal_status
    assert "paused" in goal_status
    assert "Seeded the status checks" in goal_status
    assert "Default turn budget: 20" in goal_status


def test_row_bot_status_agent_goal_categories_are_discoverable(tmp_path, monkeypatch):
    *_modules, status_tool = _fresh_status_modules(tmp_path, monkeypatch)

    assert {"agents", "agent_profiles", "goals"} <= set(status_tool._QUERY_HANDLERS)
    tool = next(
        item
        for item in status_tool.RowBotStatusTool().as_langchain_tools()
        if item.name == "row_bot_status"
    )
    assert "agents, agent_profiles, goals" in tool.description

    guide = Path("tool_guides/row_bot_status_guide/SKILL.md").read_text(encoding="utf-8").lower()
    assert "category='agents'" in guide
    assert "category='agent_profiles'" in guide
    assert "category='goals'" in guide
    assert "current durable agent runs" in guide
    assert "current goal mode status" in guide
    assert "global enabled/disabled tools" in guide
    assert "effective thread tool scope" in guide
    assert "runtime-bound" in guide
    assert "read-only through row_bot_status in v1" in guide

    manifest_source = Path("scripts/app_payload_manifest.py").read_text(encoding="utf-8")
    assert '"tool_guides"' in manifest_source


def test_row_bot_status_agents_goals_empty_state_and_overview(tmp_path, monkeypatch):
    *_modules, status_tool = _fresh_status_modules(tmp_path, monkeypatch)

    agents = status_tool._row_bot_status("agents")
    assert "No durable Agent Runs recorded." in agents
    assert "max concurrent 3" in agents

    goals = status_tool._row_bot_status("goals")
    assert "No Goal Mode records yet." in goals
    assert "Default turn budget: 20" in goals

    overview = status_tool._row_bot_status("overview")
    assert "**Agents**" in overview
    assert "**Agent Profiles**" in overview
    assert "**Goals**" in overview


def test_row_bot_status_tools_distinguishes_global_and_effective_profile_scope(tmp_path, monkeypatch):
    threads, _profiles, _agent_runs, _goals, agent, status_tool = _fresh_status_modules(
        tmp_path,
        monkeypatch,
    )
    thread_id = threads.create_thread("Browser status thread")
    threads._set_thread_agent_profile(thread_id, "browser_debugger")
    agent._set_active_runtime_context(
        thread_id=thread_id,
        enabled_tool_names=[],
        tool_allowlist=["browser", "filesystem", "row_bot_status", "system_info", "vision"],
        agent_profile_id="browser_debugger",
    )

    tools = status_tool._row_bot_status("tools")

    assert "**Tools**" in tools
    assert "Global catalog:" in tools
    assert "**Effective Thread Tool Scope**" in tools
    assert "Active profile: Browser Debugger" in tools
    assert "selected tools are runtime-bound" in tools
    assert "other global tools are not bound" in tools
    assert "Effective tools:" in tools
    for tool_id in ("browser", "filesystem", "row_bot_status", "system_info", "vision"):
        assert f"({tool_id})" in tools


def test_row_bot_status_tools_keeps_global_catalog_without_profile_allowlist(tmp_path, monkeypatch):
    *_modules, status_tool = _fresh_status_modules(tmp_path, monkeypatch)

    tools = status_tool._row_bot_status("tools")

    assert "**Tools**" in tools
    assert "Global catalog:" in tools
    assert "**Effective Thread Tool Scope**" not in tools
    assert "runtime-bound" not in tools
