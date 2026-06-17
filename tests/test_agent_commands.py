from __future__ import annotations

import importlib
import sys


def _fresh_command_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
        "row_bot.agent_runs",
        "row_bot.agent_commands",
        "row_bot.slash_commands",
        "row_bot.channels.commands",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.agent_runs as agent_runs
    import row_bot.agent_commands as agent_commands
    import row_bot.slash_commands as slash_commands
    commands = importlib.import_module("row_bot.channels.commands")

    tasks = importlib.reload(tasks)
    threads = importlib.reload(threads)
    agent_runs = importlib.reload(agent_runs)
    agent_commands = importlib.reload(agent_commands)
    slash_commands = importlib.reload(slash_commands)
    commands = importlib.reload(commands)
    return threads, agent_runs, agent_commands, slash_commands, commands


def test_app_slash_profile_commands_set_clear_and_list(tmp_path, monkeypatch):
    threads, _agent_runs, _agent_commands, slash_commands, _commands = _fresh_command_modules(
        tmp_path,
        monkeypatch,
    )
    thread_id = threads.create_thread("Profile slash")
    import row_bot.agent_profiles as agent_profiles

    custom = agent_profiles.save_agent_profile(
        slug="skillful_reviewer",
        display_name="Skillful Reviewer",
        instructions="Review with a skill.",
        tool_policy_json={"capability": "read_only"},
        skill_policy_json={"skills_override": ["release_notes"]},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )

    specs = {spec.id: spec for spec in slash_commands.get_command_specs(include_skills=False)}
    assert {"profiles", "profile", "agents", "agent"} <= set(specs)
    assert slash_commands.resolve_command_text("/profile reviewer")[0].id == "profile"

    response = slash_commands.dispatch_text_command(thread_id, "/profile reviewer")
    assert response and "Reviewer" in response
    assert threads._get_thread_agent_profile(thread_id) == {
        "id": "builtin:reviewer",
        "slug": "reviewer",
    }

    current = slash_commands.dispatch_text_command(thread_id, "/profile")
    assert current and "reviewer" in current

    listing = slash_commands.dispatch_text_command(thread_id, "/profiles review")
    assert listing and "`reviewer`" in listing

    custom_response = slash_commands.dispatch_text_command(thread_id, f"/profile {custom['slug']}")
    assert custom_response and "Skillful Reviewer" in custom_response
    assert threads.get_thread_skills_override(thread_id) == ["release_notes"]

    cleared = slash_commands.dispatch_text_command(thread_id, "/profile clear")
    assert cleared and "cleared" in cleared.lower()
    assert threads._get_thread_agent_profile(thread_id) == {"id": "", "slug": ""}
    assert threads.get_thread_skills_override(thread_id) is None


def test_direct_agent_request_parser_is_explicit_about_profiles(tmp_path, monkeypatch):
    _threads, _agent_runs, agent_commands, slash_commands, commands = _fresh_command_modules(
        tmp_path,
        monkeypatch,
    )

    generic = agent_commands.parse_agent_spawn_text(
        "Use another agent to write a 600 word essay and save it as smoke.pdf"
    )
    assert generic is not None
    assert generic.profile == "worker"
    assert generic.explicit_profile is False
    assert generic.objective.startswith("write a 600 word essay")

    explicit = agent_commands.parse_agent_spawn_text(
        "Use a reviewer agent to review the latest diff"
    )
    assert explicit is not None
    assert explicit.profile == "reviewer"
    assert explicit.explicit_profile is True
    assert explicit.objective == "review the latest diff"

    natural_without_agent_noun = agent_commands.parse_agent_spawn_text(
        "Use reviewer to review the release notes"
    )
    assert natural_without_agent_noun is not None
    assert natural_without_agent_noun.profile == "reviewer"

    slash_profile = agent_commands.parse_agent_spawn_text("/agent researcher research Row-Bot history")
    assert slash_profile is not None
    assert slash_profile.profile == "researcher"
    assert slash_profile.source == "slash"

    slash_generic = agent_commands.parse_agent_spawn_text("/agent write a PDF")
    assert slash_generic is not None
    assert slash_generic.profile == "worker"
    assert slash_generic.explicit_profile is False

    assert agent_commands.parse_agent_spawn_text("Use a quantum specialist agent to explain qubits") is None
    assert slash_commands.resolve_command_text("/agent reviewer review this")[0].id == "agent"
    assert commands.is_thread_scoped_command("/agent reviewer review this")


def test_direct_agent_commands_spawn_with_explicit_or_worker_profile(tmp_path, monkeypatch):
    threads, _agent_runs, _agent_commands, slash_commands, commands = _fresh_command_modules(
        tmp_path,
        monkeypatch,
    )
    thread_id = threads.create_thread("Direct agent command")

    import row_bot.agent_runner as agent_runner

    captured: list[dict] = []

    def fake_spawn_agent_run(objective: str, **kwargs):
        captured.append({"objective": objective, **kwargs})
        profile = kwargs.get("profile") or "worker"
        return {
            "id": f"run-{len(captured)}",
            "status": "queued",
            "display_name": kwargs.get("display_name") or "Agent",
            "profile_slug": profile,
        }

    monkeypatch.setattr(agent_runner, "spawn_agent_run", fake_spawn_agent_run)

    app_response = slash_commands.dispatch_text_command(
        thread_id,
        "/agent reviewer review this patch",
        enabled_tool_names=["filesystem", "row_bot_status"],
    )
    assert app_response and "Started Agent" in app_response
    assert captured[-1]["profile"] == "reviewer"
    assert captured[-1]["objective"] == "review this patch"
    assert captured[-1]["wait"] is False

    channel_response = commands.dispatch(
        "sms",
        "/agent write a smoke report",
        thread_id=thread_id,
        enabled_tool_names=["filesystem"],
    )
    assert channel_response and "`worker`" in channel_response
    assert captured[-1]["profile"] == "worker"
    assert captured[-1]["objective"] == "write a smoke report"


def test_channel_profile_commands_are_thread_scoped(tmp_path, monkeypatch):
    threads, _agent_runs, _agent_commands, _slash_commands, commands = _fresh_command_modules(
        tmp_path,
        monkeypatch,
    )
    first = threads.create_thread("Channel one")
    second = threads.create_thread("Channel two")

    assert commands.is_thread_scoped_command("/profile reviewer")
    assert commands.is_thread_scoped_command("/profiles")
    assert commands.dispatch("sms", "/profile reviewer") == (
        "Could not identify the current conversation thread."
    )

    response = commands.dispatch("sms", "/profile reviewer", thread_id=first)
    assert response and "Reviewer" in response
    assert threads._get_thread_agent_profile(first)["slug"] == "reviewer"
    assert threads._get_thread_agent_profile(second) == {"id": "", "slug": ""}

    listing = commands.dispatch("sms", "/profiles research", thread_id=first)
    assert listing and "`researcher`" in listing

    cleared = commands.dispatch("sms", "/profile clear", thread_id=first)
    assert cleared and "cleared" in cleared.lower()
    assert threads._get_thread_agent_profile(first) == {"id": "", "slug": ""}


def test_agents_command_lists_current_thread_runs(tmp_path, monkeypatch):
    threads, agent_runs, _agent_commands, slash_commands, commands = _fresh_command_modules(
        tmp_path,
        monkeypatch,
    )
    first = threads.create_thread("Agent parent")
    second = threads.create_thread("Other parent")

    agent_runs.create_agent_run(
        run_id="run-one",
        kind="subagent",
        status="running",
        parent_thread_id=first,
        thread_id="child-one",
        display_name="Review Run",
        profile_id="reviewer",
        status_message="Reviewing",
    )
    agent_runs.create_agent_run(
        run_id="run-two",
        kind="subagent",
        status="completed",
        parent_thread_id=second,
        thread_id="child-two",
        display_name="Other Run",
        profile_id="worker",
    )

    app_response = slash_commands.dispatch_text_command(first, "/agents")
    assert app_response and "run-one" in app_response
    assert "run-two" not in app_response

    channel_response = commands.dispatch("slack", "/agents", thread_id=first)
    assert channel_response and "Review Run" in channel_response
    assert "Other Run" not in channel_response

    global_response = slash_commands.dispatch_text_command(first, "/agents all")
    assert global_response and "run-one" in global_response and "run-two" in global_response
