"""models.dev's catalogue fills in what bare model lists don't say; a provider's own list wins (fake HTTP only)."""
from __future__ import annotations

import json
import os
import time

import pytest

pytestmark = pytest.mark.subsystem

API = {
    "openai": {"id": "openai", "models": {
        "gpt-7": {"id": "gpt-7", "tool_call": True, "limit": {"context": 2_000_000, "output": 128_000},
                  "modalities": {"input": ["text", "image"], "output": ["text"]}},
        "gpt-image-3": {"id": "gpt-image-3", "tool_call": False, "limit": {"context": 0},
                        "modalities": {"input": ["text", "image"], "output": ["image"]}},
    }},
    "cerebras": {"id": "cerebras", "models": {
        "qwen-4-coder": {"id": "qwen-4-coder", "tool_call": True, "limit": {"context": 262_144},
                         "modalities": {"input": ["text"], "output": ["text"]}},
    }},
    "mistral": {"id": "mistral", "models": {
        "mistral-small-9": {"id": "mistral-small-9", "tool_call": True, "limit": {"context": 128_000},
                            "modalities": {"input": ["text", "image"], "output": ["text"]}},
    }},
    "groq": {"id": "groq", "models": {
        "openai/gpt-oss-120b": {"id": "openai/gpt-oss-120b", "tool_call": True, "reasoning": True,
                                "limit": {"context": 131_072}, "modalities": {"input": ["text"], "output": ["text"]}},
    }},
    "zai": {"id": "zai", "models": {
        "glm-5.3": {"id": "glm-5.3", "tool_call": True, "reasoning": True, "limit": {"context": 1_000_000},
                    "modalities": {"input": ["text"], "output": ["text"]}},
    }},
    "alibaba": {"id": "alibaba", "models": {
        "qwen3.8-max": {"id": "qwen3.8-max", "tool_call": True, "reasoning": True, "limit": {"context": 262_144},
                        "modalities": {"input": ["text"], "output": ["text"]}},
    }},
    "some-other-gateway": {"id": "some-other-gateway", "models": {"x": {"id": "x"}}},
}


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """The catalogue with its saved copy in tmp_path and a download that counts its calls."""
    from row_bot.providers import capability_catalog

    downloads = []

    def download() -> bytes:
        downloads.append(1)
        return json.dumps(API).encode()

    monkeypatch.setattr(capability_catalog, "_saved_path", lambda: tmp_path / "model_capabilities.json")
    monkeypatch.setattr(capability_catalog, "_download", download)
    monkeypatch.setattr(capability_catalog, "_loaded", None)
    capability_catalog.downloads = downloads
    return capability_catalog


def test_a_refresh_fetches_and_keeps_the_catalogue_then_waits_an_hour(catalogue, tmp_path):
    assert catalogue.refresh() is True
    assert catalogue.refresh() is False
    assert len(catalogue.downloads) == 1

    saved = json.loads((tmp_path / "model_capabilities.json").read_text(encoding="utf-8"))
    assert saved["source"] == "https://models.dev/api.json"
    assert set(saved["providers"]) == {"openai", "cerebras", "mistral", "groq", "zai", "alibaba"}
    assert catalogue.lookup("openai", "gpt-7") == catalogue.ModelCapabilities(
        tool_calling=True, vision=True, context_window=2_000_000, outputs=frozenset({"text"}))
    assert catalogue.lookup("codex", "gpt-7").context_window == 2_000_000
    assert catalogue.lookup("openai", "gpt-unknown") is None

    hour_ago = time.time() - 3601
    os.utime(tmp_path / "model_capabilities.json", (hour_ago, hour_ago))
    assert catalogue.refresh() is True
    assert len(catalogue.downloads) == 2


def test_when_the_fetch_fails_the_newer_of_the_saved_and_bundled_copies_is_read(catalogue, tmp_path, monkeypatch):
    def offline() -> bytes:
        raise OSError("no route to models.dev")

    monkeypatch.setattr(catalogue, "_download", offline)
    old = catalogue.trim({"openai": {"models": {"gpt-7": {"limit": {"context": 1_000}, "modalities": {}}}}})
    new = catalogue.trim(API)
    (tmp_path / "model_capabilities.json").write_text(catalogue.document(old, "2026-10-01T00:00:00+00:00"), encoding="utf-8")
    bundled = tmp_path / "bundled.json"
    bundled.write_text(catalogue.document(new, "2026-11-01T00:00:00+00:00"), encoding="utf-8")
    monkeypatch.setattr(catalogue, "BUNDLED_PATH", bundled)
    hour_ago = time.time() - 3601
    os.utime(tmp_path / "model_capabilities.json", (hour_ago, hour_ago))

    assert catalogue.refresh() is False
    assert catalogue.lookup("openai", "gpt-7").context_window == 2_000_000


def test_a_new_model_on_openais_bare_list_is_ready_from_the_catalogue(catalogue, monkeypatch):
    import httpx

    import row_bot.models as models
    from row_bot.providers import auth_store
    from row_bot.providers.capabilities import snapshot_supports_surface

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "gpt-7", "object": "model"}, {"id": "gpt-image-3", "object": "model"}]}

    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: _Response())
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider: "synthetic" if provider == "openai" else "")
    monkeypatch.setattr(models, "_cloud_model_cache", {})

    result = models.refresh_cloud_provider_models("openai")

    assert result.status == "live"
    chat = models._cloud_model_cache["gpt-7"]
    assert chat["ctx"] == 2_000_000
    assert chat["vision"] is True
    assert chat["capabilities_snapshot"]["tool_calling"] is True
    image = models._cloud_model_cache["gpt-image-3"]["capabilities_snapshot"]
    assert snapshot_supports_surface(image, "image") and not snapshot_supports_surface(image, "chat")


def test_a_bare_preset_row_is_filled_and_a_described_one_is_believed(catalogue):
    from row_bot.providers.presets import PRESETS, row_metadata

    catalogue.refresh()

    assert row_metadata(PRESETS["cerebras"], {"id": "qwen-4-coder"}) == {
        "tool_calling": True, "context_window": 262_144}
    # Mistral's list describes its models: what it says (no tools, no images) wins over the catalogue.
    described = {"id": "mistral-small-9", "max_context_length": 32_000,
                 "capabilities": {"function_calling": False, "vision": False, "completion_chat": True}}
    assert row_metadata(PRESETS["mistral"], described) == {"tool_calling": False, "context_window": 32_000}
    # A model the catalogue doesn't know keeps the old rule.
    assert row_metadata(PRESETS["cerebras"], {"id": "brand-new"}) == {"context_window": 32_768}


def test_a_model_the_catalogue_says_reasons_gets_a_thinking_control_in_its_providers_style(catalogue):
    """On 9 Nov no model of the twelve new providers, and few of OpenAI's or Anthropic's, showed a thinking control:
    only OpenRouter's list says which models reason."""
    from row_bot.providers.reasoning import resolve_reasoning_capabilities

    catalogue.refresh()

    groq = resolve_reasoning_capabilities("groq", "openai/gpt-oss-120b")
    assert groq.supported_efforts == ("low", "medium", "high") and groq.request_style == "openai"
    zai = resolve_reasoning_capabilities("zai", "glm-5.3")
    assert zai.thinking_mode == "toggle" and zai.can_disable and zai.request_style == "thinking_type"
    assert resolve_reasoning_capabilities("dashscope", "qwen3.8-max").request_style == "enable_thinking"
    # No reasoning in the catalogue, or a provider whose way of taking it isn't known: no control.
    assert resolve_reasoning_capabilities("cerebras", "qwen-4-coder") is None
    assert resolve_reasoning_capabilities("mistral", "mistral-small-9") is None


@pytest.mark.parametrize(("provider", "model", "selection", "sent"), [
    ("groq", "openai/gpt-oss-120b", {"kind": "effort", "effort": "high"}, {"reasoning_effort": "high"}),
    ("zai", "glm-5.3", {"kind": "off"}, {"thinking": {"type": "disabled"}}),
    ("dashscope", "qwen3.8-max", {"kind": "on"}, {"enable_thinking": True}),
])
def test_the_thinking_choice_reaches_the_provider_as_its_api_takes_it(catalogue, provider, model, selection, sent):
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from row_bot.providers.reasoning import (
        ReasoningRequestPlan,
        ReasoningSelection,
        model_ref,
        resolve_reasoning_capabilities,
    )
    from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

    catalogue.refresh()
    bodies = []

    class _Client:
        def post(self, url, **kwargs):
            bodies.append(kwargs["json"])
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {"choices": [{"message": {"content": "ok"}}]})

    caps = resolve_reasoning_capabilities(provider, model)
    plan = ReasoningRequestPlan(model_ref(provider, model), ReasoningSelection(**selection), caps)
    ChatOpenAICompatible(model_name=model, base_url="https://x.test/v1", endpoint={"provider_id": provider},
                         http_client=_Client(), reasoning_plan=plan).invoke([HumanMessage(content="hi")])

    assert sent.items() <= bodies[0].items()
