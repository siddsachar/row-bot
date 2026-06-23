from __future__ import annotations

import pytest

from row_bot.providers import catalog
from row_bot.providers.models import TransportMode


pytestmark = pytest.mark.subsystem


def test_provider_definitions_have_stable_first_class_shape() -> None:
    definitions = {definition.id: definition for definition in catalog.list_provider_definitions()}

    for provider_id in ("ollama", "openai", "anthropic", "google", "xai", "openrouter", "minimax"):
        assert provider_id in definitions
        definition = definitions[provider_id]
        assert definition.display_name
        assert definition.default_transport in set(TransportMode)
        assert definition.auth_methods
        assert definition.risk_label

    assert catalog.get_provider_definition("missing-provider") is None


@pytest.mark.parametrize(
    ("model_id", "cached", "expected"),
    [
        ("model:openai:gpt-4o", None, "openai"),
        ("anthropic/claude-sonnet-4", None, "openrouter"),
        ("gpt-4o-audio-preview", None, "openai"),
        ("llama3:8b", "ollama", "ollama"),
        ("gemini-2.5-pro", None, "google"),
        ("unknown-local", None, None),
    ],
)
def test_provider_inference_and_cache_key_splitting(model_id: str, cached: str | None, expected: str | None) -> None:
    provider_hint, runtime_model = catalog.split_model_cache_key(model_id)

    assert catalog.infer_provider_id(runtime_model, cached or provider_hint) == expected


def test_split_model_cache_key_handles_bare_empty_and_malformed_values() -> None:
    assert catalog.split_model_cache_key("model:openai:gpt-4o") == ("openai", "gpt-4o")
    assert catalog.split_model_cache_key("gpt-4o") == (None, "gpt-4o")
    assert catalog.split_model_cache_key("") == (None, "")
    assert catalog.split_model_cache_key("model::missing-provider") == (None, "model::missing-provider")


def test_internal_set_and_transport_helpers_are_tolerant() -> None:
    assert catalog._str_set("one") == {"one"}
    assert catalog._str_set(["one", "", 2]) == {"one", "2"}
    assert catalog._str_set(None) == set()
    assert catalog._transport_from_value("openai_responses", TransportMode.OPENAI_CHAT) == TransportMode.OPENAI_RESPONSES
    assert catalog._transport_from_value("bad", TransportMode.OPENAI_CHAT) == TransportMode.OPENAI_CHAT
    assert catalog._transport_set_from_values(
        ["openai_chat", "bad", TransportMode.ANTHROPIC_MESSAGES.value],
        frozenset({TransportMode.OLLAMA_CHAT}),
    ) == frozenset({TransportMode.OPENAI_CHAT, TransportMode.ANTHROPIC_MESSAGES})
    assert catalog._transport_set_from_values([], frozenset({TransportMode.OLLAMA_CHAT})) == frozenset(
        {TransportMode.OLLAMA_CHAT}
    )


def test_legacy_model_info_conversion_and_cache_roundtrip_preserve_fields() -> None:
    info = catalog.model_info_from_legacy(
        "model:openai:gpt-4o",
        {
            "label": "GPT 4o",
            "ctx": 128000,
            "vision": True,
            "capabilities_snapshot": {
                "tasks": ["chat"],
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
                "tool_calling": True,
                "streaming": False,
                "transport": "openai_responses",
                "endpoint_compatibility": ["openai_responses"],
                "billing_label": "test billing",
                "source_confidence": "verified",
            },
        },
    )

    assert info is not None
    assert info.provider_id == "openai"
    assert info.model_id == "gpt-4o"
    assert info.context_window == 128000
    assert info.transport == TransportMode.OPENAI_RESPONSES
    assert info.tool_calling is True
    assert info.streaming is False
    assert "vision" in info.capabilities

    entry = catalog.model_info_to_cache_entry(info)
    assert entry["provider"] == "openai"
    assert entry["ctx"] == 128000
    assert entry["vision"] is True
    assert entry["capabilities_snapshot"]["billing_label"] == "test billing"
    assert catalog.legacy_cache_to_model_infos({"unknown-local": {"label": "Unknown"}}) == []


def test_model_info_from_metadata_handles_modalities_risk_and_unknown_provider() -> None:
    info = catalog.model_info_from_metadata(
        "custom_openai_test",
        "llava-custom",
        {"input_modalities": ["text", "image"], "tool_calling": False, "streaming": False},
        display_name="Custom LLaVA",
        context_window=4096,
        transport=TransportMode.OPENAI_CHAT,
        risk_label="local",
        source="test",
    )

    assert info.display_name == "Custom LLaVA"
    assert info.context_window == 4096
    assert info.risk_label == "local"
    assert "image" in info.input_modalities
    assert info.tool_calling is False
    assert info.streaming is False


def test_capability_helpers_detect_vision_voice_and_metadata_image_inputs() -> None:
    assert catalog._name_suggests_vision_model("vendor/qwen2.5-vl:7b") is True
    assert catalog._name_suggests_vision_model("qwen3:14b") is False
    assert catalog._name_suggests_voice_model("gpt-4o-transcribe") is True
    assert catalog._name_suggests_voice_model("gpt-4o") is False
    assert catalog._metadata_suggests_image_input({"capabilities": ["completion", "vision"]}) is True
    assert catalog._metadata_suggests_image_input({"architecture": {"input_modalities": ["text", "image"]}}) is True
    assert catalog._metadata_suggests_image_input({"modalities": ["text"]}) is False


@pytest.mark.parametrize(
    ("provider_id", "model_id", "metadata", "expected_tasks", "expected_inputs", "expected_outputs"),
    [
        ("openai", "text-embedding-3-large", {}, {"embedding"}, {"text"}, {"text"}),
        ("openai", "gpt-image-1", {}, {"image_generation"}, {"text"}, {"image"}),
        ("google", "gemini-3.1-flash-image-preview", {}, {"image_generation", "image_edit"}, {"text", "image"}, {"image"}),
        ("google", "veo-3.1-generate-preview", {}, {"video_generation"}, {"text"}, {"video"}),
        ("openai", "whisper-1", {}, {"transcription"}, {"audio"}, {"text"}),
        ("openai", "tts-1", {}, {"tts"}, {"text"}, {"audio"}),
        ("openai", "omni-moderation-latest", {}, {"moderation"}, {"text"}, {"text"}),
        ("openai", "gpt-realtime", {}, {"realtime"}, {"text", "audio"}, {"text", "audio"}),
    ],
)
def test_capability_classification_for_non_chat_surfaces(
    provider_id: str,
    model_id: str,
    metadata: dict,
    expected_tasks: set[str],
    expected_inputs: set[str],
    expected_outputs: set[str],
) -> None:
    classified = catalog.classify_model_capabilities(provider_id, model_id, metadata)

    assert classified["tasks"] == expected_tasks
    assert classified["input_modalities"] == expected_inputs
    assert classified["output_modalities"] == expected_outputs
    assert classified["tool_calling"] is False


def test_capability_classification_uses_openrouter_and_ollama_metadata() -> None:
    routed = catalog.classify_model_capabilities(
        "openrouter",
        "vendor/chat-tools",
        {"supported_parameters": ["tools"], "architecture": {"modality": "text+image->text"}},
    )
    local = catalog.classify_model_capabilities(
        "ollama",
        "unknown-family:latest",
        {"capabilities": ["completion", "vision"], "tool_calling": False},
    )

    assert routed["tool_calling"] is True
    assert "image" in routed["input_modalities"]
    assert "vision" in routed["capabilities"]
    assert local["transport"] == TransportMode.OLLAMA_CHAT
    assert "image" in local["input_modalities"]
    assert local["tool_calling"] is False
