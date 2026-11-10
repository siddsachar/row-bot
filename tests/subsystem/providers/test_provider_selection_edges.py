from __future__ import annotations

import json

import pytest

import row_bot.providers.config as provider_config
from row_bot.providers import selection

pytestmark = pytest.mark.subsystem

CHAT = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"], "tool_calling": True}


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Scratch providers.json, catalog and stars; Ollama and OpenAI connected, nothing contacted."""
    import row_bot.providers.model_catalog_cache as cache
    from row_bot.providers import model_catalog, model_visibility

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        version=cache.CACHE_VERSION, generated_at=123.0, cloud_cache={
            "model:anthropic:claude-sonnet-4-5": {"provider": "anthropic", "label": "Claude Work",
                                                  "capabilities_snapshot": CHAT},
            "model:openai:gpt-4o": {"provider": "openai", "label": "Fast OpenAI", "capabilities_snapshot": CHAT},
        }, ollama_rows=[{"model_id": "qwen3:27b", "display_name": "Qwen 27B", "installed": True,
                         "capabilities_snapshot": CHAT}], provider_status={}, warnings=(), reason="test",
    ))
    settings = tmp_path / "model_settings.json"
    monkeypatch.setattr(model_visibility, "_settings_path", lambda: settings)
    statuses = {provider: {"configured": True, "runtime_enabled": True} for provider in ("ollama", "openai")}
    monkeypatch.setattr(model_catalog, "_provider_status_by_id", lambda: statuses)
    return settings


def _star(settings, *refs: str) -> None:
    settings.write_text(json.dumps({"model_visibility": {"favourites": list(refs)}}), encoding="utf-8")


def test_model_ref_parsing_labels_and_choice_value_roundtrip() -> None:
    assert selection.model_ref("openai", "gpt-4o") == "model:openai:gpt-4o"
    assert selection.parse_model_ref("model:local:qwen3:14b") == ("ollama", "qwen3:14b")
    assert selection.parse_model_ref("model::missing") is None
    assert selection.parse_model_ref("gpt-4o") is None

    value = selection.model_choice_value("gpt-4o", provider_id="openai")
    assert value == "model:openai:gpt-4o"
    assert selection.model_id_from_choice_value(value) == "gpt-4o"
    assert selection.provider_id_from_choice_value(value) == "openai"
    assert selection.provider_id_from_choice_value("qwen3:14b") == "ollama"
    assert selection.provider_display_label("custom_openai_local_lab") == "Custom Local Lab"
    assert selection.provider_icon_label("openai") == "OpenAI"
    assert selection.provider_icon_label("unknown") == ""
    assert selection.format_model_choice_label("openai", "gpt-4o", include_icon=True).startswith("OpenAI ")


def test_canonicalize_model_selection_handles_default_provider_refs_stars_and_inactive(isolated_profile) -> None:
    with pytest.raises(selection.ModelSelectionError, match="empty"):
        selection.canonicalize_model_selection("")
    assert selection.canonicalize_model_selection("", allow_default=True).source == "default"
    with pytest.raises(selection.ModelSelectionError, match="Default"):
        selection.canonicalize_model_selection("default")

    provider_ref = selection.canonicalize_model_selection("model:openai:gpt-4o")
    assert provider_ref.ref == "model:openai:gpt-4o"
    assert provider_ref.source == "provider_ref"

    _star(isolated_profile, "model:openai:gpt-4o")
    starred = selection.canonicalize_model_selection("Fast OpenAI")
    assert starred.ref == "model:openai:gpt-4o"
    assert starred.source == "star"

    # Anthropic isn't connected: its star is listed but can't be chosen by name.
    _star(isolated_profile, "model:anthropic:claude-sonnet-4-5")
    with pytest.raises(selection.ModelSelectionError, match="inactive"):
        selection.canonicalize_model_selection("Claude Work")

    inferred = selection.canonicalize_model_selection("gpt-4o")
    assert inferred.ref == "model:openai:gpt-4o"
    assert inferred.source == "inferred_provider"

    with pytest.raises(selection.ModelSelectionError, match="Cannot infer"):
        selection.canonicalize_model_selection("totally-unknown-model")


def test_canonicalize_model_selection_handles_custom_endpoint_matches_and_ambiguity() -> None:
    from row_bot.providers.custom import save_custom_endpoint

    save_custom_endpoint(
        {
            "id": "lab",
            "name": "Lab",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth_required": False,
            "models": [{"id": "lab-chat", "model_id": "lab-chat", "display_name": "Lab Chat"}],
        }
    )

    custom = selection.canonicalize_model_selection("Lab Chat")
    assert custom.ref == "model:custom_openai_lab:lab-chat"
    assert custom.source == "custom_endpoint_model"

    save_custom_endpoint(
        {
            "id": "other",
            "name": "Other",
            "base_url": "http://127.0.0.1:9000/v1",
            "auth_required": False,
            "models": [{"id": "lab-chat", "model_id": "lab-chat", "display_name": "Lab Chat"}],
        }
    )
    with pytest.raises(selection.ModelSelectionError, match="Ambiguous"):
        selection.canonicalize_model_selection("Lab Chat")


def test_model_selection_diagnostics_reports_resolution_details(monkeypatch) -> None:
    class FakeResolved:
        selection_ref = "model:openai:gpt-4o"
        provider_id = "openai"
        model_id = "gpt-4o"
        runtime_model = "gpt-4o"
        provider_display_name = "OpenAI API"
        source = "provider_ref"

    monkeypatch.setattr("row_bot.providers.resolution.resolve_provider_config", lambda *_args, **_kwargs: FakeResolved())

    diagnostics = selection.model_selection_diagnostics(
        "model:openai:gpt-4o",
        runtime_surface="chat",
        runtime_mode="agent",
        tools_bound=True,
    )

    assert diagnostics["raw_stored_model_override"] == "model:openai:gpt-4o"
    assert diagnostics["selection_provider_id"] == "openai"
    assert diagnostics["provider_display_name"] == "OpenAI API"
    assert diagnostics["tools_bound"] is True


def test_model_choice_options_include_diagnostics_and_inactive_include_values() -> None:
    ref = "model:openai:text-embedding-3-large"

    active_options, diagnostics = selection.list_model_choice_options("chat", return_diagnostics=True)
    assert active_options == []
    assert diagnostics["surface"] == "chat"
    assert diagnostics["options"] == 0

    inactive = selection.list_model_choice_options("chat", include_values=[ref], include_inactive=True)
    included = next(option for option in inactive if option["value"] == ref)
    assert included["active"] is False
    assert included["reason"].startswith("Capability metadata")
    assert str(included["label"]).startswith("Unavailable:")


def test_catalog_model_selection_requires_a_starred_brain_choice(isolated_profile) -> None:
    with pytest.raises(selection.ModelSelectionError, match="not pinned for Brain"):
        selection.resolve_catalog_model_selection(
            "model:openai:gpt-4o",
            surface="chat",
            require_agent_ready=True,
            require_pinned=True,
        )

    _star(isolated_profile, "model:openai:gpt-4o")

    resolved = selection.resolve_catalog_model_selection(
        "model:openai:gpt-4o",
        surface="chat",
        require_agent_ready=True,
        require_pinned=True,
    )
    assert resolved.ref == "model:openai:gpt-4o"
    assert selection.resolve_catalog_model_selection(
        "Fast OpenAI",
        surface="chat",
        require_agent_ready=True,
        require_pinned=True,
    ).ref == "model:openai:gpt-4o"


def test_starred_model_choice_summaries_are_agent_readable(isolated_profile) -> None:
    _star(isolated_profile, "model:ollama:qwen3:27b")

    summaries = selection.pinned_model_choice_summaries("chat")

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary["ref"] == "model:ollama:qwen3:27b"
    assert summary["canonical_ref"] == "model:ollama:qwen3:27b"
    assert summary["config_value"] == "ollama/qwen3:27b"
    assert summary["display_name"] == "Qwen 27B"
    assert summary["provider_id"] == "ollama"
    assert summary["model_id"] == "qwen3:27b"
    assert summary["active"] is True
    assert summary["reason"] == ""
    assert summary["source"] == "star"


def test_resolve_selection_refs_and_unknown_bare_models_are_stable() -> None:
    parsed = selection.resolve_selection("model:openai:gpt-4o")
    assert parsed is not None
    assert parsed.provider_id == "openai"
    assert parsed.model_id == "gpt-4o"

    unknown = selection.resolve_selection("local-only-family")
    assert unknown is not None
    assert unknown.provider_id == "ollama"
    assert unknown.ref == "model:ollama:local-only-family"
