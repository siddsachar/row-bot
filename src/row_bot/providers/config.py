from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import pathlib
import tempfile
import threading
import time
from typing import Any, Callable

from row_bot.data_paths import get_row_bot_data_dir
import row_bot.secret_store as secret_store

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir(create=False)
CONFIG_PATH = DATA_DIR / "providers.json"
CONFIG_VERSION = 1
_WRITER_LOCK = threading.RLock()
_WRITER_LOCAL = threading.local()


class ProviderConfigError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@contextmanager
def provider_config_transaction(path: pathlib.Path | str | None = None):
    """Serialize canonical config writers across threads and processes."""
    target = _path(path).absolute()
    with _WRITER_LOCK:
        owned = getattr(_WRITER_LOCAL, "paths", set())
        if str(target) in owned:
            yield
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.with_suffix(target.suffix + ".lock").open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + 2
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ProviderConfigError("provider_settings_busy") from None
                    time.sleep(0.01)
            _WRITER_LOCAL.paths = owned | {str(target)}
            try:
                yield
            finally:
                _WRITER_LOCAL.paths = owned
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def provider_config_revision(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()

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


def load_provider_config(path: pathlib.Path | str | None = None, *, strict: bool = False) -> dict[str, Any]:
    target = _path(path)
    try:
        if target.exists():
            if strict and target.stat().st_size > 2 * 1024 * 1024:
                raise ProviderConfigError("provider_settings_unavailable")
            raw = json.loads(target.read_text(encoding="utf-8"))
            if strict and (not isinstance(raw, dict) or not isinstance(raw.get("providers", {}), dict)):
                raise ProviderConfigError("provider_settings_unavailable")
            return normalize_provider_config(raw)
    except Exception:
        if strict:
            raise ProviderConfigError("provider_settings_unavailable") from None
        logger.warning("Failed to load provider config from %s", target, exc_info=True)
    return normalize_provider_config({})


def save_provider_config(config: dict[str, Any], path: pathlib.Path | str | None = None,
                         *, expected_revision: str | None = None) -> dict[str, Any]:
    target = _path(path)
    normalized = normalize_provider_config(config)
    with provider_config_transaction(target):
        if expected_revision is not None and provider_config_revision(load_provider_config(target, strict=True)) != expected_revision:
            raise ProviderConfigError("revision_conflict")
        write_provider_metadata(target, normalized)
    return normalized


def write_provider_metadata(target: pathlib.Path, payload: dict[str, Any]) -> None:
    """Publish provider-owned JSON under the caller's canonical writer admission."""
    with tempfile.NamedTemporaryFile("w", delete=False, dir=target.parent, encoding="utf-8") as tmp:
        json.dump(payload, tmp, indent=2)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    try:
        pathlib.Path(temp_name).replace(target)
    finally:
        pathlib.Path(temp_name).unlink(missing_ok=True)


def update_provider_config(updater: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    with provider_config_transaction():
        cfg = load_provider_config(strict=True)
        revision = provider_config_revision(cfg)
        updater(cfg)
        return save_provider_config(cfg, expected_revision=revision)


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
