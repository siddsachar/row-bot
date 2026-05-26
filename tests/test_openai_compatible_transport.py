from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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
