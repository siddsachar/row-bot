import base64
import json
from types import SimpleNamespace

import row_bot.providers.config as provider_config
from row_bot.providers.claude_subscription import ClaudeSubscriptionTokenSet, save_claude_subscription_oauth_tokens
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


class _SDKError(Exception):
    def __init__(self, status_code, text):
        super().__init__(text)
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, text=text)


class _FakeStream:
    def __init__(self, events):
        self.events = list(events)

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeMessages:
    def __init__(self, *, creates=None, streams=None):
        self.creates = list(creates or [])
        self.streams = list(streams or [])
        self.create_calls = []
        self.stream_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if not self.creates:
            raise AssertionError("No fake create response queued")
        response = self.creates.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        if not self.streams:
            raise AssertionError("No fake stream response queued")
        response = self.streams.pop(0)
        if isinstance(response, Exception):
            raise response
        return _FakeStream(response)


class _FakeAnthropicClient:
    def __init__(self, *, creates=None, streams=None):
        self.messages = _FakeMessages(creates=creates, streams=streams)


class _ClientFactory:
    def __init__(self, clients):
        self.clients = list(clients)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.clients:
            raise AssertionError("No fake Anthropic client queued")
        return self.clients.pop(0)


def _jwt(claims):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).rstrip(b"=").decode("ascii")

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}.sig"


def _valid_token(exp=1893456000):
    return _jwt({"exp": exp, "sub": "user-123"})


def _save_runtime_tokens(access_token=None, refresh_token="refresh-token"):
    token = access_token or _valid_token()
    save_claude_subscription_oauth_tokens(ClaudeSubscriptionTokenSet(
        access_token=token,
        refresh_token=refresh_token,
    ))
    return token


def test_runtime_factory_builds_claude_subscription_transport(tmp_path, monkeypatch):
    import row_bot.providers.runtime as runtime
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    try:
        _save_runtime_tokens()
        model = runtime.create_chat_model("claude-sonnet-4-6", provider_id="claude_subscription")
    finally:
        _set_backend_for_tests(None)

    assert isinstance(model, ChatClaudeSubscriptionMessages)
    assert model.model_name == "claude-sonnet-4-6"
    assert model.base_url == "https://api.anthropic.com"


def test_runtime_factory_requires_row_bot_oauth_tokens(tmp_path, monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    try:
        try:
            runtime.create_chat_model("claude-sonnet-4-6", provider_id="claude_subscription")
        except ValueError as exc:
            assert "Row-Bot-owned OAuth tokens" in str(exc)
        else:
            raise AssertionError("Expected Claude Subscription runtime to require Row-Bot OAuth")
    finally:
        _set_backend_for_tests(None)


def test_claude_subscription_sdk_client_uses_oauth_auth_token_not_api_key(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-used")
    _set_backend_for_tests(_MemoryKeyring())
    access_token = _save_runtime_tokens()
    client = _FakeAnthropicClient(creates=[{
        "id": "msg_1",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "Hello"}],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }])
    factory = _ClientFactory([client])
    model = ChatClaudeSubscriptionMessages(
        model_name="claude-sonnet-4-6",
        base_url="https://api.anthropic.test/v1",
        client_factory=factory,
    )
    try:
        result = model.invoke([HumanMessage(content="hello")])
    finally:
        _set_backend_for_tests(None)

    assert result.content == "Hello"
    client_kwargs = factory.calls[0]
    assert client_kwargs["auth_token"] == access_token
    assert client_kwargs["base_url"] == "https://api.anthropic.test"
    assert "api_key" not in client_kwargs
    headers = client_kwargs["default_headers"]
    assert "interleaved-thinking-2025-05-14" in headers["anthropic-beta"]
    assert "fine-grained-tool-streaming-2025-05-14" in headers["anthropic-beta"]
    assert "claude-code-20250219" in headers["anthropic-beta"]
    assert "oauth-2025-04-20" in headers["anthropic-beta"]
    assert "context-1m-2025-08-07" not in headers["anthropic-beta"]
    assert headers["user-agent"].startswith("claude-cli/")
    assert headers["x-app"] == "cli"


def test_claude_subscription_request_adds_compat_system_without_stream_flag(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage
    from row_bot.providers import claude_subscription as claude_auth
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(creates=[{"content": [{"type": "text", "text": "Hello"}]}])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    try:
        result = model.invoke([SystemMessage(content="Be brief."), HumanMessage(content="hello")])
    finally:
        _set_backend_for_tests(None)

    assert result.content == "Hello"
    request = client.messages.create_calls[0]
    assert "stream" not in request
    assert request["model"] == "claude-sonnet-4-6"
    assert request["system"] == [
        {"type": "text", "text": claude_auth.CLAUDE_SUBSCRIPTION_CLAUDE_CODE_SYSTEM_PREFIX},
        {"type": "text", "text": "Be brief."},
    ]
    assert request["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_claude_subscription_preserves_image_blocks_and_prefixes_bare_tools(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(creates=[{"content": [{"type": "text", "text": "seen"}]}])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    message = HumanMessage(content=[
        {"type": "text", "text": "What do you see?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ])
    tools = [
        {"type": "function", "function": {"name": "lookup", "description": "Lookup.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "mcp_filesystem_read_file", "description": "Read.", "parameters": {"type": "object", "properties": {}}}},
    ]

    try:
        result = model.invoke([message], tools=tools)
    finally:
        _set_backend_for_tests(None)

    assert result.content == "seen"
    request = client.messages.create_calls[0]
    assert request["messages"][0]["content"] == [
        {"type": "text", "text": "What do you see?"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
    ]
    assert [tool["name"] for tool in request["tools"]] == ["mcp_lookup", "mcp_filesystem_read_file"]


def test_claude_subscription_replays_tool_calls_with_wire_prefix_and_results_unchanged(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(creates=[{"content": [{"type": "text", "text": "done"}]}])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    messages = [
        HumanMessage(content="lookup"),
        AIMessage(content="", tool_calls=[{"name": "lookup_order", "args": {"order_id": "42"}, "id": "toolu_1", "type": "tool_call"}]),
        ToolMessage(content="Order 42 shipped.", name="lookup_order", tool_call_id="toolu_1"),
    ]

    try:
        result = model.invoke(messages)
    finally:
        _set_backend_for_tests(None)

    assert result.content == "done"
    assert client.messages.create_calls[0]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "lookup"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "mcp_lookup_order", "input": {"order_id": "42"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "Order 42 shipped."}]},
    ]


def test_claude_subscription_maps_oauth_prefixed_tool_use_response(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    import row_bot.providers.transports.claude_subscription_messages as claude_transport
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(claude_transport.claude_auth, "_known_row_bot_tool", lambda name: name == "lookup_order")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(creates=[{
        "content": [{
            "type": "tool_use",
            "id": "toolu_1",
            "name": "mcp_lookup_order",
            "input": {"order_id": "42"},
        }],
        "usage": {"input_tokens": 4, "output_tokens": 8},
    }])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    try:
        result = model.invoke([HumanMessage(content="lookup")])
    finally:
        _set_backend_for_tests(None)

    assert result.tool_calls == [{"name": "lookup_order", "args": {"order_id": "42"}, "id": "toolu_1", "type": "tool_call"}]
    assert result.response_metadata["token_usage"] == {"input_tokens": 4, "output_tokens": 8}


def test_claude_subscription_runtime_tool_name_knows_langchain_tool_names():
    from row_bot.providers.claude_subscription import claude_subscription_runtime_tool_name

    assert claude_subscription_runtime_tool_name("mcp_calculate") == "calculate"


def test_claude_subscription_preserves_native_mcp_tool_response_name(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    import row_bot.providers.transports.claude_subscription_messages as claude_transport
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(
        claude_transport.claude_auth,
        "_known_row_bot_tool",
        lambda name: name in {"native_tool", "mcp_native_tool"},
    )
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(creates=[{
        "content": [{
            "type": "tool_use",
            "id": "toolu_1",
            "name": "mcp_native_tool",
            "input": {},
        }],
    }])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    try:
        result = model.invoke([HumanMessage(content="lookup")])
    finally:
        _set_backend_for_tests(None)

    assert result.tool_calls[0]["name"] == "mcp_native_tool"


def test_claude_subscription_streams_text_deltas(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(streams=[[
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
    ]])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    try:
        chunks = list(model._stream([HumanMessage(content="hello")]))
    finally:
        _set_backend_for_tests(None)

    assert [chunk.message.content for chunk in chunks] == ["Hel", "lo", ""]
    assert chunks[-1].message.chunk_position == "last"
    assert "stream" not in client.messages.stream_calls[0]


def test_claude_subscription_streams_tool_call_chunks_with_runtime_name(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    import row_bot.providers.transports.claude_subscription_messages as claude_transport
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(claude_transport.claude_auth, "_known_row_bot_tool", lambda name: name == "lookup_order")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    client = _FakeAnthropicClient(streams=[[
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "mcp_lookup_order", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"order_id\""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": ":\"42\"}"}},
    ]])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", anthropic_client=client)
    try:
        chunks = list(model._stream([HumanMessage(content="lookup")]))
    finally:
        _set_backend_for_tests(None)

    tool_chunks = [chunk.message for chunk in chunks if chunk.message.tool_call_chunks]
    assert tool_chunks[0].tool_call_chunks[0]["name"] == "lookup_order"
    assert tool_chunks[0].tool_call_chunks[0]["id"] == "toolu_1"
    assert tool_chunks[1].tool_call_chunks[0]["name"] is None
    assert tool_chunks[1].tool_call_chunks[0]["id"] is None
    assert tool_chunks[1].tool_call_chunks[0]["args"] == "{\"order_id\""
    assert tool_chunks[2].tool_call_chunks[0]["name"] is None
    assert tool_chunks[2].tool_call_chunks[0]["id"] is None
    assert tool_chunks[2].tool_call_chunks[0]["args"] == ":\"42\"}"
    merged = tool_chunks[0] + tool_chunks[1] + tool_chunks[2]
    assert merged.tool_call_chunks[0]["name"] == "lookup_order"
    assert merged.tool_calls == [{"name": "lookup_order", "args": {"order_id": "42"}, "id": "toolu_1", "type": "tool_call"}]


def test_claude_subscription_refreshes_once_after_401(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    import row_bot.providers.transports.claude_subscription_messages as claude_transport
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    old_token = _save_runtime_tokens()
    new_token = _valid_token(exp=1893457000)
    monkeypatch.setattr(
        claude_transport.claude_auth,
        "refresh_claude_subscription_token",
        lambda refresh_token: ClaudeSubscriptionTokenSet(access_token=new_token, refresh_token=refresh_token),
    )
    factory = _ClientFactory([
        _FakeAnthropicClient(creates=[_SDKError(401, "expired")]),
        _FakeAnthropicClient(creates=[{"content": [{"type": "text", "text": "after refresh"}]}]),
    ])
    model = ChatClaudeSubscriptionMessages(model_name="claude-sonnet-4-6", client_factory=factory)
    try:
        result = model.invoke([HumanMessage(content="hello")])
    finally:
        _set_backend_for_tests(None)

    assert result.content == "after refresh"
    assert factory.calls[0]["auth_token"] == old_token
    assert factory.calls[1]["auth_token"] == new_token


def test_claude_subscription_error_messages_are_actionable(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    _set_backend_for_tests(_MemoryKeyring())
    _save_runtime_tokens()
    cases = [
        (403, "plan not eligible", "plan not eligible"),
        (429, "too many requests", "rate/usage limit"),
        (500, "upstream failed", "Transient Anthropic service error"),
    ]
    try:
        for status_code, text, expected in cases:
            model = ChatClaudeSubscriptionMessages(
                model_name="claude-sonnet-4-6",
                anthropic_client=_FakeAnthropicClient(creates=[_SDKError(status_code, text)]),
            )
            try:
                model.invoke([HumanMessage(content="hello")])
            except RuntimeError as exc:
                assert expected in str(exc)
            else:
                raise AssertionError(f"Expected HTTP {status_code} to raise")
    finally:
        _set_backend_for_tests(None)
