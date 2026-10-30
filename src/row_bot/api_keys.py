import logging
import os
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

import row_bot.secret_store as secret_store
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# Store data in the active Row-Bot data directory.
DATA_DIR = get_row_bot_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

KEYS_PATH = DATA_DIR / "api_keys.json"
_METADATA_VERSION = 2
_session_keys: dict[str, str] = {}
# Saved keys read by apply_keys, held in this process only: never in its environment, which every program it
# starts (shell commands, Developer Studio commands, MCP servers) would inherit.
_loaded_keys: dict[str, str] = {}
_last_storage_warning = ""
# Row-Bot's own secrets in its environment: the launcher's shared secret, the server secret store's key, the
# updater's token and a host's setup code (H1; `row-bot serve` also takes that one out at start).
ROW_BOT_PROCESS_SECRETS = frozenset(
    {"ROW_BOT_LAUNCH_SECRET", "ROW_BOT_SECRET_STORE_KEY", "ROW_BOT_UPDATER_TOKEN", "ROW_BOT_SETUP_CODE"}
)
# Tokens Row-Bot reads from its environment besides the saved keys.
_OTHER_KEY_NAMES = frozenset({"GITHUB_TOKEN", "GH_TOKEN", "NGROK_AUTHTOKEN"})

# All API keys the app can use – label shown in UI → env-var name
API_KEY_DEFINITIONS = {
    "Tavily": "TAVILY_API_KEY",
}

# Telegram Bot credentials – stored the same way but managed
# in the Channels settings tab rather than the API-Keys tab.
TELEGRAM_KEY_DEFINITIONS = {
    "Telegram Bot Token": "TELEGRAM_BOT_TOKEN",
    "Telegram User ID": "TELEGRAM_USER_ID",
}

# OpenRouter credentials – managed in the Cloud settings tab.
OPENROUTER_KEY_DEFINITIONS = {
    "OpenRouter API Key": "OPENROUTER_API_KEY",
}

# OpenAI direct API credentials – managed in the Cloud settings tab.
OPENAI_KEY_DEFINITIONS = {
    "OpenAI API Key": "OPENAI_API_KEY",
}

# Anthropic (Claude) API credentials – managed in the Cloud settings tab.
ANTHROPIC_KEY_DEFINITIONS = {
    "Anthropic API Key": "ANTHROPIC_API_KEY",
}

# Google AI (Gemini) API credentials – managed in the Cloud settings tab.
GOOGLE_KEY_DEFINITIONS = {
    "Google AI API Key": "GOOGLE_API_KEY",
}

# xAI (Grok) API credentials – managed in the Cloud settings tab.
XAI_KEY_DEFINITIONS = {
    "xAI API Key": "XAI_API_KEY",
}

# MiniMax API credentials – managed in the Providers settings tab.
MINIMAX_KEY_DEFINITIONS = {
    "MiniMax API Key": "MINIMAX_API_KEY",
}

# Atlas Cloud API credentials – managed in the Providers settings tab.
ATLASCLOUD_KEY_DEFINITIONS = {
    "Atlas Cloud API Key": "ATLASCLOUD_API_KEY",
}

# ── Cloud provider configuration ────────────────────────────────────────────
_CLOUD_CONFIG_PATH = DATA_DIR / "cloud_config.json"

_DEFAULT_CLOUD_CONFIG: dict[str, object] = {
    "starred_models": [],
}


def get_cloud_config() -> dict:
    """Return cloud privacy settings (fills in defaults for missing keys)."""
    cfg = dict(_DEFAULT_CLOUD_CONFIG)
    if _CLOUD_CONFIG_PATH.exists():
        try:
            with open(_CLOUD_CONFIG_PATH, "r") as f:
                stored = json.load(f)
            cfg.update(stored)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load cloud config from %s", _CLOUD_CONFIG_PATH, exc_info=True)
    return cfg


def set_cloud_config(key: str, value: object) -> None:
    """Update a single cloud config key and persist."""
    cfg = get_cloud_config()
    cfg[key] = value
    try:
        with open(_CLOUD_CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        logger.warning("Failed to save cloud config to %s", _CLOUD_CONFIG_PATH, exc_info=True)


def _load_keys() -> dict[str, str]:
    """Load saved keys. Returns {env_var: value} for compatibility callers."""
    metadata = _read_key_file()
    if _is_legacy_key_file(metadata):
        if _migrate_legacy_keys(metadata):
            metadata = _read_key_file()
        else:
            return {str(k): str(v) for k, v in metadata.items() if isinstance(v, str) and v}

    keys: dict[str, str] = {}
    for env_var in _metadata_keys(metadata):
        value = _get_stored_key(env_var)
        if value:
            keys[env_var] = value
    keys.update(_session_keys)
    return keys


def _save_keys(keys: dict[str, str]):
    """Persist a complete key mapping using secure storage when available."""
    existing = set(_load_keys()) | set(_metadata_keys(_read_key_file())) | set(_session_keys)
    wanted = {str(k): str(v) for k, v in keys.items() if isinstance(v, str) and v}
    for env_var in existing - set(wanted):
        delete_key(env_var)
    for env_var, value in wanted.items():
        set_key(env_var, value)


def get_key(env_var: str) -> str:
    """Return a key value (empty string if not set): a saved key first, then the environment's."""
    env_var = str(env_var)
    if env_var in _session_keys:
        return _session_keys[env_var]
    if _loaded_keys.get(env_var):
        return _loaded_keys[env_var]
    if os.environ.get(env_var):
        return os.environ[env_var]
    value = _get_stored_key(env_var)
    if value:
        return value
    legacy = _legacy_plaintext_keys()
    return legacy.get(env_var, "")


def set_key(env_var: str, value: str):
    """Save a single key; this process reads it at once, programs it starts never see it."""
    env_var = str(env_var)
    value = str(value or "").strip()
    if not value:
        delete_key(env_var)
        return
    try:
        secret_store.set_secret(env_var, value)
        _session_keys.pop(env_var, None)
        _write_metadata_key(env_var, value)
        _clear_storage_warning()
    except secret_store.SecretStoreError as exc:
        _session_keys[env_var] = value
        _set_storage_warning(f"Secure API key storage is unavailable; {env_var} is saved for this session only.")
        logger.warning("Using session-only API key storage for %s: %s", env_var, exc)
        secret_store.notify_change("api_keys")
    _loaded_keys[env_var] = value


def set_key_for_data_dir(data_dir: pathlib.Path | str, env_var: str, value: str) -> None:
    """Save a key for an explicit Row-Bot data directory.

    Used by migration when importing into a target profile that may differ from
    the currently running app profile. This never falls back to plaintext.
    """
    env_var = str(env_var)
    value = str(value or "").strip()
    if not value:
        return
    target_dir = pathlib.Path(data_dir)
    target_path = target_dir / "api_keys.json"
    service = secret_store.service_name_for(target_dir)
    secret_store.set_secret(env_var, value, service=service)
    _write_metadata_key_at(target_path, service, env_var, value)


def get_key_for_data_dir(data_dir: pathlib.Path | str, env_var: str) -> str:
    """Return a key stored for an explicit Row-Bot data directory."""
    service = secret_store.service_name_for(pathlib.Path(data_dir))
    try:
        return secret_store.get_secret(str(env_var), service=service) or ""
    except secret_store.SecretStoreError:
        return ""


def delete_key(env_var: str) -> None:
    """Remove a saved key from secure storage, metadata and this process."""
    env_var = str(env_var)
    try:
        secret_store.delete_secret(env_var)
    except secret_store.SecretStoreError:
        pass
    _session_keys.pop(env_var, None)
    _loaded_keys.pop(env_var, None)
    _remove_metadata_key(env_var)
    secret_store.notify_change("api_keys")


def apply_keys():
    """Read all saved keys into this process. Never into its environment, which child programs inherit."""
    _loaded_keys.clear()
    _loaded_keys.update({env_var: value for env_var, value in _load_keys().items() if value})


def saved_key_names() -> set[str]:
    """Names of every key Row-Bot saves or reads from its environment; never values.

    Used to keep keys out of programs started for the person, such as their
    own terminal app, and for the agent. Reads metadata only, never the keyring.
    """
    names = {str(name) for name in _metadata_keys(_read_key_file())}
    names.update(_legacy_plaintext_keys())
    names.update(_session_keys)
    names.update(_loaded_keys)
    names.update(_OTHER_KEY_NAMES)
    for definitions in (API_KEY_DEFINITIONS, TELEGRAM_KEY_DEFINITIONS, OPENROUTER_KEY_DEFINITIONS,
                        OPENAI_KEY_DEFINITIONS, ANTHROPIC_KEY_DEFINITIONS, GOOGLE_KEY_DEFINITIONS,
                        XAI_KEY_DEFINITIONS, MINIMAX_KEY_DEFINITIONS, ATLASCLOUD_KEY_DEFINITIONS):
        names.update(definitions.values())
    from row_bot.channels.passive_catalog import core_channel_secret_names
    from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV

    names.update(PROVIDER_API_KEY_ENV.values())
    names.update(core_channel_secret_names())
    # Tools and channels loaded in this process declare their own keys.
    tools = sys.modules.get("row_bot.tools.registry")
    if tools is not None:
        names.update(tools.get_all_required_api_keys().values())
    channels = sys.modules.get("row_bot.channels.registry")
    if channels is not None:
        names.update(str(getattr(field, "env_key", "") or "") for channel in channels.all_channels()
                     for field in getattr(channel, "config_fields", []) or [] if getattr(field, "storage", "") == "env")
    names.discard("")
    return names


def child_environment(environ: Mapping[str, str] | None = None, *, keep: Iterable[str] = ()) -> dict[str, str]:
    """The environment for a program started for the agent: a shell or Developer Studio command, an MCP server.

    This process's environment without the keys Row-Bot saves or reads (an API key set for a self-hosted
    install, a channel token) or its own secrets, so the program can't read them. ``keep`` names ones to pass
    on anyway: an MCP server's explicit opt-in.
    """
    source = os.environ if environ is None else environ
    hidden = {name.upper() for name in saved_key_names() | ROW_BOT_PROCESS_SECRETS}
    hidden -= {str(name).upper() for name in keep}
    env = {name: value for name, value in source.items() if name.upper() not in hidden}
    return _with_managed_ffmpeg(env)


def _with_managed_ffmpeg(env: dict[str, str]) -> dict[str, str]:
    """The ffmpeg Row-Bot downloaded, on the program's PATH, so a command can use it too (5.3.1 A1).

    Only the reviewed program, checked against its fingerprint before each use; the person's own
    ffmpeg on PATH comes first.
    """
    try:
        from row_bot import ffmpeg_runtime

        managed = ffmpeg_runtime.installed()
    except Exception:
        managed = None
    if managed is None:
        return env
    key = next((name for name in env if name.upper() == "PATH"), "PATH")
    folder = os.path.dirname(managed.executable)
    current = env.get(key, "")
    if folder not in current.split(os.pathsep):
        env[key] = os.pathsep.join(part for part in (current, folder) if part)
    return env


def key_status(env_var: str) -> dict[str, Any]:
    """Return display-safe storage status for a key."""
    env_var = str(env_var)
    metadata = _read_key_file()
    entry = _metadata_entry(metadata, env_var)
    if env_var in _session_keys:
        return {"configured": True, "source": "session", "fingerprint": secret_store.fingerprint(_session_keys[env_var])}
    if entry:
        value = _get_stored_key(env_var)
        configured = bool(value) or bool(entry.get("configured"))
        return {
            "configured": configured,
            "source": "keyring",
            "fingerprint": entry.get("fingerprint", ""),
            "updated_at": entry.get("updated_at", ""),
        }
    legacy = _legacy_plaintext_keys()
    if legacy.get(env_var):
        return {"configured": True, "source": "legacy_plaintext", "fingerprint": secret_store.fingerprint(legacy[env_var])}
    if os.environ.get(env_var):
        return {"configured": True, "source": "environment", "fingerprint": secret_store.fingerprint(os.environ[env_var])}
    return {"configured": False, "source": "", "fingerprint": ""}


def is_secure_storage_available() -> bool:
    """Return True if the OS keyring can accept secrets."""
    return secret_store.is_available()


def get_storage_warning() -> str:
    """Return the last non-fatal storage warning, if any."""
    return _last_storage_warning


def migrate_legacy_keys() -> bool:
    """Import legacy plaintext api_keys.json into the OS keyring."""
    data = _read_key_file()
    return _migrate_legacy_keys(data) if _is_legacy_key_file(data) else True


def _read_key_file() -> dict[str, Any]:
    if not KEYS_PATH.exists():
        return _empty_metadata()
    try:
        with open(KEYS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else _empty_metadata()
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load API keys from %s", KEYS_PATH, exc_info=True)
        return _empty_metadata()


def _empty_metadata() -> dict[str, Any]:
    return {"version": _METADATA_VERSION, "storage": "keyring", "service": secret_store.SERVICE_NAME, "keys": {}}


def _is_legacy_key_file(data: dict[str, Any]) -> bool:
    return bool(data) and data.get("version") != _METADATA_VERSION and all(isinstance(v, str) for v in data.values())


def _metadata_keys(data: dict[str, Any]) -> list[str]:
    keys = data.get("keys", {}) if isinstance(data, dict) else {}
    if not isinstance(keys, dict):
        return []
    return [str(k) for k, v in keys.items() if isinstance(v, dict) and v.get("configured")]


def _metadata_entry(data: dict[str, Any], env_var: str) -> dict[str, Any]:
    keys = data.get("keys", {}) if isinstance(data, dict) else {}
    entry = keys.get(env_var) if isinstance(keys, dict) else None
    return dict(entry) if isinstance(entry, dict) else {}


def _write_metadata(metadata: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(KEYS_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def _write_metadata_key(env_var: str, value: str) -> None:
    _write_metadata_key_at(KEYS_PATH, secret_store.SERVICE_NAME, env_var, value)


def _write_metadata_key_at(path: pathlib.Path, service: str, env_var: str, value: str) -> None:
    metadata = _read_key_file()
    if path != KEYS_PATH:
        metadata = _read_key_file_at(path)
    if _is_legacy_key_file(metadata):
        metadata = _empty_metadata()
    metadata.setdefault("version", _METADATA_VERSION)
    metadata.setdefault("storage", "keyring")
    metadata["service"] = service
    metadata.setdefault("keys", {})[env_var] = {
        "configured": True,
        "fingerprint": secret_store.fingerprint(value),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_metadata_at(path, metadata)


def _remove_metadata_key(env_var: str) -> None:
    metadata = _read_key_file()
    if _is_legacy_key_file(metadata):
        return
    keys = metadata.setdefault("keys", {})
    if isinstance(keys, dict):
        keys.pop(env_var, None)
    _write_metadata(metadata)


def _get_stored_key(env_var: str) -> str:
    try:
        metadata = _read_key_file()
        service = metadata.get("service") if isinstance(metadata, dict) else ""
        if isinstance(service, str) and service and service != secret_store.SERVICE_NAME:
            value = secret_store.get_secret(env_var, service=service)
            if value:
                return value
        return secret_store.get_secret(env_var) or ""
    except secret_store.SecretStoreError:
        _set_storage_warning("Secure API key storage is unavailable. legacy plaintext keys may still be used until secure storage works.")
        return ""


def _read_key_file_at(path: pathlib.Path) -> dict[str, Any]:
    if path == KEYS_PATH:
        return _read_key_file()
    if not path.exists():
        return _empty_metadata()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else _empty_metadata()
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to load API key metadata from %s", path, exc_info=True)
        return _empty_metadata()


def _write_metadata_at(path: pathlib.Path, metadata: dict[str, Any]) -> None:
    if path == KEYS_PATH:
        _write_metadata(metadata)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def _legacy_plaintext_keys() -> dict[str, str]:
    data = _read_key_file()
    if _is_legacy_key_file(data):
        return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}
    return {}


def _migrate_legacy_keys(data: dict[str, Any]) -> bool:
    if not _is_legacy_key_file(data):
        return True
    legacy = {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}
    if not legacy:
        _write_metadata(_empty_metadata())
        return True
    try:
        for env_var, value in legacy.items():
            secret_store.set_secret(env_var, value)
        for env_var, value in legacy.items():
            if secret_store.get_secret(env_var) != value:
                raise secret_store.SecretStoreError(f"failed to verify {env_var}")
    except secret_store.SecretStoreError as exc:
        _set_storage_warning("Secure API key storage is unavailable; legacy plaintext api_keys.json is still in use.")
        logger.warning("Failed to migrate legacy API keys to keyring: %s", exc)
        return False
    metadata = _empty_metadata()
    for env_var, value in legacy.items():
        metadata["keys"][env_var] = {
            "configured": True,
            "fingerprint": secret_store.fingerprint(value),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    _write_metadata(metadata)
    _clear_storage_warning()
    return True


def _set_storage_warning(message: str) -> None:
    global _last_storage_warning
    _last_storage_warning = message


def _clear_storage_warning() -> None:
    global _last_storage_warning
    _last_storage_warning = ""
