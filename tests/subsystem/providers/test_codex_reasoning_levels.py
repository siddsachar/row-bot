"""The ChatGPT plan's thinking levels come from its live list, and a model it doesn't describe still gets them.

On 5.3.3 GPT-6.1 Sol showed no Thinking control: ChatGPT's list names its levels as
``supported_reasoning_levels: [{"effort": ..., "description": ...}]`` and Row-Bot read only
``supported_reasoning_efforts``, so every GPT-6 model was saved with ``reasoning: null`` (fake HTTP only).
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.subsystem

# What models.dev says, for the fallback: the GPT-6 models reason, and so does a new Grok.
API = {
    "openai": {"id": "openai", "models": {
        "gpt-6.1-sol": {"id": "gpt-6.1-sol", "tool_call": True, "reasoning": True, "limit": {"context": 400_000},
                        "modalities": {"input": ["text", "image"], "output": ["text"]}},
    }},
    "xai": {"id": "xai", "models": {
        "grok-5": {"id": "grok-5", "tool_call": True, "reasoning": True, "limit": {"context": 256_000},
                   "modalities": {"input": ["text"], "output": ["text"]}},
    }},
}

LEVELS = [
    {"effort": "low", "description": "Fast responses with lighter reasoning"},
    {"effort": "medium", "description": "Balances speed and reasoning depth"},
    {"effort": "high", "description": "Greater reasoning depth"},
    {"effort": "xhigh", "description": "Extra high reasoning depth"},
]


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    from row_bot.providers import capability_catalog, config as provider_config

    monkeypatch.setattr(capability_catalog, "_saved_path", lambda: tmp_path / "model_capabilities.json")
    monkeypatch.setattr(capability_catalog, "_download", lambda: json.dumps(API).encode())
    monkeypatch.setattr(capability_catalog, "_loaded", None)
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    capability_catalog.refresh()
    return capability_catalog


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, models):
        self.models = models

    def get(self, url, **kwargs):
        return _Response({"models": self.models})


def _live(*models):
    from row_bot.providers.codex import fetch_codex_model_infos

    infos = fetch_codex_model_infos(access_token="synthetic", http_client=_Client(list(models)))
    return {info.model_id: info for info in infos}


@pytest.mark.parametrize("row", [
    # ChatGPT's /models list (ModelInfo in openai/codex codex-rs/protocol/src/openai_models.rs, rust-v0.171.0).
    {"supported_reasoning_levels": LEVELS, "default_reasoning_level": "medium"},
    # Codex's app server (Model.ts): camelCase objects naming ``reasoningEffort``.
    {"supportedReasoningEfforts": [{"reasoningEffort": item["effort"], "description": ""} for item in LEVELS],
     "defaultReasoningEffort": "medium"},
    # The older lists named the levels as strings.
    {"supported_reasoning_efforts": ["low", "medium", "high", "xhigh"], "default_reasoning_effort": "medium"},
])
def test_each_shape_of_the_live_list_gives_the_models_levels_and_default(catalogue, row):
    from row_bot.providers.reasoning import ReasoningCapabilities

    info = _live({"slug": "gpt-7-nova", "display_name": "GPT-7 Nova", **row})["gpt-7-nova"]
    caps = ReasoningCapabilities.from_json(info.reasoning)

    assert caps.supported_efforts == ("low", "medium", "high", "xhigh")
    assert caps.default_effort == "medium"
    assert caps.source == "codex_live_catalog" and caps.request_style == "openai_responses"
    assert "reasoning" in info.capabilities


def test_a_model_the_live_list_says_nothing_about_takes_the_catalogues_word(catalogue):
    infos = _live({"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"},
                  {"slug": "gpt-7-nova", "display_name": "GPT-7 Nova", "supported_reasoning_levels": []})

    assert infos["gpt-6.1-sol"].reasoning["supported_efforts"] == ["low", "medium", "high", "xhigh"]
    # Neither the list nor models.dev says it reasons: no control is made up.
    assert infos["gpt-7-nova"].reasoning is None


def test_gpt_6_1_sol_from_the_live_list_ends_with_the_pickers_choices(catalogue, monkeypatch):
    import row_bot.models as models
    import row_bot.providers.codex as codex
    from row_bot.providers.reasoning import reasoning_choices, resolve_reasoning_capabilities

    live = _live({"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol",
                  "supported_reasoning_levels": LEVELS[1:], "default_reasoning_level": "high"})
    monkeypatch.setattr(models, "_cloud_model_cache", {})
    monkeypatch.setattr(models, "_save_cloud_cache", lambda: None)
    monkeypatch.setattr(models, "fetch_context_catalog", lambda: 0)
    monkeypatch.setattr(codex, "list_codex_model_infos", lambda force_refresh=False: list(live.values()))

    assert models.refresh_cloud_provider_models("codex").status == "live"
    caps = resolve_reasoning_capabilities("codex", "gpt-6.1-sol")

    # The live list's own levels, not the fallback's: this model has no Low.
    assert caps.supported_efforts == ("medium", "high", "xhigh")
    assert [choice.label for choice in reasoning_choices(caps)] == ["Provider default", "Medium", "High", "XHigh"]


def test_a_catalog_saved_before_the_fix_with_no_levels_shows_the_picker_without_a_refresh(catalogue, monkeypatch):
    """The 5.3.3 eval profile's saved row for GPT-6.1 Sol, as it was written: ``reasoning: null``."""
    import row_bot.models as models
    from row_bot.application.reasoning_controls import reasoning_view

    monkeypatch.setattr(models, "_cloud_model_cache", {"model:codex:gpt-6.1-sol": {
        "provider": "codex", "source": "codex_live_catalog", "transport": "openai_responses",
        "capabilities_snapshot": {
            "capabilities": ["chat", "streaming", "text", "vision"], "input_modalities": ["image", "text"],
            "output_modalities": ["text"], "tasks": ["responses"], "transport": "openai_responses",
            "source_confidence": "live_chatgpt_codex_catalog", "reasoning": None,
        },
    }})

    view = reasoning_view("conversation-a", "model:codex:gpt-6.1-sol")

    assert view["available"] is True
    assert [choice["label"] for choice in view["choices"]] == ["Provider default", "Low", "Medium", "High", "XHigh"]


def test_a_new_grok_on_the_xai_sign_in_gets_the_same_control_as_on_an_api_key(catalogue):
    from row_bot.providers.reasoning import resolve_reasoning_capabilities

    oauth = resolve_reasoning_capabilities("xai_oauth", "grok-5")
    api_key = resolve_reasoning_capabilities("xai", "grok-5")

    assert oauth.supported_efforts == api_key.supported_efforts == ("low", "high")
    assert oauth.mandatory and not oauth.can_disable


def test_levels_listed_as_objects_never_become_their_text():
    from row_bot.providers.reasoning import ReasoningCapabilities

    caps = ReasoningCapabilities.from_json({"supported_efforts": [{"effort": "low"}, "high", {"other": 1}, 3]})

    assert caps.supported_efforts == ("low", "high")


def test_a_documented_gpt6_model_sees_images_like_the_gpt5_ones(monkeypatch):
    import row_bot.providers.codex as codex

    monkeypatch.setattr(codex, "FALLBACK_CODEX_MODELS", [
        {"id": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol"}, {"id": "codex-mini", "display_name": "Codex Mini"}])
    infos = {info.model_id: info for info in codex.fallback_codex_model_infos()}

    assert "image" in infos["gpt-6.1-sol"].input_modalities and "vision" in infos["gpt-6.1-sol"].capabilities
    assert "image" not in infos["codex-mini"].input_modalities
