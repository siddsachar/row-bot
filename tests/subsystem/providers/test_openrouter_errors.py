"""OpenRouter's failures are logged with its own details, and a busy free model says so plainly (F6).

A fake OpenRouter chat API answers the production SDK client in process with
the error bodies OpenRouter sends, so nothing here reaches the network.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from langchain_core.messages import HumanMessage

from row_bot.providers.transports import openrouter_cancellable
from row_bot.providers.transports.openrouter_cancellable import CancellableChatOpenRouter
from row_bot.providers.transports.openrouter_errors import FreeModelBusy
from tests.contracts.client_platform.test_headless_lifecycle import platform, submit  # noqa: F401

pytestmark = pytest.mark.subsystem

GEMMA = "google/gemma-4-31b-it:free"
PROMPT = "PROMPT-TEXT-NEVER-LOGGED"
UPSTREAM_LIMIT = {"error": {
    "message": "Provider returned error", "code": 429,
    "metadata": {"provider_name": "Google AI Studio",
                 "raw": f"{GEMMA} is temporarily rate-limited upstream. Please retry shortly."}},
    "user_id": "user_2fixture"}


@pytest.fixture
def openrouter_api(monkeypatch) -> dict[str, Any]:
    """OpenRouter's chat API behind the production SDK client: queued (status, body) answers."""
    state: dict[str, Any] = {"answers": [], "requests": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"] += 1
        status, body = state["answers"].pop(0)
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    sync_client = openrouter_cancellable.cancellable_http_client
    monkeypatch.setattr(openrouter_cancellable, "cancellable_http_client",
                        lambda **kwargs: sync_client(transport=transport, **kwargs))
    return state


def _model(model_name: str = GEMMA) -> CancellableChatOpenRouter:
    return CancellableChatOpenRouter(model_name=model_name, openrouter_api_key="test-key", max_retries=0)


def _logged(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records
                     if record.name.endswith("openrouter_errors"))


def test_a_rate_limited_free_model_fails_in_plain_words_and_logs_openrouters_reason(openrouter_api, caplog):
    openrouter_api["answers"] = [(429, UPSTREAM_LIMIT)]
    caplog.set_level(logging.WARNING)

    with pytest.raises(FreeModelBusy) as raised:
        _model().invoke([HumanMessage(content=PROMPT)])

    assert str(raised.value) == (
        f"{GEMMA} is busy right now: its provider is limiting free use. Wait a moment and resume, "
        "or switch to the Free Models Router, which picks a free model that is available.")
    assert raised.value.status_code == 429, "a backup model still sees the rate limit"
    assert openrouter_api["requests"] == 1
    logged = _logged(caplog)
    assert f"model={GEMMA} status=429 code=429 provider=Google AI Studio" in logged
    assert "temporarily rate-limited upstream" in logged
    assert PROMPT not in logged and "user_2fixture" not in logged and "test-key" not in logged


def test_the_logged_upstream_reply_is_redacted_and_short(openrouter_api, caplog):
    secret = "sk-or-v1-" + "a" * 48
    body = {"error": {"message": "Provider returned error", "code": 400,
                      "metadata": {"provider_name": "Chutes", "raw": f"bad key {secret} " + "x" * 2000}}}
    openrouter_api["answers"] = [(400, body)]
    caplog.set_level(logging.WARNING)

    with pytest.raises(Exception) as raised:
        _model().invoke([HumanMessage(content=PROMPT)])

    assert not isinstance(raised.value, FreeModelBusy), "a refused request is not a busy model"
    logged = _logged(caplog)
    assert "status=400 code=400 provider=Chutes" in logged
    assert secret not in logged and "[redacted-secret]" in logged
    assert len(logged) < 600


@pytest.mark.parametrize(("model_name", "body"), [
    # A paid model's rate limit keeps the provider's own error and wording.
    ("google/gemma-4-31b-it", UPSTREAM_LIMIT),
    # The account's own free allowance is used up: the chat says when it comes back (turn_failure).
    (GEMMA, {"error": {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 "
                                  "free model requests per day", "code": 429}}),
])
def test_other_rate_limits_keep_their_own_error(openrouter_api, model_name, body):
    openrouter_api["answers"] = [(429, body)]

    with pytest.raises(Exception) as raised:
        _model(model_name).invoke([HumanMessage(content=PROMPT)])

    assert not isinstance(raised.value, FreeModelBusy)
    assert str(raised.value) == body["error"]["message"]


def test_a_busy_free_model_mid_stream_fails_in_plain_words(openrouter_api, caplog):
    chunk = {"id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": GEMMA, "choices": [],
             "error": {"code": 429, "message": "Provider returned error"}}
    openrouter_api["answers"] = [(200, f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n")]
    caplog.set_level(logging.WARNING)

    with pytest.raises(FreeModelBusy):
        list(_model().stream([HumanMessage(content=PROMPT)]))

    assert f"model={GEMMA} status=- code=429" in _logged(caplog)


class _OpenRouterTurn:
    """The real agent loop over a graph whose model call goes to the fake OpenRouter."""

    def stream(self, *_args, **_kwargs):
        from row_bot import agent

        class Graph:
            def stream(self, *_args, **_kwargs):
                _model().invoke([HumanMessage(content=PROMPT)])
                yield  # A generator, as a real stream is.

        return agent._stream_graph(Graph(), {}, {"configurable": {}})

    def resume(self, *_args, **_kwargs):
        return self.stream()


def test_the_chat_says_a_free_model_is_busy_and_names_the_free_models_router(
        platform, openrouter_api, monkeypatch):  # noqa: F811
    openrouter_api["answers"] = [(429, UPSTREAM_LIMIT)]
    published: list[tuple[str, dict]] = []
    original = platform.projection.publish

    def recording(conversation_id, kind, payload, *args, **kwargs):
        published.append((kind, dict(payload)))
        return original(conversation_id, kind, payload, *args, **kwargs)

    monkeypatch.setattr(platform.projection, "publish", recording)
    accepted = submit(platform, _OpenRouterTurn(), "busy-free-model")
    assert platform.registry.get(accepted["execution_id"]).producer_done.wait(10)

    [error] = [payload for kind, payload in published if kind == "generation.error"]
    assert error["code"] == "provider_unavailable"
    assert "is busy right now" in error["message"] and "Free Models Router" in error["message"]
    final = [payload for kind, payload in published if kind == "generation.state"][-1]
    assert final["status"] == "interrupted"
    assert final["failure_reason"] == error["message"]
