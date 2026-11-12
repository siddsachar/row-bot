from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def _fresh_agent(tmp_path, reload_for_data_dir):
    (agent,) = reload_for_data_dir(tmp_path / "data", "row_bot.agent")
    return agent


def _quiet_prompt_dependencies(agent, monkeypatch) -> None:
    decision = SimpleNamespace(
        allowed=False,
        reason="disabled_for_test",
        candidates_seen=0,
        selected=[],
        trace={},
    )
    monkeypatch.setattr(agent, "get_context_size", lambda *args, **kwargs: 200_000)
    monkeypatch.setattr("row_bot.prompts.get_platform_context", lambda: "")
    monkeypatch.setattr("row_bot.self_knowledge.build_static_self_knowledge_block", lambda: "")
    monkeypatch.setattr("row_bot.self_knowledge.build_dynamic_self_knowledge_block", lambda: "")
    monkeypatch.setattr("row_bot.skills.get_skills_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("row_bot.plugins.registry.get_skills_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("row_bot.memory_policy.build_auto_recall", lambda *args, **kwargs: decision)
    monkeypatch.setattr("row_bot.memory_policy.record_recall_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("row_bot.memory_policy.touch_selected_memories", lambda *args, **kwargs: None)


def _prompt_for(agent, *, surface: str, selected_mode: str = "agent", channel_streaming: bool = False) -> str:
    agent._set_active_runtime_context(
        thread_id=f"progress-{surface}-{selected_mode}",
        runtime_surface=surface,
        requested_runtime_mode="agent",
        selected_runtime_mode=selected_mode,
        enabled_tool_names=[],
        channel_streaming=channel_streaming,
    )
    result = agent._pre_model_trim({
        "execution_budget": agent.new_execution_budget(f"progress-{surface}-{selected_mode}"),
        "messages": [
            SystemMessage(content="Base system"),
            HumanMessage(content="Please do a multi-step check."),
        ]
    })
    def text(content) -> str:
        # The system prompt is a string, or blocks with cache markers when the provider caches it.
        if isinstance(content, list):
            return "\n".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content)
        return str(content or "")

    return "\n".join(
        text(getattr(message, "content", ""))
        for message in result["llm_input_messages"]
        if getattr(message, "type", "") == "system"
    )


def test_interactive_progress_contract_is_injected_for_interactive_agent_surfaces(tmp_path, monkeypatch, reload_for_data_dir):
    agent = _fresh_agent(tmp_path, reload_for_data_dir)
    _quiet_prompt_dependencies(agent, monkeypatch)

    # remote_client: a chat on another of the owner's signed-in devices.
    for surface in ("normal_chat", "remote_client", "designer", "developer"):
        prompt = _prompt_for(agent, surface=surface)
        assert "INTERACTIVE PROGRESS:" in prompt
        assert "before your first tool step, and whenever you move on to something new" in prompt
        assert "don't repeat these lines in the final answer" in prompt
        # A progress line on its own ended the turn mid-task three times (R1).
        assert "A reply with no tool call ends your turn" in prompt


def test_streaming_channel_gets_sparse_progress_contract(tmp_path, monkeypatch, reload_for_data_dir):
    agent = _fresh_agent(tmp_path, reload_for_data_dir)
    _quiet_prompt_dependencies(agent, monkeypatch)

    prompt = _prompt_for(agent, surface="channel", channel_streaming=True)

    assert "INTERACTIVE PROGRESS:" in prompt
    assert "keep progress updates especially sparse" in prompt


def test_progress_contract_is_omitted_for_noninteractive_or_nonstreaming_paths(tmp_path, monkeypatch, reload_for_data_dir):
    agent = _fresh_agent(tmp_path, reload_for_data_dir)
    _quiet_prompt_dependencies(agent, monkeypatch)

    assert "INTERACTIVE PROGRESS:" not in _prompt_for(agent, surface="approval")
    assert "INTERACTIVE PROGRESS:" not in _prompt_for(
        agent,
        surface="normal_chat",
        selected_mode="chat_only",
    )
    assert "INTERACTIVE PROGRESS:" not in _prompt_for(
        agent,
        surface="channel",
        channel_streaming=False,
    )



def test_a_workflow_run_keeps_its_conversation_posted_sparsely(tmp_path, monkeypatch, reload_for_data_dir):
    agent = _fresh_agent(tmp_path, reload_for_data_dir)
    _quiet_prompt_dependencies(agent, monkeypatch)

    token = agent._background_workflow_var.set(True)
    try:
        prompt = _prompt_for(agent, surface="normal_chat")
    finally:
        agent._background_workflow_var.reset(token)
    assert "INTERACTIVE PROGRESS:" in prompt and "In a workflow run keep them sparse" in prompt


def _rounds(agent, silent: int, *, spoke_at: int | None = None) -> list:
    messages = [SystemMessage(content="Base system"), HumanMessage(content="Check the orders and draft a reply.")]
    for index in range(silent):
        said = "Checking the October orders." if index == spoke_at else ""
        messages += [AIMessage(content=said, tool_calls=[{"name": "read_file", "args": {}, "id": f"call-{index}"}]),
                     ToolMessage(content="rows", tool_call_id=f"call-{index}")]
    return messages


def _request(agent, messages: list) -> list:
    agent._set_active_runtime_context(thread_id="progress-nudge", runtime_surface="normal_chat",
                                      requested_runtime_mode="agent", selected_runtime_mode="agent",
                                      enabled_tool_names=[])
    return agent._pre_model_trim({"execution_budget": agent.new_execution_budget("progress-nudge"),
                                  "messages": messages})["llm_input_messages"]


def test_after_three_silent_tool_rounds_the_next_request_asks_for_one_line(tmp_path, monkeypatch,
                                                                           reload_for_data_dir):
    """Owner, 28 Oct: Row-Bot often works through tools in silence. A note asks for a line, in that request only."""
    agent = _fresh_agent(tmp_path, reload_for_data_dir)
    _quiet_prompt_dependencies(agent, monkeypatch)

    messages = _rounds(agent, 3)
    sent = _request(agent, messages)
    note = sent[-1]
    assert isinstance(note, HumanMessage) and note.content == agent.PROGRESS_NOTE
    assert note.additional_kwargs == {"ephemeral": True, "row_bot_note": "progress"}, "Learning can tell it apart"
    assert len(messages) == 8 and not any(m.additional_kwargs.get("ephemeral") for m in messages), "never stored"

    def noted(sent: list) -> bool:
        return any(message.content == agent.PROGRESS_NOTE for message in sent)

    assert not noted(_request(agent, _rounds(agent, 2))), "two rounds: nothing yet"
    assert not noted(_request(agent, _rounds(agent, 4))), "once per silent stretch"
    assert not noted(_request(agent, _rounds(agent, 4, spoke_at=1))), "it spoke: the count starts again"
    assert noted(_request(agent, _rounds(agent, 8))), "still silent: asked again"
