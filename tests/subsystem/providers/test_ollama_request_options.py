"""Every request for the local chat model carries one ``num_ctx`` and the chat ``keep_alive`` (WP B2, F13).

Ollama reloads a model's runner, and drops its prompt cache, whenever a request
asks for another ``num_ctx`` than the loaded one; a request without one asks for
the server default. These tests drive each path that talks to the chat model
against a fake Ollama on loopback and read what reached the wire.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

import row_bot.models as models
import row_bot.providers.runtime as runtime
from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan, ReasoningSelection
from row_bot.providers.transports.ollama_local import OLLAMA_CHAT_KEEP_ALIVE, RowBotChatOllama
from tests.helpers.fake_ollama import fake_streaming_ollama

MODEL = "fake-local:7b"
MODEL_REF = f"model:ollama:{MODEL}"
USER_CONTEXT = 16_384
NATIVE_CONTEXT = 32_768


@pytest.fixture
def ollama(monkeypatch):
    """A fake Ollama serving the default chat model, with a fixed context setting."""
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    with fake_streaming_ollama(tokens=["ok"], model=MODEL) as server:
        monkeypatch.setattr(models, "_ollama_base_url", lambda: server.url)
        monkeypatch.setattr(models, "_current_model", MODEL_REF)
        monkeypatch.setattr(models, "_num_ctx", USER_CONTEXT)
        monkeypatch.setattr(models, "_local_context_mode", "fixed")
        monkeypatch.setattr(models, "get_model_max_context", lambda *args, **kwargs: NATIVE_CONTEXT)
        monkeypatch.setattr(models, "_observed_local_context", {})
        monkeypatch.setattr(models, "_override_llm_cache", {})
        monkeypatch.setattr(models, "_llm_instance", None)
        yield server


def _chat_requests(server) -> list[dict]:
    return [request for request in server.requests if request["path"] == "/api/chat"]


def _ollama_on_plan() -> ReasoningRequestPlan:
    return ReasoningRequestPlan(
        MODEL_REF,
        ReasoningSelection(kind="on"),
        ReasoningCapabilities(thinking_mode="toggle", can_disable=True, request_style="ollama"),
    )


def test_every_chat_path_sends_one_context_size_and_the_keep_alive(ollama) -> None:
    import row_bot.agent as agent
    from row_bot.application import conversation_naming
    from row_bot.providers.background import background_model_for

    prompt = [HumanMessage(content="hi")]

    models.get_llm().invoke(prompt)  # the default chat model
    models.get_llm_for(MODEL_REF).invoke(prompt)  # the agent
    runtime.create_chat_model(MODEL_REF).invoke(prompt)  # Chat Only
    # A thread with a reasoning choice: Chat Only gets the fallback wrapper, which
    # hides num_ctx from callers that pass their own options.
    runtime.create_chat_model(MODEL_REF, reasoning_plan=_ollama_on_plan()).bind(
        options={"num_predict": 64},
    ).invoke(prompt)
    agent._compaction_model(SimpleNamespace(model_ref=MODEL_REF, provider_id="ollama"), 64).invoke(prompt)
    background_model_for("extraction", thread_model=MODEL_REF).invoke(prompt)
    conversation_naming._title_model(MODEL_REF).invoke(prompt)

    requests = _chat_requests(ollama)
    assert len(requests) == 7
    assert {request["options"]["num_ctx"] for request in requests} == {USER_CONTEXT}
    assert {request["keep_alive"] for request in requests} == {OLLAMA_CHAT_KEEP_ALIVE}
    # Caller options still apply alongside the context size.
    assert requests[3]["options"]["num_predict"] == 64


def test_vision_on_the_chat_model_reuses_its_runner(ollama, monkeypatch) -> None:
    import row_bot.vision as vision

    calls: list[dict] = []
    client = SimpleNamespace(chat=lambda **kwargs: calls.append(kwargs) or {"message": {"content": "a cat"}})
    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr(models, "_ollama_client", lambda: client)
    models.get_llm().invoke([HumanMessage(content="hi")])

    assert vision.VisionService()._analyze_ollama_local("aGk=", "what is this?", model=MODEL_REF) == "a cat"

    chat_request = _chat_requests(ollama)[0]
    assert calls[0]["model"] == MODEL
    assert calls[0]["options"]["num_ctx"] == chat_request["options"]["num_ctx"] == USER_CONTEXT
    assert calls[0]["keep_alive"] == chat_request["keep_alive"] == OLLAMA_CHAT_KEEP_ALIVE


def test_vision_on_a_separate_model_keeps_its_own_short_lifetime(ollama, monkeypatch) -> None:
    import row_bot.vision as vision

    calls: list[dict] = []
    client = SimpleNamespace(chat=lambda **kwargs: calls.append(kwargs) or {"message": {"content": "a cat"}})
    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr(models, "_ollama_client", lambda: client)

    vision.VisionService()._analyze_ollama_local("aGk=", "what is this?", model="model:ollama:llava:7b")

    assert calls[0]["model"] == "llava:7b"
    assert calls[0]["keep_alive"] == "5m"
    assert "options" not in calls[0]


def test_the_tool_probe_does_not_reload_the_chat_model(ollama, monkeypatch) -> None:
    import httpx

    from row_bot.providers import ollama as ollama_provider

    sent: list[dict] = []
    timeouts: list[float] = []

    def post(url, json, timeout):
        sent.append(json)
        timeouts.append(timeout)
        message = {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "row_bot_probe", "arguments": {"value": "ok"}}},
        ]} if len(sent) == 2 else {"role": "assistant", "content": "ok"}
        return SimpleNamespace(status_code=200, text="", json=lambda: {"message": message})

    monkeypatch.setattr(httpx, "post", post)
    ollama_provider._tool_probe_cache.clear()

    result = ollama_provider.probe_ollama_tool_round_trip(MODEL, force=True, timeout=20.0)

    assert result["tool_round_trip"] is True
    # The model loads first, with the probe's own settings, so the probe neither reloads it
    # nor counts a slow cold load against its 20 seconds.
    assert sent[0]["messages"] == []
    assert timeouts[0] > 20.0 and timeouts[1:] == [20.0, 20.0]
    assert [request["options"] for request in sent] == [{"temperature": 0, "num_ctx": USER_CONTEXT}] * 3
    assert [request["keep_alive"] for request in sent] == [OLLAMA_CHAT_KEEP_ALIVE] * 3


def test_a_user_keep_alive_for_the_server_is_respected(ollama, monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "-1")

    models.get_llm_for(MODEL_REF).invoke([HumanMessage(content="hi")])

    request = _chat_requests(ollama)[0]
    assert request["options"]["num_ctx"] == USER_CONTEXT
    assert "keep_alive" not in request


def test_caller_options_keep_the_model_context_size() -> None:
    model = RowBotChatOllama(model=MODEL, num_ctx=8_192, base_url="http://127.0.0.1:1")

    params = model._chat_params([HumanMessage(content="hi")], options={"num_predict": 20, "temperature": 0.1})

    assert params["options"] == {"num_predict": 20, "temperature": 0.1, "num_ctx": 8_192}
    assert params["keep_alive"] == OLLAMA_CHAT_KEEP_ALIVE
