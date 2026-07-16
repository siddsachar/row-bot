from __future__ import annotations

import importlib
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def _fresh_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    import row_bot.agent as agent

    return importlib.reload(agent)


def _message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("thinking") or block.get("data") or ""))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _has_cache_control(message) -> bool:
    content = getattr(message, "content", "")
    return isinstance(content, list) and any(
        isinstance(block, dict) and "cache_control" in block
        for block in content
    )


def _marked_messages(messages) -> list[tuple[str, str]]:
    return [
        (getattr(message, "type", ""), _message_text(message))
        for message in messages
        if _has_cache_control(message)
    ]


def _combined_text(messages) -> str:
    return "\n".join(_message_text(message) for message in messages)


def test_plugin_skill_prompt_receives_active_profile_tool_allowlist(tmp_path, monkeypatch):
    agent = _fresh_agent(tmp_path, monkeypatch)

    captured_allowlists: list[tuple[str, ...] | None] = []

    def fake_plugin_skills_prompt(*, allow_names=None) -> str:
        captured_allowlists.append(
            None if allow_names is None else tuple(str(name) for name in allow_names)
        )
        allowed = set(allow_names or []) if allow_names is not None else None
        if allowed is None or "rss_reader" in allowed:
            return "RSS_PLUGIN_SKILL_SENTINEL"
        return ""

    monkeypatch.setattr(agent, "get_context_size", lambda: 200_000)
    monkeypatch.setattr(agent, "trim_messages", lambda messages, **kwargs: list(messages))
    monkeypatch.setattr(agent, "get_current_model", lambda: "model:openai:gpt-4o")
    monkeypatch.setattr(agent, "is_cloud_model", lambda model: True)
    monkeypatch.setattr(agent, "get_cloud_provider", lambda model: "openai")
    monkeypatch.setattr(agent, "is_background_workflow", lambda: False)
    monkeypatch.setattr("row_bot.self_knowledge.build_static_self_knowledge_block", lambda: "")
    monkeypatch.setattr("row_bot.self_knowledge.build_dynamic_self_knowledge_block", lambda: "")
    monkeypatch.setattr("row_bot.skills.get_skills_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("row_bot.plugins.registry.get_skills_prompt", fake_plugin_skills_prompt)

    agent._set_active_runtime_context(
        thread_id="plugin-skill-selected",
        enabled_tool_names=("filesystem", "rss_reader"),
        tool_allowlist=["filesystem"],
    )
    selected_without_plugin = agent._pre_model_trim({
        "execution_budget": agent.new_execution_budget("plugin-without"),
        "messages": [
            SystemMessage(content="ROOT_STABLE_SENTINEL"),
            HumanMessage(content="Use tools."),
        ]
    })["llm_input_messages"]

    agent._set_active_runtime_context(
        thread_id="plugin-skill-selected",
        enabled_tool_names=("filesystem", "rss_reader"),
        tool_allowlist=["rss_reader"],
    )
    selected_with_plugin = agent._pre_model_trim({
        "execution_budget": agent.new_execution_budget("plugin-with"),
        "messages": [
            SystemMessage(content="ROOT_STABLE_SENTINEL"),
            HumanMessage(content="Use tools."),
        ]
    })["llm_input_messages"]

    agent._set_active_runtime_context(
        thread_id="plugin-skill-inherited",
        enabled_tool_names=("filesystem", "rss_reader"),
    )
    inherited = agent._pre_model_trim({
        "execution_budget": agent.new_execution_budget("plugin-inherited"),
        "messages": [
            SystemMessage(content="ROOT_STABLE_SENTINEL"),
            HumanMessage(content="Use tools."),
        ]
    })["llm_input_messages"]

    assert captured_allowlists == [("filesystem",), ("rss_reader",), None]
    assert "RSS_PLUGIN_SKILL_SENTINEL" not in _combined_text(selected_without_plugin)
    assert "RSS_PLUGIN_SKILL_SENTINEL" in _combined_text(selected_with_plugin)
    assert "RSS_PLUGIN_SKILL_SENTINEL" in _combined_text(inherited)


def test_anthropic_cache_markers_stay_on_stable_system_context_only(tmp_path, monkeypatch):
    agent = _fresh_agent(tmp_path, monkeypatch)

    decision = SimpleNamespace(
        allowed=True,
        reason="selected",
        candidates_seen=1,
        selected=[{
            "id": "mem_dynamic",
            "entity_type": "fact",
            "subject": "Dynamic Memory",
            "description": "DYNAMIC_RECALL_SENTINEL",
            "score": 0.9,
        }],
        trace={},
    )

    monkeypatch.setattr(agent, "get_context_size", lambda: 200_000)
    monkeypatch.setattr(agent, "trim_messages", lambda messages, **kwargs: list(messages))
    monkeypatch.setattr(agent, "get_current_model", lambda: "model:anthropic:claude-sonnet-4-5")
    monkeypatch.setattr(agent, "is_cloud_model", lambda model: True)
    monkeypatch.setattr(agent, "get_cloud_provider", lambda model: "anthropic")
    monkeypatch.setattr(agent, "is_background_workflow", lambda: False)
    monkeypatch.setattr(agent, "_agent_runtime_system_context", lambda: "RUNTIME_DYNAMIC_SENTINEL")
    monkeypatch.setattr("row_bot.prompts.get_platform_context", lambda: "PLATFORM_STABLE_SENTINEL")
    monkeypatch.setattr("row_bot.self_knowledge.build_static_self_knowledge_block", lambda: "SELF_STATIC_SENTINEL")
    monkeypatch.setattr("row_bot.self_knowledge.build_dynamic_self_knowledge_block", lambda: "DYNAMIC_STATE_SENTINEL")
    monkeypatch.setattr("row_bot.skills.get_skills_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("row_bot.plugins.registry.get_skills_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("row_bot.memory_policy.build_auto_recall", lambda *args, **kwargs: decision)
    monkeypatch.setattr("row_bot.memory_policy.touch_selected_memories", lambda recall_decision: None)
    monkeypatch.setattr("row_bot.memory_policy.record_recall_trace", lambda recall_decision, **kwargs: None)

    agent._set_active_runtime_context(thread_id="cache-stability", enabled_tool_names=())
    agent.set_active_model_override("model:anthropic:claude-sonnet-4-5")
    try:
        result = agent._pre_model_trim({
            "execution_budget": agent.new_execution_budget("cache-stability"),
            "messages": [
                SystemMessage(content="ROOT_STABLE_SENTINEL"),
                HumanMessage(content="first turn"),
                AIMessage(content="first answer"),
                HumanMessage(content="second turn"),
            ]
        })["llm_input_messages"]
    finally:
        agent.set_active_model_override("")

    marked = _marked_messages(result)

    assert marked, "Anthropic should receive at least one cache breakpoint"
    assert all(message_type == "system" for message_type, _text in marked)
    assert any(
        any(stable_sentinel in text for stable_sentinel in (
            "ROOT_STABLE_SENTINEL",
            "PLATFORM_STABLE_SENTINEL",
            "SELF_STATIC_SENTINEL",
        ))
        for _message_type, text in marked
    )
    for dynamic_sentinel in (
        "Current date and time:",
        "RUNTIME_DYNAMIC_SENTINEL",
        "DYNAMIC_STATE_SENTINEL",
        "DYNAMIC_RECALL_SENTINEL",
        "first turn",
        "second turn",
    ):
        assert all(dynamic_sentinel not in text for _message_type, text in marked)


def test_chat_only_prompt_uses_stable_chat_contract_without_agent_runtime_context(tmp_path, monkeypatch):
    agent = _fresh_agent(tmp_path, monkeypatch)

    monkeypatch.setattr("row_bot.threads.get_latest_checkpoint_messages", lambda thread_id: [])
    monkeypatch.setattr(agent, "trim_messages", lambda messages, **kwargs: list(messages))
    monkeypatch.setattr(agent, "_agent_runtime_system_context", lambda: "RUNTIME_DYNAMIC_SENTINEL")
    monkeypatch.setattr("row_bot.self_knowledge.build_static_self_knowledge_block", lambda: "SELF_STATIC_SENTINEL")
    monkeypatch.setattr("row_bot.self_knowledge.build_dynamic_self_knowledge_block", lambda: "DYNAMIC_STATE_SENTINEL")

    messages = agent._build_chat_only_messages("chat-stability", "hello", context_window=4096)
    system_text = "\n".join(_message_text(message) for message in messages if message.type == "system")

    assert "Do not answer an imagined or unrelated task" in system_text
    assert "tools are not available here" in system_text
    assert "RUNTIME_DYNAMIC_SENTINEL" not in system_text
    assert "DYNAMIC_STATE_SENTINEL" not in system_text
