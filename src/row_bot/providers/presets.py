"""OpenAI-compatible API providers that differ only in their facts.

Each is a row: where it lives, how it's paid for, the key's environment name and
what its model list says. The catalog, key storage, model listing, the key check
and the runtime read this table, so a provider here is a row, not a module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Any, Literal

from row_bot.providers.models import AuthMethod, ModelInfo, ProviderDefinition, TransportMode

logger = logging.getLogger(__name__)

# What a model list calls its non-chat models, and the names they usually carry.
_NON_CHAT_TYPES = frozenset({
    "audio", "embedding", "embeddings", "image", "moderation", "rerank", "speech",
    "transcription", "tts", "video",
})
_NON_CHAT_MARKERS = (
    "embed", "rerank", "whisper", "tts", "guard", "moderation", "transcribe", "parakeet",
    "orpheus", "clip",
)


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    base_url: str
    key_env: str
    billing: Literal["pay_per_use", "credits"]
    icon: str
    # Assumed when a model's row gives no context length; most rows give one.
    context_window: int = 32_768
    # Model types that take chat requests, when the list types its models more
    # finely than chat or not (Together's "language" models are base models).
    chat_types: frozenset[str] | None = None
    # A router sends prompts on to other companies' models.
    risk_label: str = "cloud_provider"


PRESETS: dict[str, ProviderPreset] = {
    preset.id: preset
    for preset in (
        # DeepSeek's and Z.AI's lists name their models only; both offer 128K.
        ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
                       "pay_per_use", "DS", context_window=128_000),
        ProviderPreset("mistral", "Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "pay_per_use", "MI"),
        ProviderPreset("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "pay_per_use", "GQ"),
        ProviderPreset("moonshot", "Kimi (Moonshot)", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY",
                       "pay_per_use", "KI"),
        ProviderPreset("zai", "Z.AI (GLM)", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY",
                       "pay_per_use", "Z", context_window=128_000),
        ProviderPreset("dashscope", "Qwen (Alibaba Cloud)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                       "DASHSCOPE_API_KEY", "pay_per_use", "QW"),
        ProviderPreset("together", "Together AI", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
                       "pay_per_use", "TG", chat_types=frozenset({"chat"})),
        ProviderPreset("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
                       "pay_per_use", "FW"),
        ProviderPreset("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "pay_per_use", "CB"),
        ProviderPreset("huggingface", "Hugging Face", "https://router.huggingface.co/v1", "HF_TOKEN",
                       "pay_per_use", "HF", risk_label="third_party_router"),
        ProviderPreset("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                       "credits", "NV"),
        ProviderPreset("vercel", "Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "AI_GATEWAY_API_KEY",
                       "credits", "VG", risk_label="third_party_router"),
    )
}


def get_preset(provider_id: str | None) -> ProviderPreset | None:
    return PRESETS.get(str(provider_id or ""))


def preset_definition(preset: ProviderPreset) -> ProviderDefinition:
    return ProviderDefinition(
        id=preset.id,
        display_name=preset.name,
        auth_methods=(AuthMethod.API_KEY,),
        default_transport=TransportMode.OPENAI_CHAT,
        base_url=preset.base_url,
        risk_label=preset.risk_label,
        icon=preset.icon,
    )


def preset_endpoint(preset: ProviderPreset) -> dict[str, Any]:
    """The compatible transport's endpoint for this provider."""
    return {
        "provider_id": preset.id,
        "display_name": preset.name,
        "base_url": preset.base_url,
        "transport": TransportMode.OPENAI_CHAT.value,
        "profile": preset.id,
    }


def models_url(preset: ProviderPreset) -> str:
    return f"{preset.base_url}/models"


def _rows(payload: Any) -> list[dict[str, Any]]:
    # Together answers with a bare list; the others wrap it in "data".
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _positive(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _is_chat(preset: ProviderPreset, row: dict[str, Any]) -> bool:
    model_id = str(row.get("id") or "").lower()
    if any(marker in model_id.rsplit("/", 1)[-1] for marker in _NON_CHAT_MARKERS):
        return False
    kind = str(row.get("type") or "").strip().lower()
    if kind and (kind not in preset.chat_types if preset.chat_types else kind in _NON_CHAT_TYPES):
        return False
    capabilities = row.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("completion_chat") is False:
        return False
    return row.get("supports_chat") is not False


def row_metadata(preset: ProviderPreset, row: dict[str, Any]) -> dict[str, Any]:
    """What a model list row says about tools, images and context.

    A list that describes capabilities (Mistral's capabilities, Vercel's tags, Hugging
    Face's per-provider flags, a supports_tools field) is believed, including what it
    leaves out. Where a row says nothing, models.dev's catalogue fills in what it knows
    about the model; otherwise the provider's chat models take tools.
    """
    from row_bot.providers.capability_catalog import lookup

    known = lookup(preset.id, str(row.get("id") or ""))
    metadata: dict[str, Any] = {}
    capabilities = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else None
    tags = {str(tag).lower() for tag in row["tags"]} if isinstance(row.get("tags"), list) else None
    routes = [item for item in row["providers"] if isinstance(item, dict)] if isinstance(row.get("providers"), list) else None
    if isinstance(row.get("supports_tools"), bool):
        metadata["tool_calling"] = row["supports_tools"]
    elif capabilities is not None:
        metadata["tool_calling"] = capabilities.get("function_calling") is True
    elif tags is not None:
        metadata["tool_calling"] = "tool-use" in tags
    elif routes is not None:
        metadata["tool_calling"] = any(item.get("supports_tools") is True for item in routes)
    elif known:
        metadata["tool_calling"] = known.tool_calling
    vision = (
        row.get("supports_image_input") is True
        or row.get("supports_image_in") is True
        or (capabilities is not None and capabilities.get("vision") is True)
        or (tags is not None and "vision" in tags)
        or (capabilities is None and tags is None and known is not None and known.vision)
    )
    if vision:
        metadata["vision"] = True
    context = next((_positive(row.get(key)) for key in (
        "context_window", "context_length", "max_context_length", "max_model_len",
    ) if _positive(row.get(key))), 0)
    if not context and routes:
        context = max((_positive(item.get("context_length")) for item in routes), default=0)
    metadata["context_window"] = context or (known.context_window if known else 0) or preset.context_window
    return metadata


def model_infos_from_catalog(preset: ProviderPreset, payload: Any, *, verified_at: str = "") -> list[ModelInfo]:
    """The chat models in this provider's ``/models`` answer, and its image and video
    models when Row-Bot's image and video tools can call this provider."""
    from row_bot.providers.catalog import model_info_from_metadata
    from row_bot.providers.media import IMAGE_PROVIDER_META, VIDEO_PROVIDER_META

    makes = {kind for kind, table in (("image", IMAGE_PROVIDER_META), ("video", VIDEO_PROVIDER_META))
             if preset.id in table}
    infos: list[ModelInfo] = []
    seen: set[str] = set()
    for row in _rows(payload):
        model_id = str(row.get("id") or "").strip()
        kind = str(row.get("type") or "").strip().lower()
        if model_id and model_id not in seen and kind in makes:
            seen.add(model_id)
            info = model_info_from_metadata(
                preset.id, model_id, {"output_modalities": [kind]},
                display_name=str(row.get("name") or row.get("display_name") or model_id),
                context_window=0, risk_label=preset.risk_label, source="preset_catalog",
            )
            infos.append(replace(info, last_verified_at=verified_at) if verified_at else info)
            continue
        if not model_id or model_id in seen or not _is_chat(preset, row):
            continue
        seen.add(model_id)
        metadata = row_metadata(preset, row)
        info = model_info_from_metadata(
            preset.id,
            model_id,
            metadata,
            display_name=str(row.get("name") or row.get("display_name") or model_id),
            context_window=metadata["context_window"],
            transport=TransportMode.OPENAI_CHAT,
            risk_label=preset.risk_label,
            source="preset_catalog",
        )
        infos.append(replace(info, last_verified_at=verified_at) if verified_at else info)
    return infos


def validate_preset_key(provider_id: str, api_key: str) -> bool:
    """Ask the provider whether the key works by listing its models."""
    import httpx

    preset = PRESETS[provider_id]
    response = httpx.get(models_url(preset), headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
    if response.status_code in (401, 403):
        return False
    response.raise_for_status()
    return True
