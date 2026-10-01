from __future__ import annotations

import pytest


pytestmark = pytest.mark.subsystem


def test_provider_ref_resolution_is_explicit_and_runtime_ready() -> None:
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config("model:openai:gpt-4o-mini", allow_legacy_local=False)

    assert resolved.selection_ref == "model:openai:gpt-4o-mini"
    assert resolved.provider_id == "openai"
    assert resolved.runtime_model == "gpt-4o-mini"
    assert resolved.source == "provider_ref"
    assert resolved.base_url.startswith("https://")


def test_unknown_bare_model_requires_provider_without_legacy_fallback() -> None:
    from row_bot.providers.resolution import resolve_provider_config

    with pytest.raises(ValueError, match="Provider is required"):
        resolve_provider_config("private-model-without-prefix", allow_legacy_local=False)


def test_legacy_local_model_still_resolves_to_ollama() -> None:
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config("qwen3:1.7b")

    assert resolved.provider_id == "ollama"
    assert resolved.runtime_model == "qwen3:1.7b"
    assert resolved.source == "legacy_local"


def test_media_models_are_rejected_for_chat_surface(monkeypatch) -> None:
    from row_bot.providers import runtime
    from row_bot.providers.models import ModelTask, TransportMode

    monkeypatch.setattr(
        runtime,
        "_capability_snapshot_for_selection",
        lambda *_args, **_kwargs: {
            "tasks": [ModelTask.IMAGE_GENERATION.value],
            "input_modalities": ["text"],
            "output_modalities": ["image"],
            "transport": TransportMode.OPENAI_CHAT.value,
        },
    )

    with pytest.raises(ValueError, match="not compatible with chat"):
        runtime.ensure_chat_model_compatible("gpt-image-test", "openai")


def test_capability_classifier_preserves_multimodal_chat_snapshot() -> None:
    from row_bot.providers.catalog import classify_model_capabilities

    snapshot = classify_model_capabilities(
        "openai",
        "gpt-4o",
        {"input_modalities": ["text", "image"], "tool_calling": True, "streaming": True},
    )

    assert snapshot["tool_calling"] is True
    assert snapshot["streaming"] is True
    assert "chat" in snapshot["tasks"]
    assert "image" in snapshot["input_modalities"]
    assert "vision" in snapshot["capabilities"]


def test_an_ollama_ref_keeps_its_local_identity() -> None:
    from row_bot.providers.models import TransportMode
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config("model:ollama:qwen3:14b")

    assert resolved.selection_ref == "model:ollama:qwen3:14b"
    assert resolved.provider_id == "ollama"
    assert resolved.runtime_model == "qwen3:14b"
    assert resolved.transport == TransportMode.OLLAMA_CHAT
    assert resolved.execution_location == "local"


def test_a_legacy_local_ref_becomes_an_ollama_ref() -> None:
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config("model:local:qwen3:14b")

    assert resolved.selection_ref == "model:ollama:qwen3:14b"
    assert resolved.provider_id == "ollama"
    assert resolved.runtime_model == "qwen3:14b"


def test_a_custom_endpoint_ref_uses_the_saved_endpoint(tmp_path, monkeypatch) -> None:
    import row_bot.providers.config as provider_config
    from row_bot.providers.custom import custom_provider_id, save_custom_endpoint
    from row_bot.providers.models import TransportMode
    from row_bot.providers.resolution import resolve_provider_config

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({
        "id": "local-vllm",
        "name": "Local vLLM",
        "base_url": "http://127.0.0.1:8000/v1/",
        "auth_required": False,
        "execution_location": "local",
    })
    provider_id = custom_provider_id("local-vllm")

    resolved = resolve_provider_config(f"model:{provider_id}:meta-llama/Llama-3.1-8B-Instruct")

    assert resolved.provider_id == provider_id
    assert resolved.runtime_model == "meta-llama/Llama-3.1-8B-Instruct"
    assert resolved.base_url == "http://127.0.0.1:8000/v1"
    assert resolved.execution_location == "local"
    assert resolved.transport == TransportMode.OPENAI_CHAT
