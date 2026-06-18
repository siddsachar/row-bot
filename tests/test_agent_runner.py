from __future__ import annotations

import importlib
import sys
import threading
import time


def _fresh_agent_runner_modules(tmp_path, monkeypatch):
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
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.agent_profiles as agent_profiles
    import row_bot.agent_runs as agent_runs
    import row_bot.agent_context as agent_context
    import row_bot.agent_runner as agent_runner

    tasks = importlib.reload(tasks)
    threads = importlib.reload(threads)
    agent_profiles = importlib.reload(agent_profiles)
    agent_runs = importlib.reload(agent_runs)
    agent_context = importlib.reload(agent_context)
    agent_runner = importlib.reload(agent_runner)
    return agent_runner, agent_runs, agent_profiles, agent_context, threads


def test_spawn_agent_run_creates_child_thread_and_completes(tmp_path, monkeypatch):
    agent_runner, agent_runs, _profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread(
        "Parent",
        approval_mode="block",
        model_override="model:test",
    )
    captured = {}

    def fake_invoke(prompt, enabled_tool_names, config, *, stop_event):
        captured["prompt"] = prompt
        captured["tools"] = enabled_tool_names
        captured["config"] = config
        captured["stop_event"] = stop_event
        return "review complete"

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_invoke)

    run = agent_runner.spawn_agent_run(
        "Review the auth change.",
        parent_thread_id=parent_thread_id,
        profile="quality_reviewer",
        context="Changed files: auth.py",
        enabled_tool_names=["shell", "agents", "filesystem"],
        wait=True,
    )

    assert run["status"] == "completed"
    assert run["summary"] == "review complete"
    assert run["profile_id"] == "builtin:review"
    assert run["profile_snapshot_json"]["slug"] == "review"
    assert run["parent_thread_id"] == parent_thread_id
    assert run["context_mode"] == "focused"
    assert "Review the auth change." in captured["prompt"]
    assert "PROFILE INSTRUCTIONS" in captured["prompt"]
    assert "agents" not in captured["tools"]
    assert captured["config"]["configurable"]["runtime_surface"] == "agent_child"
    assert captured["config"]["configurable"]["approval_mode"] == "block"
    assert captured["config"]["configurable"]["model_override"] == "model:test"

    child_profile = threads._get_thread_agent_profile(run["thread_id"])
    assert child_profile == {"id": "builtin:review", "slug": "review"}
    assert threads._get_thread_type(run["thread_id"]) == "agent_child"
    event_types = {event["type"] for event in agent_runs.get_agent_events(run["id"])}
    assert {"run.created", "run.started", "turn.started", "turn.completed", "run.completed"} <= event_types


def test_builtin_profile_skills_flow_to_child_agent(tmp_path, monkeypatch):
    agent_runner, agent_runs, _profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent")
    captured = {}

    def fake_invoke(prompt, enabled_tool_names, config, *, stop_event):
        captured["tools"] = enabled_tool_names
        captured["config"] = config
        return "research complete"

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_invoke)

    run = agent_runner.spawn_agent_run(
        "Research profile skills.",
        parent_thread_id=parent_thread_id,
        profile="research",
        enabled_tool_names=[
            "agents",
            "memory",
            "row_bot_status",
            "conversation_search",
            "duckduckgo",
            "web_search",
            "url_reader",
            "filesystem",
            "shell",
            "arxiv",
            "browser",
            "documents",
            "wiki",
            "wikipedia",
            "youtube",
        ],
        wait=True,
    )

    assert run["status"] == "completed"
    assert run["profile_snapshot_json"]["slug"] == "research"
    assert run["skills_override"] == ["deep_research", "web_navigator"]
    assert threads.get_thread_skills_override(run["thread_id"]) == [
        "deep_research",
        "web_navigator",
    ]
    assert "agents" not in captured["tools"]
    assert "shell" in captured["tools"]
    assert "browser" in captured["tools"]
    assert captured["config"]["configurable"]["tool_allowlist"] == run["tools_override"]
    event_types = {event["type"] for event in agent_runs.get_agent_events(run["id"])}
    assert {"run.created", "run.started", "turn.completed", "run.completed"} <= event_types


def test_profile_tool_and_skill_policy_filters_child_context(tmp_path, monkeypatch):
    agent_runner, _agent_runs, profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent")
    threads.set_thread_skills_override(parent_thread_id, ["release_notes"])
    custom = profiles.save_agent_profile(
        slug="api_doc_worker",
        display_name="API Doc Worker",
        instructions="Write API docs.",
        tool_policy_json={
            "capability": "write_capable",
            "allow_tools": ["filesystem", "row_bot_status"],
        },
        skill_policy_json={
            "skills_override": ["release_notes", "openapi"],
        },
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "single_writer"},
    )
    captured = {}

    def fake_invoke(prompt, enabled_tool_names, config, *, stop_event):
        captured["tools"] = enabled_tool_names
        captured["config"] = config
        return "done"

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_invoke)

    run = agent_runner.spawn_agent_run(
        "Write docs.",
        parent_thread_id=parent_thread_id,
        profile=custom["id"],
        enabled_tool_names=["shell", "filesystem", "row_bot_status", "gmail", "agents"],
        wait=True,
    )

    assert captured["tools"] == ["filesystem", "row_bot_status"]
    assert captured["config"]["configurable"]["tool_allowlist"] == ["filesystem", "row_bot_status"]
    assert run["tools_override"] == ["filesystem", "row_bot_status"]
    assert run["skills_override"] == ["release_notes", "openapi"]
    assert threads.get_thread_skills_override(run["thread_id"]) == ["release_notes", "openapi"]


def test_profile_external_tool_allowlist_passes_through_child_config(tmp_path, monkeypatch):
    agent_runner, _agent_runs, profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent")
    custom = profiles.save_agent_profile(
        slug="external_worker",
        display_name="External Worker",
        instructions="Use selected external tools.",
        tool_policy_json={
            "capability": "read_only",
            "allow_tools": ["filesystem", "plugin_lookup", "mcp_local_echo"],
        },
        context_policy_json={"default_context_mode": "focused"},
    )
    captured = {}

    def fake_invoke(prompt, enabled_tool_names, config, *, stop_event):
        captured["tools"] = enabled_tool_names
        captured["config"] = config
        return "done"

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_invoke)

    run = agent_runner.spawn_agent_run(
        "Use external tools.",
        parent_thread_id=parent_thread_id,
        profile=custom["id"],
        enabled_tool_names=["filesystem", "mcp", "row_bot_status", "agents"],
        wait=True,
    )

    assert captured["tools"] == ["filesystem", "mcp"]
    assert captured["config"]["configurable"]["tool_allowlist"] == [
        "filesystem",
        "plugin_lookup",
        "mcp_local_echo",
    ]
    assert run["tools_override"] == ["filesystem", "plugin_lookup", "mcp_local_echo"]


def test_profile_without_allowlist_preserves_default_child_tools(tmp_path, monkeypatch):
    agent_runner, _agent_runs, _profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent")
    captured = {}

    def fake_invoke(prompt, enabled_tool_names, config, *, stop_event):
        captured["tools"] = enabled_tool_names
        captured["config"] = config
        return "done"

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_invoke)

    run = agent_runner.spawn_agent_run(
        "Implement this.",
        parent_thread_id=parent_thread_id,
        profile="worker",
        enabled_tool_names=["filesystem", "mcp", "agents"],
        wait=True,
    )

    assert captured["tools"] == ["filesystem", "mcp"]
    assert "tool_allowlist" not in captured["config"]["configurable"]
    assert run["tools_override"] == ["filesystem", "mcp"]


def test_builtin_read_only_profile_does_not_inherit_write_heavy_tools(tmp_path, monkeypatch):
    agent_runner, _agent_runs, _profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent")
    captured = {}

    def fake_invoke(prompt, enabled_tool_names, config, *, stop_event):
        captured["tools"] = enabled_tool_names
        return "done"

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_invoke)

    run = agent_runner.spawn_agent_run(
        "Review this.",
        parent_thread_id=parent_thread_id,
        profile="review",
        enabled_tool_names=["filesystem", "row_bot_status", "gmail", "image_gen", "agents"],
        wait=True,
    )

    assert run["skills_override"] == []
    assert captured["tools"] == ["filesystem", "row_bot_status"]


def test_spawn_agent_run_records_interrupt_resume_state(tmp_path, monkeypatch):
    agent_runner, agent_runs, _profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent", approval_mode="approve")

    def fake_interrupt(prompt, enabled_tool_names, config, *, stop_event):
        return {
            "type": "interrupt",
            "interrupts": [{"tool": "shell", "description": "Needs approval"}],
        }

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_interrupt)

    run = agent_runner.spawn_agent_run(
        "Inspect the release.",
        parent_thread_id=parent_thread_id,
        profile="worker",
        enabled_tool_names=["shell"],
        wait=True,
    )

    assert run["status"] == "waiting_approval"
    assert run["status_message"] == "Waiting for approval"
    assert run["resume_state_json"]["enabled_tool_names"] == ["shell"]
    assert run["resume_state_json"]["interrupts"][0]["tool"] == "shell"
    event_types = {event["type"] for event in agent_runs.get_agent_events(run["id"])}
    assert "approval.requested" in event_types


def test_approval_resume_state_preserves_profile_tool_allowlist(tmp_path, monkeypatch):
    agent_runner, _agent_runs, profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent", approval_mode="approve")
    custom = profiles.save_agent_profile(
        slug="approval_worker",
        display_name="Approval Worker",
        instructions="Request approval.",
        tool_policy_json={
            "capability": "write_capable",
            "allow_tools": ["shell", "plugin_lookup"],
        },
    )

    def fake_interrupt(prompt, enabled_tool_names, config, *, stop_event):
        return {
            "type": "interrupt",
            "interrupts": [{"tool": "shell", "description": "Needs approval"}],
        }

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_interrupt)

    run = agent_runner.spawn_agent_run(
        "Run a command.",
        parent_thread_id=parent_thread_id,
        profile=custom["id"],
        enabled_tool_names=["shell", "filesystem"],
        wait=True,
    )

    assert run["status"] == "waiting_approval"
    assert run["resume_state_json"]["enabled_tool_names"] == ["shell"]
    assert run["resume_state_json"]["tool_allowlist"] == ["shell", "plugin_lookup"]
    assert run["resume_state_json"]["config"]["configurable"]["tool_allowlist"] == [
        "shell",
        "plugin_lookup",
    ]


def test_stop_agent_run_sets_live_stop_event_and_durable_status(tmp_path, monkeypatch):
    agent_runner, _agent_runs, _profiles, _context, threads = _fresh_agent_runner_modules(
        tmp_path,
        monkeypatch,
    )
    parent_thread_id = threads.create_thread("Parent")
    started = threading.Event()

    class TaskStoppedError(Exception):
        pass

    def fake_slow(prompt, enabled_tool_names, config, *, stop_event):
        started.set()
        while not stop_event.is_set():
            time.sleep(0.01)
        raise TaskStoppedError("stopped")

    monkeypatch.setattr(agent_runner, "_invoke_agent", fake_slow)

    run = agent_runner.spawn_agent_run(
        "Wait until stopped.",
        parent_thread_id=parent_thread_id,
        profile="worker",
        enabled_tool_names=[],
        wait=False,
    )

    assert started.wait(timeout=1.0)
    stopped = agent_runner.stop_agent_run(run["id"])
    final = agent_runner.wait_for_agent_run(run["id"], timeout=2.0)

    assert stopped["status"] == "stopped"
    assert stopped["stop_requested"] is True
    assert final["status"] == "stopped"
    assert agent_runner.list_active_agent_run_ids() == []
