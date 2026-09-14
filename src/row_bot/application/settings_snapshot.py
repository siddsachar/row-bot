"""Passive saved-state snapshot for Settings surfaces without a client owner.

The richer Settings capabilities keep their existing bounded read/review/execute
owners.  This module only projects local, already-saved state used by the
NiceGUI Voice, System, Tracker, Documents, Tools, Accounts, Utilities, and
Preferences panes.  It never starts a runtime, refreshes a token, probes a
provider, or imports plugin entrypoints.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir

_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_ITEMS = 128

_UTILITY_PRESENTATION = {
    "task": ("Tasks", "Create and manage scheduled or one-off tasks."),
    "timer": ("Timer", "Set lightweight local timers."),
    "url_reader": ("URL Reader", "Read explicitly requested web pages."),
    "calculator": ("Calculator", "Evaluate calculations locally."),
    "weather": ("Weather", "Look up weather when explicitly requested."),
    "chart": ("Charts", "Create charts from supplied data."),
    "system_info": ("System Info", "Read bounded host information."),
    "conversation_search": ("Conversation Search", "Search saved conversations."),
    "custom_tool_builder": ("Custom Tool Builder", "Build reviewed local tools."),
}
_SEARCH_TOOL_PRESENTATION = {
    "web_search": "Web Search",
    "duckduckgo": "DuckDuckGo",
    "wolfram_alpha": "Wolfram Alpha",
    "arxiv": "arXiv",
    "wikipedia": "Wikipedia",
    "youtube": "YouTube",
}
_TOOL_CREDENTIALS = {
    "web_search": (("Tavily API Key", "TAVILY_API_KEY"),),
    "wolfram_alpha": (("Wolfram Alpha App ID", "WOLFRAM_ALPHA_APPID"),),
}
_WHISPER_OPTIONS = {
    "tiny": "Tiny (~39 MB, fastest)",
    "base": "Base (~74 MB, balanced)",
    "small": "Small (~244 MB, accurate)",
    "medium": "Medium (~769 MB, best accuracy)",
}
_REALTIME_VOICE_OPTIONS = {
    "alloy": "Alloy",
    "ash": "Ash",
    "ballad": "Ballad",
    "coral": "Coral",
    "echo": "Echo",
    "sage": "Sage",
    "shimmer": "Shimmer",
    "verse": "Verse",
    "marin": "Marin",
    "cedar": "Cedar",
}
_TTS_VOICE_OPTIONS = {
    "af_heart": "Heart (American Female)",
    "af_bella": "Bella (American Female)",
    "af_nicole": "Nicole (American Female)",
    "af_sarah": "Sarah (American Female)",
    "af_nova": "Nova (American Female)",
    "am_michael": "Michael (American Male)",
    "am_fenrir": "Fenrir (American Male)",
    "am_puck": "Puck (American Male)",
    "bf_emma": "Emma (British Female)",
    "bm_george": "George (British Male)",
}
_LOCAL_EMBEDDING_OPTIONS = {
    "qwen3-0.6b": "Qwen3 0.6B",
    "nomic-v1.5": "Nomic Embed Text v1.5",
    "mxbai-large-v1": "Mixedbread Embed Large v1",
}
_CLOUD_EMBEDDING_OPTIONS = {
    "openai:text-embedding-3-small": "OpenAI text-embedding-3-small",
    "openai:text-embedding-3-large": "OpenAI text-embedding-3-large",
    "google:gemini-embedding-001": "Google Gemini Embedding",
}
_FILESYSTEM_OPERATIONS = (
    "read_file",
    "list_directory",
    "file_search",
    "write_file",
    "copy_file",
    "export_to_pdf",
    "move_file",
    "file_delete",
)
_GMAIL_DEFAULT_OPERATIONS = (
    "search_gmail",
    "get_gmail_message",
    "get_gmail_thread",
    "create_gmail_draft",
)
_CALENDAR_DEFAULT_OPERATIONS = (
    "get_current_datetime",
    "search_events",
    "create_calendar_event",
    "create_calendar_events",
    "update_calendar_event",
)
_X_READ_OPERATIONS = (
    "x_search",
    "x_read_tweet",
    "x_timeline",
    "x_mentions",
    "x_user_info",
)
_X_POST_OPERATIONS = ("x_post_tweet", "x_reply", "x_quote", "x_delete_tweet")
_X_ENGAGE_OPERATIONS = (
    "x_like",
    "x_unlike",
    "x_repost",
    "x_unrepost",
    "x_bookmark",
    "x_unbookmark",
)


def _read_json(path: Path, *, default: Any) -> Any:
    """Read one bounded JSON file and fail closed to the supplied default."""

    try:
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return default
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return default
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, maximum: int = 2048) -> str:
    return str(value or "")[:maximum]


def _strings(value: Any, *, allowed: tuple[str, ...] | None = None) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    items = [_text(item, 256) for item in value[:_MAX_ITEMS] if isinstance(item, str)]
    return [item for item in items if allowed is None or item in allowed]


def _choice_rows(values: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"value": key, "label": label} for key, label in values.items()]


def _local_path_is_file(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if normalized.startswith("//") or "://" in normalized:
        return False
    try:
        return Path(value).is_file()
    except OSError:
        return False


def _local_path_is_dir(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if normalized.startswith("//") or "://" in normalized:
        return False
    try:
        return Path(value).is_dir()
    except OSError:
        return False


def _credential_status(name: str) -> dict[str, Any]:
    """Return display-safe credential metadata; never return the credential."""

    try:
        from row_bot.api_keys import key_status

        status = key_status(name)
    except Exception:
        status = {}
    return {
        "configured": status.get("configured") is True,
        "source": _text(status.get("source") or "none", 64),
        "fingerprint": _text(status.get("fingerprint"), 128),
    }


def _tool_documents(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw = _mapping(_read_json(root / "tools_config.json", default={}))
    tools = (
        _mapping(raw.get("tools"))
        if isinstance(raw.get("tools"), Mapping)
        else {
            str(key): value
            for key, value in raw.items()
            if key not in {"tool_configs", "global"}
        }
    )
    return tools, _mapping(raw.get("tool_configs")), _mapping(raw.get("global"))


def _registered_tools() -> dict[str, dict[str, Any]]:
    """Inspect already-registered static metadata without constructing tools."""

    try:
        from row_bot.tools import registry

        records = registry.get_passive_tool_records()
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw in records[:_MAX_ITEMS]:
        if not isinstance(raw, Mapping):
            continue
        identity = _text(raw.get("id"), 256)
        if identity:
            result[identity] = dict(raw)
    return result


def _enabled(
    tool_id: str,
    saved: Mapping[str, Any],
    registered: Mapping[str, Mapping[str, Any]],
) -> bool | None:
    if isinstance(saved.get(tool_id), bool):
        return bool(saved[tool_id])
    value = registered.get(tool_id, {}).get("enabled")
    return value if isinstance(value, bool) else None


def _token_state(path: Path) -> str:
    """Classify an OAuth token from local metadata without refreshing it."""

    if not path.is_file():
        return "not_authenticated"
    raw = _read_json(path, default=None)
    if not isinstance(raw, Mapping):
        return "unavailable"
    expiry = raw.get("expiry") or raw.get("expires_at")
    if isinstance(expiry, str) and expiry:
        try:
            normalized = expiry.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed <= datetime.now(timezone.utc):
                return "expired"
        except ValueError:
            return "unavailable"
    return "saved_unchecked"


def _voice(root: Path) -> dict[str, Any]:
    runtime = _mapping(_read_json(root / "voice_runtime_settings.json", default={}))
    local = _mapping(_read_json(root / "voice_settings.json", default={}))
    tts = _mapping(_read_json(root / "tts_settings.json", default={}))
    whisper = _text(local.get("whisper_model") or "small", 32)
    if whisper not in _WHISPER_OPTIONS:
        whisper = "small"
    realtime_voice = _text(runtime.get("realtime_voice") or "marin", 32)
    if realtime_voice not in _REALTIME_VOICE_OPTIONS:
        realtime_voice = "marin"
    tts_voice = _text(tts.get("voice") or "af_heart", 32)
    if tts_voice not in _TTS_VOICE_OPTIONS:
        tts_voice = "af_heart"
    try:
        speed = max(0.5, min(2.0, float(tts.get("speed", 1.0))))
    except (TypeError, ValueError):
        speed = 1.0
    return {
        "availability": "available",
        "runtime": {
            "talk_provider": _text(runtime.get("talk_provider") or "local", 64),
            "talk_model": _text(runtime.get("talk_model") or "local-whisper", 128),
            "dictation_provider": _text(
                runtime.get("dictation_provider") or "local", 64
            ),
            "dictation_model": _text(
                runtime.get("dictation_model") or "local-whisper", 128
            ),
            "speech_output_provider": _text(
                runtime.get("speech_output_provider") or "local", 64
            ),
            "speech_output_model": _text(
                runtime.get("speech_output_model") or "local-kokoro", 128
            ),
            "speech_output_voice": _text(
                runtime.get("speech_output_voice") or tts_voice, 64
            ),
            "realtime_voice": realtime_voice,
            "captions_enabled": runtime.get("captions_enabled") is not False,
            "talk_auto_start": runtime.get("talk_auto_start") is True,
            "realtime_fallback_to_local": runtime.get("realtime_fallback_to_local")
            is not False,
        },
        "local": {
            "whisper_model": whisper,
            "sensevoice_path_configured": bool(
                _text(local.get("sensevoice_model_path"))
            ),
            "runtime_state": "cached_unknown",
        },
        "tts": {
            "installed": (root / "kokoro" / "kokoro-v1.0.fp16.onnx").is_file()
            and (root / "kokoro" / "voices-v1.0.bin").is_file(),
            "enabled": tts.get("enabled") is True,
            "voice": tts_voice,
            "speed": speed,
            "auto_speak": tts.get("auto_speak") is not False,
        },
        "openai_realtime_credential": _credential_status("OPENAI_API_KEY"),
        "talk_providers": _choice_rows(
            {"local": "Local", "openai_realtime": "OpenAI Realtime"}
        ),
        "dictation_providers": _choice_rows({"local": "Local"}),
        "speech_output_providers": _choice_rows({"local": "Local"}),
        "whisper_options": _choice_rows(_WHISPER_OPTIONS),
        "realtime_voice_options": _choice_rows(_REALTIME_VOICE_OPTIONS),
        "tts_voice_options": _choice_rows(_TTS_VOICE_OPTIONS),
    }


def _system(
    root: Path,
    tools: Mapping[str, Any],
    tool_configs: Mapping[str, Any],
    registered: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    filesystem = _mapping(tool_configs.get("filesystem"))
    shell = _mapping(tool_configs.get("shell"))
    routes = _mapping(_read_json(root / "access_routes.json", default={}))
    channel_config = _mapping(_read_json(root / "channels_config.json", default={}))
    tunnel = _mapping(channel_config.get("tunnel"))
    user = _mapping(_read_json(root / "user_config.json", default={}))
    cua = _mapping(_read_json(root / "computer_use_settings.json", default={}))
    log_level = _text(user.get("file_log_level") or "DEBUG", 16).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        log_level = "DEBUG"
    workspace = _text(filesystem.get("workspace_root"), 4096)
    selected_ops = _strings(
        filesystem.get("selected_operations"), allowed=_FILESYSTEM_OPERATIONS
    )
    if not selected_ops:
        selected_ops = list(_FILESYSTEM_OPERATIONS)
    listen_mode = _text(routes.get("listen_mode") or "local_only", 32)
    if listen_mode not in {"local_only", "local_network"}:
        listen_mode = "local_only"
    configured_origins = _strings(routes.get("configured_origins"))
    return {
        "availability": "available",
        "workspace": {
            "path": workspace,
            "configured": bool(workspace),
            "exists": bool(workspace and _local_path_is_dir(workspace)),
        },
        "shell": {
            "available": "shell" in registered or "shell" in tools,
            "enabled": _enabled("shell", tools, registered),
            "blocked_patterns": _text(shell.get("blocked_commands"), 4096),
        },
        "browser": {
            "available": "browser" in registered or "browser" in tools,
            "enabled": _enabled("browser", tools, registered),
            "runtime_state": "cached_unknown",
        },
        "computer_use": {
            "available": "computer_use" in registered or "computer_use" in tools,
            "enabled": _enabled("computer_use", tools, registered),
            "disclosure_acknowledged": int(cua.get("acknowledged_notice_version") or 0)
            == 2,
            "system_binary_configured": cua.get("allow_system_cua") is True
            and bool(_text(cua.get("system_cua_path"))),
            "runtime_state": "cached_unknown",
        },
        "file_operations": {
            "available": "filesystem" in registered or "filesystem" in tools,
            "enabled": _enabled("filesystem", tools, registered),
            "selected": selected_ops,
            "options": list(_FILESYSTEM_OPERATIONS),
        },
        "tunnel": {
            "provider": _text(tunnel.get("provider") or "ngrok", 64),
            "credential": _credential_status("NGROK_AUTHTOKEN"),
            "runtime_state": "not_checked",
            "active_count": None,
        },
        "remote_access": {
            "listen_mode": listen_mode,
            "configured_origins": configured_origins,
            "host_admission_managed_externally": "ROW_BOT_ALLOWED_HOSTS" in os.environ,
            "tailscale_state": "not_checked",
        },
        "mobile_access": _mobile_access(root),
        "logging": {
            "level": log_level,
            "directory": str(root / "logs"),
        },
    }


def _mobile_access(root: Path) -> dict[str, Any]:
    path = root / "mobile.db"
    if not path.is_file():
        return {"availability": "missing", "active_devices": 0, "active_sessions": 0}
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            connection.execute("PRAGMA query_only = ON")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not {"access_devices", "access_sessions"}.issubset(tables):
                return {
                    "availability": "unavailable",
                    "active_devices": 0,
                    "active_sessions": 0,
                }
            devices = int(
                connection.execute(
                    "SELECT COUNT(*) FROM access_devices WHERE revoked_at IS NULL"
                ).fetchone()[0]
            )
            sessions = int(
                connection.execute(
                    "SELECT COUNT(*) FROM access_sessions WHERE revoked_at IS NULL"
                ).fetchone()[0]
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return {
            "availability": "unavailable",
            "active_devices": 0,
            "active_sessions": 0,
        }
    return {
        "availability": "available",
        "active_devices": max(0, devices),
        "active_sessions": max(0, sessions),
    }


def _tracker(
    root: Path,
    tools: Mapping[str, Any],
    registered: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    result = {
        "availability": "missing",
        "tool_available": "tracker" in registered or "tracker" in tools,
        "enabled": _enabled("tracker", tools, registered),
        "items": [],
        "total_entries": 0,
    }
    path = root / "tracker" / "tracker.db"
    if not path.is_file():
        return result
    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                """
                SELECT t.id, t.name, t.type, t.unit, t.icon, COUNT(e.id), MAX(e.timestamp)
                  FROM trackers AS t
             LEFT JOIN entries AS e ON e.tracker_id = t.id
              GROUP BY t.id, t.name, t.type, t.unit, t.icon
              ORDER BY t.name COLLATE NOCASE
                 LIMIT ?
                """,
                (_MAX_ITEMS,),
            ).fetchall()
            total_entries = int(
                connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        result["availability"] = "unavailable"
        return result
    result.update(
        availability="available",
        total_entries=max(0, total_entries),
        items=[
            {
                "tracker_id": _text(row[0], 128),
                "name": _text(row[1], 256),
                "kind": _text(row[2], 64),
                "unit": _text(row[3], 64) or None,
                "icon": _text(row[4], 64) or None,
                "entry_count": max(0, int(row[5] or 0)),
                "last_event_at": _text(row[6], 80) or None,
            }
            for row in rows
        ],
    )
    return result


def _documents(root: Path) -> dict[str, Any]:
    config = _mapping(_read_json(root / "embedding_config.json", default={}))
    provider = _text(config.get("provider") or "local", 16)
    if provider not in {"local", "cloud"}:
        provider = "local"
    local_model = _text(config.get("local_model") or "mxbai-large-v1", 128)
    if local_model not in _LOCAL_EMBEDDING_OPTIONS:
        local_model = "mxbai-large-v1"
    cloud_model = _text(
        config.get("cloud_model") or "openai:text-embedding-3-small", 128
    )
    if cloud_model not in _CLOUD_EMBEDDING_OPTIONS:
        cloud_model = "openai:text-embedding-3-small"
    dimension = config.get("dimension")
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
        dimension = None
    return {
        "availability": "available",
        "embedding": {
            "provider": provider,
            "local_model": local_model,
            "cloud_model": cloud_model,
            "dimension": dimension,
            "auto_unload": config.get("auto_unload") is True,
            "runtime_state": "cached_unknown",
            "local_options": _choice_rows(_LOCAL_EMBEDDING_OPTIONS),
            "cloud_options": _choice_rows(_CLOUD_EMBEDDING_OPTIONS),
        },
    }


def _tools(
    tools: Mapping[str, Any],
    tool_configs: Mapping[str, Any],
    global_config: Mapping[str, Any],
    registered: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    loading_mode = _text(global_config.get("external_tool_loading_mode") or "auto", 16)
    if loading_mode not in {"auto", "eager"}:
        loading_mode = "auto"
    compression = _text(global_config.get("compression_mode") or "off", 16)
    if compression not in {"off", "deep"}:
        compression = "off"
    items = []
    for tool_id, fallback_label in _SEARCH_TOOL_PRESENTATION.items():
        record = registered.get(tool_id, {})
        credentials = [
            {"label": label, "name": name, **_credential_status(name)}
            for label, name in _TOOL_CREDENTIALS.get(tool_id, ())
        ]
        items.append(
            {
                "tool_id": tool_id,
                "label": _text(record.get("label") or fallback_label, 256),
                "available": tool_id in registered or tool_id in tools,
                "enabled": _enabled(tool_id, tools, registered),
                "configured_fields": sorted(
                    _text(key, 128)
                    for key, value in _mapping(tool_configs.get(tool_id)).items()
                    if not str(key).startswith("_") and value not in (None, "", [])
                )[:_MAX_ITEMS],
                "credentials": credentials,
            }
        )
    return {
        "availability": "available",
        "external_loading_mode": loading_mode,
        "compression_mode": compression,
        "items": items,
    }


def _account(
    *,
    account_id: str,
    enabled: bool | None,
    configured: bool,
    authentication_state: str,
    credentials_path: str = "",
    credential: dict[str, Any] | None = None,
    operations: list[str] | None = None,
    read_operations: list[str] | None = None,
    post_operations: list[str] | None = None,
    engage_operations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "enabled": enabled,
        "configured": configured,
        "authentication_state": authentication_state,
        "credentials_path": credentials_path,
        "credential": credential,
        "operations": operations or [],
        "read_operations": read_operations or [],
        "post_operations": post_operations or [],
        "engage_operations": engage_operations or [],
    }


def _accounts(
    root: Path,
    tools: Mapping[str, Any],
    tool_configs: Mapping[str, Any],
    registered: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    github_credential = _credential_status("GITHUB_TOKEN")
    gmail = _mapping(tool_configs.get("gmail"))
    calendar = _mapping(tool_configs.get("calendar"))
    x_config = _mapping(tool_configs.get("x"))
    gmail_path = _text(
        gmail.get("credentials_path") or root / "gmail" / "credentials.json", 4096
    )
    calendar_path = _text(
        calendar.get("credentials_path") or root / "gmail" / "credentials.json", 4096
    )
    x_id = _credential_status("X_CLIENT_ID")
    x_secret = _credential_status("X_CLIENT_SECRET")
    gmail_ops = _strings(gmail.get("selected_operations")) or list(
        _GMAIL_DEFAULT_OPERATIONS
    )
    calendar_ops = _strings(calendar.get("selected_operations")) or list(
        _CALENDAR_DEFAULT_OPERATIONS
    )
    read_ops = _strings(x_config.get("read_operations")) or list(_X_READ_OPERATIONS)
    post_ops = _strings(x_config.get("post_operations")) or list(_X_POST_OPERATIONS)
    engage_ops = _strings(x_config.get("engage_operations")) or list(
        _X_ENGAGE_OPERATIONS
    )
    return {
        "availability": "available",
        "github": _account(
            account_id="github",
            enabled=None,
            configured=github_credential["configured"],
            authentication_state=(
                "configured_unchecked"
                if github_credential["configured"]
                else "not_configured"
            ),
            credential=github_credential,
        ),
        "gmail": _account(
            account_id="gmail",
            enabled=_enabled("gmail", tools, registered),
            configured=_local_path_is_file(gmail_path),
            authentication_state=_token_state(root / "gmail" / "token.json"),
            credentials_path=gmail_path,
            operations=gmail_ops,
        ),
        "calendar": _account(
            account_id="calendar",
            enabled=_enabled("calendar", tools, registered),
            configured=_local_path_is_file(calendar_path),
            authentication_state=_token_state(root / "calendar" / "token.json"),
            credentials_path=calendar_path,
            operations=calendar_ops,
        ),
        "x": _account(
            account_id="x",
            enabled=_enabled("x", tools, registered),
            configured=x_id["configured"] and x_secret["configured"],
            authentication_state=_token_state(root / "x" / "token.json"),
            credential={
                "configured": x_id["configured"] and x_secret["configured"],
                "source": x_id["source"]
                if x_id["source"] == x_secret["source"]
                else "mixed",
                "fingerprint": "configured"
                if x_id["configured"] and x_secret["configured"]
                else "",
            },
            operations=[*read_ops, *post_ops, *engage_ops],
            read_operations=read_ops,
            post_operations=post_ops,
            engage_operations=engage_ops,
        ),
    }


def _utilities(
    tools: Mapping[str, Any], registered: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    items = []
    for tool_id, (fallback_label, description) in _UTILITY_PRESENTATION.items():
        record = registered.get(tool_id, {})
        items.append(
            {
                "utility_id": tool_id,
                "label": _text(record.get("label") or fallback_label, 256),
                "description": description,
                "available": tool_id in registered or tool_id in tools,
                "enabled": _enabled(tool_id, tools, registered),
            }
        )
    return {"availability": "available", "items": items}


def _preferences(root: Path) -> dict[str, Any]:
    user = _mapping(_read_json(root / "user_config.json", default={}))
    identity = _mapping(user.get("identity"))
    app = _mapping(_read_json(root / "app_config.json", default={}))
    dream = _mapping(_read_json(root / "dream_config.json", default={}))
    journal = _read_json(root / "dream_journal.json", default=[])
    last_dream = (
        journal[-1]
        if isinstance(journal, list) and journal and isinstance(journal[-1], Mapping)
        else {}
    )
    updates = _mapping(_read_json(root / "update_config.json", default={}))
    try:
        from row_bot.version import __version__
    except Exception:
        __version__ = "unknown"
    channel = _text(updates.get("channel") or "stable", 16)
    if channel not in {"stable", "beta"}:
        channel = "stable"
    window_mode = _text(app.get("window_mode") or "ask", 16)
    if window_mode not in {"ask", "native", "browser"}:
        window_mode = "ask"
    try:
        start = max(0, min(23, int(dream.get("window_start", 1))))
        end = max(0, min(23, int(dream.get("window_end", 5))))
    except (TypeError, ValueError):
        start, end = 1, 5
    skipped = _strings(updates.get("skipped_versions"))
    return {
        "availability": "available",
        "identity": {
            "name": _text(identity.get("name") or "Row-Bot", 128),
            "personality": _text(identity.get("personality"), 200),
            "personality_max_length": 200,
            "self_improvement_enabled": identity.get("self_improvement") is not False,
        },
        "window_mode": window_mode,
        "dream_cycle": {
            "enabled": dream.get("enabled") is not False,
            "window_start": start,
            "window_end": end,
            "last_run": _text(last_dream.get("timestamp"), 80) or None,
            "last_summary": _text(last_dream.get("summary"), 2048),
        },
        "updates": {
            "current_version": _text(__version__, 64),
            "channel": channel,
            "last_check": _text(updates.get("last_check"), 80) or None,
            "last_success": _text(updates.get("last_success"), 80) or None,
            "skipped_versions": skipped,
            "runtime_state": "cached",
        },
        "migration": {"available": True, "sources": ["Hermes Agent", "OpenClaw"]},
    }


def _plugins(validate: Callable[[], None]) -> dict[str, Any]:
    """Reuse the manifest-only plugin catalog owner, never the plugin loader."""

    try:
        from row_bot.application.plugin_commands import read_plugin_catalog

        page = read_plugin_catalog(limit=50, validate=validate)
    except Exception:
        return {
            "availability": "unavailable",
            "total": 0,
            "installed": 0,
            "enabled": 0,
            "items": [],
        }
    items = [
        {
            "plugin_id": _text(item.get("plugin_id"), 128),
            "name": _text(item.get("name"), 256),
            "version": _text(item.get("version"), 64),
            "enabled": item.get("enabled") is True,
            "health": _text(item.get("health") or "unknown", 64),
        }
        for item in page.get("items", [])
        if isinstance(item, Mapping) and item.get("installed") is True
    ]
    return {
        "availability": _text(page.get("availability") or "unavailable", 32),
        "total": max(0, int(page.get("total") or 0)),
        "installed": len(items),
        "enabled": sum(1 for item in items if item["enabled"]),
        "items": items,
    }


def _revision(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def read_settings_snapshot(
    *, validate: Callable[[], None] = lambda: None
) -> dict[str, Any]:
    """Return one bounded, cached-only Settings snapshot."""

    validate()
    root = get_row_bot_data_dir(create=False).absolute()
    tools, tool_configs, global_config = _tool_documents(root)
    registered = _registered_tools()
    sections = {
        "voice": _voice(root),
        "system": _system(root, tools, tool_configs, registered),
        "tracker": _tracker(root, tools, registered),
        "documents": _documents(root),
        "tools": _tools(tools, tool_configs, global_config, registered),
        "accounts": _accounts(root, tools, tool_configs, registered),
        "utilities": _utilities(tools, registered),
        "plugins": _plugins(validate),
        "preferences": _preferences(root),
    }
    validate()
    return {"schema_version": 1, "revision": _revision(sections), **sections}


__all__ = ["read_settings_snapshot"]
