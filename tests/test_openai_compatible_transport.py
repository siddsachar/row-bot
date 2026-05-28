import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from providers.tool_protocol import format_validation_retry_result
from providers.transports.openai_compatible import ChatOpenAICompatible


class _Response:
    status_code = 200
    text = "{}"

    def __init__(self, payload=None):
        self._payload = payload or {"choices": [{"message": {"content": "hello"}}]}

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _StreamResponse:
    status_code = 200
    text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}'
        yield b'data: {"choices":[{"delta":{"content":"answer"}}]}'
        yield b"data: [DONE]"


class _StreamingClient(_Client):
    def stream(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        return _StreamResponse()


class _EmptyStreamResponse:
    status_code = 200
    text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        yield b"data: [DONE]"


class _EmptyStreamingClient(_Client):
    def stream(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        return _EmptyStreamResponse()


class _ToolStreamResponse:
    status_code = 200
    text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\\"q\\":\\"x\\"}"}}]}}]}'
        yield b"data: [DONE]"


class _ToolStreamingClient(_Client):
    def stream(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        return _ToolStreamResponse()


class _ReasoningTextToolStreamResponse:
    status_code = 200
    text = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        yield b'data: {"choices":[{"delta":{"reasoning_content":"I should retry.\\n<tool_call>\\n<function=thoth_status>\\n<parameter=category>\\ntools\\n</parameter>\\n</function>\\n</tool_call>"}}]}'
        yield b"data: [DONE]"


class _ReasoningTextToolStreamingClient(_Client):
    def stream(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        return _ReasoningTextToolStreamResponse()


class _ReasoningOnlyStreamResponse:
    status_code = 200
    text = ""

    def __init__(self, reasoning: str):
        self.reasoning = reasoning

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        payload = {
            "choices": [{
                "delta": {
                    "reasoning_content": self.reasoning,
                }
            }]
        }
        yield f"data: {json.dumps(payload)}".encode("utf-8")
        yield b"data: [DONE]"


class _ReasoningOnlyStreamingClient(_Client):
    def __init__(self, reasoning: str):
        super().__init__()
        self.reasoning = reasoning

    def stream(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        return _ReasoningOnlyStreamResponse(self.reasoning)


class _UnreadErrorStreamResponse:
    status_code = 400

    @property
    def text(self):
        raise RuntimeError("Attempted to access streaming response content")

    def json(self):
        raise RuntimeError("response body has not been read")

    def read(self):
        return b'{"error":{"message":"prompt exceeds context window"}}'

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_lines(self):
        return iter(())


class _UnreadErrorStreamingClient(_Client):
    def stream(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        return _UnreadErrorStreamResponse()


class _ReasoningResponse(_Response):
    def __init__(self):
        super().__init__({
            "choices": [{
                "message": {
                    "content": "hello",
                    "reasoning_content": "thinking",
                },
            }],
        })


def test_openai_compatible_transport_moves_system_messages_first():
    client = _Client()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:8000/v1",
        endpoint={"system_message_mode": "system_first", "message_content_mode": "string_text"},
        http_client=client,
    )

    model.invoke([HumanMessage(content="hi"), SystemMessage(content="rules")])

    body = client.calls[0][1]["json"]
    assert body["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hi"},
    ]


def test_openai_compatible_transport_consolidates_multiple_system_messages():
    client = _Client()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:8080/v1",
        endpoint={"system_message_mode": "system_first", "message_content_mode": "string_text"},
        http_client=client,
    )

    model.invoke([
        SystemMessage(content="Root rules"),
        HumanMessage(content="hi"),
        SystemMessage(content="Late context"),
        HumanMessage(content="continue"),
    ])

    body = client.calls[0][1]["json"]
    assert body["messages"] == [
        {"role": "system", "content": "Root rules\n\nLate context"},
        {"role": "user", "content": "hi"},
        {"role": "user", "content": "continue"},
    ]


def test_openai_compatible_transport_keeps_native_tool_history_for_custom_profiles():
    client = _Client()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:8000/v1",
        endpoint={
            "system_message_mode": "system_first",
            "message_content_mode": "string_text",
            "tool_history_mode": "native_required",
            "drop_unsupported_params": True,
        },
        http_client=client,
    )

    model.invoke([
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"q": "x"}, "id": "call_1"}]),
        ToolMessage(content="result", tool_call_id="call_1"),
    ], tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}])

    body = client.calls[0][1]["json"]
    assert "tools" in body
    assert body["messages"][1]["tool_calls"][0]["function"]["name"] == "lookup"
    assert body["messages"][-1] == {"role": "tool", "content": "result", "tool_call_id": "call_1"}


def test_openai_compatible_transport_applies_runtime_context_param(monkeypatch):
    monkeypatch.setattr("models.get_context_size", lambda model_name=None: 32_768)
    client = _Client()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:8080/v1",
        endpoint={
            "provider_id": "custom_openai_llamacpp",
            "supports_runtime_context_override": True,
            "context_param_name": "n_ctx",
        },
        http_client=client,
    )

    model.invoke([HumanMessage(content="hi")])

    body = client.calls[0][1]["json"]
    assert body["n_ctx"] == 32_768


def test_openai_compatible_transport_preserves_reasoning_content():
    client = _Client()
    client.post = lambda url, **kwargs: _Response({
        "choices": [{
            "message": {
                "content": "hello",
                "reasoning_content": "thinking",
            },
        }],
    })
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={"provider_id": "custom_openai_lm-studio"},
        http_client=client,
    )

    result = model.invoke([HumanMessage(content="hi")])

    assert result.content == "hello"
    assert result.additional_kwargs["reasoning_content"] == "thinking"


def test_openai_compatible_transport_streams_reasoning_chunks():
    client = _StreamingClient()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={"provider_id": "custom_openai_lm-studio"},
        http_client=client,
    )

    chunks = list(model.stream([HumanMessage(content="hi")]))

    assert chunks[0].additional_kwargs["reasoning_content"] == "thinking"
    assert chunks[1].content == "answer"


def test_openai_compatible_transport_falls_back_when_probe_marks_streaming_bad():
    client = _Client()
    client.post = lambda url, **kwargs: client.calls.append((url, kwargs)) or _ReasoningResponse()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={
            "provider_id": "custom_openai_lm-studio",
            "last_probe": {"streaming_ok": False},
        },
        http_client=client,
    )

    chunks = list(model.stream([HumanMessage(content="hi")]))

    body = client.calls[0][1]["json"]
    assert body["stream"] is False
    assert chunks[0].additional_kwargs["reasoning_content"] == "thinking"
    assert chunks[1].content == "hello"


def test_openai_compatible_transport_retries_empty_stream_with_non_stream():
    client = _EmptyStreamingClient()
    client.post = lambda url, **kwargs: client.calls.append((url, kwargs)) or _ReasoningResponse()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={
            "provider_id": "custom_openai_lm-studio",
            "last_probe": {"streaming_ok": True},
        },
        http_client=client,
    )

    chunks = list(model.stream([HumanMessage(content="hi")]))

    stream_body = client.calls[0][1]["json"]
    fallback_body = client.calls[1][1]["json"]
    assert stream_body["stream"] is True
    assert fallback_body["stream"] is False
    assert chunks[0].additional_kwargs["reasoning_content"] == "thinking"
    assert chunks[1].content == "hello"


def test_openai_compatible_transport_streams_tool_call_chunks():
    client = _ToolStreamingClient()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={
            "provider_id": "custom_openai_tools",
            "last_probe": {"streaming_ok": True, "tool_calling": True},
        },
        http_client=client,
    )

    chunks = list(model.stream([HumanMessage(content="weather?")]))

    tool_chunks = [chunk for chunk in chunks if chunk.tool_call_chunks]
    assert tool_chunks[0].tool_call_chunks == [{
        "name": "lookup",
        "args": '{"q":"x"}',
        "id": "call_1",
        "index": 0,
        "type": "tool_call_chunk",
    }]


def test_openai_compatible_transport_recovers_reasoning_text_tool_call():
    client = _ReasoningTextToolStreamingClient()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={
            "provider_id": "custom_openai_tools",
            "last_probe": {"streaming_ok": True, "tool_calling": True},
        },
        http_client=client,
    )

    chunks = list(model.stream([HumanMessage(content="what tools?")]))

    assert chunks[0].additional_kwargs["reasoning_content"].startswith("I should retry.")
    tool_chunks = [chunk for chunk in chunks if chunk.tool_call_chunks]
    assert tool_chunks[0].tool_call_chunks == [{
        "name": "thoth_status",
        "args": '{"category": "tools"}',
        "id": "text_call_0",
        "index": 0,
        "type": "tool_call_chunk",
    }]


def test_openai_compatible_transport_promotes_reasoning_only_after_successful_tool_result():
    client = _Client()
    client.post = lambda url, **kwargs: _Response({
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": "Enabled tools: 29. Disabled tools: 3.",
            },
        }],
    })
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={"provider_id": "custom_openai_tools"},
        http_client=client,
    )

    result = model.invoke([
        HumanMessage(content="what tools?"),
        AIMessage(content="", tool_calls=[{"name": "thoth_status", "args": {"category": "tools"}, "id": "call_1"}]),
        ToolMessage(content="**Tools** (29 enabled, 3 disabled)", name="thoth_status", tool_call_id="call_1"),
    ])

    assert result.content == "Enabled tools: 29. Disabled tools: 3."
    assert "reasoning_content" not in result.additional_kwargs


def test_openai_compatible_transport_stream_promotes_reasoning_only_after_successful_tool_result():
    client = _ReasoningOnlyStreamingClient("Enabled tools: 29. Disabled tools: 3.")
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={"provider_id": "custom_openai_tools"},
        http_client=client,
    )

    chunks = list(model.stream([
        HumanMessage(content="what tools?"),
        AIMessage(content="", tool_calls=[{"name": "thoth_status", "args": {"category": "tools"}, "id": "call_1"}]),
        ToolMessage(content="**Tools** (29 enabled, 3 disabled)", name="thoth_status", tool_call_id="call_1"),
    ]))

    assert chunks[0].additional_kwargs["reasoning_content"] == "Enabled tools: 29. Disabled tools: 3."
    assert any(chunk.content == "Enabled tools: 29. Disabled tools: 3." for chunk in chunks)


def test_openai_compatible_transport_rejects_reasoning_only_after_validation_repair():
    client = _Client()
    client.post = lambda url, **kwargs: _Response({
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": "I need to provide a valid category argument.",
            },
        }],
    })
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={"provider_id": "custom_openai_tools", "display_name": "Local Qwen"},
        http_client=client,
    )
    repair = format_validation_retry_result(
        tool_name="thoth_status",
        detail="missing or invalid required argument: category",
        fields=["category"],
    )

    with pytest.raises(RuntimeError, match="stopped after a tool error"):
        model.invoke([
            HumanMessage(content="what tools?"),
            AIMessage(content="", tool_calls=[{"name": "thoth_status", "args": {}, "id": "call_1"}]),
            ToolMessage(content=repair, name="thoth_status", tool_call_id="call_1"),
        ])


def test_openai_compatible_transport_still_recovers_text_tool_call_after_validation_repair():
    client = _ReasoningTextToolStreamingClient()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={
            "provider_id": "custom_openai_tools",
            "last_probe": {"streaming_ok": True, "tool_calling": True},
        },
        http_client=client,
    )
    repair = format_validation_retry_result(
        tool_name="thoth_status",
        detail="missing or invalid required argument: category",
        fields=["category"],
    )

    chunks = list(model.stream([
        HumanMessage(content="what tools?"),
        AIMessage(content="", tool_calls=[{"name": "thoth_status", "args": {}, "id": "call_1"}]),
        ToolMessage(content=repair, name="thoth_status", tool_call_id="call_1"),
    ]))

    tool_chunks = [chunk for chunk in chunks if chunk.tool_call_chunks]
    assert tool_chunks[0].tool_call_chunks[0]["name"] == "thoth_status"
    assert tool_chunks[0].tool_call_chunks[0]["args"] == '{"category": "tools"}'


def test_openai_compatible_transport_does_not_recover_text_tool_call_when_content_exists():
    client = _Client()
    client.post = lambda url, **kwargs: _Response({
        "choices": [{
            "message": {
                "content": "Here is text.",
                "reasoning_content": (
                    "<tool_call><function=thoth_status>"
                    "<parameter=category>tools</parameter>"
                    "</function></tool_call>"
                ),
            },
        }],
    })
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:1234/v1",
        endpoint={"provider_id": "custom_openai_tools"},
        http_client=client,
    )

    result = model.invoke([HumanMessage(content="what tools?")])

    assert result.content == "Here is text."
    assert result.tool_calls == []


def test_openai_compatible_transport_reports_unread_stream_error_body():
    client = _UnreadErrorStreamingClient()
    model = ChatOpenAICompatible(
        model_name="qwen",
        base_url="http://127.0.0.1:8080/v1",
        endpoint={"display_name": "llama.cpp local"},
        http_client=client,
    )

    try:
        list(model.stream([HumanMessage(content="hi")]))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected streaming HTTP 400 to raise")

    assert "llama.cpp local rejected the chat request (HTTP 400)" in message
    assert "prompt exceeds context window" in message
    assert "streaming response content" not in message
