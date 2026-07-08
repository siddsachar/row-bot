import base64
import json
import threading

import row_bot.providers.config as provider_config
from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.providers.xai_catalog import XAI_COMPOSER_MODEL_ID
from row_bot.providers.xai_oauth import XAIOAuthTokenSet, save_xai_oauth_tokens
from row_bot.secret_store import _set_backend_for_tests


class _MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class _SSETextResponse:
    def __init__(self, status_code=200, events=None, payload=None, text=""):
        self.status_code = status_code
        self.events = list(events or [])
        self._payload = payload if payload is not None else {}
        self.text = text

    def iter_lines(self):
        for event in self.events:
            if isinstance(event, bytes):
                yield event
            else:
                yield str(event).encode("utf-8")

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _jwt(claims):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).rstrip(b"=").decode("ascii")

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.sig"


def _valid_token(exp=1893456000):
    return _jwt({"exp": exp, "sub": "user-123"})


def _sse_event(event_type, payload):
    data = dict(payload)
    data.setdefault("type", event_type)
    return [
        f"event: {event_type}",
        f"data: {json.dumps(data)}",
        "",
    ]


def _text_sse(*parts):
    lines = []
    for part in parts:
        lines.extend(_sse_event("response.output_text.delta", {"delta": part}))
    lines.extend(_sse_event("response.completed", {
        "response": {
            "id": "resp_123",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    }))
    return lines


def test_xai_oauth_request_uses_bearer_responses_body_and_vision_blocks(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    access_token = _valid_token()
    client = _HttpClient([_SSETextResponse(payload={
        "id": "resp_vision",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello"}],
        }],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    })])
    model = ChatXAIOAuthResponses(
        model_name="grok-4",
        base_url="https://api.x.ai/v1",
        http_client=client,
    )
    tool = {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up an order.",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
        },
    }
    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=access_token, refresh_token="refresh-token"))
        result = model.invoke([
            SystemMessage(content="Be brief."),
            HumanMessage(content=[
                {"type": "text", "text": "What do you see?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "low"}},
            ]),
        ], tools=[tool], tool_choice="auto")
    finally:
        _set_backend_for_tests(None)

    assert result.content == "Hello"
    assert result.response_metadata["response_id"] == "resp_vision"
    assert result.response_metadata["token_usage"] == {"input_tokens": 3, "output_tokens": 2}
    assert client.closed is False
    assert client.calls[0][0] == "https://api.x.ai/v1/responses"
    request = client.calls[0][1]
    assert request["headers"] == {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": request["headers"]["User-Agent"],
    }
    assert request["headers"]["User-Agent"].startswith("Row-Bot/")
    body = request["json"]
    assert body["model"] == "grok-4"
    assert body["instructions"] == "Be brief."
    assert body["store"] is False
    assert "stream" not in body
    assert body["input"] == [{
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "What do you see?"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "low"},
        ],
    }]
    assert body["tools"] == [{
        "type": "function",
        "name": "lookup_order",
        "description": "Look up an order.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    }]
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is True
    assert "x-api-key" not in request["headers"]


def test_xai_oauth_replays_tool_calls_and_reads_response_tool_calls(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    client = _HttpClient([_SSETextResponse(events=[
        *_sse_event("response.output_item.done", {
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup_order",
                "arguments": "{\"order_id\":\"42\"}",
            },
        }),
        *_sse_event("response.completed", {"response": {"id": "resp_tool"}}),
    ])])
    model = ChatXAIOAuthResponses(model_name="grok-4", http_client=client)
    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-token"))
        result = model.invoke([
            HumanMessage(content="lookup"),
            AIMessage(content="", tool_calls=[{
                "name": "lookup_order",
                "args": {"order_id": "41"},
                "id": "call_old",
                "type": "tool_call",
            }]),
            ToolMessage(content="Order 41 shipped.", name="lookup_order", tool_call_id="call_old"),
        ])
    finally:
        _set_backend_for_tests(None)

    assert result.tool_calls == [{
        "name": "lookup_order",
        "args": {"order_id": "42"},
        "id": "call_1",
        "type": "tool_call",
    }]
    assert client.calls[0][1]["json"]["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "lookup"}]},
        {"type": "function_call", "call_id": "call_old", "name": "lookup_order", "arguments": "{\"order_id\":\"41\"}"},
        {"type": "function_call_output", "call_id": "call_old", "output": "Order 41 shipped."},
    ]


def test_xai_oauth_ignores_non_function_output_items(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    client = _HttpClient([_SSETextResponse(events=[
        *_sse_event("response.output_text.delta", {"delta": "answer"}),
        *_sse_event("response.output_item.done", {
            "item": {
                "type": "web_search_call",
                "status": "in_progress",
            },
        }),
        *_sse_event("response.completed", {"response": {"id": "resp_composer"}}),
    ])])
    model = ChatXAIOAuthResponses(model_name=XAI_COMPOSER_MODEL_ID, http_client=client)
    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-token"))
        result = model.invoke([HumanMessage(content="hello")])
    finally:
        _set_backend_for_tests(None)

    assert result.content == "answer"
    assert result.tool_calls == []
    assert result.response_metadata["response_id"] == "resp_composer"


def test_xai_oauth_streams_text_tool_chunks_and_last_marker(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    client = _HttpClient([_SSETextResponse(events=[
        *_sse_event("response.output_text.delta", {"delta": "Hi"}),
        *_sse_event("response.output_item.done", {
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup_order",
                "arguments": "{\"order_id\":\"42\"}",
            },
        }),
    ])])
    model = ChatXAIOAuthResponses(model_name="grok-4", http_client=client)
    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-token"))
        chunks = list(model._stream([HumanMessage(content="hello")]))
    finally:
        _set_backend_for_tests(None)

    assert chunks[0].message.content == "Hi"
    assert chunks[1].message.tool_call_chunks[0] == {
        "name": "lookup_order",
        "args": "{\"order_id\":\"42\"}",
        "id": "call_1",
        "index": 0,
        "type": "tool_call_chunk",
    }
    assert chunks[-1].message.chunk_position == "last"


def test_xai_oauth_refreshes_once_after_401(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    import row_bot.providers.transports.xai_oauth_responses as transport
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    old_token = _valid_token(exp=1893456000)
    new_token = _valid_token(exp=1893457000)
    client = _HttpClient([
        _SSETextResponse(status_code=401, payload={"error": {"message": "expired"}}, text="expired"),
        _SSETextResponse(events=_text_sse("after refresh")),
    ])
    monkeypatch.setattr(
        transport.xai_auth,
        "refresh_xai_oauth_token",
        lambda refresh_token: XAIOAuthTokenSet(access_token=new_token, refresh_token=refresh_token),
    )
    model = ChatXAIOAuthResponses(model_name="grok-4", http_client=client)
    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=old_token, refresh_token="refresh-token"))
        result = model.invoke([HumanMessage(content="hello")])
    finally:
        _set_backend_for_tests(None)

    assert result.content == "after refresh"
    assert client.calls[0][1]["headers"]["Authorization"] == f"Bearer {old_token}"
    assert client.calls[1][1]["headers"]["Authorization"] == f"Bearer {new_token}"


def test_xai_oauth_403_is_actionable_without_runtime_fallback(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    client = _HttpClient([
        _SSETextResponse(
            status_code=403,
            payload={"error": {"message": "subscription required"}},
            text="subscription required",
        ),
    ])
    model = ChatXAIOAuthResponses(model_name="grok-4", http_client=client)
    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-token"))
        try:
            model.invoke([HumanMessage(content="hello")])
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected 403 to raise")
    finally:
        _set_backend_for_tests(None)

    assert "xAI OAuth access denied" in message
    assert "separate xAI API key provider" in message
    assert "fallback" not in message.lower()


def test_xai_oauth_stream_cancellation_closes_blocking_response(tmp_path, monkeypatch):
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    class _BlockingResponse:
        status_code = 200
        text = ""

        def __init__(self) -> None:
            self.iterating = threading.Event()
            self.closed = threading.Event()
            self.close_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            self.close_calls += 1
            self.closed.set()

        def iter_lines(self):
            self.iterating.set()
            assert self.closed.wait(timeout=1)
            if False:
                yield b""

    class _StreamingClient(_HttpClient):
        def __init__(self) -> None:
            super().__init__([])
            self.response = _BlockingResponse()

        def stream(self, method, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.response

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    client = _StreamingClient()
    model = ChatXAIOAuthResponses(model_name="grok-4", http_client=client)
    scope = CancellationScope()
    events: list[dict] = []

    def consume() -> None:
        with use_cancellation_scope(scope):
            events.extend(model._iter_response_events({"stream": True, "input": []}))

    try:
        save_xai_oauth_tokens(XAIOAuthTokenSet(access_token=_valid_token(), refresh_token="refresh-token"))
        worker = threading.Thread(target=consume)
        worker.start()
        assert client.response.iterating.wait(timeout=1)
        scope.cancel("test")
        worker.join(timeout=1)
    finally:
        _set_backend_for_tests(None)

    assert not worker.is_alive()
    assert client.response.close_calls == 1
    assert events == []
