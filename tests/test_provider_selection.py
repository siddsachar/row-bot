import pathlib

import row_bot.api_keys as api_keys
import row_bot.providers.config as provider_config
from row_bot.providers.selection import (
    add_quick_choice_for_model,
    deactivate_quick_choice,
    deactivate_quick_choice_for_error,
    grouped_quick_choices,
    list_model_choice_options,
    list_quick_model_ids,
    list_quick_choices,
    model_choice_options_map,
    model_choice_value,
    migrate_legacy_starred_models,
    provider_display_label,
    prune_stale_custom_quick_choices,
    refresh_quick_choice_capability_snapshots,
    remove_quick_choices_for_missing_models,
    remove_quick_choices_for_provider,
    remove_quick_choice_for_model,
    resolve_selection,
    validate_quick_choices_for_surface,
)


def test_legacy_starred_models_migrate_to_quick_choices(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": ["gpt-4o", "qwen3:14b"]})

    quick = migrate_legacy_starred_models(cloud_models=["gpt-4o"])

    assert [choice["model_id"] for choice in quick] == ["gpt-4o"]
    assert list_quick_model_ids("chat") == ["gpt-4o"]


def test_quick_choice_add_remove_and_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("claude-sonnet-4-5", display_name="Claude Work")

    resolved = resolve_selection("Claude Work")
    assert resolved is not None
    assert resolved.provider_id == "anthropic"
    assert resolved.model_id == "claude-sonnet-4-5"

    remove_quick_choice_for_model("claude-sonnet-4-5")
    assert list_quick_model_ids("chat") == []


def test_route_refs_resolve_but_are_not_runtime_active(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    resolved = resolve_selection("route:balanced")

    assert resolved is not None
    assert resolved.kind == "route"
    assert resolved.active is False


def test_quick_choices_filter_by_capability_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model(
        "text-embedding-3-large",
        provider_id="openai",
        capabilities_snapshot={
            "tasks": ["embedding"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
        },
    )

    assert list_quick_choices("chat") == []
    assert [choice["model_id"] for choice in list_quick_choices("embeddings")] == ["text-embedding-3-large"]
    stored = provider_config.load_provider_config()["quick_choices"][0]
    assert stored["inactive_surfaces"]["chat"].startswith("Capability metadata")


def test_surface_inactive_quick_choice_stays_available_for_other_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("gpt-4o", provider_id="openai", display_name="GPT Work")
    assert deactivate_quick_choice(model_id="gpt-4o", provider_id="openai", surface="chat", reason="Chat probe failed") is True

    assert list_quick_choices("chat") == []
    visible_elsewhere = list_quick_choices("")
    assert [choice["display_name"] for choice in visible_elsewhere] == ["GPT Work"]


def test_globally_inactive_quick_choice_resolves_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("gpt-4o", provider_id="openai", display_name="GPT Work")
    assert deactivate_quick_choice(model_id="gpt-4o", provider_id="openai", reason="Model removed by provider") is True

    assert list_quick_choices("") == []
    inactive = list_quick_choices("", include_inactive=True)
    assert inactive[0]["active"] is False
    assert inactive[0]["inactive_reason"] == "Model removed by provider"
    resolved = resolve_selection("GPT Work")
    assert resolved is not None
    assert resolved.active is False
    assert resolved.reason == "Model removed by provider"


def test_repinning_quick_choice_reactivates_it(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("gpt-4o", provider_id="openai")
    deactivate_quick_choice(model_id="gpt-4o", provider_id="openai", reason="Temporary failure")
    add_quick_choice_for_model("gpt-4o", provider_id="openai")

    assert list_quick_choices("chat")[0]["active"] is True


def test_surface_specific_pin_visibility_keeps_media_out_of_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model(
        "gpt-image-1",
        provider_id="openai",
        surface="image",
        capabilities_snapshot={
            "tasks": ["image_generation"],
            "input_modalities": ["text"],
            "output_modalities": ["image"],
        },
    )

    assert list_quick_model_ids("chat") == []
    assert list_quick_model_ids("image") == ["gpt-image-1"]
    stored = provider_config.load_provider_config()["quick_choices"][0]
    assert stored["visibility"] == ["image"]


def test_voice_quick_choices_do_not_leak_into_chat_picker(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("gpt-realtime", provider_id="openai", surface="voice")

    assert list_quick_model_ids("chat") == []
    assert list_quick_model_ids("voice") == ["gpt-realtime"]
    stored = provider_config.load_provider_config()["quick_choices"][0]
    assert stored["visibility"] == ["voice"]
    assert stored["capabilities_snapshot"]["tasks"] == ["realtime"]


def test_included_voice_value_is_blocked_from_chat_picker(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    ref = "model:openai:gpt-4o-audio-preview"
    options = list_model_choice_options("chat", include_values=[ref])
    inactive = list_model_choice_options("chat", include_values=[ref], include_inactive=True)

    assert ref not in {option["value"] for option in options}
    included = next(option for option in inactive if option["value"] == ref)
    assert included["active"] is False
    assert "not compatible with chat" in included["reason"]


def test_deactivate_quick_choice_for_error_uses_normalized_next_action(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("gpt-image-1", provider_id="openai")
    assert deactivate_quick_choice_for_error(
        model_id="gpt-image-1",
        provider_id="openai",
        surface="chat",
        error=ValueError("model is not a chat model"),
    ) is True

    inactive = list_quick_choices("chat", include_inactive=True)
    assert inactive[0]["active"] is False
    assert inactive[0]["inactive_reason"] == "Choose a model whose capability badges match this surface."


def test_quick_choice_remove_supports_custom_provider_id(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    provider_id = "custom_openai_dummy"
    add_quick_choice_for_model("row-bot-dummy-chat", provider_id=provider_id)
    remove_quick_choice_for_model("row-bot-dummy-chat", provider_id=provider_id)

    assert list_quick_choices("") == []


def test_quick_choices_keep_same_model_id_for_different_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_config.save_provider_config({
        "custom_endpoints": [{
            "id": "lab",
            "name": "Lab",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth_required": False,
        }],
    })

    add_quick_choice_for_model("shared-model", provider_id="openrouter", display_name="Routed Shared")
    add_quick_choice_for_model("shared-model", provider_id="custom_openai_lab", display_name="Lab Shared")

    choices = list_quick_choices("")

    assert {choice["id"] for choice in choices} == {
        "model:openrouter:shared-model",
        "model:custom_openai_lab:shared-model",
    }
    assert {choice["display_name"] for choice in choices} == {"Routed Shared", "Lab Shared"}


def test_remove_quick_choices_for_provider_is_provider_qualified(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_config.save_provider_config({
        "custom_endpoints": [
            {"id": "old", "name": "Old", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False},
            {"id": "new", "name": "New", "base_url": "http://127.0.0.1:9000/v1", "auth_required": False},
        ],
    })

    add_quick_choice_for_model("shared-model", provider_id="custom_openai_old", display_name="Old Shared")
    add_quick_choice_for_model("shared-model", provider_id="custom_openai_new", display_name="New Shared")

    assert remove_quick_choices_for_provider("custom_openai_old") == 1

    choices = list_quick_choices("")
    assert [choice["id"] for choice in choices] == ["model:custom_openai_new:shared-model"]


def test_remove_quick_choices_for_missing_models_keeps_valid_provider_models(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_config.save_provider_config({
        "custom_endpoints": [
            {"id": "lab", "name": "Lab", "base_url": "http://127.0.0.1:8000/v1", "auth_required": False},
            {"id": "other", "name": "Other", "base_url": "http://127.0.0.1:9000/v1", "auth_required": False},
        ],
    })

    add_quick_choice_for_model("old-model", provider_id="custom_openai_lab")
    add_quick_choice_for_model("current-model", provider_id="custom_openai_lab")
    add_quick_choice_for_model("old-model", provider_id="custom_openai_other")

    assert remove_quick_choices_for_missing_models("custom_openai_lab", {"current-model"}) == 1

    assert {choice["id"] for choice in list_quick_choices("")} == {
        "model:custom_openai_lab:current-model",
        "model:custom_openai_other:old-model",
    }


def test_stale_deleted_custom_quick_choice_can_be_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model("ghost-model", provider_id="custom_openai_deleted")

    assert prune_stale_custom_quick_choices() == 1
    assert provider_config.load_provider_config()["quick_choices"] == []


def test_stale_missing_custom_model_is_pruned_when_endpoint_models_are_known(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_config.save_provider_config({
        "custom_endpoints": [{
            "id": "lab",
            "name": "Lab",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth_required": False,
            "models": [{"id": "current-model", "model_id": "current-model"}],
        }],
    })
    add_quick_choice_for_model("old-model", provider_id="custom_openai_lab")
    add_quick_choice_for_model("current-model", provider_id="custom_openai_lab")

    assert prune_stale_custom_quick_choices() == 1

    assert [choice["id"] for choice in list_quick_choices("")] == ["model:custom_openai_lab:current-model"]


def test_model_choice_options_disambiguate_same_model_id_by_provider(tmp_path, monkeypatch):
    import row_bot.providers.runtime as provider_runtime

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(
        provider_runtime,
        "provider_status",
        lambda provider_id: {"runtime_enabled": True} if provider_id == "codex" else {},
    )

    snapshot = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}
    add_quick_choice_for_model("gpt-5.5", provider_id="openai", display_name="GPT-5.5", capabilities_snapshot=snapshot)
    add_quick_choice_for_model("gpt-5.5", provider_id="codex", display_name="GPT-5.5", capabilities_snapshot=snapshot)

    options = list_model_choice_options("chat")
    option_map = model_choice_options_map("chat")

    assert [option["value"] for option in options] == [
        "model:openai:gpt-5.5",
        "model:codex:gpt-5.5",
    ]
    assert "OpenAI API" in option_map["model:openai:gpt-5.5"]
    assert "ChatGPT / Codex" in option_map["model:codex:gpt-5.5"]
    assert model_choice_value("gpt-5.5") == "model:openai:gpt-5.5"

    resolved = resolve_selection("model:codex:gpt-5.5")
    assert resolved is not None
    assert resolved.provider_id == "codex"
    assert resolved.model_id == "gpt-5.5"


def test_model_choice_value_preserves_ollama_provider_ref():
    assert model_choice_value("qwen3:14b", provider_id="ollama") == "model:ollama:qwen3:14b"
    assert model_choice_value("model:ollama:qwen3:14b") == "model:ollama:qwen3:14b"
    assert model_choice_value("model:local:qwen3:14b") == "model:ollama:qwen3:14b"


def test_included_ollama_value_stays_provider_qualified(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    options = list_model_choice_options("chat", include_values=["model:ollama:qwen3:14b"])

    assert options[0]["value"] == "model:ollama:qwen3:14b"
    assert options[0]["provider_id"] == "ollama"


def test_unknown_bare_selection_resolves_to_ollama_not_openrouter(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    resolved = resolve_selection("qwen3:14b")

    assert resolved is not None
    assert resolved.ref == "model:ollama:qwen3:14b"
    assert resolved.provider_id == "ollama"


def test_codex_vision_quick_choice_survives_capability_refresh(tmp_path, monkeypatch):
    import row_bot.providers.runtime as provider_runtime
    from row_bot.providers.codex import fallback_codex_model_infos

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(
        provider_runtime,
        "provider_status",
        lambda provider_id: {"runtime_enabled": True} if provider_id == "codex" else {},
    )

    model_info = next(info for info in fallback_codex_model_infos() if info.model_id == "gpt-5.5")
    add_quick_choice_for_model(
        "gpt-5.5",
        provider_id="codex",
        display_name="GPT-5.5",
        capabilities_snapshot=model_info.capability_snapshot(),
        surface="vision",
    )

    options = list_model_choice_options("vision")
    stored = provider_config.load_provider_config()["quick_choices"][0]

    assert options[0]["value"] == "model:codex:gpt-5.5"
    assert "ChatGPT / Codex" in options[0]["label"]
    assert "image" in stored["capabilities_snapshot"]["input_modalities"]


def test_provider_display_label_uses_dynamic_provider_metadata():
    assert provider_display_label("codex") == "ChatGPT / Codex"
    assert provider_display_label("custom_openai_local_vllm") == "Custom Local Vllm"


def test_grouped_quick_choices_separates_surface_models_and_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model(
        "gpt-4o",
        provider_id="openai",
        capabilities_snapshot={
            "tasks": ["chat"],
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
        },
    )
    add_quick_choice_for_model(
        "gpt-image-1",
        provider_id="openai",
        capabilities_snapshot={
            "tasks": ["image_generation"],
            "input_modalities": ["text"],
            "output_modalities": ["image"],
        },
    )

    groups = {group["id"]: group["choices"] for group in grouped_quick_choices(include_inactive=True, include_media_defaults=False)}

    assert [choice["model_id"] for choice in groups["chat"]] == ["gpt-4o", "gpt-image-1"]
    assert groups["chat"][1]["active"] is False
    assert [choice["model_id"] for choice in groups["vision"]] == ["gpt-4o"]
    assert [choice["model_id"] for choice in groups["image"]] == ["gpt-image-1"]
    assert "routes" not in groups

    route_groups = {group["id"]: group["choices"] for group in grouped_quick_choices(include_inactive=True, include_routes=True, include_media_defaults=False)}
    assert {choice["route_id"] for choice in route_groups["routes"]} >= {"balanced", "private"}
    assert all(choice["active"] is False for choice in route_groups["routes"])

    visible_groups = {group["id"]: group["choices"] for group in grouped_quick_choices(include_inactive=False, include_media_defaults=False)}
    assert [choice["model_id"] for choice in visible_groups["chat"]] == ["gpt-4o"]
    assert [choice["model_id"] for choice in visible_groups["image"]] == ["gpt-image-1"]


def test_grouped_quick_choices_refreshes_stale_capability_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    add_quick_choice_for_model(
        "qwen3.6:27b",
        provider_id="ollama",
        capabilities_snapshot={
            "tasks": ["chat"],
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
        },
    )
    add_quick_choice_for_model("gpt-5.4", provider_id="openai", capabilities_snapshot={"tasks": ["chat"]})

    groups = {group["id"]: group["choices"] for group in grouped_quick_choices(include_inactive=True, include_media_defaults=False)}

    assert [choice["model_id"] for choice in groups["vision"]] == ["gpt-5.4"]
    stored = {choice["model_id"]: choice for choice in provider_config.load_provider_config()["quick_choices"]}
    assert "image" not in stored["qwen3.6:27b"]["capabilities_snapshot"]["input_modalities"]
    assert "image" in stored["gpt-5.4"]["capabilities_snapshot"]["input_modalities"]


def test_custom_quick_choice_refreshes_from_endpoint_model_cache(tmp_path, monkeypatch):
    from row_bot.providers.custom import custom_provider_id, save_custom_endpoint

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_id = custom_provider_id("llama-cpp")
    save_custom_endpoint({
        "id": "llama-cpp",
        "base_url": "http://127.0.0.1:8081/v1",
        "profile": "llama_cpp",
        "auth_required": False,
        "models": [{
            "id": "qwen3.5-9b",
            "model_id": "qwen3.5-9b",
            "provider": provider_id,
            "vision": True,
            "capabilities_snapshot": {
                "capabilities": ["chat", "streaming", "text", "vision"],
                "input_modalities": ["image", "text"],
                "output_modalities": ["text"],
                "tasks": ["chat"],
                "transport": "openai_chat",
            },
        }],
    })
    add_quick_choice_for_model(
        "qwen3.5-9b",
        provider_id=provider_id,
        capabilities_snapshot={
            "capabilities": ["chat", "streaming", "text"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "tasks": ["chat"],
            "transport": "openai_chat",
        },
    )
    refresh_quick_choice_capability_snapshots()

    options = list_model_choice_options("vision")
    stored = provider_config.load_provider_config()["quick_choices"][0]
    assert options[0]["value"] == f"model:{provider_id}:qwen3.5-9b"
    assert "image" in stored["capabilities_snapshot"]["input_modalities"]
    assert "vision" not in stored.get("inactive_surfaces", {})


def test_quick_choice_refresh_preserves_openrouter_cached_tool_metadata(tmp_path, monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    model_id = "qwen/qwen3.7-max"
    monkeypatch.setitem(models._cloud_model_cache, model_id, {
        "label": "Qwen: Qwen3.7 Max",
        "ctx": 1_000_000,
        "provider": "openrouter",
        "capabilities_snapshot": {
            "tasks": ["chat"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "tool_calling": True,
            "streaming": True,
            "transport": "openai_chat",
            "endpoint_compatibility": ["openai_chat"],
        },
    })
    add_quick_choice_for_model(
        model_id,
        provider_id="openrouter",
        capabilities_snapshot={
            "tasks": ["chat"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "tool_calling": None,
            "transport": "openai_chat",
        },
    )

    refresh_quick_choice_capability_snapshots()

    stored = provider_config.load_provider_config()["quick_choices"][0]
    assert stored["model_id"] == model_id
    assert stored["capabilities_snapshot"]["tool_calling"] is True


def test_include_values_does_not_resurrect_inactive_vision_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    provider_id = "custom_openai_lm-studio"
    ref = f"model:{provider_id}:qwen/qwen3.5-9b"
    add_quick_choice_for_model(
        "qwen/qwen3.5-9b",
        provider_id=provider_id,
        capabilities_snapshot={
            "tasks": ["chat"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "transport": "openai_chat",
        },
        surface="vision",
    )
    validate_quick_choices_for_surface("vision")

    options = list_model_choice_options("vision", include_values=[ref])
    inactive_options = list_model_choice_options("vision", include_values=[ref], include_inactive=True)

    assert ref not in {option["value"] for option in options}
    included = next(option for option in inactive_options if option["value"] == ref)
    assert included["active"] is False
    assert "not compatible with vision" in included["reason"]


def test_include_values_respects_custom_endpoint_manual_vision_off_without_quick_choice(tmp_path, monkeypatch):
    from row_bot.providers.custom import save_custom_endpoint

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    provider_id = "custom_openai_lm-studio"
    ref = f"model:{provider_id}:qwen/qwen3.5-9b"
    save_custom_endpoint({
        "id": "lm-studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "manual_capabilities": {"vision": False},
        "models": [{
            "id": "qwen/qwen3.5-9b",
            "model_id": "qwen/qwen3.5-9b",
            "capabilities_snapshot": {
                "tasks": ["chat"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        }],
    })

    options = list_model_choice_options("vision", include_values=[ref])
    inactive_options = list_model_choice_options("vision", include_values=[ref], include_inactive=True)

    assert ref not in {option["value"] for option in options}
    included = next(option for option in inactive_options if option["value"] == ref)
    assert included["active"] is False
    assert included["reason"] == "manual vision capability disabled"


def test_models_tab_copy_explains_catalog_pinning_before_picker():
    source = pathlib.Path("ui/settings.py").read_text(encoding="utf-8")

    assert "Pin models in the catalog below before looking for them here." in source
    assert "No pinned Vision choices yet. Pin Vision models in the catalog below." in source
