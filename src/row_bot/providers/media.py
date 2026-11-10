from __future__ import annotations

from collections.abc import Mapping
import math
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
    "minimax": {"key": "MINIMAX_API_KEY", "label": "MiniMax", "emoji": "M", "route": "minimax",
                "base_url": "https://api.minimax.io/v1"},
    "zai": {"key": "ZAI_API_KEY", "label": "Z.AI", "emoji": "Z", "route": "zai",
            "base_url": "https://api.z.ai/api/paas/v4"},
    "dashscope": {"key": "DASHSCOPE_API_KEY", "label": "Qwen (Alibaba Cloud)", "emoji": "QW", "route": "dashscope",
                  "base_url": "https://dashscope-intl.aliyuncs.com/api/v1"},
    "atlascloud": {"key": "ATLASCLOUD_API_KEY", "label": "Atlas Cloud", "emoji": "AC", "route": "atlascloud",
                   "base_url": "https://api.atlascloud.ai/api/v1"},
}

VIDEO_PROVIDER_META: dict[str, dict[str, str]] = {
    "google": {"key": "GOOGLE_API_KEY", "label": "Google", "emoji": "💎", "route": "google"},
    "xai": {"key": "XAI_API_KEY", "label": "xAI", "emoji": "𝕏", "route": "xai"},
    "xai_oauth": {"auth": "oauth", "label": "xAI Grok", "emoji": "X", "risk_label": "subscription", "route": "xai"},
    "openrouter": {"key": "OPENROUTER_API_KEY", "label": "OpenRouter", "emoji": "🌐", "route": "openrouter",
                   "base_url": "https://openrouter.ai/api/v1"},
    "together": {"key": "TOGETHER_API_KEY", "label": "Together AI", "emoji": "TG", "route": "together",
                 "base_url": "https://api.together.xyz/v2"},
    "minimax": {"key": "MINIMAX_API_KEY", "label": "MiniMax", "emoji": "M", "route": "minimax",
                "base_url": "https://api.minimax.io/v2"},
    "zai": {"key": "ZAI_API_KEY", "label": "Z.AI", "emoji": "Z", "route": "zai",
            "base_url": "https://api.z.ai/api/paas/v4"},
    "dashscope": {"key": "DASHSCOPE_API_KEY", "label": "Qwen (Alibaba Cloud)", "emoji": "QW", "route": "dashscope",
                  "base_url": "https://dashscope-intl.aliyuncs.com/api/v1"},
    "atlascloud": {"key": "ATLASCLOUD_API_KEY", "label": "Atlas Cloud", "emoji": "AC", "route": "atlascloud",
                   "base_url": "https://api.atlascloud.ai/api/v1"},
}

# The image and video models of providers whose model lists leave them out: MiniMax
# lists its text models only and Z.AI's list names no media models. Their refresh adds
# these to the catalog beside the listed ones. Every other provider's media models come
# from its own list.
UNLISTED_MEDIA_MODELS: dict[str, dict[str, dict[str, str]]] = {
    "image": {
        "minimax": {"image-01": "Image-01"},
        "zai": {"glm-image": "GLM-Image"},
    },
    "video": {
        "minimax": {"MiniMax-H3": "MiniMax H3", "MiniMax-H3-Highspeed": "MiniMax H3 Highspeed",
                    "MiniMax-H3-Lite": "MiniMax H3 Lite", "MiniMax-H3-Max": "MiniMax H3 Max"},
        "zai": {"cogvideox-3": "CogVideoX-3"},
    },
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

    return bool(meta.get("key")) and bool(media_api_key(provider_id))


def media_api_key(provider_id: str) -> str:
    """A media provider's API key, read as chat reads it: the key saved in Settings › Models
    (the provider panel keeps it per provider), else the older saved key or the environment's."""
    from row_bot.providers.auth_store import get_provider_secret

    try:
        return str(get_provider_secret(provider_id) or "").strip()
    except Exception:
        return ""


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


def generation_options(provider_id: str, model: str) -> dict[str, list] | None:
    """The choices a model's catalogue lists for each setting it takes; None when it lists none."""
    from row_bot.providers.capability_resolution import resolve_capability_snapshot

    parameters = resolve_capability_snapshot(provider_id, model, include_static_fallback=False).get(
        "generation_parameters")
    options = parameters.get("options") if isinstance(parameters, Mapping) else None
    if not isinstance(options, Mapping):
        return None
    return {str(name): list(values) for name, values in options.items() if isinstance(values, list)}


# Resolutions in order of size, to find the one a model takes nearest the one asked for.
_RESOLUTION_PIXELS = {"360p": 360, "480p": 480, "512": 512, "720p": 720, "768": 768, "768p": 768, "1k": 1024,
                      "1080p": 1080, "1.5k": 1536, "2k": 2048, "4k": 4096}


def _ratio(value: str, separator: str) -> float | None:
    width, _, height = str(value).partition(separator)
    try:
        return math.log(float(width) / float(height))
    except (ValueError, ZeroDivisionError):
        return None


def _nearest(target: float | None, choices: list, measure) -> Any:
    measured = [(measure(choice), choice) for choice in choices]
    usable = [(size, choice) for size, choice in measured if size is not None]
    if target is None or not usable:
        return None
    return min(usable, key=lambda item: abs(item[0] - target))[1]


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def nearest_duration(seconds: int, choices: list) -> int | None:
    """The length in seconds, of those a model takes, nearest the one asked for."""
    return _nearest(float(seconds), choices, _seconds)


def nearest_resolution(resolution: str, choices: list) -> str | None:
    """The resolution, of those a model takes, nearest the one asked for, in the model's own spelling."""
    return _nearest(_RESOLUTION_PIXELS.get(resolution.lower()), choices,
                    lambda choice: _RESOLUTION_PIXELS.get(str(choice).lower()))


def nearest_aspect_ratio(aspect_ratio: str, choices: list) -> str | None:
    """The aspect ratio ("W:H"), of those a model takes, nearest the one asked for."""
    return _nearest(_ratio(aspect_ratio, ":"), choices, lambda choice: _ratio(choice, ":"))


def nearest_size(aspect_ratio: str, resolution: str, choices: list) -> str | None:
    """The size ("WxH"), of those a model takes, nearest the aspect ratio and then the resolution asked for."""
    shape = nearest_aspect_ratio(aspect_ratio, [f"{w}:{h}" for w, _, h in (str(c).partition("x") for c in choices)])
    if shape is None:
        return None
    same_shape = [choice for choice in choices if _ratio(choice, "x") == _ratio(shape, ":")]

    def short_side(choice: str) -> float | None:
        width, _, height = choice.partition("x")
        return float(min(int(width), int(height)))

    return _nearest(_RESOLUTION_PIXELS.get(resolution.lower()), same_shape, short_side)
