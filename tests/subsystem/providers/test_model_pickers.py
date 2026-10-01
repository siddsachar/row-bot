"""One model list and one availability rule for every picker and the save (B226).

The composer, the Brain default, Vision, Image and Video all list the same
saved rows, judged by the same rule, and the Brain save accepts exactly what
the Brain picker marks available. Saved metadata only: fixture
``providers.json`` and ``model_catalog_cache.json`` files in ``tmp_path``, fixed
provider statuses, and no provider is ever contacted.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from row_bot.application import client_models_settings as owner
from row_bot.application import provider_default_model as default_model
from row_bot.providers import config, model_catalog, saved_model_settings
from row_bot.providers import model_catalog_cache as cache
from row_bot.providers.config import ProviderConfigError

pytestmark = pytest.mark.subsystem

CODEX_FULL = {
    "tasks": ["responses"], "input_modalities": ["image", "text"], "output_modalities": ["text"],
    "capabilities": ["chat", "streaming", "text", "vision"], "transport": "openai_responses",
    "tool_calling": True, "streaming": True,
}
# The ChatGPT subscription's own catalog row as its writer stored it before
# B226: capabilities and inputs, but no outputs or tasks.
CODEX_OWN_ROW = {
    "display_name": "", "context_window": 272_000, "input_modalities": ["image", "text"],
    "capabilities": ["chat", "streaming", "text", "vision"], "tool_calling": None, "streaming": True,
    "source_confidence": "live_chatgpt_codex_catalog", "source": "codex_live_catalog", "reasoning": None,
}
DOCUMENTED_CLAUDE = {
    "tasks": ["chat"], "input_modalities": ["image", "text"], "output_modalities": ["text"],
    "capabilities": ["chat", "streaming", "text", "tool_calling", "vision"],
    "transport": "anthropic_messages", "tool_calling": True, "streaming": True,
    "source_confidence": "documented_claude_model",
}
SEEING_CHAT = {"tasks": ["chat"], "input_modalities": ["image", "text"], "output_modalities": ["text"]}
STATUSES = {
    "codex": {"configured": True, "runtime_enabled": True},
    "claude_subscription": {"configured": True, "runtime_enabled": True},
    "openai": {"configured": False, "runtime_enabled": False},
}
SOL, LUNA = "model:codex:gpt-5.6-sol", "model:codex:gpt-5.6-luna"
SONNET, MYSTERY = "model:claude_subscription:claude-sonnet-5", "model:claude_subscription:claude-mystery"
UNCONNECTED = "model:openai:gpt-unconnected"


def _pin(provider: str, model: str, name: str, snapshot: dict) -> dict:
    """A pinned chat choice as providers.json stores it."""
    return {
        "id": f"model:{provider}:{model}", "kind": "model", "provider_id": provider, "model_id": model,
        "display_name": name, "visibility": ["chat", "workflow", "channels", "designer", "status_tool"],
        "pinned": True, "order": 1000, "recommended": False, "source": "manual",
        "capabilities_snapshot": snapshot, "risk_label": "subscription", "active": True,
        "inactive_reason": "", "inactive_surfaces": {}, "last_validated_at": "", "last_error": "",
    }


def _saved(model: str, name: str, snapshot: dict, provider: str = "codex") -> dict:
    return {"provider": provider, "model_id": model, "label": name, "ctx": 272_000,
            "capabilities_snapshot": snapshot}


@pytest.fixture
def saved_state(tmp_path, monkeypatch):
    """Fixture providers.json and model_catalog_cache.json; nothing real is read."""
    import row_bot.models as models
    from row_bot import agent, vision_runtime
    from row_bot.providers import claude_subscription, codex, runtime, selection
    from row_bot.tools import image_gen_tool, registry, video_gen_tool

    providers = tmp_path / "providers.json"
    providers.write_text(json.dumps({
        "providers": {"codex": {"catalog_cache": {"source": "live_chatgpt_codex_catalog", "models": [
            {**CODEX_OWN_ROW, "id": "gpt-5.6-sol", "display_name": "GPT-5.6 Sol (ChatGPT)"},
            {**CODEX_OWN_ROW, "id": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna (ChatGPT)"},
        ]}}},
        "quick_choices": [
            _pin("codex", "gpt-5.6-sol", "GPT-5.6 Sol (ChatGPT)", CODEX_FULL),
            _pin("codex", "gpt-5.6-luna", "GPT-5.6 Luna (ChatGPT)", CODEX_FULL),
            _pin("claude_subscription", "claude-sonnet-5", "Claude Sonnet 5", DOCUMENTED_CLAUDE),
            _pin("claude_subscription", "claude-mystery", "Claude Mystery", {}),
            _pin("openai", "gpt-unconnected", "GPT Unconnected", SEEING_CHAT),
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", providers)
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(1, 1000.0, {
        SOL: _saved("gpt-5.6-sol", "GPT-5.6 Sol (ChatGPT)", CODEX_FULL),
        LUNA: _saved("gpt-5.6-luna", "GPT-5.6 Luna (ChatGPT)", CODEX_FULL),
        UNCONNECTED: _saved("gpt-unconnected", "GPT Unconnected", SEEING_CHAT, provider="openai"),
    }, [], {}, (), "fixture"))
    settings_path = tmp_path / "model_settings.json"
    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(models, "_SETTINGS_PATH", settings_path)

    # Fixed local provider status; no provider, token or catalog is contacted.
    monkeypatch.setattr(model_catalog, "_provider_status_by_id", lambda: STATUSES)
    monkeypatch.setattr(runtime, "provider_status", lambda provider_id, **_kwargs: dict(STATUSES.get(provider_id, {})))
    selection._provider_status_picker_cache.clear()
    for module, name in ((codex, "fetch_codex_model_infos"),
                         (claude_subscription, "fetch_claude_subscription_model_infos"),
                         (cache, "start_model_catalog_refresh_background"),
                         (cache, "refresh_model_catalog_cache")):
        monkeypatch.setattr(module, name, lambda *_a, **_kw: pytest.fail("a picker read contacted a provider"))

    vision = SimpleNamespace(model="", enabled=True, camera_index=0)
    monkeypatch.setattr(models, "get_current_model", lambda: "")
    monkeypatch.setattr(models, "get_context_policy", lambda ref, **_kw: SimpleNamespace(
        policy_kind="provider", native_max=None, effective_context=0, warning=""))
    monkeypatch.setattr(models, "get_cloud_context_override", lambda: None)
    monkeypatch.setattr(vision_runtime, "get_vision_service", lambda: vision)
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: None)
    monkeypatch.setattr(registry, "get_tool", lambda name: None)
    monkeypatch.setattr(registry, "get_tool_config", lambda name, key, fallback: fallback)
    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {})
    monkeypatch.setattr(video_gen_tool, "get_available_video_models", lambda: {})
    return vision


def _options(surface: str) -> dict[str, dict]:
    return {option["selection_ref"]: option for option in owner.read_models_settings()[surface]["options"]}


def _review(ref: str) -> dict:
    _, provider, model = ref.split(":", 2)
    revision = default_model.read_default_model().revision
    return default_model.review_default_model(revision, provider, model, validate=lambda: None)


def test_a_subscription_own_catalog_row_without_outputs_keeps_chat_and_vision():
    rows = model_catalog.build_saved_model_catalog_rows(
        cloud_cache={SOL: _saved("gpt-5.6-sol", "GPT-5.6 Sol (ChatGPT)", CODEX_FULL)},
        ollama_rows=[],
        provider_config={"providers": {"codex": {"catalog_cache": {"models": [
            {**CODEX_OWN_ROW, "id": "gpt-5.6-sol", "display_name": "GPT-5.6 Sol (ChatGPT)"}]}}}},
    )

    row = next(row for row in rows if row.selection_ref == SOL)
    assert {"chat", "vision"} <= set(row.categories)
    assert row.context_window == 272_000
    assert row.display_name == "GPT-5.6 Sol (ChatGPT)"


def test_a_pinned_subscription_model_with_only_documented_metadata_is_offered_and_savable(saved_state):
    brain, vision = _options("brain"), _options("vision")

    assert brain[SONNET]["available"] is True
    assert brain[SONNET]["label"].startswith("Claude Sonnet 5")
    assert brain[SONNET]["billing"] == "subscription"
    assert vision[SONNET]["available"] is True
    assert _review(SONNET)["model_id"] == "claude-sonnet-5"
    owner.update_model_surface("vision", "default", selection_ref=SONNET, validate=lambda: None)
    assert saved_state.model == SONNET


def test_a_pinned_model_without_saved_metadata_is_listed_with_a_reason_to_refresh(saved_state):
    option = _options("brain")[MYSTERY]

    assert option["available"] is False
    assert option["unavailable_reason"] == "metadata_missing"
    assert "Refresh" in option["reason"]
    with pytest.raises(ProviderConfigError, match="model_configuration_unavailable"):
        _review(MYSTERY)


def test_every_brain_option_marked_available_passes_the_save_and_no_other(saved_state):
    brain = _options("brain")

    assert {SOL, LUNA, SONNET, MYSTERY, UNCONNECTED} <= set(brain)
    assert brain[UNCONNECTED]["unavailable_reason"] == "configuration_required"
    for ref, option in brain.items():
        if option["available"]:
            assert _review(ref)["operation"] == "provider.default_model.save"
        else:
            with pytest.raises(ProviderConfigError, match="model_configuration_unavailable"):
                _review(ref)


def test_every_vision_option_marked_available_saves_and_no_other(saved_state):
    vision = _options("vision")

    assert {ref for ref, option in vision.items() if option["available"]} == {SOL, LUNA, SONNET}
    assert vision[UNCONNECTED]["unavailable_reason"] == "configuration_required"
    for ref, option in vision.items():
        if option["available"]:
            owner.update_model_surface("vision", "default", selection_ref=ref, validate=lambda: None)
            assert saved_state.model == ref
        else:
            with pytest.raises(ValueError, match="model_configuration_unavailable"):
                owner.update_model_surface("vision", "default", selection_ref=ref, validate=lambda: None)


def test_the_composer_offers_the_brain_list_with_the_same_availability(saved_state, monkeypatch):
    from row_bot.api.v1.routes import cached_choices
    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import registry as plugin_registry
    from row_bot.tools import registry as tool_registry

    monkeypatch.setattr(tool_registry, "get_all_tools", lambda: [])
    monkeypatch.setattr(plugin_registry, "get_loaded_manifests", lambda: [])
    monkeypatch.setattr(mcp_runtime, "get_catalog_snapshot", lambda: {})

    composer = {model["model_ref"]: model for model in cached_choices()["models"]}
    brain = _options("brain")

    assert {ref: model["available"] for ref, model in composer.items()} == {
        ref: option["available"] for ref, option in brain.items()}
    assert composer[SOL]["billing"] == "subscription"
    assert composer[MYSTERY]["unavailable_reason"] == "metadata_missing"
    assert composer[UNCONNECTED]["unavailable_reason"] == "configuration_required"
