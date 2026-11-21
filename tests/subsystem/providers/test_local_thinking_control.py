"""A chat's Thinking choice reaches a model on a local server (local-models uplift L4).

A hybrid Qwen model on llama.cpp, vLLM or SGLang on this computer gets On and Off, sent as the template's own
``chat_template_kwargs.enable_thinking``; the chat's choice beats the endpoint's reasoning mode. A remote
endpoint, LM Studio (whose chat API ignores the field) and Learning's personal models get no control, and the
personal models keep thinking off whatever the chat asks. These drive the transport against a server on
loopback and read what reached it.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage

import row_bot.providers.config as provider_config
from row_bot.providers import custom
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint
from row_bot.providers.reasoning import (
    ReasoningCapabilities,
    ReasoningRequestPlan,
    ReasoningSelection,
    reasoning_choices,
    resolve_reasoning_capabilities,
)
from row_bot.providers.runtime import create_chat_model
from tests.helpers.fake_provider_http import Reply, fake_provider_http

pytestmark = pytest.mark.subsystem

ANSWER = Reply(body=json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "4"},
                                             "finish_reason": "stop"}]}).encode())
MODEL = "qwen3.5-9b"
CHAT = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}


@pytest.fixture
def providers(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")


def _endpoint(endpoint_id: str, base_url: str = "http://127.0.0.1:8090/v1", *, profile: str = "llama_cpp",
              location: str = "local", model: str = MODEL, **extra) -> str:
    save_custom_endpoint({"id": endpoint_id, "name": endpoint_id, "base_url": base_url, "profile": profile,
                          "execution_location": location, "auth_required": False,
                          "models": [{"id": model, "model_id": model, "provider": custom_provider_id(endpoint_id),
                                      "capabilities_snapshot": CHAT}], **extra})
    return custom_provider_id(endpoint_id)


def _plan(provider: str, kind: str, caps: ReasoningCapabilities | None = None, model: str = MODEL):
    caps = caps or resolve_reasoning_capabilities(provider, model)
    return ReasoningRequestPlan(f"model:{provider}:{model}", ReasoningSelection(kind=kind), caps)


def _sent(server, provider: str, plan: ReasoningRequestPlan | None = None, model: str = MODEL) -> dict:
    create_chat_model(model, provider, reasoning_plan=plan).invoke([HumanMessage(content="What is 2 + 2?")])
    return json.loads(server.seen[-1].body)


def _thinking(body: dict):
    return (body.get("chat_template_kwargs") or {}).get("enable_thinking")


@pytest.mark.parametrize("profile", ["llama_cpp", "vllm", "sglang"])
def test_a_hybrid_qwen_on_a_local_server_offers_thinking_on_and_off(providers, profile):
    provider = _endpoint("mine", profile=profile)

    caps = resolve_reasoning_capabilities(provider, MODEL)

    assert [choice.label for choice in reasoning_choices(caps)] == ["Provider default", "On", "Off"]
    assert caps.default_enabled is True  # the template thinks unless told not to


@pytest.mark.parametrize("kind, thinks", [("off", False), ("on", True)])
def test_the_chats_thinking_choice_reaches_a_local_llama_cpp_server(providers, kind, thinks):
    with fake_provider_http([ANSWER]) as server:
        provider = _endpoint("mine", f"{server.url}/v1")

        assert _thinking(_sent(server, provider, _plan(provider, kind))) is thinks


@pytest.mark.parametrize("mode, kind, thinks", [("off", "on", True), ("on", "off", False)])
def test_the_chats_choice_beats_the_endpoints_reasoning_mode(providers, mode, kind, thinks):
    with fake_provider_http([ANSWER]) as server:
        provider = _endpoint("mine", f"{server.url}/v1", reasoning_mode=mode)

        assert resolve_reasoning_capabilities(provider, MODEL).default_enabled is (mode == "on")
        assert _thinking(_sent(server, provider)) is (mode == "on")  # the chat's default: the endpoint's mode
        assert _thinking(_sent(server, provider, _plan(provider, kind))) is thinks


@pytest.mark.parametrize("profile, location, model", [
    ("llama_cpp", "remote", MODEL),  # a server elsewhere keeps its endpoint-wide setting only
    ("lmstudio", "local", MODEL),  # LM Studio's chat API ignores the template switch
    ("llama_cpp", "local", "llama3.1-8b"),  # a model that doesn't think
    ("llama_cpp", "local", "qwen3-4b-instruct-2507"),  # a Qwen variant that answers one way only
])
def test_no_thinking_control_where_the_request_could_not_change_it(providers, profile, location, model):
    provider = _endpoint("other", profile=profile, location=location, model=model)

    assert resolve_reasoning_capabilities(provider, model) is None


def test_a_remote_custom_endpoint_sends_what_it_sent_before(providers):
    with fake_provider_http([ANSWER]) as server:
        provider = _endpoint("gateway", f"{server.url}/v1", location="remote")

        assert "chat_template_kwargs" not in _sent(server, provider)


@pytest.mark.parametrize("endpoint_id, profile, extra", [
    ("row-bot-personal-row-bot", "llama_cpp", {"managed": "learning"}),
    ("row-bot-personal-lmstudio", "lmstudio", {}),
    ("row-bot-personal-llamacpp", "llama_cpp", {}),
])
def test_learnings_personal_models_keep_thinking_off_whatever_the_chat_asks(providers, monkeypatch, endpoint_id,
                                                                            profile, extra):
    with fake_provider_http([ANSWER]) as server:
        monkeypatch.setattr(custom, "managed_connection", lambda endpoint, start=True: (f"{server.url}/v1", "key"))
        provider = _endpoint(endpoint_id, f"{server.url}/v1", profile=profile, reasoning_mode="off", **extra)
        assert resolve_reasoning_capabilities(provider, MODEL) is None
        # Even a request cut that carries an On choice (a stale snapshot) doesn't reach the model.
        caps = ReasoningCapabilities(can_disable=True, thinking_mode="toggle", request_style="chat_template")

        body = _sent(server, provider, _plan(provider, "on", caps))

        assert _thinking(body) is False


def test_ollamas_qwen3_8_gets_its_thinking_control():
    caps = resolve_reasoning_capabilities("ollama", "qwen3.8:27b")

    assert caps is not None and caps.thinking_mode == "toggle" and caps.can_disable
