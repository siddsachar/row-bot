"""What ``row-bot run`` needs from the providers: an Anthropic Messages custom endpoint, a declared endpoint
model, and one longest-reply limit for every model the process makes.

Requests go to scripted endpoints on loopback; nothing leaves the machine.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import row_bot.providers.runtime as runtime
from row_bot.providers import custom
from row_bot.providers.readiness import evaluate_agent_readiness
from row_bot.providers.selection import model_ref
from row_bot.providers.transports.ollama_local import RowBotChatOllama
from tests.helpers.fake_chat_endpoint import MODEL, fake_chat_endpoint, text

pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def _own_provider_config(tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    from row_bot import secret_store

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(secret_store, "_backend_override", secret_store.MemoryBackend())
    monkeypatch.setattr(runtime, "_reply_token_limit", None)


@contextmanager
def _anthropic_messages_endpoint() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            seen.append({"path": self.path, "key": self.headers.get("x-api-key"), "body": body})
            reply = json.dumps({"id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model"),
                                "content": [{"type": "text", "text": "Hello from Messages."}],
                                "stop_reason": "end_turn", "stop_sequence": None,
                                "usage": {"input_tokens": 12, "output_tokens": 4}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def _endpoint(endpoint_id: str, base_url: str, transport: str, key: str = "") -> str:
    custom.save_custom_endpoint({"id": endpoint_id, "base_url": base_url, "transport": transport,
                                 "auth_required": bool(key), "api_key": key})
    return custom.custom_provider_id(endpoint_id)


def test_a_custom_endpoint_can_speak_anthropic_messages(monkeypatch):
    with _anthropic_messages_endpoint() as (url, seen):
        provider = _endpoint("gateway", url, "anthropic_messages", key="sk-ant-test")
        answer = runtime.create_chat_model("glm-5.3-flash", provider_id=provider).invoke("Hi")

    assert answer.content == "Hello from Messages."
    [request] = seen
    assert (request["path"], request["key"], request["body"]["model"]) == ("/v1/messages", "sk-ant-test",
                                                                           "glm-5.3-flash")


def test_one_reply_limit_reaches_every_kind_of_model(monkeypatch):
    runtime.limit_reply_tokens(321)
    with _anthropic_messages_endpoint() as (url, seen), fake_chat_endpoint([text("Hi.")]) as chat:
        runtime.create_chat_model(MODEL, provider_id=_endpoint("chat", chat.url, "openai_chat")).invoke("Hi")
        runtime.create_chat_model("m", provider_id=_endpoint("messages", url, "anthropic_messages")).invoke("Hi")
        responses = runtime.create_chat_model("m", provider_id=_endpoint("responses", chat.url, "openai_responses"))

    assert chat.requests[0]["max_tokens"] == 321
    assert seen[0]["body"]["max_tokens"] == 321
    assert responses.max_tokens == 321
    assert runtime.with_reply_limit(RowBotChatOllama(model="qwen3:8b")).num_predict == 321


def test_without_a_reply_limit_each_provider_keeps_its_own_default():
    with fake_chat_endpoint([text("Hi.")]) as chat:
        runtime.create_chat_model(MODEL, provider_id=_endpoint("chat", chat.url, "openai_chat")).invoke("Hi")

    assert "max_tokens" not in chat.requests[0]
    assert runtime.with_reply_limit(RowBotChatOllama(model="qwen3:8b")).num_predict is None


def test_a_declared_endpoint_model_is_agent_ready_without_a_probe():
    provider = _endpoint("bench", "http://127.0.0.1:9/v1", "openai_chat")
    assert not evaluate_agent_readiness(model_ref(provider, MODEL), refresh_provider_status=False).ready

    custom.declare_endpoint_model("bench", MODEL)
    readiness = evaluate_agent_readiness(model_ref(provider, MODEL), refresh_provider_status=False)

    assert readiness.ready, readiness.errors
    assert (readiness.tool_calling, readiness.tool_round_trip, readiness.streaming_tool_calling) == (True, True, False)
