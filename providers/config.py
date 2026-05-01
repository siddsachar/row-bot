from __future__ import annotations

import copy
import json
import logging
import os
import pathlib
import tempfile
from typing import Any, Callable

import secret_store

logger = logging.getLogger(__name__)

DATA_DIR = pathlib.Path(os.environ.get("THOTH_DATA_DIR", pathlib.Path.home() / ".thoth"))
CONFIG_PATH = DATA_DIR / "providers.json"
CONFIG_VERSION = 1

DEFAULT_ROUTE_PROFILES: list[dict[str, Any]] = [
    {
        "id": "balanced",
        "display_name": "Balanced",
        "description": "Use the selected default model with conservative fallback controls.",
        "enabled": True,
        "primary": "",
        "fallbacks": [],
        "triggers": ["auth_failure", "quota", "rate_limit", "timeout", "provider_5xx"],
        "data_policy": "allow_api_key",
        "max_fallbacks_per_turn": 1,
        "task_routes": {},
    },
    {
        "id": "private",
        "display_name": "Private",
        "description": "Prefer local/private models and avoid third-party routers.",
        "enabled": True,
        "primary": "",
        "fallbacks": [],
        "triggers": [],
        "data_policy": "local_only",
        "max_fallbacks_per_turn": 0,
        "task_routes": {},
    },
    {
        "id": "fast",
        "display_name": "Fast",
        "description": "Prefer low-latency choices once routing is enabled.",
        "enabled": True,
        "primary": "",
        "fallbacks": [],
        "triggers": ["timeout", "provider_5xx"],
        "data_policy": "allow_api_key",
        "max_fallbacks_per_turn": 1,
        "task_routes": {},
    },
    {
        "id": "best",
        "display_name": "Best",
        "description": "Prefer highest-quality configured choices once routing is enabled.",
        "enabled": True,
        "primary": "",
        "fallbacks": [],
        "triggers": ["quota", "rate_limit", "provider_5xx"],
        "data_policy": "allow_api_key",
        "max_fallbacks_per_turn": 1,
        "task_routes": {},
    },
    {
        "id": "cheap",
        "display_name": "Cheap",
        "description": "Prefer lower-cost choices once routing is enabled.",
        "enabled": True,
        "primary": "",
        "fallbacks": [],
        "triggers": ["quota", "rate_limit"],
        "data_policy": "allow_api_key",
        "max_fallbacks_per_turn": 1,
        "task_routes": {},
    },
]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "providers": {},
    "quick_choices": [],
    "routes": DEFAULT_ROUTE_PROFILES,
    "custom_endpoints": [],
}


def _path(path: pathlib.Path | str | None = None) -> pathlib.Path:
    return pathlib.Path(path) if path is not None else CONFIG_PATH


def normalize_provider_config(raw: Any) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["version"] = CONFIG_VERSION
    if not isinstance(cfg.get("providers"), dict):
        cfg["providers"] = {}
    if not isinstance(cfg.get("quick_choices"), list):
        cfg["quick_choices"] = []
    if not isinstance(cfg.get("custom_endpoints"), list):
        cfg["custom_endpoints"] = []

    routes = cfg.get("routes") if isinstance(cfg.get("routes"), list) else []
    by_id = {str(r.get("id")): dict(r) for r in routes if isinstance(r, dict) and r.get("id")}
    merged_routes: list[dict[str, Any]] = []
    for default_route in DEFAULT_ROUTE_PROFILES:
        route = dict(default_route)
        route.update(by_id.pop(default_route["id"], {}))
        merged_routes.append(route)
    merged_routes.extend(by_id.values())
    cfg["routes"] = merged_routes
    return cfg


def load_provider_config(path: pathlib.Path | str | None = None) -> dict[str, Any]:
    target = _path(path)
    try:
        if target.exists():
            return normalize_provider_config(json.loads(target.read_text()))
    except Exception:
        logger.warning("Failed to load provider config from %s", target, exc_info=True)
    return normalize_provider_config({})


def save_provider_config(config: dict[str, Any], path: pathlib.Path | str | None = None) -> dict[str, Any]:
    target = _path(path)
    normalized = normalize_provider_config(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=target.parent, encoding="utf-8") as tmp:
        json.dump(normalized, tmp, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    pathlib.Path(temp_name).replace(target)
    return normalized


def update_provider_config(updater: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    cfg = load_provider_config()
    updater(cfg)
    return save_provider_config(cfg)


def mask_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(normalize_provider_config(config))

    def _mask(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                lower = str(key).lower()
                if any(word in lower for word in ("secret", "token", "api_key", "key")) and isinstance(item, str):
                    result[key] = secret_store.fingerprint(item)
                else:
                    result[key] = _mask(item)
            return result
        if isinstance(value, list):
            return [_mask(item) for item in value]
        return value

    return _mask(masked)