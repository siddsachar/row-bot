"""Reviewed local mutations for the passive Settings snapshot.

Only explicitly allowlisted saved fields are handled here.  Live probes,
authentication, installs, runtime lifecycle actions, OS integrations, and
destructive subsystem actions without a Settings owner remain outside this
module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from typing import Any
from uuid import UUID

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime import admissions

from .settings_snapshot import read_settings_snapshot

_PAGES = {
    "voice",
    "system",
    "tracker",
    "knowledge",
    "documents",
    "tools",
    "accounts",
    "utilities",
    "preferences",
}
_TOOL_IDS = {
    "web_search",
    "duckduckgo",
    "wolfram_alpha",
    "arxiv",
    "wikipedia",
    "youtube",
}
_UTILITY_IDS = {
    "task",
    "timer",
    "url_reader",
    "calculator",
    "weather",
    "chart",
    "system_info",
    "conversation_search",
    "custom_tool_builder",
}
_SECRET_FIELDS = {
    ("voice", "openai_realtime_credential"): "OPENAI_API_KEY",
    ("system", "tunnel.credential"): "NGROK_AUTHTOKEN",
    ("tools", "web_search.credential"): "TAVILY_API_KEY",
    ("tools", "wolfram_alpha.credential"): "WOLFRAM_ALPHA_APPID",
    ("accounts", "github.credential"): "GITHUB_TOKEN",
    ("accounts", "x.client_id"): "X_CLIENT_ID",
    ("accounts", "x.client_secret"): "X_CLIENT_SECRET",
}
_ENUMS = {
    ("voice", "runtime.talk_provider"): {"local", "openai_realtime"},
    ("voice", "runtime.dictation_provider"): {"local"},
    ("voice", "runtime.speech_output_provider"): {"local"},
    ("voice", "runtime.realtime_voice"): {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    },
    ("voice", "local.whisper_model"): {"tiny", "base", "small", "medium"},
    ("voice", "tts.voice"): {
        "af_heart",
        "af_bella",
        "af_nicole",
        "af_sarah",
        "af_nova",
        "am_michael",
        "am_fenrir",
        "am_puck",
        "bf_emma",
        "bm_george",
    },
    ("system", "tunnel.provider"): {"ngrok"},
    ("system", "remote_access.listen_mode"): {"local_only", "local_network"},
    ("system", "logging.level"): {"DEBUG", "INFO", "WARNING", "ERROR"},
    ("documents", "embedding.provider"): {"local", "cloud"},
    ("documents", "embedding.local_model"): {
        "qwen3-0.6b",
        "nomic-v1.5",
        "mxbai-large-v1",
    },
    ("documents", "embedding.cloud_model"): {
        "openai:text-embedding-3-small",
        "openai:text-embedding-3-large",
        "google:gemini-embedding-001",
    },
    ("tools", "external_loading_mode"): {"auto", "eager"},
    ("tools", "compression_mode"): {"off", "deep"},
    ("preferences", "window_mode"): {"ask", "native", "browser"},
    ("preferences", "updates.channel"): {"stable", "beta"},
}
_BOOL_FIELDS = {
    ("voice", "runtime.captions_enabled"),
    ("voice", "runtime.talk_auto_start"),
    ("voice", "runtime.realtime_fallback_to_local"),
    ("voice", "tts.enabled"),
    ("voice", "tts.auto_speak"),
    ("system", "shell.enabled"),
    ("system", "browser.enabled"),
    ("system", "computer_use.enabled"),
    ("system", "file_operations.enabled"),
    ("tracker", "enabled"),
    ("knowledge", "memory_enabled"),
    ("documents", "embedding.auto_unload"),
    ("accounts", "gmail.enabled"),
    ("accounts", "calendar.enabled"),
    ("accounts", "x.enabled"),
    ("preferences", "identity.self_improvement_enabled"),
    ("preferences", "dream_cycle.enabled"),
}
_TEXT_FIELDS = {
    ("voice", "runtime.talk_model"): 128,
    ("voice", "runtime.dictation_model"): 128,
    ("voice", "runtime.speech_output_model"): 128,
    ("voice", "runtime.speech_output_voice"): 64,
    ("system", "workspace.path"): 4096,
    ("system", "shell.blocked_patterns"): 4096,
    ("accounts", "gmail.credentials_path"): 4096,
    ("accounts", "calendar.credentials_path"): 4096,
    ("preferences", "identity.name"): 128,
    ("preferences", "identity.personality"): 200,
}
_LIST_FIELDS = {
    ("system", "file_operations.selected"): {
        "read_file",
        "list_directory",
        "file_search",
        "write_file",
        "copy_file",
        "export_to_pdf",
        "move_file",
        "file_delete",
    },
    ("system", "remote_access.configured_origins"): None,
    ("accounts", "gmail.operations"): {
        "search_gmail",
        "get_gmail_message",
        "get_gmail_thread",
        "create_gmail_draft",
        "send_gmail_message",
    },
    ("accounts", "calendar.operations"): {
        "get_current_datetime",
        "search_events",
        "create_calendar_event",
        "create_calendar_events",
        "update_calendar_event",
        "move_calendar_event",
        "delete_calendar_event",
    },
    ("accounts", "x.read_operations"): {
        "x_search",
        "x_read_tweet",
        "x_timeline",
        "x_mentions",
        "x_user_info",
    },
    ("accounts", "x.post_operations"): {
        "x_post_tweet",
        "x_reply",
        "x_quote",
        "x_delete_tweet",
    },
    ("accounts", "x.engage_operations"): {
        "x_like",
        "x_unlike",
        "x_repost",
        "x_unrepost",
        "x_bookmark",
        "x_unbookmark",
    },
}
_SAFE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class SettingsCommandError(ValueError):
    def __init__(self, code: str, current_revision: str | None = None):
        self.code = code
        self.current_revision = current_revision
        super().__init__(code)


def _uuid(value: Any) -> str:
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise SettingsCommandError("invalid_settings_command") from None
    return value


def _normal_value(page: Any, field: Any, value: Any) -> tuple[str, str, Any, bool]:
    if page not in _PAGES or type(field) is not str or not 1 <= len(field) <= 128:
        raise SettingsCommandError("invalid_settings_command")
    key = (page, field)
    secret = key in _SECRET_FIELDS
    if secret:
        if value is not None and (
            type(value) is not str
            or not value.strip()
            or len(value.encode("utf-8")) > 16 * 1024
        ):
            raise SettingsCommandError("invalid_settings_command")
        return page, field, value.strip() if isinstance(value, str) else None, True
    if key in _ENUMS:
        if type(value) is not str or value not in _ENUMS[key]:
            raise SettingsCommandError("invalid_settings_command")
    elif key in _BOOL_FIELDS:
        if type(value) is not bool:
            raise SettingsCommandError("invalid_settings_command")
    elif key in _TEXT_FIELDS:
        if (
            type(value) is not str
            or "\0" in value
            or len(value.encode("utf-8")) > _TEXT_FIELDS[key]
        ):
            raise SettingsCommandError("invalid_settings_command")
        value = value.strip() if field in {"identity.name"} else value
        if field == "identity.name" and not value:
            raise SettingsCommandError("invalid_settings_command")
        if field == "identity.personality":
            from row_bot.identity import sanitize_personality

            value = sanitize_personality(value)
    elif key in _LIST_FIELDS:
        allowed = _LIST_FIELDS[key]
        if (
            type(value) is not list
            or len(value) > 128
            or any(
                type(item) is not str or not item or len(item) > 256 for item in value
            )
            or allowed is not None
            and any(item not in allowed for item in value)
        ):
            raise SettingsCommandError("invalid_settings_command")
        value = list(dict.fromkeys(value))
        if key == ("system", "remote_access.configured_origins"):
            try:
                from row_bot.access.config import canonical_origin

                value = list(dict.fromkeys(canonical_origin(item) for item in value))
            except ValueError:
                raise SettingsCommandError("invalid_settings_command") from None
    elif key == ("voice", "tts.speed"):
        if (
            type(value) not in {int, float}
            or not math.isfinite(value)
            or not 0.5 <= value <= 2
        ):
            raise SettingsCommandError("invalid_settings_command")
        value = float(value)
    elif key == ("documents", "embedding.dimension"):
        if value is not None and (type(value) is not int or not 1 <= value <= 100000):
            raise SettingsCommandError("invalid_settings_command")
    elif key in {
        ("preferences", "dream_cycle.window_start"),
        ("preferences", "dream_cycle.window_end"),
    }:
        if type(value) is not int or not 0 <= value <= 23:
            raise SettingsCommandError("invalid_settings_command")
    elif page == "tools" and field.endswith(".enabled"):
        tool_id = field.removesuffix(".enabled")
        if tool_id not in _TOOL_IDS or type(value) is not bool:
            raise SettingsCommandError("invalid_settings_command")
    elif page == "utilities" and field.endswith(".enabled"):
        tool_id = field.removesuffix(".enabled")
        if tool_id not in _UTILITY_IDS or type(value) is not bool:
            raise SettingsCommandError("invalid_settings_command")
    elif key == ("tracker", "delete_all"):
        if value is not True:
            raise SettingsCommandError("invalid_settings_command")
    else:
        raise SettingsCommandError("settings_action_unavailable")
    return page, field, deepcopy(value), False


def _intent(
    settings_revision: Any, page: Any, field: Any, value: Any
) -> dict[str, Any]:
    if type(settings_revision) is not str or not re.fullmatch(
        r"[a-f0-9]{64}", settings_revision
    ):
        raise SettingsCommandError("invalid_settings_command")
    page, field, value, secret = _normal_value(page, field, value)
    return {
        "settings_revision": settings_revision,
        "page": page,
        "field": field,
        "value": value,
        "secret": secret,
    }


def review_settings_update(
    settings_revision: str,
    page: str,
    field: str,
    value: Any,
    *,
    validate: Callable[[], None],
) -> dict[str, Any]:
    validate()
    intent = _intent(settings_revision, page, field, value)
    current = read_settings_snapshot(validate=validate)
    if current["revision"] != settings_revision:
        raise SettingsCommandError("settings_changed", current["revision"])
    if page == "preferences" and field in {
        "dream_cycle.window_start",
        "dream_cycle.window_end",
    }:
        other = (
            current["preferences"]["dream_cycle"]["window_end"]
            if field.endswith("window_start")
            else current["preferences"]["dream_cycle"]["window_start"]
        )
        if intent["value"] == other:
            raise SettingsCommandError("invalid_settings_command")
    digest = admissions.keyed_digest(
        {key: item for key, item in intent.items() if key != "secret"}
    )
    if intent["secret"]:
        summary = (
            "Remove saved credential"
            if intent["value"] is None
            else "Replace saved credential"
        )
    elif isinstance(intent["value"], list):
        summary = f"Save {len(intent['value'])} selected values"
    elif (page, field) == ("tracker", "delete_all"):
        summary = (
            "Delete all tracker data, including every tracker and entry. "
            "This cannot be undone."
        )
    else:
        summary = str(intent["value"])[:256]
    validate()
    return {
        "schema_version": 1,
        "operation": "settings.update",
        "settings_revision": settings_revision,
        "page": page,
        "field": field,
        "value_summary": summary,
        "secret": intent["secret"],
        "action_digest": digest,
    }


def _read_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise SettingsCommandError("settings_unavailable") from None
    if type(value) is not dict:
        raise SettingsCommandError("settings_unavailable")
    return value


def _write_document(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
        if len(data.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError, RecursionError):
        try:
            temporary.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise SettingsCommandError("settings_save_unconfirmed") from None


def _set_path(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = document
    for part in path[:-1]:
        child = target.setdefault(part, {})
        if type(child) is not dict:
            child = target[part] = {}
        target = child
    target[path[-1]] = deepcopy(value)


def _write_json_setting(root: Path, page: str, field: str, value: Any) -> None:
    if page == "voice" and field.startswith("runtime."):
        path, keys = root / "voice_runtime_settings.json", (field.split(".", 1)[1],)
    elif page == "voice" and field == "local.whisper_model":
        path, keys = root / "voice_settings.json", ("whisper_model",)
    elif page == "voice" and field.startswith("tts."):
        path, keys = root / "tts_settings.json", (field.split(".", 1)[1],)
    elif page == "documents" and field.startswith("embedding."):
        path, keys = root / "embedding_config.json", (field.split(".", 1)[1],)
    elif page == "system" and field == "tunnel.provider":
        path, keys = root / "channels_config.json", ("tunnel", "provider")
    elif page == "system" and field.startswith("remote_access."):
        path, keys = root / "access_routes.json", (field.split(".", 1)[1],)
    elif page == "system" and field == "logging.level":
        path, keys = root / "user_config.json", ("file_log_level",)
    elif page == "preferences" and field.startswith("identity."):
        key = field.split(".", 1)[1]
        key = "self_improvement" if key == "self_improvement_enabled" else key
        path, keys = root / "user_config.json", ("identity", key)
    elif page == "preferences" and field == "window_mode":
        path, keys = root / "app_config.json", ("window_mode",)
    elif page == "preferences" and field.startswith("dream_cycle."):
        path, keys = root / "dream_config.json", (field.split(".", 1)[1],)
    elif page == "preferences" and field == "updates.channel":
        path, keys = root / "update_config.json", ("channel",)
    else:
        raise SettingsCommandError("settings_action_unavailable")
    from row_bot.providers.config import provider_config_transaction

    with provider_config_transaction(path):
        document = _read_document(path)
        if page == "system" and field.startswith("remote_access."):
            document.setdefault("version", 2)
        _set_path(document, keys, value)
        _write_document(path, document)


def _write_tool_setting(root: Path, page: str, field: str, value: Any) -> None:
    path = root / "tools_config.json"
    from row_bot.providers.config import provider_config_transaction

    with provider_config_transaction(path):
        document = _read_document(path)
        tools = document.get("tools")
        if tools is None:
            tools = {
                key: item
                for key, item in document.items()
                if key not in {"tool_configs", "global"} and type(item) is bool
            }
            document["tools"] = tools
        configs = document.setdefault("tool_configs", {})
        global_config = document.setdefault("global", {})
        if any(type(item) is not dict for item in (tools, configs, global_config)):
            raise SettingsCommandError("settings_unavailable")
        if page == "tracker" and field == "enabled":
            tools["tracker"] = value
        elif page == "knowledge" and field == "memory_enabled":
            tools["memory"] = value
        elif page == "utilities" and field.endswith(".enabled"):
            tools[field.removesuffix(".enabled")] = value
        elif page == "tools" and field.endswith(".enabled"):
            tools[field.removesuffix(".enabled")] = value
        elif page == "tools" and field == "external_loading_mode":
            global_config["external_tool_loading_mode"] = value
        elif page == "tools" and field == "compression_mode":
            global_config["compression_mode"] = value
        elif page == "system" and field == "workspace.path":
            configs.setdefault("filesystem", {})["workspace_root"] = value
        elif page == "system" and field == "shell.blocked_patterns":
            configs.setdefault("shell", {})["blocked_commands"] = value
        elif page == "system" and field == "file_operations.selected":
            configs.setdefault("filesystem", {})["selected_operations"] = value
        elif page == "system" and field in {
            "shell.enabled",
            "browser.enabled",
            "computer_use.enabled",
            "file_operations.enabled",
        }:
            identity = {
                "shell.enabled": "shell",
                "browser.enabled": "browser",
                "computer_use.enabled": "computer_use",
                "file_operations.enabled": "filesystem",
            }[field]
            tools[identity] = value
        elif page == "accounts" and field.endswith(".enabled"):
            tools[field.removesuffix(".enabled")] = value
        elif page == "accounts" and field in {
            "gmail.credentials_path",
            "calendar.credentials_path",
        }:
            identity = field.split(".", 1)[0]
            configs.setdefault(identity, {})["credentials_path"] = value
        elif page == "accounts" and (page, field) in _LIST_FIELDS:
            identity, name = field.split(".", 1)
            key = "selected_operations" if identity in {"gmail", "calendar"} else name
            configs.setdefault(identity, {})[key] = value
        else:
            raise SettingsCommandError("settings_action_unavailable")
        _write_document(path, document)
    registry = sys.modules.get("row_bot.tools.registry")
    if registry is not None:
        registry.reload_saved_config()


def _clear_tracker_data(
    root: Path, settings_revision: str, *, validate: Callable[[], None]
) -> None:
    """Atomically clear the reviewed local tracker rows."""

    path = root / "tracker" / "tracker.db"
    if not path.is_file():
        return
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(path), timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        validate()
        current = read_settings_snapshot(validate=validate)
        if current["revision"] != settings_revision:
            raise SettingsCommandError("settings_changed", current["revision"])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"entries", "trackers"}.issubset(tables):
            raise SettingsCommandError("settings_unavailable")
        connection.execute("DELETE FROM entries")
        connection.execute("DELETE FROM trackers")
        validate()
        connection.commit()
    except SettingsCommandError:
        if connection is not None:
            connection.rollback()
        raise
    except sqlite3.Error:
        if connection is not None:
            connection.rollback()
        raise SettingsCommandError("settings_save_unconfirmed") from None
    finally:
        if connection is not None:
            connection.close()


def _apply(intent: dict[str, Any], *, validate: Callable[[], None]) -> dict[str, Any]:
    validate()
    root = get_row_bot_data_dir(create=False).absolute()
    page, field, value = intent["page"], intent["field"], intent["value"]
    if (page, field) == ("tracker", "delete_all"):
        _clear_tracker_data(root, intent["settings_revision"], validate=validate)
    elif intent["secret"]:
        from row_bot import api_keys

        name = _SECRET_FIELDS[(page, field)]
        if value is None:
            api_keys.delete_key(name)
        else:
            api_keys.set_key(name, value)
    elif (
        page in {"tools", "tracker", "knowledge", "utilities"}
        or page == "system"
        and field
        in {
            "workspace.path",
            "shell.enabled",
            "shell.blocked_patterns",
            "browser.enabled",
            "computer_use.enabled",
            "file_operations.enabled",
            "file_operations.selected",
        }
        or page == "accounts"
    ):
        _write_tool_setting(root, page, field, value)
    else:
        _write_json_setting(root, page, field, value)
    validate()
    return read_settings_snapshot(validate=validate)


def execute_settings_update(
    *,
    owner_id: str,
    key: str,
    command: dict[str, Any],
    validate: Callable[[], None],
    validate_review: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    validate()
    if type(command) is not dict or command.get("type") != "settings.update":
        raise SettingsCommandError("invalid_settings_command")
    command_id = _uuid(command.get("command_id"))
    if key != command_id or type(command.get("payload")) is not dict:
        raise SettingsCommandError("invalid_settings_command")
    payload = command["payload"]
    expected = {
        "settings_revision",
        "page",
        "field",
        "value",
        "action_digest",
        "review_id",
    }
    if set(payload) != expected or type(payload.get("action_digest")) is not str:
        raise SettingsCommandError("invalid_settings_command")
    intent = _intent(
        payload["settings_revision"],
        payload["page"],
        payload["field"],
        payload["value"],
    )
    target = f"settings:snapshot:{intent['page']}:{intent['field']}"
    previous = admissions.read_command_metadata(owner_id, command_id)
    if previous is not None:
        try:
            replay = admissions.claim_command(owner_id, key, command, target)
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise SettingsCommandError(str(error), error.current_revision) from None
            replay = admissions.read_command_receipt(owner_id, command_id)
        if replay is None:
            raise SettingsCommandError("settings_save_unconfirmed")
        return replay
    review = review_settings_update(
        intent["settings_revision"],
        intent["page"],
        intent["field"],
        intent["value"],
        validate=validate,
    )
    if review["action_digest"] != payload["action_digest"]:
        raise SettingsCommandError("settings_review_changed")
    validate_review(review)
    initial = {
        "command_id": command_id,
        "status": "partial",
        "code": "settings_save_unconfirmed",
    }
    try:
        replay = admissions.claim_command(
            owner_id,
            key,
            command,
            target,
            exclusive_target=True,
            initial_result=initial,
        )
    except admissions.AdmissionError as error:
        raise SettingsCommandError(str(error), error.current_revision) from None
    if replay is not None:
        return replay
    try:
        snapshot = _apply(intent, validate=validate)
        result = {
            "command_id": command_id,
            "status": "completed",
            "code": None,
            "settings_revision": snapshot["revision"],
            "snapshot": snapshot,
        }
    except Exception:
        result = {
            "command_id": command_id,
            "status": "partial",
            "code": "settings_save_unconfirmed",
            "settings_revision": None,
            "snapshot": None,
        }
    return admissions.complete_command(owner_id, key, result)


def read_settings_receipt(
    command_id: str, *, owner_id: str, validate: Callable[[], None]
) -> dict[str, Any] | None:
    validate()
    command_id = _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if (
        metadata is None
        or metadata["type"] != "settings.update"
        or not metadata["target"].startswith("settings:snapshot:")
    ):
        return None
    result = admissions.read_command_receipt(owner_id, command_id)
    validate()
    return result


__all__ = [
    "SettingsCommandError",
    "execute_settings_update",
    "read_settings_receipt",
    "review_settings_update",
]
