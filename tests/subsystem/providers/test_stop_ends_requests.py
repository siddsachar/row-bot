"""Stop ends a provider request at once, waiting for headers or mid-stream, and only that request.

A run's Stop cancels its cancellation scope. Each request registers with the scope as it starts and Stop shuts the
request's socket down: on Linux closing it doesn't wake a read blocked in another thread and the provider never
sees the client go, so a turn stayed "stopping" until the provider answered (before headers nothing was registered
at all, on any OS). These drive each transport family, the provider SDKs on Row-Bot's HTTP clients (built as the
runtime builds them) and Row-Bot's own transports, against a provider on loopback that holds the request open,
sending nothing or only part of the answer, and read what reached it.
"""

from __future__ import annotations

import contextvars
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.providers.transports.cancellable_http import RequestStopped
from tests.helpers.fake_provider_http import HOLD, Hold, Reply, fake_provider_http

# How a blocked read is ended differs by OS (closing a socket doesn't wake it on Linux), so these run everywhere.
pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

PROMPT = [HumanMessage(content="What is 2 + 2?")]
# Stop ends the call at once; the bound only keeps a broken test from hanging.
PROMPTLY = 2.0


def _sse(*events: dict, named: bool = False, done: bool = False) -> bytes:
    lines = [(f"event: {event['type']}\n" if named else "") + f"data: {json.dumps(event)}\n\n" for event in events]
    return "".join(lines + (["data: [DONE]\n\n"] if done else [])).encode()


def _chat_chunk(delta: dict, finish: str | None = None) -> dict:
    return {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


CHAT_FIRST = _sse(_chat_chunk({"role": "assistant", "content": "Hel"}))
CHAT_ANSWER = _sse(_chat_chunk({"role": "assistant", "content": "4"}), _chat_chunk({}, "stop"), done=True)
_MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "content": [], "model": "claude-test",
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}
ANTHROPIC_FIRST = _sse(
    {"type": "message_start", "message": _MESSAGE},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
    named=True)
ANTHROPIC_ANSWER = _sse(
    {"type": "message_start", "message": _MESSAGE},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "4"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
     "usage": {"output_tokens": 1}},
    {"type": "message_stop"},
    named=True)
RESPONSES_FIRST = _sse({"type": "response.output_text.delta", "delta": "Hel"}, named=True)
RESPONSES_ANSWER = _sse(
    {"type": "response.output_text.delta", "delta": "4"},
    {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": []}},
    named=True)
OLLAMA_FIRST = json.dumps({"model": "m", "message": {"role": "assistant", "content": "Hel"}, "done": False}).encode() + b"\n"
OLLAMA_ANSWER = b"\n".join(json.dumps(line).encode() for line in (
    {"model": "m", "message": {"role": "assistant", "content": "4"}, "done": False},
    {"model": "m", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"},
)) + b"\n"


@dataclass(frozen=True)
class Family:
    build: Callable[[str, pytest.MonkeyPatch], Any]
    first: bytes
    answer: bytes
    sdk: bool
    content_type: str = "text/event-stream"


def _runtime_model(provider: str, model: str, base_env: str, path: str = "") -> Callable:
    def build(url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
        from row_bot.providers import runtime

        monkeypatch.setattr(runtime, "get_provider_secret", lambda *_args, **_kwargs: "test-key")
        monkeypatch.setenv(base_env, url + path)
        return runtime.create_chat_model(model, provider_id=provider)

    return build


def _openai_compatible(url: str, _monkeypatch: pytest.MonkeyPatch) -> Any:
    from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

    endpoint = {"provider_id": "custom_stop_test", "base_url": f"{url}/v1", "transport": "openai_chat"}
    return ChatOpenAICompatible(model_name="m", api_key="k", base_url=f"{url}/v1", endpoint=endpoint)


def _local_openai_compatible(url: str, _monkeypatch: pytest.MonkeyPatch) -> Any:
    # A server on this computer (llama.cpp, LM Studio, vLLM): its own idle limit, and Stop as anywhere.
    from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

    endpoint = {"provider_id": "custom_openai_llamacpp-stop", "base_url": f"{url}/v1", "transport": "openai_chat",
                "profile": "llama_cpp", "execution_location": "local"}
    return ChatOpenAICompatible(model_name="m", base_url=f"{url}/v1", endpoint=endpoint)


def _codex(url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from row_bot.providers import codex
    from row_bot.providers.transports.codex_responses import ChatCodexResponses

    monkeypatch.setattr(codex, "codex_runtime_credentials",
                        lambda **_kwargs: SimpleNamespace(access_token="t", account_id="a"))
    return ChatCodexResponses(model_name="gpt-test", base_url=url)


def _xai_sign_in(url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from row_bot.providers import xai_oauth
    from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses

    monkeypatch.setattr(xai_oauth, "xai_oauth_runtime_credentials", lambda **_kwargs: SimpleNamespace(access_token="t"))
    # It only talks to x.ai hosts; here, to the provider on loopback.
    monkeypatch.setattr(xai_oauth, "xai_oauth_base_url", lambda override=None: override)
    return ChatXAIOAuthResponses(model_name="grok-test", base_url=url)


def _ollama_cloud(url: str, _monkeypatch: pytest.MonkeyPatch) -> Any:
    from row_bot.providers.transports.ollama_cloud import ChatOllamaCloud

    return ChatOllamaCloud(model_name="m", api_key="k", base_url=url)


FAMILIES = {
    "openai": Family(_runtime_model("openai", "gpt-4.1", "OPENAI_API_BASE", "/v1"), CHAT_FIRST, CHAT_ANSWER, True),
    "anthropic": Family(_runtime_model("anthropic", "claude-sonnet-4-5", "ANTHROPIC_API_URL"),
                        ANTHROPIC_FIRST, ANTHROPIC_ANSWER, True),
    "openrouter": Family(_runtime_model("openrouter", "openai/gpt-4.1", "OPENROUTER_API_BASE", "/api/v1"),
                         CHAT_FIRST, CHAT_ANSWER, True),
    "openai_compatible": Family(_openai_compatible, CHAT_FIRST, CHAT_ANSWER, False),
    "local_openai_compatible": Family(_local_openai_compatible, CHAT_FIRST, CHAT_ANSWER, False),
    "codex": Family(_codex, RESPONSES_FIRST, RESPONSES_ANSWER, False),
    "xai_sign_in": Family(_xai_sign_in, RESPONSES_FIRST, RESPONSES_ANSWER, False),
    "ollama_cloud": Family(_ollama_cloud, OLLAMA_FIRST, OLLAMA_ANSWER, False, "application/x-ndjson"),
}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def _streamed(model: Any, received: list[str], first: threading.Event | None = None) -> None:
    for chunk in model.stream(PROMPT):
        if text := _text(chunk.content):
            received.append(text)
            if first is not None:
                first.set()


def _under_scope(call: Callable[[], Any], scope: CancellationScope) -> tuple[threading.Thread, dict]:
    """Run *call* on another thread under *scope*, as a turn's producer runs the model."""
    outcome: dict = {}

    def run() -> None:
        with use_cancellation_scope(scope):
            try:
                outcome["result"] = call()
            except BaseException as error:  # noqa: BLE001 - the test reads it
                outcome["error"] = error

    worker = threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True)
    worker.start()
    return worker, outcome


def _ended_without_an_answer(family: Family, outcome: dict) -> None:
    if family.sdk:
        # The SDK passed Stop on as its own error: it didn't retry the request.
        assert isinstance(outcome.get("error"), RequestStopped), outcome
    elif "result" in outcome and outcome["result"] is not None:
        assert _text(outcome["result"].content) == ""


@pytest.mark.parametrize("way", ["stream", "invoke"])
@pytest.mark.parametrize("name", FAMILIES)
def test_stop_ends_a_request_the_provider_has_not_answered(name: str, way: str, monkeypatch) -> None:
    family = FAMILIES[name]
    answer = Reply(body=family.answer, content_type=family.content_type)
    with fake_provider_http([HOLD, answer]) as server:
        model = family.build(server.url, monkeypatch)
        received: list[str] = []
        scope = CancellationScope()
        call = (lambda: _streamed(model, received)) if way == "stream" else (lambda: model.invoke(PROMPT))
        worker, outcome = _under_scope(call, scope)
        held = server.held(0)

        scope.cancel("user")
        worker.join(PROMPTLY)

        assert not worker.is_alive()
        assert held.hung_up.wait(PROMPTLY)  # the provider sees the client go, so it stops working on the answer
        assert received == []
        _ended_without_an_answer(family, outcome)
        assert len(server.seen) == 1
        # The next request through the same model works as usual.
        _streamed(model, received)
        assert received == ["4"]


@pytest.mark.parametrize("name", FAMILIES)
def test_stop_ends_a_stream_mid_answer_and_passes_nothing_more_on(name: str, monkeypatch) -> None:
    family = FAMILIES[name]
    with fake_provider_http([Hold(chunks=(family.first,), content_type=family.content_type)]) as server:
        model = family.build(server.url, monkeypatch)
        received: list[str] = []
        first = threading.Event()
        scope = CancellationScope()
        worker, _outcome = _under_scope(lambda: _streamed(model, received, first), scope)
        held = server.held(0)
        assert first.wait(PROMPTLY)

        scope.cancel("user")
        worker.join(PROMPTLY)

        assert not worker.is_alive()
        assert held.hung_up.wait(PROMPTLY)
        assert received == ["Hel"]
        assert len(server.seen) == 1


@pytest.mark.parametrize("name", FAMILIES)
def test_stop_leaves_another_runs_request_alone(name: str, monkeypatch) -> None:
    # Two runs on one model (one SDK client): Stop for the first never touches the second's connection.
    family = FAMILIES[name]
    with fake_provider_http([HOLD, HOLD]) as server:
        model = family.build(server.url, monkeypatch)
        stopped, other = CancellationScope(), CancellationScope()
        stopped_received: list[str] = []
        other_received: list[str] = []
        stopped_worker, _ = _under_scope(lambda: _streamed(model, stopped_received), stopped)
        stopped_request = server.held(0)
        other_worker, other_outcome = _under_scope(lambda: _streamed(model, other_received), other)
        other_request = server.held(1)

        stopped.cancel("user")
        stopped_worker.join(PROMPTLY)

        assert not stopped_worker.is_alive()
        assert stopped_request.hung_up.wait(PROMPTLY)
        assert not other_request.hung_up.is_set()
        assert other_worker.is_alive()
        other_request.finish(family.answer)
        other_worker.join(PROMPTLY)
        assert not other_worker.is_alive()
        assert "error" not in other_outcome
        assert other_received == ["4"]


@pytest.mark.parametrize("name", FAMILIES)
def test_a_call_stopped_before_it_starts_sends_nothing(name: str, monkeypatch) -> None:
    family = FAMILIES[name]
    with fake_provider_http([HOLD]) as server:
        model = family.build(server.url, monkeypatch)
        scope = CancellationScope()
        scope.cancel("user")
        received: list[str] = []
        worker, _outcome = _under_scope(lambda: _streamed(model, received), scope)
        worker.join(PROMPTLY)

        assert not worker.is_alive()  # a request sent would be held open
        assert server.seen == []
        assert received == []
