"""Plugin state persistence — enable/disable, config, and secrets.

Stores plugin state in ``~/.row-bot/plugin_state.json``. Plugin secrets are
stored in the OS keyring when available, with metadata in
``~/.row-bot/plugin_secrets.json``.

This module is the ONLY place plugin state is read/written.
"""

from __future__ import annotations

import json
import copy
import functools
import tempfile
import threading
import logging
import os
import pathlib
import stat
from datetime import datetime, timezone
from typing import Any

import row_bot.secret_store as secret_store
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

_STATE_PATH = DATA_DIR / "plugin_state.json"
_SECRETS_PATH = DATA_DIR / "plugin_secrets.json"
_SECRETS_VERSION = 2
_SECRETS_NAMESPACE = "plugin_secrets"

# ── In-memory caches ─────────────────────────────────────────────────────────
_state: dict[str, Any] = {}     # {plugin_id: {"enabled": bool, "config": {...}}}
_secrets: dict[str, Any] = {}
_session_secrets: dict[str, dict[str, str]] = {}
_loaded = False


_state_lock = threading.RLock()


def _locked_state(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        with _state_lock:
            return function(*args, **kwargs)
    return guarded


def _atomic_json(path: pathlib.Path, data: dict, restricted: bool = False) -> None:
    """Publish one whole state file; callers decide whether errors propagate."""
    for candidate in (path.parent, path):
        try:
            value = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise OSError("State path is not an owned regular path")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if restricted and os.name != "nt":
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
    finally:
        try:
            pathlib.Path(temporary).unlink(missing_ok=True)
        except OSError:
            logger.debug("Retained unpublished plugin state temporary file")


def _environment_state_document() -> dict[str, Any]:
    """Strict state-only read: no secret initialization, migration or keyring."""
    if get_row_bot_data_dir(create=False) != DATA_DIR:
        raise ValueError("environment_state_unavailable")
    try:
        value = _STATE_PATH.lstat()
    except FileNotFoundError:
        return {}
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400 or value.st_size > 16 * 1024 * 1024:
        raise ValueError("environment_state_unavailable")
    try:
        with _STATE_PATH.open("rb") as stream:
            encoded = stream.read(16 * 1024 * 1024 + 1)
        if len(encoded) > 16 * 1024 * 1024:
            raise ValueError("environment_state_unavailable")
        data = json.loads(encoded)
        if type(data) is not dict:
            raise ValueError("environment_state_unavailable")
        return data
    except (OSError, ValueError) as exc:
        raise ValueError("environment_state_unavailable") from exc


@_locked_state
def get_plugin_environment_state(plugin_id: str) -> dict[str, Any]:
    """Read only saved environment metadata; never prepare or load a plugin."""
    data = _environment_state_document()
    record = data.get(plugin_id, {})
    if type(record) is not dict or type(record.get("environment", {})) is not dict:
        raise ValueError("environment_state_unavailable")
    return copy.deepcopy(record.get("environment", {}))


@_locked_state
def set_plugin_environment_state(plugin_id: str, value: dict[str, Any], *, expected: dict[str, Any]) -> None:
    """CAS the additive environment field and propagate publication failure.

    Preserve all current config/enablement/unknown fields and the old ready
    reference if replacement fails. This is not a second plugin state store.
    """
    global _state
    data = _environment_state_document()
    if _loaded and data != _state:
        # A failed ordinary save or an external edit must not be silently lost
        # when environment publication refreshes the shared cache.
        raise ValueError("environment_state_changed")
    record = data.setdefault(plugin_id, {})
    if type(record) is not dict or record.get("environment", {}) != expected:
        raise ValueError("environment_state_changed")
    record["environment"] = copy.deepcopy(value)
    try:
        _atomic_json(_STATE_PATH, data)
    except OSError as exc:
        raise ValueError("environment_state_unavailable") from exc
    if _loaded:
        _state = data


# ── State I/O ────────────────────────────────────────────────────────────────
@_locked_state
def reload():
    """Force re-read of state from disk. Call before load_plugins()."""
    global _loaded
    _loaded = False
    _ensure_loaded()


@_locked_state
def _ensure_loaded():
    global _state, _secrets, _loaded
    if _loaded:
        return
    _state = _read_json(_STATE_PATH)
    _secrets = _read_json(_SECRETS_PATH)
    if _is_legacy_secrets_file(_secrets) and _migrate_legacy_secrets(_secrets):
        _secrets = _read_json(_SECRETS_PATH)
    _loaded = True


def _read_json(path: pathlib.Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read %s", path, exc_info=True)
    return {}


def _save_state():
    _write_json(_STATE_PATH, _state)


def _save_secrets():
    _write_json(_SECRETS_PATH, _secrets, restricted=True)


@_locked_state
def _write_json(path: pathlib.Path, data: dict, restricted: bool = False):
    try:
        _atomic_json(path, data, restricted)
    except OSError:
        logger.warning("Failed to write %s", path, exc_info=True)


# ── Enabled State ────────────────────────────────────────────────────────────
@_locked_state
def is_plugin_enabled(plugin_id: str) -> bool:
    _ensure_loaded()
    return _state.get(plugin_id, {}).get("enabled", False)


@_locked_state
def get_cached_plugin_enablement() -> dict[str, bool | None] | None:
    """Read only loaded enablement; never load or migrate secrets on a GET."""
    if not _loaded or get_row_bot_data_dir(create=False) != DATA_DIR:
        return None
    return {
        key: value.get("enabled") if type(value.get("enabled")) is bool else None
        for key, value in _state.copy().items() if type(value) is dict
    }


@_locked_state
def set_plugin_enabled(plugin_id: str, enabled: bool) -> None:
    _ensure_loaded()
    _state.setdefault(plugin_id, {})["enabled"] = enabled
    _save_state()
    if not enabled:
        # Disabling invalidates in-flight registration, even if a later enable
        # occurs before its old register() callback returns.
        import sys
        loaded_loader = sys.modules.get("row_bot.plugins.loader")
        if loaded_loader is not None:
            loaded_loader.revoke_registration(str(plugin_id))
        try:
            from row_bot.channels import registry as channel_registry

            channel_registry.unregister_plugin_channels(str(plugin_id))
            from row_bot.plugins.webhooks import unregister_plugin_webhooks

            unregister_plugin_webhooks(str(plugin_id))
        except Exception:
            logger.debug("Plugin runtime unregister skipped for %s", plugin_id, exc_info=True)
    _invalidate_agent_cache()
    logger.info("Plugin '%s' %s", plugin_id, "enabled" if enabled else "disabled")


@_locked_state
def mark_plugin_installed(
    plugin_id: str,
    *,
    version: str = "",
    source: str = "local",
    source_ref: str = "",
) -> None:
    """Record an installed plugin without enabling it by default."""

    _ensure_loaded()
    record = _state.setdefault(str(plugin_id), {})
    record["enabled"] = False
    record.pop("health", None)
    record["installed"] = {
        "version": str(version or ""),
        "source": str(source or "local"),
        "source_ref": str(source_ref or ""),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state()


@_locked_state
def get_plugin_install_info(plugin_id: str) -> dict[str, Any]:
    _ensure_loaded()
    installed = _state.get(str(plugin_id), {}).get("installed", {})
    return dict(installed) if isinstance(installed, dict) else {}


@_locked_state
def set_plugin_health_result(
    plugin_id: str,
    *,
    ok: bool,
    checks: list[dict[str, Any]],
) -> None:
    """Record the latest local Plugin Center health/test result."""

    _ensure_loaded()
    _state.setdefault(str(plugin_id), {})["health"] = {
        "ok": bool(ok),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": list(checks),
    }
    _save_state()


@_locked_state
def get_plugin_health_result(plugin_id: str) -> dict[str, Any]:
    _ensure_loaded()
    health = _state.get(str(plugin_id), {}).get("health", {})
    return dict(health) if isinstance(health, dict) else {}


@_locked_state
def clear_plugin_health_result(plugin_id: str) -> None:
    _ensure_loaded()
    record = _state.get(str(plugin_id), {})
    if isinstance(record, dict) and record.pop("health", None) is not None:
        _save_state()


# ── Configuration ────────────────────────────────────────────────────────────
@_locked_state
def get_plugin_config(plugin_id: str, key: str, default: Any = None) -> Any:
    _ensure_loaded()
    return _state.get(plugin_id, {}).get("config", {}).get(key, default)


@_locked_state
def set_plugin_config(plugin_id: str, key: str, value: Any) -> None:
    _ensure_loaded()
    record = _state.setdefault(plugin_id, {})
    record.setdefault("config", {})[key] = value
    record.pop("health", None)
    _save_state()


@_locked_state
def get_all_plugin_config(plugin_id: str) -> dict:
    _ensure_loaded()
    return dict(_state.get(plugin_id, {}).get("config", {}))


# ── Secrets ──────────────────────────────────────────────────────────────────
@_locked_state
def get_plugin_secret(plugin_id: str, key: str) -> str | None:
    _ensure_loaded()
    plugin_id = str(plugin_id)
    key = str(key)
    session_value = _session_secrets.get(plugin_id, {}).get(key)
    if session_value:
        return session_value
    if _is_legacy_secrets_file(_secrets):
        value = _secrets.get(plugin_id, {}).get(key)
        return value if isinstance(value, str) and value else None
    entry = _secret_metadata_entry(plugin_id, key)
    if not entry:
        return None
    try:
        return secret_store.get_secret(_secret_name(plugin_id, key), namespace=_SECRETS_NAMESPACE)
    except secret_store.SecretStoreError:
        return None


@_locked_state
def set_plugin_secret(plugin_id: str, key: str, value: str) -> None:
    _ensure_loaded()
    plugin_id = str(plugin_id)
    key = str(key)
    value = str(value or "").strip()
    if not value:
        delete_plugin_secret(plugin_id, key)
        return
    try:
        secret_store.set_secret(_secret_name(plugin_id, key), value, namespace=_SECRETS_NAMESPACE)
    except secret_store.SecretStoreError as exc:
        _session_secrets.setdefault(plugin_id, {})[key] = value
        _state.setdefault(plugin_id, {}).pop("health", None)
        _save_state()
        logger.warning("Using session-only plugin secret storage for %s/%s: %s", plugin_id, key, exc)
        return
    _session_secrets.get(plugin_id, {}).pop(key, None)
    _state.setdefault(plugin_id, {}).pop("health", None)
    metadata = _ensure_secret_metadata()
    metadata.setdefault("plugins", {}).setdefault(plugin_id, {})[key] = {
        "configured": True,
        "fingerprint": secret_store.fingerprint(value),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state()
    _save_secrets()


@_locked_state
def delete_plugin_secret(plugin_id: str, key: str) -> None:
    _ensure_loaded()
    plugin_id = str(plugin_id)
    key = str(key)
    _state.setdefault(plugin_id, {}).pop("health", None)
    try:
        secret_store.delete_secret(_secret_name(plugin_id, key), namespace=_SECRETS_NAMESPACE)
    except secret_store.SecretStoreError:
        pass
    _session_secrets.get(plugin_id, {}).pop(key, None)
    if _is_legacy_secrets_file(_secrets):
        plugin_secrets = _secrets.get(plugin_id, {})
        if isinstance(plugin_secrets, dict):
            plugin_secrets.pop(key, None)
        _save_state()
        _save_secrets()
        return
    metadata = _ensure_secret_metadata()
    plugin_meta = metadata.setdefault("plugins", {}).get(plugin_id, {})
    if isinstance(plugin_meta, dict):
        plugin_meta.pop(key, None)
    _save_state()
    _save_secrets()


@_locked_state
def get_all_plugin_secrets(plugin_id: str) -> dict[str, str]:
    _ensure_loaded()
    plugin_id = str(plugin_id)
    if _is_legacy_secrets_file(_secrets):
        values = {
            str(k): str(v)
            for k, v in _secrets.get(plugin_id, {}).items()
            if isinstance(v, str) and v
        }
    else:
        values = {}
        for key in _metadata_secret_keys(plugin_id):
            value = get_plugin_secret(plugin_id, key)
            if value:
                values[key] = value
    values.update(_session_secrets.get(plugin_id, {}))
    return values


# ── Cleanup ──────────────────────────────────────────────────────────────────
@_locked_state
def remove_plugin_state(plugin_id: str) -> None:
    """Remove all state and secrets for a plugin (on uninstall)."""
    _ensure_loaded()
    plugin_id = str(plugin_id)
    for key in set(get_all_plugin_secrets(plugin_id)) | set(_metadata_secret_keys(plugin_id)):
        try:
            secret_store.delete_secret(_secret_name(plugin_id, key), namespace=_SECRETS_NAMESPACE)
        except secret_store.SecretStoreError:
            pass
    _state.pop(plugin_id, None)
    try:
        from row_bot.channels import registry as channel_registry

        channel_registry.unregister_plugin_channels(plugin_id)
        from row_bot.plugins.webhooks import unregister_plugin_webhooks

        unregister_plugin_webhooks(plugin_id)
    except Exception:
        logger.debug("Plugin runtime unregister skipped for %s", plugin_id, exc_info=True)
    _secrets.pop(plugin_id, None)
    if not _is_legacy_secrets_file(_secrets):
        metadata = _ensure_secret_metadata()
        plugins = metadata.setdefault("plugins", {})
        if isinstance(plugins, dict):
            plugins.pop(plugin_id, None)
    _session_secrets.pop(plugin_id, None)
    _save_state()
    _save_secrets()
    _invalidate_agent_cache()


def _secret_name(plugin_id: str, key: str) -> str:
    return f"{plugin_id}:{key}"


def _empty_secret_metadata() -> dict[str, Any]:
    return {
        "version": _SECRETS_VERSION,
        "storage": "keyring",
        "service": secret_store.SERVICE_NAME,
        "plugins": {},
    }


def _is_legacy_secrets_file(data: dict[str, Any]) -> bool:
    return bool(data) and data.get("version") != _SECRETS_VERSION


def _ensure_secret_metadata() -> dict[str, Any]:
    global _secrets
    if _is_legacy_secrets_file(_secrets):
        _secrets = _empty_secret_metadata()
    if not _secrets:
        _secrets = _empty_secret_metadata()
    _secrets.setdefault("version", _SECRETS_VERSION)
    _secrets.setdefault("storage", "keyring")
    _secrets["service"] = secret_store.SERVICE_NAME
    _secrets.setdefault("plugins", {})
    return _secrets


def _secret_metadata_entry(plugin_id: str, key: str) -> dict[str, Any]:
    plugins = _secrets.get("plugins", {}) if isinstance(_secrets, dict) else {}
    plugin_meta = plugins.get(plugin_id, {}) if isinstance(plugins, dict) else {}
    entry = plugin_meta.get(key) if isinstance(plugin_meta, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def _metadata_secret_keys(plugin_id: str) -> list[str]:
    plugins = _secrets.get("plugins", {}) if isinstance(_secrets, dict) else {}
    plugin_meta = plugins.get(plugin_id, {}) if isinstance(plugins, dict) else {}
    if not isinstance(plugin_meta, dict):
        return []
    return [str(k) for k, v in plugin_meta.items() if isinstance(v, dict) and v.get("configured")]


def _migrate_legacy_secrets(data: dict[str, Any]) -> bool:
    legacy: dict[str, dict[str, str]] = {}
    for plugin_id, plugin_values in data.items():
        if not isinstance(plugin_values, dict):
            continue
        values = {str(k): str(v) for k, v in plugin_values.items() if isinstance(v, str) and v}
        if values:
            legacy[str(plugin_id)] = values
    if not legacy:
        _write_json(_SECRETS_PATH, _empty_secret_metadata(), restricted=True)
        return True
    try:
        for plugin_id, values in legacy.items():
            for key, value in values.items():
                secret_store.set_secret(_secret_name(plugin_id, key), value, namespace=_SECRETS_NAMESPACE)
        for plugin_id, values in legacy.items():
            for key, value in values.items():
                stored = secret_store.get_secret(_secret_name(plugin_id, key), namespace=_SECRETS_NAMESPACE)
                if stored != value:
                    raise secret_store.SecretStoreError(f"failed to verify {plugin_id}/{key}")
    except secret_store.SecretStoreError as exc:
        logger.warning("Failed to migrate plugin secrets to keyring; keeping legacy plugin_secrets.json: %s", exc)
        return False
    metadata = _empty_secret_metadata()
    for plugin_id, values in legacy.items():
        plugin_meta = metadata.setdefault("plugins", {}).setdefault(plugin_id, {})
        for key, value in values.items():
            plugin_meta[key] = {
                "configured": True,
                "fingerprint": secret_store.fingerprint(value),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    _write_json(_SECRETS_PATH, metadata, restricted=True)
    return True


# ── Cache Invalidation ───────────────────────────────────────────────────────
def _invalidate_agent_cache():
    if os.environ.get("ROW_BOT_TEST_MODE") == "1":
        return
    try:
        from row_bot.agent import clear_agent_cache
        clear_agent_cache()
    except ImportError:
        pass


# ── Reset (for testing) ─────────────────────────────────────────────────────
@_locked_state
def _reset():
    """Reset in-memory caches. For testing only."""
    global _state, _secrets, _session_secrets, _loaded
    _state = {}
    _secrets = {}
    _session_secrets = {}
    _loaded = False
