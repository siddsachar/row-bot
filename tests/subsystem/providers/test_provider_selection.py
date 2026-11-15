"""Model choices for channels, agents and the status tool: your starred models, provider-qualified.

Saved metadata only: fixture ``providers.json``, ``model_catalog_cache.json`` and
``model_settings.json`` files under ``tmp_path`` and fixed provider statuses; no
provider is contacted.
"""
import json

import pytest

import row_bot.providers.config as provider_config
from row_bot.providers.selection import (
    ModelSelectionError,
    canonicalize_model_selection,
    list_model_choice_options,
    model_choice_value,
    provider_display_label,
    resolve_selection,
    starred_model_refs,
)

CHAT = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}
REALTIME = {"tasks": ["realtime"], "input_modalities": ["audio", "text"], "output_modalities": ["audio", "text"]}
IMAGE = {"tasks": ["image_generation"], "input_modalities": ["text"], "output_modalities": ["image"]}


def _write_catalog_cache(monkeypatch, tmp_path, *, cloud=None, ollama_rows=()):
    import row_bot.providers.model_catalog_cache as cache

    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        version=cache.CACHE_VERSION, generated_at=123.0, cloud_cache=dict(cloud or {}),
        ollama_rows=list(ollama_rows), provider_status={}, warnings=(), reason="test",
    ))


def _cloud(provider: str, model: str, snapshot: dict, label: str = "") -> tuple[str, dict]:
    return f"model:{provider}:{model}", {"provider": provider, "label": label or model, "ctx": 128_000,
                                          "capabilities_snapshot": snapshot}


def _ollama_vision_row(model_id: str) -> dict:
    return {
        "provider_id": "ollama",
        "model_id": model_id,
        "display_name": model_id,
        "installed": True,
        "capabilities_snapshot": {
            "capabilities": ["chat", "streaming", "text", "vision"],
            "input_modalities": ["image", "text"],
            "output_modalities": ["text"],
            "tasks": ["chat"],
            "transport": "ollama_chat",
        },
    }


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A scratch profile: no stars, an empty catalog, every provider but Ollama unconnected."""
    from row_bot.providers import model_catalog, model_visibility

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    settings = tmp_path / "model_settings.json"
    monkeypatch.setattr(model_visibility, "_settings_path", lambda: settings)
    statuses = {"ollama": {"configured": True, "runtime_enabled": True}}
    monkeypatch.setattr(model_catalog, "_provider_status_by_id", lambda: statuses)
    _write_catalog_cache(monkeypatch, tmp_path)

    def star(*refs: str) -> None:
        settings.write_text(json.dumps({"model_visibility": {"favourites": list(refs)}}), encoding="utf-8")

    return type("Profile", (), {"star": staticmethod(star), "statuses": statuses, "tmp": tmp_path})


def test_choices_are_your_stars_for_the_job_in_the_order_you_starred_them(profile, monkeypatch):
    _write_catalog_cache(monkeypatch, profile.tmp, cloud=dict([
        _cloud("openai", "gpt-5.5", CHAT, "GPT-5.5"),
        _cloud("codex", "gpt-5.5", CHAT, "GPT-5.5"),
        _cloud("openai", "gpt-realtime", REALTIME),
        _cloud("openai", "gpt-image-1", IMAGE),
        _cloud("openai", "gpt-unstarred", CHAT),
    ]))
    profile.statuses.update({"openai": {"configured": True, "runtime_enabled": True},
                             "codex": {"configured": True, "runtime_enabled": True}})
    profile.star("model:codex:gpt-5.5", "model:openai:gpt-realtime", "model:openai:gpt-5.5",
                 "model:openai:gpt-image-1")

    chat = list_model_choice_options("chat")
    # The same model id from two providers stays two provider-qualified choices.
    assert [option["value"] for option in chat] == ["model:codex:gpt-5.5", "model:openai:gpt-5.5"]
    labels = {option["value"]: option["label"] for option in chat}
    assert "ChatGPT / Codex" in labels["model:codex:gpt-5.5"] and "OpenAI API" in labels["model:openai:gpt-5.5"]
    # Channels and agents read the chat list; a voice or image star stays in its own list.
    assert [option["value"] for option in list_model_choice_options("channels")] == [
        option["value"] for option in chat]
    assert [option["value"] for option in list_model_choice_options("image")] == ["model:openai:gpt-image-1"]
    assert starred_model_refs("voice") == ["model:openai:gpt-realtime"]


def test_a_star_that_isnt_ready_is_listed_only_with_its_reason(profile, monkeypatch):
    _write_catalog_cache(monkeypatch, profile.tmp, cloud=dict([_cloud("anthropic", "claude-opus-4-7", CHAT)]))
    profile.star("model:anthropic:claude-opus-4-7", "model:openai:gone-from-the-catalog")

    assert list_model_choice_options("chat") == []
    (inactive,) = list_model_choice_options("chat", include_inactive=True)
    assert inactive["value"] == "model:anthropic:claude-opus-4-7"
    assert inactive["active"] is False and "Connect" in inactive["reason"]
    # A star whose model is gone from the catalog simply isn't listed.
    assert starred_model_refs("chat") == []


def test_a_bare_name_resolves_to_exactly_one_of_your_models(profile, monkeypatch):
    _write_catalog_cache(monkeypatch, profile.tmp, cloud=dict([
        _cloud("openai", "gpt-5.5", CHAT), _cloud("codex", "gpt-5.5", CHAT), _cloud("openai", "o3", CHAT)]))
    profile.statuses.update({"openai": {"configured": True, "runtime_enabled": True},
                             "codex": {"configured": True, "runtime_enabled": True}})
    profile.star("model:openai:o3", "model:openai:gpt-5.5", "model:codex:gpt-5.5")

    assert canonicalize_model_selection("o3").ref == "model:openai:o3"
    with pytest.raises(ModelSelectionError, match="Ambiguous model selection 'gpt-5.5'"):
        canonicalize_model_selection("gpt-5.5")
    assert canonicalize_model_selection("model:codex:gpt-5.5").provider_id == "codex"


def test_model_choice_value_preserves_ollama_provider_ref():
    assert model_choice_value("qwen3:14b", provider_id="ollama") == "model:ollama:qwen3:14b"
    assert model_choice_value("model:ollama:qwen3:14b") == "model:ollama:qwen3:14b"
    assert model_choice_value("model:local:qwen3:14b") == "model:ollama:qwen3:14b"
    assert model_choice_value("gpt-5.5") == "model:openai:gpt-5.5"


def test_included_ollama_value_stays_provider_qualified(profile):
    options = list_model_choice_options("chat", include_values=["model:ollama:qwen3:14b"])

    assert options[0]["value"] == "model:ollama:qwen3:14b"
    assert options[0]["provider_id"] == "ollama"


def test_included_ollama_vision_value_uses_cached_catalog_snapshot(profile, monkeypatch):
    model_id = "qwen3.6:35b-a3b-mtp-q4_K_M"
    ref = f"model:ollama:{model_id}"
    _write_catalog_cache(monkeypatch, profile.tmp, ollama_rows=[_ollama_vision_row(model_id)])

    options = list_model_choice_options("vision", include_values=[ref])

    assert [option["value"] for option in options] == [ref]
    assert options[0]["provider_id"] == "ollama"
    assert options[0]["source"] == "included_value"


def test_included_voice_value_is_blocked_from_chat_picker(profile):
    ref = "model:openai:gpt-4o-audio-preview"
    options = list_model_choice_options("chat", include_values=[ref])
    inactive = list_model_choice_options("chat", include_values=[ref], include_inactive=True)

    assert ref not in {option["value"] for option in options}
    included = next(option for option in inactive if option["value"] == ref)
    assert included["active"] is False
    assert "not compatible with chat" in included["reason"]


def test_include_values_respects_custom_endpoint_manual_vision_off(profile):
    from row_bot.providers.custom import save_custom_endpoint

    provider_id = "custom_openai_lm-studio"
    ref = f"model:{provider_id}:qwen/qwen3.5-9b"
    save_custom_endpoint({
        "id": "lm-studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "manual_capabilities": {"vision": False},
        "models": [{"id": "qwen/qwen3.5-9b", "model_id": "qwen/qwen3.5-9b", "capabilities_snapshot": CHAT}],
    })

    options = list_model_choice_options("vision", include_values=[ref])
    inactive_options = list_model_choice_options("vision", include_values=[ref], include_inactive=True)

    assert ref not in {option["value"] for option in options}
    included = next(option for option in inactive_options if option["value"] == ref)
    assert included["active"] is False
    assert included["reason"] == "manual vision capability disabled"


def test_stored_values_resolve_to_provider_qualified_models(profile):
    assert resolve_selection("qwen3:14b").ref == "model:ollama:qwen3:14b"
    codex = resolve_selection("model:codex:gpt-5.5")
    assert (codex.provider_id, codex.model_id) == ("codex", "gpt-5.5")
    assert resolve_selection("claude-sonnet-4-5").provider_id == "anthropic"
    assert resolve_selection("") is None


def test_provider_display_label_uses_dynamic_provider_metadata():
    assert provider_display_label("codex") == "ChatGPT / Codex"
    assert provider_display_label("custom_openai_local_vllm") == "Custom Local Vllm"
