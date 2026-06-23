from __future__ import annotations

import pytest

import row_bot.api_keys as api_keys
import row_bot.providers.config as provider_config
from row_bot.providers import selection


pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def isolated_provider_config(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(selection, "load_provider_config", provider_config.load_provider_config)
    monkeypatch.setattr(selection, "save_provider_config", provider_config.save_provider_config)
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_config.save_provider_config({})
    selection._provider_status_picker_cache.clear()
    yield
    provider_config.save_provider_config({})
    selection._provider_status_picker_cache.clear()


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


def test_canonicalize_model_selection_handles_default_provider_refs_quick_choices_and_inactive() -> None:
    with pytest.raises(selection.ModelSelectionError, match="empty"):
        selection.canonicalize_model_selection("")
    assert selection.canonicalize_model_selection("", allow_default=True).source == "default"
    with pytest.raises(selection.ModelSelectionError, match="Default"):
        selection.canonicalize_model_selection("default")

    provider_ref = selection.canonicalize_model_selection("model:openai:gpt-4o")
    assert provider_ref.ref == "model:openai:gpt-4o"
    assert provider_ref.source == "provider_ref"

    selection.add_quick_choice_for_model("claude-sonnet-4-5", provider_id="anthropic", display_name="Claude Work")
    quick = selection.canonicalize_model_selection("Claude Work")
    assert quick.ref == "model:anthropic:claude-sonnet-4-5"
    assert quick.source == "quick_choice"

    selection.deactivate_quick_choice(model_id="claude-sonnet-4-5", provider_id="anthropic", reason="removed")
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


def test_provider_status_picker_cache_and_codex_inactive_reason(monkeypatch) -> None:
    import row_bot.providers.runtime as provider_runtime

    calls = {"count": 0}

    def fake_provider_status(provider_id: str, refresh_tokens: bool = False):
        calls["count"] += 1
        assert refresh_tokens is False
        return {"runtime_enabled": False}

    monkeypatch.setattr(provider_runtime, "provider_status", fake_provider_status)
    choice = selection._quick_choice_for_model(
        "gpt-5.5",
        provider_id="codex",
        capabilities_snapshot={"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]},
    )

    assert selection._surface_inactive_reason(choice, "chat") == (
        "Codex account is connected, but direct chat runtime is not enabled yet."
    )
    assert selection._surface_inactive_reason(choice, "chat") == (
        "Codex account is connected, but direct chat runtime is not enabled yet."
    )
    assert calls["count"] == 1


def test_model_choice_options_include_diagnostics_and_inactive_include_values() -> None:
    ref = "model:openai:text-embedding-3-large"

    active_options, diagnostics = selection.list_model_choice_options("chat", return_diagnostics=True)
    assert active_options == []
    assert diagnostics["surface"] == "chat"
    assert diagnostics["quick_choices_read_only"] is True

    inactive = selection.list_model_choice_options("chat", include_values=[ref], include_inactive=True)
    included = next(option for option in inactive if option["value"] == ref)
    assert included["active"] is False
    assert included["reason"].startswith("Capability metadata")
    assert selection.model_choice_options_map("chat", include_values=[ref], include_inactive=True)[ref].startswith(
        "Unavailable:"
    )


def test_capability_snapshot_helpers_and_validation_reason_cleanup(monkeypatch) -> None:
    provider_config.save_provider_config(
        {
            "quick_choices": [
                {
                    "id": "model:openai:gpt-image-1",
                    "kind": "model",
                    "provider_id": "openai",
                    "model_id": "gpt-image-1",
                    "display_name": "GPT Image",
                    "visibility": ["chat", "image"],
                    "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]},
                    "inactive_surfaces": {"chat": selection._surface_unsupported_reason("chat"), "image": "manual"},
                    "last_error": selection._surface_unsupported_reason("chat"),
                    "active": True,
                }
            ]
        }
    )
    monkeypatch.setattr(
        selection,
        "_inferred_capability_snapshot",
        lambda choice: {"tasks": ["image_generation"], "input_modalities": ["text"], "output_modalities": ["image"]},
    )

    refreshed = selection.refresh_quick_choice_capability_snapshots()

    assert refreshed[0]["capabilities_snapshot"]["tasks"] == ["image_generation"]
    assert refreshed[0]["inactive_surfaces"] == {"image": "manual"}
    assert refreshed[0]["last_error"] == ""
    assert selection._is_auto_capability_reason(selection._surface_unsupported_reason("vision")) is True
    assert selection._surface_unsupported_reason("status_tool").endswith("status tool.")


def test_seed_configured_media_quick_choices_updates_existing_defaults(monkeypatch) -> None:
    image_choice = {
        "id": "model:openai:gpt-image-1",
        "kind": "model",
        "provider_id": "openai",
        "model_id": "gpt-image-1",
        "display_name": "GPT Image",
        "visibility": ["image"],
        "capabilities_snapshot": {"tasks": ["image_generation"], "input_modalities": ["text"], "output_modalities": ["image"]},
        "source": "image_tool_default",
        "active": True,
        "inactive_reason": "",
        "inactive_surfaces": {},
    }
    updated_choice = {**image_choice, "display_name": "GPT Image Updated"}
    image_calls = iter([image_choice, updated_choice])

    monkeypatch.setattr(selection, "_media_tool_selection", lambda tool_name, default_model: "openai/gpt-image-1")
    monkeypatch.setattr(
        selection,
        "_quick_choice_for_media_selection",
        lambda _selection, surface: next(image_calls) if surface == "image" else None,
    )

    first = selection.seed_configured_media_quick_choices()
    second = selection.seed_configured_media_quick_choices()

    assert first[0]["display_name"] == "GPT Image"
    assert second[0]["display_name"] == "GPT Image Updated"


def test_resolve_selection_routes_refs_and_unknown_bare_models_are_stable() -> None:
    assert selection.route_ref("balanced") == "route:balanced"
    route = selection.resolve_selection("route:balanced")
    assert route is not None
    assert route.kind == "route"
    assert route.active is False

    parsed = selection.resolve_selection("model:openai:gpt-4o")
    assert parsed is not None
    assert parsed.provider_id == "openai"
    assert parsed.model_id == "gpt-4o"

    unknown = selection.resolve_selection("local-only-family")
    assert unknown is not None
    assert unknown.provider_id == "ollama"
    assert unknown.ref == "model:ollama:local-only-family"
