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
from row_bot.providers import config, model_catalog, model_visibility, saved_model_settings
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
# Any Claude model name is Claude Code's to accept, so the model without saved metadata is a Codex one.
SONNET, MYSTERY = "model:claude_subscription:claude-sonnet-5", "model:codex:gpt-mystery"
UNCONNECTED = "model:openai:gpt-unconnected"
# In the catalog, never pinned.
TERRA = "model:codex:gpt-5.6-terra"


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
            _pin("codex", "gpt-mystery", "GPT Mystery", {}),
            _pin("openai", "gpt-unconnected", "GPT Unconnected", SEEING_CHAT),
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", providers)
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(1, 1000.0, {
        SOL: _saved("gpt-5.6-sol", "GPT-5.6 Sol (ChatGPT)", CODEX_FULL),
        LUNA: _saved("gpt-5.6-luna", "GPT-5.6 Luna (ChatGPT)", CODEX_FULL),
        TERRA: _saved("gpt-5.6-terra", "GPT-5.6 Terra (ChatGPT)", CODEX_FULL),
        UNCONNECTED: _saved("gpt-unconnected", "GPT Unconnected", SEEING_CHAT, provider="openai"),
    }, [], {}, (), "fixture"))
    settings_path = tmp_path / "model_settings.json"
    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(models, "_SETTINGS_PATH", settings_path)
    # Stars too: a star a test adds stays in its own folder, never the suite's shared one for the next run.
    monkeypatch.setattr(model_visibility, "_settings_path", lambda: settings_path)

    # Fixed local provider status; no provider, token or catalog is contacted.
    monkeypatch.setattr(model_catalog, "_provider_status_by_id", lambda: STATUSES)
    monkeypatch.setattr(runtime, "provider_status", lambda provider_id, **_kwargs: dict(STATUSES.get(provider_id, {})))
    selection._provider_status_picker_cache.clear()
    for module, name in ((codex, "fetch_codex_model_infos"),
                         (claude_subscription, "discover_claude_plan_models"),
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


def test_every_picker_marks_favourites_and_hidden_models_alike(saved_state, monkeypatch, tmp_path):
    """F10: one preference for every list; the options themselves don't change."""
    from row_bot.providers import model_visibility

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    before = _options("brain")
    model_visibility.update_model_visibility("favourite", SONNET)
    model_visibility.update_model_visibility("favourite", LUNA)
    model_visibility.update_model_visibility("hide", SOL)
    brain, vision = _options("brain"), _options("vision")

    assert set(brain) == set(before)
    for options in (brain, vision):
        assert options[SONNET]["favourite_rank"] == 1 and options[LUNA]["favourite_rank"] == 2
        assert options[SOL]["hidden"] is True and options[SOL]["available"] is True
    assert "favourite_rank" not in brain[MYSTERY] and "hidden" not in brain[MYSTERY]
    # A hidden model can still be chosen.
    owner.update_model_surface("vision", "default", selection_ref=SOL, validate=lambda: None)
    assert saved_state.model == SOL


def test_a_starred_model_is_listed_in_every_picker_for_its_jobs_and_unstarring_takes_it_out(
    saved_state, monkeypatch, tmp_path,
):
    """U10: one concept. A star lists a model wherever a model is chosen; pins still list theirs."""
    from row_bot.api.v1.routes import cached_choices
    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import registry as plugin_registry
    from row_bot.providers import client_status, model_visibility
    from row_bot.tools import registry as tool_registry

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tool_registry, "get_all_tools", lambda: [])
    monkeypatch.setattr(plugin_registry, "get_loaded_manifests", lambda: [])
    monkeypatch.setattr(mcp_runtime, "get_catalog_snapshot", lambda: {})
    pinned = set(_options("brain"))
    assert TERRA not in pinned

    model_visibility.update_model_visibility("favourite", TERRA)
    brain, vision = _options("brain"), _options("vision")
    composer = {model["model_ref"]: model for model in cached_choices()["models"]}
    for options in (brain, vision):
        assert options[TERRA]["available"] is True and options[TERRA]["favourite_rank"] == 1
    assert composer[TERRA]["favourite_rank"] == 1
    assert set(brain) == pinned | {TERRA}  # every pinned model stays listed
    assert _review(TERRA)["model_id"] == "gpt-5.6-terra"
    # An agent may be started on it, as on a pinned model.
    from row_bot.providers.selection import resolve_catalog_model_selection

    agent = resolve_catalog_model_selection(TERRA, surface="chat", require_agent_ready=True, require_pinned=True)
    assert agent.ref == TERRA
    owner.update_model_surface("vision", "default", selection_ref=TERRA, validate=lambda: None)
    assert saved_state.model == TERRA
    # The Catalog shows the star on the row and counts it as listed.
    page = client_status.list_cached_models(provider_id="codex", surface="chat")
    row = next(item for item in page.items if item.selection_ref == TERRA)
    assert row.favourite_rank == 1 and row.hidden is False
    summary = owner.catalog_provider_summary("chat")
    # Sol and Luna pinned, Terra starred (Mystery has no saved details for chat yet).
    assert next(item for item in summary["providers"] if item["provider_id"] == "codex")["pinned"] == 3

    model_visibility.update_model_visibility("unfavourite", TERRA)
    assert TERRA not in _options("brain")
    # Vision still lists it while it is Vision's current model.
    assert TERRA in _options("vision")


def test_with_nothing_pinned_or_starred_a_picker_lists_the_ready_models_for_its_job(saved_state, tmp_path):
    """A new profile that just connected ChatGPT isn't shown "No models yet" beside a catalog of ready models."""
    from row_bot.providers import model_visibility

    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    config.CONFIG_PATH.write_text(json.dumps({**saved, "quick_choices": []}), encoding="utf-8")

    brain = _options("brain")
    assert {SOL, LUNA, TERRA} <= set(brain) and UNCONNECTED not in brain, "ready ones only"
    assert all(option["available"] for option in brain.values())

    model_visibility.update_model_visibility("favourite", LUNA)
    assert set(_options("brain")) == {LUNA}, "a star keeps the list to the starred"


def test_a_provider_connected_after_another_lists_its_ready_models_until_one_is_starred(saved_state):
    """Ollama's model was chosen at first run; ChatGPT connected later still shows in the Brain picker (and so in
    its search), rather than only in the Catalog."""
    from row_bot.providers import model_visibility

    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    config.CONFIG_PATH.write_text(json.dumps({**saved, "quick_choices": [
        _pin("claude_subscription", "claude-sonnet-5", "Claude Sonnet 5", DOCUMENTED_CLAUDE)]}), encoding="utf-8")

    brain = _options("brain")
    assert SONNET in brain
    assert {SOL, LUNA, TERRA} <= set(brain), "nothing of ChatGPT's is chosen yet: its ready models are listed"

    model_visibility.update_model_visibility("favourite", TERRA)
    assert set(_options("brain")) == {SONNET, TERRA}, "a star keeps ChatGPT's list to the starred one"
