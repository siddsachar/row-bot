"""The OpenAI-compatible providers that are rows in one table (5.3.3 PR 3)."""
from __future__ import annotations

import httpx
from langchain_core.messages import HumanMessage
import pytest

import row_bot.models as models
import row_bot.providers.auth_store as auth_store
import row_bot.providers.runtime as runtime
from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV
from row_bot.providers.catalog import get_provider_definition, provider_billing
from row_bot.providers.models import AuthMethod
from row_bot.providers.presets import PRESETS
from row_bot.providers.readiness import evaluate_agent_readiness


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("refused", request=httpx.Request("GET", "https://x.test"),
                                        response=httpx.Response(self.status_code))

    def json(self):
        return self.payload


@pytest.fixture
def cloud_cache():
    saved = dict(models._cloud_model_cache)
    models._cloud_model_cache.clear()
    yield models._cloud_model_cache
    models._cloud_model_cache.clear()
    models._cloud_model_cache.update(saved)


def _serve(monkeypatch, provider_id: str, payload, status_code: int = 200) -> dict:
    """Give ``provider_id`` a key and answer its model list with ``payload``."""
    seen: dict = {}

    def fake_get(url, **kwargs):
        seen["url"], seen["headers"] = url, dict(kwargs.get("headers") or {})
        return _Response(payload, status_code)

    monkeypatch.setattr(auth_store, "get_provider_secret",
                        lambda provider, *args: f"{provider}-key" if provider == provider_id else "")
    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def _snapshot(provider_id: str, model_id: str) -> dict:
    return models._cloud_model_cache[f"model:{provider_id}:{model_id}"]["capabilities_snapshot"]


def test_every_preset_is_a_keyed_provider_with_its_own_key_and_billing():
    assert len(PRESETS) == 12
    for preset in PRESETS.values():
        definition = get_provider_definition(preset.id)
        assert definition is not None and definition.display_name == preset.name
        assert definition.auth_methods == (AuthMethod.API_KEY,)
        assert definition.base_url == preset.base_url
        assert PROVIDER_API_KEY_ENV[preset.id] == preset.key_env
        assert provider_billing(preset.id) in {"pay_per_use", "credits"}
    assert len({preset.key_env for preset in PRESETS.values()}) == 12


def test_a_keyed_preset_is_listed_among_configured_providers(monkeypatch):
    monkeypatch.setattr(runtime, "is_provider_available", lambda provider_id: provider_id == "groq")

    assert "groq" in runtime.list_configured_provider_ids()
    assert "deepseek" not in runtime.list_configured_provider_ids()


def test_a_bare_model_list_lists_chat_models_with_tools_and_the_preset_context(monkeypatch, cloud_cache):
    seen = _serve(monkeypatch, "deepseek", {"object": "list", "data": [
        {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
        {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
    ]})

    assert models.fetch_cloud_models("deepseek") == 2
    assert seen["url"] == "https://api.deepseek.com/models"
    assert seen["headers"]["Authorization"] == "Bearer deepseek-key"
    assert _snapshot("deepseek", "deepseek-chat")["tool_calling"] is True
    # An alias models.dev doesn't name: unknown, never a guessed 128K that could cap it (I33).
    assert models.get_cloud_model_context("model:deepseek:deepseek-chat") == 0


def test_a_list_that_names_capabilities_is_believed_including_what_it_leaves_out(monkeypatch, cloud_cache):
    _serve(monkeypatch, "mistral", {"data": [
        {"id": "mistral-large-latest", "max_context_length": 131_072,
         "capabilities": {"completion_chat": True, "function_calling": True, "vision": True}},
        {"id": "open-mistral-nemo", "max_context_length": 131_072,
         "capabilities": {"completion_chat": True, "function_calling": False}},
        {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
    ]})

    assert models.fetch_cloud_models("mistral") == 2
    large = _snapshot("mistral", "mistral-large-latest")
    assert large["tool_calling"] is True and "image" in large["input_modalities"]
    assert _snapshot("mistral", "open-mistral-nemo")["tool_calling"] is False
    assert models.get_cloud_model_context("model:mistral:mistral-large-latest") == 131_072


@pytest.mark.parametrize(("provider_id", "payload", "tools", "no_tools", "context"), [
    # Together answers with a bare list and types its models; only "chat" ones chat.
    ("together", [
        {"id": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "type": "chat", "context_length": 131_072},
        {"id": "meta-llama/Llama-3-8b-hf", "type": "language", "context_length": 8_192},
        {"id": "BAAI/bge-large-en-v1.5", "type": "embedding"},
    ], "meta-llama/Llama-3.3-70B-Instruct-Turbo", None, 131_072),
    # Vercel's tags say which models call tools; its "language" type is chat.
    ("vercel", {"data": [
        {"id": "openai/gpt-5", "type": "language", "context_window": 400_000, "tags": ["tool-use", "vision"]},
        {"id": "perplexity/sonar", "type": "language", "context_window": 127_000, "tags": []},
        {"id": "openai/text-embedding-3-small", "type": "embedding"},
    ]}, "openai/gpt-5", "perplexity/sonar", 400_000),
    # Hugging Face's router lists each model's providers and what each supports.
    ("huggingface", {"data": [
        {"id": "openai/gpt-oss-120b", "providers": [
            {"provider": "cerebras", "context_length": 131_072, "supports_tools": True},
            {"provider": "novita", "context_length": 65_536},
        ]},
        {"id": "google/gemma-2-2b-it", "providers": [{"provider": "nebius", "context_length": 8_192}]},
    ]}, "openai/gpt-oss-120b", "google/gemma-2-2b-it", 131_072),
    # Groq's list carries speech models beside the chat ones.
    ("groq", {"data": [
        {"id": "llama-3.3-70b-versatile", "context_window": 131_072},
        {"id": "whisper-large-v3", "context_window": 448},
    ]}, "llama-3.3-70b-versatile", None, 131_072),
])
def test_each_list_shape_gives_the_chat_models_and_their_tools(
    monkeypatch, cloud_cache, provider_id, payload, tools, no_tools, context,
):
    _serve(monkeypatch, provider_id, payload)

    assert models.fetch_cloud_models(provider_id) == (2 if no_tools else 1)
    assert _snapshot(provider_id, tools)["tool_calling"] is True
    assert models.get_cloud_model_context(f"model:{provider_id}:{tools}") == context
    if no_tools:
        assert _snapshot(provider_id, no_tools)["tool_calling"] is False
    listed = {key for key in cloud_cache if key.startswith(f"model:{provider_id}:")}
    assert not any("embed" in key or "whisper" in key or "Llama-3-8b-hf" in key for key in listed)


def test_a_failed_list_keeps_the_saved_models(monkeypatch, cloud_cache):
    cloud_cache["model:groq:llama-3.3-70b-versatile"] = {"provider": "groq", "label": "Llama", "ctx": 131_072}
    _serve(monkeypatch, "groq", {"error": "busy"}, status_code=503)

    assert models.fetch_cloud_models("groq") == 0
    assert "model:groq:llama-3.3-70b-versatile" in cloud_cache


def test_a_listed_model_with_tools_is_agent_ready_and_one_without_is_not(monkeypatch, cloud_cache):
    _serve(monkeypatch, "vercel", {"data": [
        {"id": "openai/gpt-5", "type": "language", "context_window": 400_000, "tags": ["tool-use"]},
        {"id": "perplexity/sonar", "type": "language", "context_window": 127_000, "tags": []},
    ]})
    models.fetch_cloud_models("vercel")

    ready = evaluate_agent_readiness("model:vercel:openai/gpt-5", status={"configured": True})
    not_ready = evaluate_agent_readiness("model:vercel:perplexity/sonar", status={"configured": True})

    assert ready.ready, ready.errors
    assert not not_ready.ready
    assert not_ready.tool_calling is False


def test_a_preset_model_runs_on_the_compatible_transport_with_its_key(monkeypatch):
    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id, *args: "groq-key")

    model = runtime.create_chat_model("model:groq:llama-3.3-70b-versatile")

    assert type(model).__name__ == "ChatOpenAICompatible"
    assert model.model_name == "llama-3.3-70b-versatile"
    assert model.base_url == "https://api.groq.com/openai/v1"
    assert model.endpoint["provider_id"] == "groq"


def test_a_preset_model_without_a_key_says_where_to_add_one(monkeypatch):
    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id, *args: "")

    with pytest.raises(ValueError, match="Groq API key not configured"):
        runtime.create_chat_model("model:groq:llama-3.3-70b-versatile")


def test_a_captured_preset_runtime_calls_the_preset_with_its_key(monkeypatch, cloud_cache):
    _serve(monkeypatch, "cerebras", {"data": [{"id": "gpt-oss-120b", "context_length": 131_072}]})
    models.fetch_cloud_models("cerebras")
    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id, *args: "cerebras-key")

    sent: list[httpx.Request] = []

    def send(client, request, **kwargs):
        sent.append(request)
        return httpx.Response(200, request=request, json={
            "id": "c1", "object": "chat.completion", "model": "gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ready"}}],
        })

    monkeypatch.setattr(httpx.Client, "send", send)

    captured = runtime.capture_chat_runtime("model:cerebras:gpt-oss-120b")
    with runtime.captured_chat_model(captured, validate=lambda: None) as model:
        reply = model.invoke([HumanMessage(content="Say ready.")])

    assert reply.content == "ready"
    assert str(sent[-1].url) == "https://api.cerebras.ai/v1/chat/completions"
    assert sent[-1].headers["Authorization"] == "Bearer cerebras-key"



def _sent_messages(monkeypatch, model_ref: str) -> list[dict]:
    import json
    from langchain_core.messages import SystemMessage

    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id, *args: "key")
    sent: list[httpx.Request] = []

    def send(client, request, **kwargs):
        sent.append(request)
        return httpx.Response(200, request=request, json={
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        })

    monkeypatch.setattr(httpx.Client, "send", send)
    runtime.create_chat_model(model_ref).invoke([
        SystemMessage(content="Rules"), SystemMessage(content="Tools"), HumanMessage(content="hi"),
    ])
    return json.loads(sent[-1].content)["messages"]


def test_cerebras_gets_one_system_message_first_because_its_templates_refuse_a_second(monkeypatch):
    assert _sent_messages(monkeypatch, "model:cerebras:qwen-3.8-27b") == [
        {"role": "system", "content": "Rules\n\nTools"}, {"role": "user", "content": "hi"},
    ]


def test_other_presets_keep_row_bots_system_messages_as_they_are(monkeypatch):
    roles = [message["role"] for message in _sent_messages(monkeypatch, "model:groq:openai/gpt-oss-120b")]
    assert roles == ["system", "system", "user"]

@pytest.mark.parametrize(("status_code", "state"), [(200, "valid"), (401, "invalid")])
def test_first_run_checks_a_preset_key_against_its_model_list(monkeypatch, status_code, state):
    from row_bot.application.client_first_run import check_provider_key

    seen = _serve(monkeypatch, "moonshot", {"data": []}, status_code=status_code)

    result = check_provider_key("moonshot", "sk-synthetic-moonshot-key")

    assert seen["url"] == "https://api.moonshot.ai/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-synthetic-moonshot-key"
    assert result["state"] == state


def test_mistral_lists_each_model_once_not_once_per_alias(monkeypatch, cloud_cache):
    _serve(monkeypatch, "mistral", {"data": [
        {"id": "codestral-2508", "name": "codestral-2508", "capabilities": {"completion_chat": True}},
        {"id": "codestral-latest", "name": "codestral-2508", "capabilities": {"completion_chat": True}},
        {"id": "mistral-code-latest", "name": "codestral-2508", "capabilities": {"completion_chat": True}},
        {"id": "voxtral-small-2507", "name": "Voxtral Small Transcribe", "capabilities": {"completion_chat": True}},
    ]})

    models.fetch_cloud_models("mistral")

    assert sorted(cloud_cache) == ["model:mistral:codestral-2508"]


def test_qwens_image_makers_and_single_purpose_models_stay_out_of_the_chat_list(monkeypatch, cloud_cache):
    ids = ["qwen3.8-max", "qwen-image-2.0", "wan2.7-image", "z-image-turbo", "qwen-mt-flash",
           "qwen3-livetranslate-flash", "tongyi-tingwu-slp", "qwen-vl-ocr-2025-11-20"]
    _serve(monkeypatch, "dashscope", {"data": [{"id": model_id} for model_id in ids]})
    monkeypatch.setattr(models, "_dashscope_media_model_infos", lambda *args: [])

    models.fetch_cloud_models("dashscope")

    assert sorted(cloud_cache) == ["model:dashscope:qwen3.8-max"]


def test_fireworks_models_read_by_their_name_not_their_path(monkeypatch, cloud_cache):
    _serve(monkeypatch, "fireworks", {"data": [{"id": "accounts/fireworks/models/kimi-k3"},
                                               {"id": "accounts/fireworks/routers/kimi-k3-fast"}]})

    models.fetch_cloud_models("fireworks")

    assert cloud_cache["model:fireworks:accounts/fireworks/models/kimi-k3"]["label"] == "kimi-k3"
    assert cloud_cache["model:fireworks:accounts/fireworks/routers/kimi-k3-fast"]["label"] == "kimi-k3-fast (router)"
