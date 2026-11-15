from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.helpers.starred_models import starred_profile


def _fresh_status_modules(tmp_path, reload_for_data_dir):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _tasks, *modules = reload_for_data_dir(
        data_dir,
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
        "row_bot.agent_runs",
        "row_bot.goals",
        "row_bot.agent",
        "row_bot.tools.row_bot_status_tool",
    )
    return tuple(modules)


@pytest.mark.slow
def test_row_bot_status_reports_agents_profiles_and_goals(tmp_path, reload_for_data_dir):
    threads, profiles, agent_runs, goals, agent, status_tool = _fresh_status_modules(
        tmp_path,
        reload_for_data_dir,
    )
    thread_id = threads.create_thread("Status thread")
    threads._set_thread_agent_profile(thread_id, "quality_reviewer")
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
        profile_id="quality_reviewer",
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
        tool_allowlist=[
            "conversation_search",
            "documents",
            "filesystem",
            "memory",
            "row_bot_status",
            "system_info",
            "url_reader",
        ],
        agent_profile_id="review",
    )

    agents = status_tool._row_bot_status("agents")
    assert "**Agents**" in agents
    assert "Current runs:" in agents
    assert "Recent runs:" not in agents
    assert "Status child" in agents
    assert "subagent/running" in agents
    assert "thread:status" in agents
    assert "3 active per parent" in agents
    assert "Dispatcher: 0 active, 0 queued" in agents

    agent_profiles = status_tool._row_bot_status("agent_profiles")
    assert "**Agent Profiles**" in agent_profiles
    assert "Active thread profile: Review" in agent_profiles
    assert "builtin " in agent_profiles
    assert "user_created 1" in agent_profiles
    assert "enabled /" in agent_profiles
    assert "total" in agent_profiles
    assert "Profiles list no tools" in agent_profiles
    assert "Active thread profile: Review (review, enabled, read_only)" in agent_profiles
    assert "selected tools" not in agent_profiles

    goal_status = status_tool._row_bot_status("goals")
    assert "**Goals**" in goal_status
    assert "Current goals:" in goal_status
    assert "Recent goals:" not in goal_status
    assert "ship status coverage" in goal_status
    assert "paused" in goal_status
    assert "Seeded the status checks" in goal_status
    assert "Default turn limit: none" in goal_status


def test_row_bot_status_reports_plugin_tools_and_stale_plugins(tmp_path, monkeypatch, reload_for_data_dir):
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)

    from row_bot.plugins import loader as plugin_loader
    from row_bot.plugins import registry as plugin_registry
    from row_bot.plugins import state as plugin_state

    manifest = SimpleNamespace(
        id="rss-reader",
        name="RSS Reader",
        version="1.0.0",
    )
    monkeypatch.setattr(plugin_registry, "get_loaded_manifests", lambda: [manifest])
    monkeypatch.setattr(plugin_state, "is_plugin_enabled", lambda plugin_id: plugin_id == "rss-reader")
    monkeypatch.setattr(
        plugin_registry,
        "get_enabled_plugin_tool_records",
        lambda: [{
            "runtime_name": "rss_fetch_feed",
            "plugin_id": "rss-reader",
            "plugin_name": "RSS Reader",
            "label": "Fetch Feed",
            "description": "Read RSS feeds",
            "destructive": False,
        }],
    )
    stale = plugin_loader.LoadResult(
        plugin_id="old-thoth",
        success=True,
        stale=True,
        stale_path=str(tmp_path / "data" / "stale_plugins" / "old-thoth"),
    )
    monkeypatch.setattr(
        plugin_loader,
        "get_load_summary",
        lambda: {"total": 2, "loaded": 1, "failed": 0, "stale": 1, "results": [stale]},
    )

    plugins = status_tool._row_bot_status("plugins")
    tools = status_tool._row_bot_status("tools")

    assert "**Plugins**" in plugins
    assert "RSS Reader (rss-reader) v1.0.0: enabled" in plugins
    assert "RSS Reader: Fetch Feed (rss_fetch_feed)" in plugins
    assert "old-thoth" in plugins
    assert "Plugin tools:" in tools
    assert "rss_fetch_feed" in tools


@pytest.mark.slow
def test_row_bot_status_agents_goals_empty_state_and_overview(tmp_path, reload_for_data_dir):
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)

    agents = status_tool._row_bot_status("agents")
    assert "No durable Agent Runs recorded." in agents
    assert "3 active per parent" in agents

    goals = status_tool._row_bot_status("goals")
    assert "No Goal Mode records yet." in goals
    assert "Default turn limit: none" in goals

    # The overview stays short (G8) and points to each area instead of repeating it.
    overview = status_tool._row_bot_status("overview")
    assert overview.startswith("**Row-Bot v")
    assert "**Agents**" not in overview and "agents, goals" in overview


def test_row_bot_status_tools_names_the_profile_and_the_turns_runtime_list(tmp_path, reload_for_data_dir):
    threads, _profiles, _agent_runs, _goals, agent, status_tool = _fresh_status_modules(
        tmp_path,
        reload_for_data_dir,
    )
    thread_id = threads.create_thread("Browser status thread")
    threads._set_thread_agent_profile(thread_id, "web_ui_checker")
    agent._set_active_runtime_context(
        thread_id=thread_id,
        enabled_tool_names=[],
        tool_allowlist=["browser", "filesystem", "row_bot_status", "system_info", "vision"],
        agent_profile_id="ui_check",
    )

    tools = status_tool._row_bot_status("tools")

    assert "**Tools**" in tools
    assert "Global catalog:" in tools
    assert "**Effective Thread Tool Scope**" in tools
    assert "Active profile: UI Check" in tools
    assert "Profiles list no tools" in tools
    assert "Runtime allow-list for this turn:" in tools
    assert "skill_search and skill_load" not in tools
    for tool_id in ("browser", "filesystem", "row_bot_status", "system_info", "vision"):
        assert f"({tool_id})" in tools


@pytest.mark.parametrize("runtime_surface", ["agent_child", "agent_child_resume"])
def test_row_bot_status_tools_explains_separate_bounded_child_skill_bridges(
    tmp_path,
    reload_for_data_dir,
    runtime_surface,
):
    threads, _profiles, _agent_runs, _goals, agent, status_tool = _fresh_status_modules(
        tmp_path,
        reload_for_data_dir,
    )
    thread_id = threads.create_thread("Restricted child status")
    threads._set_thread_agent_profile(thread_id, "web_ui_checker")
    agent._set_active_runtime_context(
        thread_id=thread_id,
        enabled_tool_names=[],
        tool_allowlist=["browser", "row_bot_status"],
        agent_profile_id="ui_check",
        runtime_surface=runtime_surface,
    )

    tools = status_tool._row_bot_status("tools")

    assert "Runtime allow-list for this turn:" in tools
    assert "skill_search and skill_load" in tools
    assert "separate from the tools" in tools
    assert "cannot add tools" in tools
    assert "profile, approval, or workspace boundaries" in tools


def test_row_bot_status_tools_keeps_global_catalog_without_profile_allowlist(tmp_path, reload_for_data_dir):
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)

    tools = status_tool._row_bot_status("tools")

    assert "**Tools**" in tools
    assert "Global catalog:" in tools
    assert "**Effective Thread Tool Scope**" not in tools


@pytest.mark.slow
def test_row_bot_status_tools_reports_inherited_profile_scope(tmp_path, reload_for_data_dir):
    threads, _profiles, _agent_runs, _goals, agent, status_tool = _fresh_status_modules(
        tmp_path,
        reload_for_data_dir,
    )
    thread_id = threads.create_thread("Worker status thread")
    threads._set_thread_agent_profile(thread_id, "worker")
    agent._set_active_runtime_context(
        thread_id=thread_id,
        enabled_tool_names=[],
        agent_profile_id="worker",
    )

    tools = status_tool._row_bot_status("tools")
    agent_profiles = status_tool._row_bot_status("agent_profiles")

    assert "**Effective Thread Tool Scope**" in tools
    assert "Active profile: Worker" in tools
    assert "Profiles list no tools" in tools
    assert "Runtime allow-list" not in tools
    assert "Active thread profile: Worker" in agent_profiles


def test_row_bot_status_model_setting_uses_strict_canonical_refs(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"})
    *_modules, agent, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    captured = {}

    monkeypatch.setattr("row_bot.self_settings.approve", lambda _change: None)
    monkeypatch.setattr("row_bot.models.set_model", lambda value: captured.setdefault("model", value))
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: captured.setdefault("cache_cleared", True))

    result = status_tool._update_setting("model", "Fast OpenAI")

    assert result.splitlines()[0] == "Active model changed to: model:openai:gpt-4o-mini"
    assert captured["model"] == "model:openai:gpt-4o-mini"
    assert captured["cache_cleared"] is True


def test_row_bot_status_thread_model_setting_writes_thread_override(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"})
    threads, *_modules, agent, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Thread model scope")
    captured = {}

    agent._set_active_runtime_context(thread_id=thread_id, runtime_surface="channel")
    monkeypatch.setattr("row_bot.self_settings.approve", lambda _change: None)
    monkeypatch.setattr("row_bot.models.set_model", lambda _value: pytest.fail("thread_model must not change global default"))
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: captured.setdefault("cache_cleared", True))

    try:
        result = status_tool._update_setting("thread_model", "Fast OpenAI")
    finally:
        agent._set_active_runtime_context()

    assert result.splitlines()[0] == "Thread model override changed to: model:openai:gpt-4o-mini"
    assert threads._get_thread_model_override(thread_id) == "model:openai:gpt-4o-mini"
    assert captured["cache_cleared"] is True


def test_row_bot_status_thread_model_default_clears_override(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"})
    threads, *_modules, agent, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Thread model clear")
    threads._set_thread_model_override(thread_id, "model:openai:gpt-4o-mini")
    captured = {}

    agent._set_active_runtime_context(thread_id=thread_id, runtime_surface="normal_chat")
    monkeypatch.setattr("row_bot.self_settings.approve", lambda _change: None)
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: captured.setdefault("cache_cleared", True))

    try:
        result = status_tool._update_setting("thread_model", "default")
    finally:
        agent._set_active_runtime_context()

    assert result.startswith("Thread model override cleared; using global default:")
    assert threads._get_thread_model_override(thread_id) == ""
    assert captured["cache_cleared"] is True


def test_row_bot_status_default_model_setting_changes_global_default(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"})
    *_modules, agent, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    captured = {}

    monkeypatch.setattr("row_bot.self_settings.approve", lambda _change: None)
    monkeypatch.setattr("row_bot.models.set_model", lambda value: captured.setdefault("model", value))
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: captured.setdefault("cache_cleared", True))

    result = status_tool._update_setting("default_model", "Fast OpenAI")

    assert result.splitlines()[0] == "Global default model changed to: model:openai:gpt-4o-mini"
    assert captured["model"] == "model:openai:gpt-4o-mini"
    assert captured["cache_cleared"] is True


def test_row_bot_status_legacy_model_refuses_threaded_ambiguity(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"})
    threads, *_modules, agent, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    thread_id = threads.create_thread("Ambiguous model scope")

    agent._set_active_runtime_context(thread_id=thread_id, runtime_surface="channel")
    monkeypatch.setattr("row_bot.models.set_model", lambda _value: pytest.fail("ambiguous model must not change global default"))

    try:
        result = status_tool._update_setting("model", "Fast OpenAI")
    finally:
        agent._set_active_runtime_context()

    assert "Model scope is ambiguous" in result
    assert threads._get_thread_model_override(thread_id) == ""


def test_row_bot_status_model_lists_pinned_brain_choices(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"})
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)

    result = status_tool._row_bot_status("model")

    assert "Pinned Brain Model Choices" in result
    assert "For natural Brain model requests" in result
    assert "Fast OpenAI - OpenAI API" in result
    assert "Canonical ref: model:openai:gpt-4o-mini" in result
    assert "provider_id: openai" in result
    assert "model_id: gpt-4o-mini" in result


def test_row_bot_status_model_lists_all_pinned_brain_choices_but_overview_stays_compact(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {f"model:openai:model-{index}": f"Model {index}" for index in range(10)})
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)

    full = status_tool._row_bot_status("model")
    compact = status_tool._query_model(compact_pinned=True)

    assert "Model 9 - OpenAI API" in full
    assert "Plus" not in full
    assert "Plus" in compact


def test_row_bot_status_model_setting_blocks_child_runtime_self_switch_before_approval(tmp_path, monkeypatch, reload_for_data_dir):
    *_modules, agent, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)

    def fail_approval(*_args, **_kwargs):
        raise AssertionError("child model switch should not request approval")

    monkeypatch.setattr("row_bot.self_settings.approve", fail_approval)
    agent._set_active_runtime_context(runtime_surface="agent_child")
    try:
        result = status_tool._update_setting("model", "model:openai:gpt-5.5")
    finally:
        agent._set_active_runtime_context()

    assert "Child Agents cannot switch their own runtime model" in result
    assert "delegate_work(model=...)" in result


def test_row_bot_status_model_setting_rejects_unpinned_canonical_ref(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {"model:openai:gpt-4o-mini": "Fast OpenAI"}, stars=[])
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    captured = {}

    monkeypatch.setattr("row_bot.self_settings.approve", lambda _change: None)
    monkeypatch.setattr("row_bot.models.set_model", lambda value: captured.setdefault("model", value))

    result = status_tool._update_setting("model", "model:openai:gpt-4o-mini")

    assert "not pinned for Brain" in result
    assert captured == {}


def test_row_bot_status_model_setting_rejects_ambiguous_model(tmp_path, monkeypatch, reload_for_data_dir):
    starred_profile(tmp_path / "models", monkeypatch, {
        "model:openai:shared-model": "Shared Model", "model:anthropic:shared-model": "Shared Model"})
    *_modules, status_tool = _fresh_status_modules(tmp_path, reload_for_data_dir)
    captured = {}

    monkeypatch.setattr("row_bot.self_settings.approve", lambda _change: None)
    monkeypatch.setattr("row_bot.models.set_model", lambda value: captured.setdefault("model", value))

    result = status_tool._update_setting("model", "Shared Model")

    assert "Ambiguous model selection" in result
    assert captured == {}
