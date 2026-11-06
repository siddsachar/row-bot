from __future__ import annotations

from typing import Any

from row_bot.providers.capabilities import snapshot_supports_surface

# Who makes images and videos, and how: each provider's route is the shape of its
# API ("openai" is POST /images/generations and /images/edits at base_url), so a
# provider that speaks a known shape is one row here.
IMAGE_PROVIDER_META: dict[str, dict[str, str]] = {
    "openai": {"key": "OPENAI_API_KEY", "label": "OpenAI", "emoji": "⬡", "route": "openai",
               "base_url": "https://api.openai.com/v1"},
    "google": {"key": "GOOGLE_API_KEY", "label": "Google", "emoji": "💎", "route": "google"},
    "xai": {"key": "XAI_API_KEY", "label": "xAI", "emoji": "𝕏", "route": "xai"},
    "xai_oauth": {"auth": "oauth", "label": "xAI Grok", "emoji": "X", "risk_label": "subscription", "route": "xai"},
    "openrouter": {"key": "OPENROUTER_API_KEY", "label": "OpenRouter", "emoji": "🌐", "route": "openrouter",
                   "base_url": "https://openrouter.ai/api/v1"},
    "vercel": {"key": "AI_GATEWAY_API_KEY", "label": "Vercel AI Gateway", "emoji": "VG", "route": "openai",
               "base_url": "https://ai-gateway.vercel.sh/v1"},
    "together": {"key": "TOGETHER_API_KEY", "label": "Together AI", "emoji": "TG", "route": "together",
                 "base_url": "https://api.together.xyz/v1"},
    "requesty": {"key": "REQUESTY_API_KEY", "label": "Requesty", "emoji": "🛰️", "route": "openai",
                 "base_url": "https://router.requesty.ai/v1"},
}

VIDEO_PROVIDER_META: dict[str, dict[str, str]] = {
    "google": {"key": "GOOGLE_API_KEY", "label": "Google", "emoji": "💎", "route": "google"},
    "xai": {"key": "XAI_API_KEY", "label": "xAI", "emoji": "𝕏", "route": "xai"},
    "xai_oauth": {"auth": "oauth", "label": "xAI Grok", "emoji": "X", "risk_label": "subscription", "route": "xai"},
    "openrouter": {"key": "OPENROUTER_API_KEY", "label": "OpenRouter", "emoji": "🌐", "route": "openrouter",
                   "base_url": "https://openrouter.ai/api/v1"},
    "together": {"key": "TOGETHER_API_KEY", "label": "Together AI", "emoji": "TG", "route": "together",
                 "base_url": "https://api.together.xyz/v2"},
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


def media_model_options(surface: str, cloud_cache: dict[str, dict[str, Any]]) -> dict[str, str]:
    """The image or video models a connected provider's own list offers, as {provider/model: label}."""
    from row_bot.providers.selection import parse_model_ref

    provider_meta = IMAGE_PROVIDER_META if surface == "image" else VIDEO_PROVIDER_META if surface == "video" else {}
    entries: dict[str, dict[str, Any]] = {}
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


def unlisted_media_note(surface: str, selection: str, cloud_cache: dict[str, dict[str, Any]]) -> str:
    """Why a saved image or video model may be gone: its provider's list no longer has it.

    Empty while the provider's list has the model, or before the provider has listed anything.
    """
    provider_id, _, model_id = selection.partition("/")
    meta = (IMAGE_PROVIDER_META if surface == "image" else VIDEO_PROVIDER_META).get(provider_id)
    if not meta or not model_id or selection in media_model_options(surface, cloud_cache):
        return ""
    if not any(str(info.get("provider") or "") == provider_id for info in cloud_cache.values()):
        return ""
    return (f"{meta['label']} no longer lists {model_id}, so it may have been retired. "
            f"Choose another {surface} model in Settings › Models.")


def media_error_text(response: Any) -> str:
    """A provider's own error message from a failed media request, briefly."""
    try:
        body = response.json()
    except ValueError:
        return str(response.text or "").strip()[:300] or "no details"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    message = error or (body.get("message") if isinstance(body, dict) else None)
    return str(message or body)[:300]
