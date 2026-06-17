from __future__ import annotations

import importlib
import sys

from langchain_core.messages import HumanMessage, SystemMessage


def _fresh_runtime_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_profiles",
        "row_bot.agent",
        "row_bot.ui.streaming",
    ):
        sys.modules.pop(name, None)

    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.agent_profiles as agent_profiles
    import row_bot.agent as agent
    import row_bot.ui.streaming as streaming

    tasks = importlib.reload(tasks)
    threads = importlib.reload(threads)
    agent_profiles = importlib.reload(agent_profiles)
    agent = importlib.reload(agent)
    streaming = importlib.reload(streaming)
    return threads, agent_profiles, agent, streaming


def _prompt_text(result: dict) -> str:
    return "\n".join(str(message.content) for message in result["llm_input_messages"])


def test_thread_agent_profile_is_injected_into_agent_mode_prompt(tmp_path, monkeypatch):
    threads, _profiles, agent, _streaming = _fresh_runtime_modules(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Profile runtime")
    threads._set_thread_agent_profile(thread_id, "reviewer")

    agent._set_active_runtime_context(thread_id=thread_id, enabled_tool_names=[])
    trimmed = agent._pre_model_trim({
        "messages": [
            SystemMessage(content="Base system"),
            HumanMessage(content="Review this change."),
        ]
    })
    prompt = _prompt_text(trimmed)

    assert "AGENT PROFILE: Reviewer" in prompt
    assert "Findings first" in prompt
    assert "capability=read_only" in prompt


def test_thread_agent_profile_is_injected_into_chat_only_prompt(tmp_path, monkeypatch):
    threads, _profiles, agent, _streaming = _fresh_runtime_modules(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Chat only profile")
    threads._set_thread_agent_profile(thread_id, "planner")

    agent._set_active_runtime_context(thread_id=thread_id, enabled_tool_names=[])
    messages = agent._build_chat_only_messages(thread_id, "Plan this.", context_window=4096)
    text = "\n".join(str(message.content) for message in messages if isinstance(message, SystemMessage))

    assert "AGENT PROFILE: Planner" in text
    assert "Produce a compact plan" in text


def test_disabled_thread_profile_warns_instead_of_silent_fallback(tmp_path, monkeypatch):
    threads, profiles, agent, _streaming = _fresh_runtime_modules(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Disabled profile")
    saved = profiles.save_agent_profile(
        slug="brief_reviewer",
        display_name="Brief Reviewer",
        description="Review briefly.",
        instructions="Only review the risk.",
        tool_policy_json={"capability": "read_only"},
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    threads._set_thread_agent_profile(thread_id, saved["id"])
    profiles.save_agent_profile({**saved, "enabled": False})

    agent._set_active_runtime_context(thread_id=thread_id, enabled_tool_names=[])
    trimmed = agent._pre_model_trim({
        "messages": [
            SystemMessage(content="Base system"),
            HumanMessage(content="Review this change."),
        ]
    })
    prompt = _prompt_text(trimmed)

    assert "THREAD AGENT PROFILE WARNING" in prompt
    assert "brief_reviewer" in prompt
    assert "disabled" in prompt


def test_thread_profile_allowlist_flows_into_chat_stream_config(tmp_path, monkeypatch):
    threads, profiles, _agent, streaming = _fresh_runtime_modules(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Profile tool filter")
    saved = profiles.save_agent_profile(
        slug="tool_filter_smoke",
        display_name="Tool Filter Smoke",
        description="Constrain tools for smoke checks.",
        instructions="Use only the selected tools.",
        tool_policy_json={
            "capability": "read_only",
            "allow_tools": ["filesystem", "row_bot_status"],
        },
        context_policy_json={"default_context_mode": "focused"},
        workspace_policy_json={"workspace_mode_default": "read_only"},
    )
    threads._set_thread_agent_profile(thread_id, saved["id"])

    config = streaming._profile_runtime_config_for_thread(thread_id)

    assert config["agent_profile_id"] == saved["id"]
    assert config["agent_profile_snapshot"]["slug"] == "tool_filter_smoke"
    assert config["tool_allowlist"] == ["filesystem", "row_bot_status"]
    assert streaming._profile_runtime_config_for_thread("missing-thread") == {}
