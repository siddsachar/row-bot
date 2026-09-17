"""Typed Models settings projection and local mutations for the React client."""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from row_bot.application.provider_default_model import read_default_model
from row_bot.providers import client_status
from row_bot.providers.selection import format_model_choice_label, model_choice_value, parse_model_ref

SURFACES = ("chat", "vision", "image", "video")


def _media_ref(value: str, *, default_provider: str) -> str:
    provider_id, _, model_id = value.partition("/")
    if not model_id:
        provider_id, model_id = default_provider, provider_id
    return model_choice_value(model_id, provider_id=provider_id) if provider_id and model_id else ""


def _picker(surface: str, current: str, rows: tuple, *, enabled: bool | None = None,
            media_options: dict[str, str] | None = None) -> dict:
    options = []
    found = False
    for row in rows:
        if surface not in row.pinned_surfaces and row.selection_ref != current:
            continue
        if surface not in row.categories and row.selection_ref != current:
            continue
        available = bool(row.configured and row.runtime_ready and row.installed and surface in row.categories)
        found |= row.selection_ref == current
        options.append({
            "selection_ref": row.selection_ref,
            "label": format_model_choice_label(row.provider_id, row.model_id, row.display_name, include_icon=False)[:256],
            "source": row.source[:80], "available": available,
            "context_window": row.context_window,
            "reason": row.status_reason[:256] if not available else "",
        })
    if current and not found:
        parsed = parse_model_ref(current)
        provider_id, model_id = parsed if parsed else ("", current)
        media_value = f"{provider_id}/{model_id}"
        available = bool(media_options and media_value in media_options)
        options.append({
            "selection_ref": current,
            "label": (media_options.get(media_value) if available and media_options else
                      format_model_choice_label(provider_id, model_id, include_icon=False))[:256],
            "source": "configured_default" if available else "included_value",
            "available": available, "context_window": None,
            "reason": "" if available else "This saved model is not available in the local catalog.",
        })
    current_option = next((option for option in options if option["selection_ref"] == current), None)
    warning = ""
    if current and (current_option is None or not current_option["available"]):
        if current.startswith("model:ollama:"):
            warning = f"Current {surface.capitalize()} local model is unavailable. Manage local models in Ollama, then refresh or choose another pinned model."
        else:
            warning = f"Current {surface.capitalize()} default is unavailable. Connect the provider, refresh the catalog, or choose another pinned model."
    return {"current_ref": current, "enabled": enabled, "warning": warning,
            "options": options[:4096]}


def read_models_settings(*, validate: Callable[[], None] = lambda: None) -> dict:
    """Read actual defaults and pinned choices; never refresh or probe a provider."""
    validate()
    from row_bot.models import (get_cloud_context_override, get_context_policy,
                                get_current_model, get_local_context_mode, get_user_context_size)
    from row_bot.tools import registry
    from row_bot.tools.image_gen_tool import DEFAULT_MODEL as IMAGE_DEFAULT, get_available_image_models
    from row_bot.tools.video_gen_tool import DEFAULT_MODEL as VIDEO_DEFAULT, get_available_video_models
    from row_bot.vision_runtime import get_vision_service

    default = read_default_model(validate=validate)
    vision = get_vision_service()
    image_tool = registry.get_tool("image_gen")
    video_tool = registry.get_tool("video_gen")
    image_value = image_tool.get_config("model", IMAGE_DEFAULT) if image_tool else registry.get_tool_config("image_gen", "model", IMAGE_DEFAULT)
    video_value = video_tool.get_config("model", VIDEO_DEFAULT) if video_tool else registry.get_tool_config("video_gen", "model", VIDEO_DEFAULT)
    brain_ref = model_choice_value(get_current_model()) or default.selection_ref or ""
    vision_ref = model_choice_value(vision.model)
    image_ref = _media_ref(str(image_value), default_provider="openai")
    video_ref = _media_ref(str(video_value), default_provider="google")
    cache, rows = client_status._read(None, include_readiness=True)
    policy = get_context_policy(brain_ref or None, allow_probe=False)
    cap = (get_cloud_context_override() if policy.policy_kind == "provider" else
           None if get_local_context_mode() == "auto" else get_user_context_size())
    result = {
        "schema_version": 1,
        "brain": _picker("chat", brain_ref, rows),
        "vision": _picker("vision", vision_ref, rows, enabled=vision.enabled),
        "image": _picker("image", image_ref, rows, enabled=registry.is_enabled("image_gen") if image_tool else False,
                         media_options=get_available_image_models()),
        "video": _picker("video", video_ref, rows, enabled=registry.is_enabled("video_gen") if video_tool else False,
                         media_options=get_available_video_models()),
        "camera_index": max(0, min(64, int(vision.camera_index))),
        "context": {"policy_kind": policy.policy_kind, "selected_cap": cap,
                    "native_max": policy.native_max, "effective_cap": policy.effective_context or None,
                    "warning": policy.warning[:512]},
        "freshness": cache.freshness, "generated_at": cache.generated_at,
        "refresh_running": cache.refresh_running,
    }
    validate()
    return result


def update_model_surface(surface: str, action: str, *, selection_ref: str | None = None,
                         enabled: bool | None = None, camera_index: int | None = None,
                         validate: Callable[[], None]) -> dict:
    """Write through the same Vision/tool owners used by NiceGUI."""
    validate()
    from row_bot.tools import registry
    from row_bot.vision_runtime import get_vision_service

    if action == "default":
        state = read_models_settings(validate=validate)
        options = state[surface]["options"]
        if not selection_ref or not any(item["selection_ref"] == selection_ref and item["available"] for item in options):
            raise ValueError("model_configuration_unavailable")
        parsed = parse_model_ref(selection_ref)
        if not parsed:
            raise ValueError("invalid_model_selection")
        provider_id, model_id = parsed
        if surface == "vision":
            get_vision_service().model = selection_ref
            from row_bot.agent import clear_agent_cache
            clear_agent_cache()
        else:
            tool = registry.get_tool("image_gen" if surface == "image" else "video_gen")
            if tool is None:
                raise ValueError("model_surface_unavailable")
            tool.set_config("model", f"{provider_id}/{model_id}")
            from row_bot.providers.selection import seed_configured_media_quick_choices
            seed_configured_media_quick_choices()
    elif action == "enabled" and type(enabled) is bool:
        if surface == "vision":
            get_vision_service().enabled = enabled
        else:
            registry.set_enabled("image_gen" if surface == "image" else "video_gen", enabled)
    elif action == "camera" and surface == "vision" and type(camera_index) is int and 0 <= camera_index <= 64:
        get_vision_service().camera_index = camera_index
    else:
        raise ValueError("invalid_model_surface_action")
    validate()
    return read_models_settings(validate=validate)


def update_model_context(policy_kind: str, cap: int | None, *, validate: Callable[[], None]) -> dict:
    validate()
    from row_bot import models
    state = read_models_settings(validate=validate)
    if state["context"]["policy_kind"] != policy_kind:
        raise ValueError("context_policy_changed")
    if cap is not None:
        models.validate_context_size(cap)
    if policy_kind == "provider":
        models.clear_cloud_context_override() if cap is None else models.set_cloud_context_size(cap)
    else:
        models.set_context_size_auto() if cap is None else models.set_context_size(cap)
    validate()
    return read_models_settings(validate=validate)


def read_agent_settings(*, validate: Callable[[], None] = lambda: None) -> dict:
    from row_bot.agent_settings import load_agent_runtime_settings
    validate()
    result = asdict(load_agent_runtime_settings())
    validate()
    return result


def save_agent_settings(fields: dict | None, *, validate: Callable[[], None]) -> dict:
    from row_bot.agent_settings import reset_agent_runtime_settings, save_agent_runtime_settings
    validate()
    saved = reset_agent_runtime_settings() if fields is None else save_agent_runtime_settings(fields)
    validate()
    return asdict(saved)


def catalog_provider_summary(surface: str, *, validate: Callable[[], None] = lambda: None) -> dict:
    if surface not in (*SURFACES, "voice"):
        raise ValueError("invalid_model_surface")
    validate()
    snapshot, rows = client_status._read(None, include_readiness=True)
    groups: dict[str, dict] = {}
    for row in rows:
        if surface not in row.categories:
            continue
        group = groups.setdefault(row.provider_id, {"provider_id": row.provider_id,
            "display_name": row.provider_display_name, "total": 0, "ready": 0, "pinned": 0})
        group["total"] += 1
        group["ready"] += int(row.configured and row.runtime_ready and bool(row.installed))
        group["pinned"] += int(surface in row.pinned_surfaces)
    validate()
    return {"schema_version": 1, "revision": snapshot.revision, "surface": surface,
            "providers": sorted(groups.values(), key=lambda row: (row["display_name"].casefold(), row["provider_id"]))}
