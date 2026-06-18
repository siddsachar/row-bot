from __future__ import annotations

from typing import Any

from row_bot.providers.capabilities import snapshot_supports_surface
from row_bot.providers.catalog import model_info_from_metadata

IMAGE_PROVIDER_META: dict[str, dict[str, str]] = {
    "openai": {"key": "OPENAI_API_KEY", "label": "OpenAI", "emoji": "⬡"},
    "google": {"key": "GOOGLE_API_KEY", "label": "Google", "emoji": "💎"},
    "xai": {"key": "XAI_API_KEY", "label": "xAI", "emoji": "𝕏"},
    "xai_oauth": {"auth": "oauth", "label": "xAI Grok", "emoji": "X", "risk_label": "subscription"},
}

VIDEO_PROVIDER_META: dict[str, dict[str, str]] = {
    "google": {"key": "GOOGLE_API_KEY", "label": "Google", "emoji": "💎"},
    "xai": {"key": "XAI_API_KEY", "label": "xAI", "emoji": "𝕏"},
    "xai_oauth": {"auth": "oauth", "label": "xAI Grok", "emoji": "X", "risk_label": "subscription"},
}

CURATED_IMAGE_MODELS: dict[str, list[dict[str, str]]] = {
    "openai": [
        {"id": "gpt-image-1.5", "label": "GPT Image 1.5"},
        {"id": "gpt-image-1", "label": "GPT Image 1"},
        {"id": "gpt-image-1-mini", "label": "GPT Image 1 Mini"},
    ],
    "google": [
        {"id": "gemini-3.1-flash-image-preview", "label": "Nano Banana 2"},
        {"id": "gemini-3-pro-image-preview", "label": "Nano Banana Pro"},
        {"id": "gemini-2.5-flash-image", "label": "Nano Banana"},
        {"id": "imagen-4.0-generate-001", "label": "Imagen 4"},
        {"id": "imagen-4.0-fast-generate-001", "label": "Imagen 4 Fast"},
        {"id": "imagen-4.0-ultra-generate-001", "label": "Imagen 4 Ultra"},
    ],
    "xai": [
        {"id": "grok-imagine-image", "label": "Grok Imagine"},
        {"id": "grok-imagine-image-quality", "label": "Grok Imagine Quality"},
    ],
    "xai_oauth": [
        {"id": "grok-imagine-image", "label": "Grok Imagine"},
        {"id": "grok-imagine-image-quality", "label": "Grok Imagine Quality"},
    ],
}

CURATED_VIDEO_MODELS: dict[str, list[dict[str, str]]] = {
    "google": [
        {"id": "veo-3.1-generate-preview", "label": "Veo 3.1"},
        {"id": "veo-3.1-fast-generate-preview", "label": "Veo 3.1 Fast"},
    ],
    "xai": [
        {"id": "grok-imagine-video", "label": "Grok Imagine Video"},
    ],
    "xai_oauth": [
        {"id": "grok-imagine-video", "label": "Grok Imagine Video"},
    ],
}


def _media_provider_available(provider_id: str, meta: dict[str, str]) -> bool:
    if meta.get("auth") == "oauth":
        if provider_id == "xai_oauth":
            try:
                from row_bot.providers.xai_oauth import xai_oauth_runtime_available

                return bool(xai_oauth_runtime_available(refresh_if_needed=False))
            except Exception:
                return False
        return False

    key_name = str(meta.get("key") or "")
    if not key_name:
        return False
    from row_bot.api_keys import get_key

    return bool(get_key(key_name))


def curated_media_cache_entries(surface: str) -> dict[str, dict[str, Any]]:
    provider_models = CURATED_IMAGE_MODELS if surface == "image" else CURATED_VIDEO_MODELS if surface == "video" else {}
    entries: dict[str, dict[str, Any]] = {}
    for provider_id, models in provider_models.items():
        for model in models:
            model_id = model["id"]
            info = model_info_from_metadata(
                provider_id,
                model_id,
                display_name=model.get("label") or model_id,
                context_window=0,
                source="curated_media_catalog",
            )
            snapshot = info.capability_snapshot()
            entries[f"{provider_id}/{model_id}"] = {
                "label": info.display_name,
                "ctx": 0,
                "provider": provider_id,
                "vision": False,
                "capabilities_snapshot": snapshot,
                "transport": info.transport.value,
                "risk_label": info.risk_label,
                "source": info.source,
            }
    return entries


def media_model_options(surface: str, cloud_cache: dict[str, dict[str, Any]]) -> dict[str, str]:
    from row_bot.providers.selection import parse_model_ref

    provider_meta = IMAGE_PROVIDER_META if surface == "image" else VIDEO_PROVIDER_META if surface == "video" else {}
    entries = curated_media_cache_entries(surface)
    for cache_key, info in cloud_cache.items():
        provider_id = str(info.get("provider") or "")
        model_id = str(cache_key or "")
        parsed = parse_model_ref(model_id)
        if parsed:
            ref_provider_id, ref_model_id = parsed
            if provider_id and provider_id != ref_provider_id:
                continue
            provider_id = provider_id or ref_provider_id
            model_id = ref_model_id
        if provider_id not in provider_meta:
            continue
        snapshot = info.get("capabilities_snapshot") if isinstance(info.get("capabilities_snapshot"), dict) else {}
        if not snapshot:
            continue
        if not snapshot_supports_surface(snapshot, surface):
            continue
        entries[f"{provider_id}/{model_id}"] = dict(info)

    options: dict[str, str] = {}
    for config_value, info in sorted(entries.items(), key=lambda item: item[0]):
        provider_id = str(info.get("provider") or config_value.split("/", 1)[0])
        meta = provider_meta.get(provider_id)
        if not meta or not _media_provider_available(provider_id, meta):
            continue
        model_id = config_value.split("/", 1)[1] if "/" in config_value else config_value
        label = str(info.get("label") or model_id)
        options[config_value] = f"{meta['emoji']}  {label}  ({meta['label']})"
    return options
