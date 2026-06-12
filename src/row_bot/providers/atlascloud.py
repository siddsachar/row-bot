from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from row_bot.providers.models import ModelInfo, ModelModality, ModelTask, TransportMode

ATLASCLOUD_PROVIDER_ID = "atlascloud"
ATLASCLOUD_BASE_URL = "https://api.atlascloud.ai/v1"

_MEDIA_TASK_MARKERS = {
    "text-to-image",
    "image-to-image",
    "image-edit",
    "reference-to-image",
    "text-to-video",
    "image-to-video",
    "video-to-video",
    "audio-to-video",
    "reference-to-video",
    "image-to-3d",
    "text-to-speech",
}
_MEDIA_NAME_MARKERS = (
    "dall-e",
    "gpt-image",
    "imagen",
    "image-generation",
    "image-preview",
    "text-to-image",
    "image-to-image",
    "image edit",
    "nano banana",
    "seedream",
    "flux",
    "ideogram",
    "hidream",
    "kling",
    "veo",
    "vidu",
    "wan-",
    "wan ",
    "hailuo",
    "luma",
    "pixverse",
    "seedance",
    "video generation",
    "text-to-video",
    "image-to-video",
)
_VOICE_OR_NON_CHAT_MARKERS = (
    "embedding",
    "embed",
    "moderation",
    "transcription",
    "transcribe",
    "tts",
    "whisper",
    "realtime",
    "speech",
    "audio",
)
_VISION_MODEL_MARKERS = (
    "qwen3-vl",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "glm-5v",
    "glm5v",
    "kimi-k2.5",
    "kimi k2.5",
    "kimi-k2.6",
    "kimi k2.6",
)
_VISION_DESCRIPTION_MARKERS = (
    "image understanding",
    "visual reasoning",
    "text and images",
    "text-and-images",
    "image input",
    "native multimodality",
    "natively understands and processes text and images",
    "multimodal model",
)

_FALLBACK_MODELS: tuple[tuple[str, str, int, bool], ...] = (
    ("deepseek-v3", "DeepSeek V3", 128_000, False),
    ("qwen-turbo", "Qwen Turbo", 128_000, False),
    ("Qwen/Qwen3-VL-235B-A22B-Instruct", "Qwen3 VL 235B A22B Instruct", 131_072, True),
)

_SUPPORTED_PARAMETER_KEYS = (
    "supported_parameters",
    "supportedParameters",
    "supported_params",
    "supportedParams",
)
_SUPPORTED_FEATURE_KEYS = (
    "supported_features",
    "supportedFeatures",
)
_SUPPORTED_PARAMETER_CONTAINER_KEYS = (
    "metadata",
    "capabilities",
    "features",
)
_SUPPORTED_FEATURE_CONTAINER_KEYS = (
    "metadata",
    "capabilities",
)
_PARAMETER_COLLECTION_KEYS = (
    "parameters",
    "params",
)
_TOOL_PARAMETER_MARKERS = {"tools", "tool_choice"}
_TOOL_FEATURE_MARKERS = _TOOL_PARAMETER_MARKERS | {"tool_calling", "function_calling"}
_TOOL_BOOLEAN_KEYS = (
    "tool_calling",
    "toolCalling",
    "tool_use",
    "toolUse",
    "tool_support",
    "toolSupport",
    "function_calling",
    "functionCalling",
)
_TOOL_CAPABILITY_KEYS = _TOOL_BOOLEAN_KEYS + ("tools", "tool_choice")

_UPSTREAM_TOOL_PROVIDER_PREFIXES = {
    "anthropic",
    "google",
    "minimaxai",
    "moonshotai",
    "openai",
    "qwen",
    "xai",
    "zai-org",
}
_UPSTREAM_OPENAI_TOOL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
_UPSTREAM_OPENAI_VISION_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4")
_UPSTREAM_ANTHROPIC_VISION_PREFIXES = ("claude-3", "claude-sonnet-4", "claude-opus-4")
_UPSTREAM_VISION_EXACT_NEGATIVES = {
    ("openai", "o3-mini"),
}


@dataclass(frozen=True)
class AtlasCloudUpstreamCapabilities:
    tool_calling: bool | None = None
    input_modalities: frozenset[str] = frozenset()
    source: str = ""


def atlascloud_models_url() -> str:
    return f"{ATLASCLOUD_BASE_URL}/models"


def atlascloud_chat_url() -> str:
    return f"{ATLASCLOUD_BASE_URL}/chat/completions"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atlascloud_metadata_int(metadata: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def atlascloud_string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def _routed_model_parts(model_id: str) -> tuple[str, str, str]:
    raw = str(model_id or "").strip()
    if "/" not in raw:
        normalized = raw.lower().replace("_", "-")
        return "", raw, normalized
    upstream, bare = raw.split("/", 1)
    normalized = bare.strip().lower().replace("_", "-")
    return upstream.strip().lower(), bare.strip(), normalized


def _upstream_model_supports_tools(model_id: str) -> bool | None:
    upstream, _bare, normalized = _routed_model_parts(model_id)
    if not upstream:
        return None
    if upstream not in _UPSTREAM_TOOL_PROVIDER_PREFIXES:
        return None
    if upstream == "openai":
        return normalized.startswith(_UPSTREAM_OPENAI_TOOL_PREFIXES)
    if upstream == "anthropic":
        return normalized.startswith("claude")
    if upstream == "google":
        return normalized.startswith("gemini")
    if upstream == "xai":
        return normalized.startswith("grok")
    if upstream == "qwen":
        return normalized.startswith("qwen")
    if upstream == "moonshotai":
        return normalized.startswith("kimi")
    if upstream == "zai-org":
        return normalized.startswith(("glm", "chatglm"))
    if upstream == "minimaxai":
        return normalized.startswith("minimax")
    return None


def _upstream_model_supports_vision(model_id: str) -> bool | None:
    upstream, _bare, normalized = _routed_model_parts(model_id)
    if not upstream:
        return None
    if (upstream, normalized) in _UPSTREAM_VISION_EXACT_NEGATIVES:
        return False
    if upstream == "openai":
        return normalized.startswith(_UPSTREAM_OPENAI_VISION_PREFIXES)
    if upstream == "anthropic":
        return normalized.startswith(_UPSTREAM_ANTHROPIC_VISION_PREFIXES)
    if upstream == "google":
        return normalized.startswith("gemini")
    if upstream == "xai":
        return normalized.startswith(("grok-4", "grok-vision"))
    if upstream == "qwen":
        return any(marker in normalized for marker in ("qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen3.5-vl"))
    if upstream == "moonshotai":
        return any(marker in normalized for marker in ("kimi-k2.5", "kimi-k2.6"))
    if upstream == "zai-org":
        return any(marker in normalized for marker in ("glm-4v", "glm4v", "glm-4.5v", "glm4.5v", "glm-5v", "glm5v"))
    return None


def atlascloud_upstream_capabilities(model_id: str) -> AtlasCloudUpstreamCapabilities:
    tool_calling = _upstream_model_supports_tools(model_id)
    vision = _upstream_model_supports_vision(model_id)
    inputs = {"text"}
    if vision is True:
        inputs.add("image")
    source = "atlascloud_upstream_capability_rules" if tool_calling is not None or vision is not None else ""
    return AtlasCloudUpstreamCapabilities(
        tool_calling=tool_calling,
        input_modalities=frozenset(inputs),
        source=source,
    )


def _normalized_parameter_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _parameter_names(value: Any, *, allow_string: bool = True) -> list[str] | None:
    if isinstance(value, str):
        return [_normalized_parameter_name(value)] if allow_string and value.strip() else None
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalized_parameter_name(item) for item in value if str(item or "").strip()]
    if isinstance(value, Mapping):
        names: list[str] = []
        for key, item in value.items():
            if item is False:
                continue
            if isinstance(item, Mapping) and item.get("supported") is False:
                continue
            names.append(_normalized_parameter_name(key))
        return names
    return None


def _bool_from_mapping(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, Mapping) and isinstance(value.get("supported"), bool):
            return bool(value.get("supported"))
    return None


def _supported_parameter_metadata(metadata: Mapping[str, Any]) -> tuple[set[str], bool]:
    for key in _SUPPORTED_PARAMETER_KEYS:
        if key not in metadata:
            continue
        values = _parameter_names(metadata.get(key), allow_string=True)
        if values is not None:
            return {item for item in values if item}, True

    for container_key in _SUPPORTED_PARAMETER_CONTAINER_KEYS:
        container = metadata.get(container_key)
        if not isinstance(container, Mapping):
            continue
        for key in _SUPPORTED_PARAMETER_KEYS:
            if key not in container:
                continue
            values = _parameter_names(container.get(key), allow_string=True)
            if values is not None:
                return {item for item in values if item}, True

    for key in _PARAMETER_COLLECTION_KEYS:
        if key not in metadata:
            continue
        values = _parameter_names(metadata.get(key), allow_string=False)
        if values is not None:
            return {item for item in values if item}, True

    return set(), False


def _supported_feature_metadata(metadata: Mapping[str, Any]) -> tuple[set[str], bool]:
    for key in _SUPPORTED_FEATURE_KEYS:
        if key not in metadata:
            continue
        values = _parameter_names(metadata.get(key), allow_string=True)
        if values is not None:
            return {item for item in values if item}, True

    for container_key in _SUPPORTED_FEATURE_CONTAINER_KEYS:
        container = metadata.get(container_key)
        if not isinstance(container, Mapping):
            continue
        for key in _SUPPORTED_FEATURE_KEYS:
            if key not in container:
                continue
            values = _parameter_names(container.get(key), allow_string=True)
            if values is not None:
                return {item for item in values if item}, True

    return set(), False


def atlascloud_modalities(metadata: Mapping[str, Any], direction: str) -> set[str] | None:
    keys = ("input_modalities", "input", "modalities") if direction == "input" else ("output_modalities", "output")
    for key in keys:
        value = metadata.get(key)
        if key == "modalities" and isinstance(value, Mapping):
            value = value.get(direction)
        values = atlascloud_string_list(value)
        if values is not None:
            return {item.lower() for item in values}
    architecture = metadata.get("architecture")
    if isinstance(architecture, Mapping):
        for key in ("input_modalities", "output_modalities", "input", "output", "modalities"):
            value = architecture.get(key)
            if key == "modalities" and isinstance(value, Mapping):
                value = value.get(direction)
            values = atlascloud_string_list(value)
            if values is not None:
                return {item.lower() for item in values}
    return None


def atlascloud_supported_parameters(metadata: Mapping[str, Any]) -> set[str]:
    values, _declared = _supported_parameter_metadata(metadata)
    return values


def atlascloud_supported_features(metadata: Mapping[str, Any]) -> set[str]:
    values, _declared = _supported_feature_metadata(metadata)
    return values


def atlascloud_tool_calling_from_metadata(metadata: Mapping[str, Any]) -> bool | None:
    supported_features, declared_features = _supported_feature_metadata(metadata)
    if declared_features:
        return bool(supported_features.intersection(_TOOL_FEATURE_MARKERS))

    direct = _bool_from_mapping(metadata, _TOOL_BOOLEAN_KEYS)
    if direct is not None:
        return direct

    supported_params, declared = _supported_parameter_metadata(metadata)
    if declared:
        return bool(supported_params.intersection(_TOOL_PARAMETER_MARKERS))

    for container_key in ("capabilities", "features"):
        container = metadata.get(container_key)
        if isinstance(container, Mapping):
            nested = _bool_from_mapping(container, _TOOL_CAPABILITY_KEYS)
            if nested is not None:
                return nested
        values = atlascloud_string_list(container)
        if values and {_normalized_parameter_name(item) for item in values}.intersection(_TOOL_PARAMETER_MARKERS):
            return True

    return None


def atlascloud_tool_calling(model_id: str, metadata: Mapping[str, Any] | None = None) -> bool | None:
    metadata_result = atlascloud_tool_calling_from_metadata(metadata or {})
    if metadata_result is not None:
        return metadata_result
    return atlascloud_upstream_capabilities(model_id).tool_calling


def atlascloud_is_media_or_non_chat_model(model_id: str, metadata: Mapping[str, Any] | None = None) -> bool:
    metadata = metadata or {}
    fields: list[str] = [model_id]
    for key in ("name", "display_name", "displayName", "description", "category", "type", "function", "model_function"):
        value = metadata.get(key)
        if isinstance(value, str):
            fields.append(value)
    for key in ("tasks", "task", "capabilities"):
        value = metadata.get(key)
        values = atlascloud_string_list(value)
        if values:
            fields.extend(values)
    text = " ".join(fields).lower().replace("_", "-")
    if any(marker in text for marker in _MEDIA_TASK_MARKERS):
        return True
    if any(marker in text for marker in _MEDIA_NAME_MARKERS):
        return True
    if any(marker in text for marker in _VOICE_OR_NON_CHAT_MARKERS):
        return True
    output_modalities = atlascloud_modalities(metadata, "output")
    if output_modalities and not output_modalities.intersection({"text"}):
        return True
    task_values = atlascloud_string_list(metadata.get("tasks") or metadata.get("task"))
    if task_values:
        normalized = {task.lower().replace("_", "-") for task in task_values}
        if normalized and not normalized.intersection({"chat", "llm", "text-generation", "responses"}):
            return True
    return False


def atlascloud_input_modalities(model_id: str, metadata: Mapping[str, Any] | None = None) -> set[str]:
    metadata = metadata or {}
    inputs = atlascloud_modalities(metadata, "input") or {"text"}
    if not inputs:
        inputs = {"text"}
    capabilities = metadata.get("capabilities")
    capability_values = {item.lower() for item in atlascloud_string_list(capabilities) or []}
    if isinstance(capabilities, Mapping):
        for key in ("vision", "image", "image_input"):
            value = capabilities.get(key)
            if value is True or (isinstance(value, Mapping) and value.get("supported")):
                capability_values.add("vision")
    if metadata.get("vision") is True:
        capability_values.add("vision")
    descriptive_text = " ".join(
        str(metadata.get(key) or "")
        for key in ("name", "display_name", "displayName", "description")
    ).lower()
    normalized_model = model_id.lower().replace("_", "-")
    if (
        "image" in inputs
        or capability_values.intersection({"vision", "image", "image_input"})
        or "image" in atlascloud_upstream_capabilities(model_id).input_modalities
        or any(marker in normalized_model for marker in _VISION_MODEL_MARKERS)
        or any(marker in descriptive_text for marker in _VISION_DESCRIPTION_MARKERS)
    ):
        inputs.add("image")
    inputs.add("text")
    return inputs


def atlascloud_output_modalities(metadata: Mapping[str, Any] | None = None) -> set[str]:
    outputs = atlascloud_modalities(metadata or {}, "output")
    return outputs or {"text"}


def atlascloud_context_window(model_id: str, metadata: Mapping[str, Any] | None = None, fallback: int = 0) -> int:
    metadata = metadata or {}
    return atlascloud_metadata_int(
        metadata,
        "context_length",
        "context_window",
        "contextWindow",
        "max_context",
        "maxContext",
        "max_input_tokens",
        "inputTokenLimit",
    ) or int(fallback or 0)


def atlascloud_model_info_from_metadata(
    model_id: str,
    metadata: Mapping[str, Any] | None = None,
    *,
    display_name: str | None = None,
    context_window: int = 0,
    source: str = "atlascloud_live_catalog",
    source_confidence: str = "live_atlascloud_model_list",
    last_verified_at: str = "",
) -> ModelInfo | None:
    metadata = dict(metadata or {})
    model_id = str(model_id or "").strip()
    if not model_id or atlascloud_is_media_or_non_chat_model(model_id, metadata):
        return None
    inputs = atlascloud_input_modalities(model_id, metadata)
    outputs = atlascloud_output_modalities(metadata)
    if "text" not in outputs:
        return None
    tool_calling = atlascloud_tool_calling(model_id, metadata)
    streaming = metadata.get("streaming") if isinstance(metadata.get("streaming"), bool) else True
    capabilities = {"text", "chat"}
    if "image" in inputs:
        capabilities.add("vision")
    if tool_calling is True:
        capabilities.add("tool_calling")
    if streaming is True:
        capabilities.add("streaming")
    return ModelInfo(
        provider_id=ATLASCLOUD_PROVIDER_ID,
        model_id=model_id,
        display_name=display_name or str(metadata.get("name") or metadata.get("display_name") or metadata.get("displayName") or model_id),
        context_window=atlascloud_context_window(model_id, metadata, fallback=context_window),
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
        risk_label="cloud_provider",
        source=source,
    )


def list_atlascloud_fallback_model_infos() -> list[ModelInfo]:
    infos: list[ModelInfo] = []
    for model_id, display_name, context_window, vision in _FALLBACK_MODELS:
        metadata: dict[str, Any] = {
            "context_window": context_window,
            "input_modalities": ["text", "image"] if vision else ["text"],
        }
        info = atlascloud_model_info_from_metadata(
            model_id,
            metadata,
            display_name=display_name,
            source="atlascloud_static_fallback",
            source_confidence="documented_atlascloud_fallback",
        )
        if info:
            infos.append(info)
    return infos


def probe_atlascloud_tool_round_trip(
    model_id: str,
    api_key: str,
    *,
    timeout: float = 30.0,
    http_client: Any | None = None,
) -> dict[str, Any]:
    """Probe whether an Atlas model performs an OpenAI-style tool round trip.

    This is optional high-confidence proof. OpenRouter-style catalog metadata
    is still enough for Agent readiness when Atlas exposes it for a model.
    """
    import httpx

    result: dict[str, Any] = {
        "ok": False,
        "chat_ok": False,
        "tool_calling": None,
        "tool_round_trip": None,
        "streaming_tool_calling": None,
        "model_id": str(model_id or ""),
        "checked_at": utc_now_iso(),
        "errors": [],
    }
    if not api_key:
        result["errors"].append("Atlas Cloud API key is missing")
        return result

    tool = {
        "type": "function",
        "function": {
            "name": "row_bot_probe_echo",
            "description": "Return the provided probe token.",
            "parameters": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
            },
        },
    }
    first_body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Call row_bot_probe_echo with token atlas-probe."}],
        "tools": [tool],
        "tool_choice": "auto",
        "stream": False,
        "max_tokens": 128,
    }
    client = http_client or httpx.Client(timeout=timeout)
    owns_client = http_client is None
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        first = client.post(atlascloud_chat_url(), headers=headers, json=first_body, timeout=timeout)
        first.raise_for_status()
        payload = first.json()
        result["chat_ok"] = True
        choice = (payload.get("choices") or [{}])[0] if isinstance(payload, Mapping) else {}
        message = choice.get("message") if isinstance(choice, Mapping) and isinstance(choice.get("message"), Mapping) else {}
        tool_calls = message.get("tool_calls") if isinstance(message, Mapping) else None
        if not isinstance(tool_calls, list) or not tool_calls:
            result["tool_calling"] = False
            result["tool_round_trip"] = False
            result["errors"].append("model did not return a structured tool call")
            return result
        result["tool_calling"] = True
        call = tool_calls[0]
        call_id = str(call.get("id") or "row_bot_probe_call") if isinstance(call, Mapping) else "row_bot_probe_call"
        followup_body = {
            "model": model_id,
            "messages": [
                first_body["messages"][0],
                message,
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"token": "atlas-probe"}),
                },
            ],
            "stream": False,
            "max_tokens": 128,
        }
        followup = client.post(atlascloud_chat_url(), headers=headers, json=followup_body, timeout=timeout)
        followup.raise_for_status()
        followup_payload = followup.json()
        followup_choice = (followup_payload.get("choices") or [{}])[0] if isinstance(followup_payload, Mapping) else {}
        followup_message = followup_choice.get("message") if isinstance(followup_choice, Mapping) and isinstance(followup_choice.get("message"), Mapping) else {}
        content = followup_message.get("content") if isinstance(followup_message, Mapping) else ""
        result["tool_round_trip"] = isinstance(content, str) and bool(content.strip())
        result["ok"] = bool(result["tool_round_trip"])
        if not result["ok"]:
            result["errors"].append("tool result follow-up did not return final text")
    except Exception as exc:
        result["errors"].append(str(exc))
    finally:
        if owns_client:
            client.close()
    return result


def _redact_probe_error(text: str, *, limit: int = 220) -> str:
    cleaned = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:limit]


def save_atlascloud_runtime_probe(probe: dict[str, Any]) -> dict[str, Any]:
    from row_bot.providers.config import update_provider_config

    safe_probe = dict(probe or {})
    model_id = str(safe_probe.get("model_id") or "").strip()
    safe_probe["provider_id"] = ATLASCLOUD_PROVIDER_ID
    safe_probe["runtime"] = "openai_compatible_chat"
    safe_probe["probed_at"] = str(safe_probe.get("probed_at") or safe_probe.get("checked_at") or utc_now_iso())
    errors = [
        _redact_probe_error(str(error), limit=220)
        for error in safe_probe.get("errors", [])
        if str(error or "").strip()
    ]
    safe_probe["errors"] = errors[:5]

    def _update(cfg: dict[str, Any]) -> None:
        entry = cfg.setdefault("providers", {}).setdefault(ATLASCLOUD_PROVIDER_ID, {})
        entry["last_runtime_probe"] = dict(safe_probe)
        if model_id:
            probes = entry.setdefault("runtime_probes", {})
            if isinstance(probes, dict):
                probes[model_id] = dict(safe_probe)
        entry["last_error"] = "" if safe_probe.get("ok") else "; ".join(errors[:2])

    cfg = update_provider_config(_update)
    return dict(cfg.get("providers", {}).get(ATLASCLOUD_PROVIDER_ID, {}).get("last_runtime_probe", {}))


def run_atlascloud_runtime_probe(
    model_id: str,
    *,
    api_key: str | None = None,
    timeout: float = 30.0,
    http_client: Any | None = None,
) -> dict[str, Any]:
    if api_key is None:
        from row_bot.providers.auth_store import get_provider_secret

        api_key = get_provider_secret(ATLASCLOUD_PROVIDER_ID)
    probe = probe_atlascloud_tool_round_trip(
        model_id,
        str(api_key or ""),
        timeout=timeout,
        http_client=http_client,
    )
    return save_atlascloud_runtime_probe(probe)
