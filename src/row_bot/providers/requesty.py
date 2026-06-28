"""Requesty OpenAI-compatible provider helpers.

Requesty (https://requesty.ai) is an OpenAI-compatible LLM gateway that uses
the same ``provider/model`` naming as OpenRouter (e.g. ``openai/gpt-4o-mini``).
Its ``/v1/models`` endpoint returns an OpenAI-shaped catalog, but the capability
fields differ from OpenRouter: it exposes ``context_window`` (not
``context_length``) and ``supports_tool_calling`` / ``supports_reasoning`` /
``supports_vision`` booleans (not a ``supported_parameters`` array).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from row_bot.providers.models import ModelInfo, ModelModality, ModelTask, TransportMode

REQUESTY_PROVIDER_ID = "requesty"
REQUESTY_BASE_URL = "https://router.requesty.ai/v1"

_NON_CHAT_TASK_MARKERS = {
    "embedding",
    "embeddings",
    "image_generation",
    "image-generation",
    "image_generation",
    "image-edit",
    "video_generation",
    "video-generation",
    "text-to-speech",
    "speech",
    "transcription",
    "moderation",
    "realtime",
}
_NON_CHAT_NAME_MARKERS = (
    "embedding",
    "embed",
    "whisper",
    "tts",
    "speech",
    "audio",
    "realtime",
    "moderation",
    "gpt-image",
    "dall-e",
    "imagen",
    "image-generation",
    "text-to-image",
    "video",
)


def requesty_models_url() -> str:
    return f"{REQUESTY_BASE_URL}/models"


def requesty_chat_url() -> str:
    return f"{REQUESTY_BASE_URL}/chat/completions"


def requesty_context_window(metadata: Mapping[str, Any], fallback: int = 0) -> int:
    """Resolve a context window from Requesty's ``context_window`` field.

    Falls back to OpenRouter-style ``context_length`` for robustness, then to
    the supplied ``fallback``.
    """
    for key in ("context_window", "contextWindow", "context_length", "max_tokens"):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return int(fallback or 0)


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def _bool_from_mapping(mapping: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, Mapping) and isinstance(value.get("supported"), bool):
            return bool(value.get("supported"))
    return None


def _parameter_names(value: Any) -> set[str] | None:
    if isinstance(value, str):
        return {value.strip().lower().replace("-", "_")} if value.strip() else set()
    if isinstance(value, Mapping):
        names: set[str] = set()
        for key, item in value.items():
            if item is False:
                continue
            if isinstance(item, Mapping) and item.get("supported") is False:
                continue
            names.add(str(key).strip().lower().replace("-", "_"))
        return names
    values = _string_list(value)
    if values is None:
        return None
    return {item.lower().replace("-", "_") for item in values}


def requesty_tool_calling(metadata: Mapping[str, Any]) -> bool | None:
    """Map Requesty's ``supports_tool_calling`` boolean to tool support."""
    direct = _bool_from_mapping(
        metadata,
        "supports_tool_calling",
        "supportsToolCalling",
        "tool_calling",
        "toolCalling",
        "function_calling",
        "functionCalling",
    )
    if direct is not None:
        return direct
    for container_key in ("metadata", "capabilities", "features"):
        container = metadata.get(container_key)
        if isinstance(container, Mapping):
            nested = _bool_from_mapping(
                container,
                "supports_tool_calling",
                "supportsToolCalling",
                "tool_calling",
                "tools",
                "tool_choice",
                "function_calling",
            )
            if nested is not None:
                return nested
    for key in ("supported_parameters", "supportedParameters", "supported_params", "parameters"):
        names = _parameter_names(metadata.get(key))
        if names is not None:
            return bool(names.intersection({"tools", "tool_choice"}))
    for container_key in ("metadata", "capabilities", "features"):
        container = metadata.get(container_key)
        if not isinstance(container, Mapping):
            continue
        for key in ("supported_parameters", "supportedParameters", "supported_params", "parameters"):
            names = _parameter_names(container.get(key))
            if names is not None:
                return bool(names.intersection({"tools", "tool_choice"}))
    return None


def requesty_supports_vision(metadata: Mapping[str, Any]) -> bool | None:
    """Map Requesty's ``supports_vision`` boolean to image-input support."""
    direct = _bool_from_mapping(metadata, "supports_vision", "supportsVision", "vision", "image_input", "imageInput")
    if direct is not None:
        return direct
    for container_key in ("metadata", "capabilities", "features"):
        container = metadata.get(container_key)
        if isinstance(container, Mapping):
            nested = _bool_from_mapping(container, "supports_vision", "supportsVision", "vision", "image_input", "imageInput")
            if nested is not None:
                return nested
    return None


def requesty_modalities(metadata: Mapping[str, Any], direction: str) -> set[str] | None:
    keys = ("input_modalities", "input", "modalities") if direction == "input" else ("output_modalities", "output")
    for key in keys:
        value = metadata.get(key)
        if key == "modalities" and isinstance(value, Mapping):
            value = value.get(direction)
        values = _string_list(value)
        if values is not None:
            return {item.lower() for item in values}
    architecture = metadata.get("architecture")
    if isinstance(architecture, Mapping):
        for key in ("input_modalities", "output_modalities", "input", "output", "modalities"):
            value = architecture.get(key)
            if key == "modalities" and isinstance(value, Mapping):
                value = value.get(direction)
            values = _string_list(value)
            if values is not None:
                return {item.lower() for item in values}
    return None


def requesty_is_media_or_non_chat_model(model_id: str, metadata: Mapping[str, Any] | None = None) -> bool:
    metadata = metadata or {}
    task_values = _string_list(metadata.get("tasks") or metadata.get("task"))
    if task_values:
        normalized_tasks = {task.lower().replace("_", "-") for task in task_values}
        if normalized_tasks.intersection(_NON_CHAT_TASK_MARKERS):
            return True
        if not normalized_tasks.intersection({"chat", "llm", "text-generation", "responses"}):
            return True

    output_modalities = requesty_modalities(metadata, "output")
    if output_modalities and "text" not in output_modalities:
        return True

    fields = [model_id]
    for key in ("id", "name", "display_name", "displayName", "description", "type", "category"):
        value = metadata.get(key)
        if isinstance(value, str):
            fields.append(value)
    text = " ".join(fields).lower().replace("_", "-")
    return any(marker in text for marker in _NON_CHAT_NAME_MARKERS)


def requesty_input_modalities(metadata: Mapping[str, Any] | None = None) -> set[str]:
    metadata = metadata or {}
    inputs = requesty_modalities(metadata, "input") or {"text"}
    vision = requesty_supports_vision(metadata)
    if vision is False:
        inputs.discard(ModelModality.IMAGE.value)
    elif vision is True:
        inputs.add(ModelModality.IMAGE.value)
    inputs.add(ModelModality.TEXT.value)
    return inputs


def requesty_output_modalities(metadata: Mapping[str, Any] | None = None) -> set[str]:
    outputs = requesty_modalities(metadata or {}, "output")
    return outputs or {ModelModality.TEXT.value}


def requesty_model_info_from_metadata(
    model_id: str,
    metadata: Mapping[str, Any] | None = None,
    *,
    display_name: str | None = None,
    context_window: int = 0,
    source: str = "requesty_live_catalog",
    source_confidence: str = "live_requesty_model_list",
    last_verified_at: str = "",
) -> ModelInfo | None:
    metadata = dict(metadata or {})
    model_id = str(model_id or "").strip()
    if not model_id or requesty_is_media_or_non_chat_model(model_id, metadata):
        return None
    outputs = requesty_output_modalities(metadata)
    if ModelModality.TEXT.value not in outputs:
        return None
    inputs = requesty_input_modalities(metadata)
    tool_calling = requesty_tool_calling(metadata)
    streaming = metadata.get("streaming") if isinstance(metadata.get("streaming"), bool) else True
    capabilities = {"text", "chat"}
    if ModelModality.IMAGE.value in inputs:
        capabilities.add("vision")
    if tool_calling is True:
        capabilities.add("tool_calling")
    if streaming is True:
        capabilities.add("streaming")
    return ModelInfo(
        provider_id=REQUESTY_PROVIDER_ID,
        model_id=model_id,
        display_name=display_name or str(metadata.get("name") or metadata.get("display_name") or metadata.get("displayName") or model_id),
        context_window=requesty_context_window(metadata, fallback=context_window),
        transport=TransportMode.OPENAI_CHAT,
        capabilities=frozenset(capabilities),
        input_modalities=frozenset(inputs),
        output_modalities=frozenset(outputs),
        tasks=frozenset({ModelTask.CHAT.value}),
        tool_calling=tool_calling,
        streaming=streaming,
        endpoint_compatibility=frozenset({TransportMode.OPENAI_CHAT}),
        source_confidence=source_confidence,
        last_verified_at=last_verified_at,
        risk_label="third_party_router",
        source=source,
    )
