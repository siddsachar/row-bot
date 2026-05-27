from types import SimpleNamespace
import logging


def test_chat_only_prompt_is_compact_and_guards_against_imagined_tasks():
    from prompts import get_chat_only_system_prompt

    prompt = get_chat_only_system_prompt()

    assert "Do not answer an imagined or unrelated task" in prompt
    assert "If the user only greets you" in prompt
    assert "tools are not available here" in prompt
    assert "access to tools" not in prompt
    assert "Developer Studio" not in prompt
    assert "Designer Studio" not in prompt
    assert "background workflows" not in prompt
    assert "long-term memory requires Agent Mode" in prompt


def _chat_ready_result():
    from providers.models import TransportMode
    from providers.readiness import ChatReadinessResult

    return ChatReadinessResult(
        ready=True,
        provider_id="custom_openai_lab",
        model_id="local-chat",
        runtime_model="local-chat",
        selection_ref="model:custom_openai_lab:local-chat",
        transport=TransportMode.OPENAI_CHAT,
        context_window=32768,
        credential_status="configured",
    )


def test_build_chat_only_messages_for_fresh_thread_has_no_hidden_history(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    import agent
    import threads

    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", lambda thread_id: [])
    monkeypatch.setattr(agent, "trim_messages", lambda messages, **kwargs: list(messages))

    messages = agent._build_chat_only_messages("fresh-thread", "hello", context_window=32768)

    assert [message.type for message in messages] == ["system", "human"]
    assert messages[-1].content == "hello"
    assert "Do not answer an imagined or unrelated task" in messages[0].content


def test_chat_only_history_marks_prior_tools_without_tool_bodies():
    import agent

    content = agent._chat_only_content_from_ui_message({
        "content": "Earlier answer",
        "tool_results": [
            {
                "name": "thoth_status",
                "content": "SECRET_STATUS_BODY with current model and enabled tools",
            }
        ],
    })

    assert "Earlier Agent Mode turn used tool(s):" in content
    assert "- thoth_status" in content
    assert "SECRET_STATUS_BODY" not in content
    assert "enabled tools" not in content


def test_stream_chat_only_streams_and_persists_without_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    from langchain_core.messages import AIMessageChunk
    import agent
    import providers.readiness as readiness
    import threads

    class FakeLLM:
        def stream(self, messages):
            self.messages = messages
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "thinking"})
            yield AIMessageChunk(content="hello")
            yield AIMessageChunk(content=" world")

    persisted = []
    monkeypatch.setattr(readiness, "evaluate_chat_readiness", lambda model_label: _chat_ready_result())
    monkeypatch.setattr(agent, "_chat_only_llm", lambda model_label: FakeLLM())
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", lambda thread_id: [])
    monkeypatch.setattr(threads, "append_checkpoint_messages", lambda thread_id, messages: persisted.extend(messages) or True)

    events = list(agent.stream_chat_only(
        "hi",
        {"configurable": {"thread_id": "thread-chat", "model_override": "model:custom_openai_lab:local-chat"}},
    ))

    assert ("thinking_token", "thinking") in events
    assert [payload for event_type, payload in events if event_type == "done"] == ["hello world"]
    assert [getattr(message, "type", "") for message in persisted] == ["human", "ai"]
    assert getattr(persisted[-1], "content", "") == "hello world"


def test_stream_chat_only_llm_creation_error_names_selected_model(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    import agent
    import providers.readiness as readiness
    import threads

    selected = "model:ollama:vendor/non-tool-chat:14b"
    monkeypatch.setattr(readiness, "evaluate_chat_readiness", lambda model_label: _chat_ready_result())
    monkeypatch.setattr(agent, "_chat_only_llm", lambda model_label: (_ for _ in ()).throw(ValueError("status code: 400: does not support tools")))
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", lambda thread_id: [])

    events = list(agent.stream_chat_only(
        "hi",
        {"configurable": {"thread_id": "thread-chat", "model_override": selected}},
    ))

    errors = [payload for event_type, payload in events if event_type == "error"]
    assert errors
    assert "vendor/non-tool-chat:14b" in errors[0]
    assert "agent-default:cloud" not in errors[0]


def test_friendly_api_error_does_not_call_generic_400_tool_error(monkeypatch):
    import agent

    message = agent._friendly_api_error("status code: 400: bad request", "model:ollama:local-chat:14b")

    assert "does not support tool calling" not in message
    assert "API error" in message


def test_stream_agent_auto_routes_visible_chat_only_without_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    import agent
    import providers.readiness as readiness

    def _boom(*args, **kwargs):
        raise AssertionError("Agent graph should not be constructed for Chat Only routing")

    monkeypatch.setattr(agent, "get_agent_graph", _boom)
    monkeypatch.setattr(agent, "stream_chat_only", lambda *args, **kwargs: iter([("token", "chat"), ("done", "chat")]))
    captured = {}

    def _readiness(model_label, **kwargs):
        captured["probe_ollama_tools"] = kwargs.get("probe_ollama_tools")
        return SimpleNamespace(selected_mode="chat_only", selection_reason="chat ready")

    monkeypatch.setattr(readiness, "evaluate_runtime_readiness", _readiness)

    events = list(agent.stream_agent(
        "hi",
        [],
        {"configurable": {"thread_id": "thread-chat", "runtime_surface": "normal_chat", "runtime_mode": "auto"}},
    ))

    assert events == [("token", "chat"), ("done", "chat")]
    assert captured["probe_ollama_tools"] is False


def test_stream_agent_logs_resolved_runtime_decision(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    import agent
    import providers.readiness as readiness

    monkeypatch.setattr(
        readiness,
        "evaluate_runtime_readiness",
        lambda model_label, **kwargs: SimpleNamespace(
            selected_mode="agent",
            selection_reason="agent ready",
            agent=SimpleNamespace(context_window=65536),
            chat=SimpleNamespace(context_window=65536),
        ),
    )
    monkeypatch.setattr(agent, "get_agent_graph", lambda *args, **kwargs: object())
    monkeypatch.setattr(agent, "_should_summarize", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_stream_graph", lambda *args, **kwargs: iter([("done", "agent")]))

    with caplog.at_level(logging.INFO, logger="agent"):
        events = list(agent.stream_agent(
            "hi",
            ["thoth_status"],
            {"configurable": {"thread_id": "thread-chat", "runtime_surface": "normal_chat", "runtime_mode": "auto"}},
        ))

    assert events == [("done", "agent")]
    runtime_logs = [record.message for record in caplog.records if "runtime decision:" in record.message]
    assert runtime_logs
    assert "requested=auto" in runtime_logs[-1]
    assert "selected=agent" in runtime_logs[-1]
    assert "tools_enabled=1" in runtime_logs[-1]
    assert "tools_bound=True" in runtime_logs[-1]


def test_thoth_status_model_reports_effective_runtime(monkeypatch):
    import agent
    import models
    import providers.readiness as readiness
    import providers.resolution as resolution
    import tools.thoth_status_tool as thoth_status_tool

    override_token = models._active_model_override.set("model:ollama:local-chat:14b")
    agent._set_active_runtime_context(
        thread_id="thread-chat",
        runtime_surface="normal_chat",
        requested_runtime_mode="auto",
        selected_runtime_mode="chat_only",
        runtime_reason="chat model",
        model_override="model:ollama:local-chat:14b",
        enabled_tool_names=(),
    )
    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:qwen3.6:27b")
    monkeypatch.setattr(models, "get_context_size", lambda model=None: 32768)
    monkeypatch.setattr(models, "get_provider_emoji", lambda model: "model")
    monkeypatch.setattr(models, "get_user_context_size", lambda: 32768)
    monkeypatch.setattr(models, "get_cloud_context_size", lambda: 131072)
    monkeypatch.setattr(
        resolution,
        "resolve_provider_config",
        lambda *args, **kwargs: SimpleNamespace(
            runtime_model="local-chat:14b",
            provider_id="ollama",
            provider_display_name="Ollama Local",
            execution_location="local",
            risk_label="local_private",
        ),
    )
    monkeypatch.setattr(
        readiness,
        "evaluate_runtime_readiness",
        lambda *args, **kwargs: SimpleNamespace(
            selected_mode="chat_only",
            selection_reason="chat ready",
        ),
    )

    try:
        output = thoth_status_tool._query_model()
    finally:
        models._active_model_override.reset(override_token)

    assert "model:ollama:local-chat:14b" in output
    assert "Runtime model: local-chat:14b" in output
    assert "Readiness: Chat Only - tools and actions are off (chat ready)" in output
    assert "Active turn runtime: Chat Only - tools and actions are off, requested auto on normal_chat" in output
    assert "Override active (global default: model:ollama:qwen3.6:27b)" in output


def test_ollama_parameter_schema_error_is_agent_mode_failure():
    import agent

    message = "expected element type <function> but have <parameter> (status code: -1)"

    assert agent._tool_support_error(message) is True
    assert "does not support tool calling" in agent._friendly_api_error(
        message,
        "model:ollama:qwen3.6:27b",
    )


def test_stream_agent_auto_does_not_silently_fallback_on_tool_schema_error(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    import agent
    import providers.readiness as readiness

    message = "expected element type <function> but have <parameter> (status code: -1)"
    monkeypatch.setattr(
        readiness,
        "evaluate_runtime_readiness",
        lambda model_label, **kwargs: SimpleNamespace(selected_mode="agent", selection_reason="agent ready"),
    )
    monkeypatch.setattr(agent, "get_agent_graph", lambda *args, **kwargs: object())
    monkeypatch.setattr(agent, "_should_summarize", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_stream_graph", lambda *args, **kwargs: iter([("error", message)]))
    monkeypatch.setattr(agent, "stream_chat_only", lambda *args, **kwargs: iter([("token", "chat"), ("done", "chat")]))

    events = list(agent.stream_agent(
        "hi",
        [],
        {"configurable": {"thread_id": "thread-chat", "runtime_surface": "normal_chat", "runtime_mode": "auto"}},
    ))

    assert events == [("error", message)]


def test_agent_runtime_context_overrides_stale_chat_only_claims():
    import agent

    context = agent._agent_runtime_system_context()

    assert "Agent Mode is active" in context
    assert "Do not claim this turn is Chat Only" in context
    assert "long-term memory" in context


def test_agent_graph_uses_provider_qualified_override(monkeypatch):
    import agent

    captured = {}
    def _ready(model_label):
        captured["ready"] = model_label
        return SimpleNamespace(
            provider_id="ollama",
            runtime_model="vendor/non-tool-chat:14b",
            capability_source="test",
            confidence="high",
        )

    def _llm(model_label):
        captured["llm"] = model_label
        return object()

    monkeypatch.setattr(agent, "get_current_model", lambda: "model:ollama:qwen3:14b")
    monkeypatch.setattr(agent, "_ensure_agent_mode_ready", _ready)
    monkeypatch.setattr(agent, "get_llm_for", _llm)
    monkeypatch.setattr(agent, "get_context_size", lambda model_label=None: 32768)
    monkeypatch.setattr(agent, "create_react_agent", lambda **kwargs: {"model": kwargs["model"], "tools": kwargs["tools"]})
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: None)
    monkeypatch.setattr(agent, "get_agent_system_prompt", lambda: "system")
    agent._agent_cache.clear()

    graph = agent.get_agent_graph([], model_override="model:ollama:vendor/non-tool-chat:14b")

    assert graph
    assert captured["ready"] == "model:ollama:vendor/non-tool-chat:14b"
    assert captured["llm"] == "model:ollama:vendor/non-tool-chat:14b"


def test_stream_agent_forced_workflow_uses_agent_path(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / ".thoth"))
    import agent

    monkeypatch.setattr(agent, "get_agent_graph", lambda *args, **kwargs: object())
    monkeypatch.setattr(agent, "_should_summarize", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_stream_graph", lambda *args, **kwargs: iter([("done", "agent")]))
    monkeypatch.setattr(
        agent,
        "stream_chat_only",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("workflow must not route Chat Only")),
    )

    events = list(agent.stream_agent(
        "run workflow",
        [],
        {"configurable": {"thread_id": "workflow-thread", "runtime_surface": "workflow", "runtime_mode": "agent"}},
    ))

    assert events == [("done", "agent")]


def test_stream_agent_coerces_string_recursion_limit(monkeypatch):
    import agent

    captured = {}

    class FakeGraph:
        def stream(self, input_data, config=None, stream_mode=None):
            captured["recursion_limit"] = config["recursion_limit"]
            assert config["recursion_limit"] > 0
            return iter([])

        def get_state(self, config):
            return None

    monkeypatch.setattr(agent, "get_agent_graph", lambda *args, **kwargs: FakeGraph())
    monkeypatch.setattr(agent, "_should_summarize", lambda *args, **kwargs: False)

    events = list(agent.stream_agent(
        "hi",
        [],
        {
            "configurable": {
                "thread_id": "thread-agent",
                "runtime_surface": "workflow",
                "runtime_mode": "agent",
            },
            "recursion_limit": "50",
        },
    ))

    assert events == [("done", "")]
    assert captured["recursion_limit"] == 50
    assert isinstance(captured["recursion_limit"], int)


def test_forced_agent_surfaces_are_wired_in_callers():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ui_streaming = (root / "ui" / "streaming.py").read_text(encoding="utf-8")
    tasks = (root / "tasks.py").read_text(encoding="utf-8")
    approval = (root / "channels" / "approval.py").read_text(encoding="utf-8")
    task_dialog = (root / "ui" / "task_dialog.py").read_text(encoding="utf-8")

    assert '"runtime_surface": runtime_surface' in ui_streaming
    assert '"runtime_mode": "agent"' in ui_streaming
    assert "_agent_ready_forced_surface" in ui_streaming
    assert 'append_checkpoint_messages(gen.thread_id' in ui_streaming
    assert 'model_override=configurable.get("model_override")' in ui_streaming
    assert "get_agent_graph()" not in ui_streaming
    assert '"runtime_surface": "workflow"' in tasks
    assert '"runtime_mode": "agent"' in tasks
    assert '"runtime_surface": "approval"' in approval
    assert '"runtime_mode": "agent"' in approval
    assert "evaluate_agent_readiness(cur_model_ov)" in task_dialog
    assert "Workflows require Agent Mode" in task_dialog
