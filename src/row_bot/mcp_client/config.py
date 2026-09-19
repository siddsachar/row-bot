"""Persistent MCP client configuration.

Stored separately from the built-in tools config so broken MCP settings can be
quarantined without affecting existing tool toggles.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import wraps
import hashlib
import json
import os
import stat
import threading
import uuid
import sys
from typing import Any, Callable, Iterator

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.mcp_client.logging import log_event, mask_mapping

DATA_DIR = get_row_bot_data_dir(create=False)
CONFIG_PATH = DATA_DIR / "mcp_servers.json"

CURRENT_VERSION = 1
VALID_TRANSPORTS = {"stdio", "http", "streamable_http", "streamable-http", "sse"}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CURRENT_VERSION,
    "enabled": False,
    "marketplace": {
        "enabled": True,
        "sources": ["official", "pulsemcp", "smithery", "glama"],
    },
    "servers": {},
}

_config_cache: dict[str, Any] | None = None

_CONFIG_LOCK = threading.RLock()
SAVED_CONFIG_BYTE_LIMIT = 8 * 1024 * 1024


class McpConfigurationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SavedMcpConfiguration:
    document: dict[str, Any]
    digest: str
    exists: bool
    identity: str = ""


@contextmanager
def configuration_transaction() -> Iterator[None]:
    """Serialize canonical config mutations; runtime waits remain outside it."""
    with _CONFIG_LOCK:
        yield


def configuration_recovery_required(*, excluding: tuple[str, str] | None = None) -> bool:
    """Consult only bounded canonical receipts; uncertainty never means empty."""
    from row_bot.runtime import admissions
    try:
        pending = admissions.read_unfinished_target_commands("settings:mcp", limit=32)
    except admissions.AdmissionError:
        raise McpConfigurationError("mcp_configuration_recovery_unavailable") from None
    return pending["overflow"] or any(
        (row["owner_id"], row["key"]) != excluding for row in pending["items"])


def require_configuration_write_available(*, excluding: tuple[str, str] | None = None) -> None:
    if configuration_recovery_required(excluding=excluding):
        raise McpConfigurationError("mcp_configuration_recovery_required")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def read_saved_configuration() -> SavedMcpConfiguration:
    """Read bounded saved bytes without imports, cache refresh or directory creation.

    The shared native guard pins Windows ancestors and supplies a descriptor
    for POSIX relative no-follow opens. Config source digests are private.
    """
    from row_bot.file_ownership import directory_identity, guard_directory
    path = CONFIG_PATH.absolute()
    with _CONFIG_LOCK:
        try:
            if not path.parent.exists():
                return SavedMcpConfiguration(_safe_copy(DEFAULT_CONFIG), "missing", False)
            with guard_directory(path.parent, directory_identity(path.parent, parent=True)) as directory:
                try:
                    before = os.stat(path.name if directory is not None else path,
                                     dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    return SavedMcpConfiguration(_safe_copy(DEFAULT_CONFIG), "missing", False)
                if (not stat.S_ISREG(before.st_mode) or
                        getattr(before, "st_file_attributes", 0) & 0x400 or
                        before.st_size > SAVED_CONFIG_BYTE_LIMIT):
                    raise ValueError
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                descriptor = os.open(path.name if directory is not None else path, flags, dir_fd=directory)
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(descriptor)
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise ValueError
                    data = stream.read(SAVED_CONFIG_BYTE_LIMIT + 1)
                    finished = os.fstat(descriptor)
                    current = os.stat(path.name if directory is not None else path,
                                      dir_fd=directory, follow_symlinks=False)
                def identity(info):
                    # Windows pathname stat still exposes birth time in ctime,
                    # while descriptor fstat reports change time on this Python.
                    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                            info.st_ctime_ns if os.name != "nt" else None)
                if (len(data) > SAVED_CONFIG_BYTE_LIMIT or identity(opened) != identity(finished)
                        or identity(current) != identity(finished)):
                    raise ValueError
            def invalid_constant(_value):
                raise ValueError
            document = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object,
                                  parse_constant=invalid_constant)
            if (type(document) is not dict or type(document.get("servers", {})) is not dict
                    or type(document.get("version", 1)) is not int or document.get("version", 1) != 1
                    or len(document.get("servers", {})) > 10000
                    or any(type(server) is not dict for server in document.get("servers", {}).values())):
                raise ValueError
            return SavedMcpConfiguration(document, hashlib.sha256(data).hexdigest(), True,
                                         f"{opened.st_dev}:{opened.st_ino}")
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise McpConfigurationError("mcp_configuration_unavailable") from None


def publish_saved_configuration(document: dict[str, Any], *, expected_digest: str,
                                command_id: str, persist_recovery: Callable,
                                validate: Callable[[], None] = lambda: None,
                                recovery=None) -> SavedMcpConfiguration:
    """Publish exact JSON through the existing metadata-safe file owner."""
    global _config_cache
    from row_bot.developer.edits import publish_text_revision
    data = json.dumps(document, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
    if len(data.encode("utf-8")) > SAVED_CONFIG_BYTE_LIMIT:
        raise McpConfigurationError("mcp_configuration_too_large")
    with _CONFIG_LOCK:
        validate()
        if recovery is None and read_saved_configuration().digest != expected_digest:
            raise McpConfigurationError("revision_conflict")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            publish_text_revision(CONFIG_PATH.parent.absolute(), CONFIG_PATH.name, data,
                expected_digest=expected_digest, command_id=command_id,
                persist_recovery=persist_recovery, recovery=recovery, validate=validate,
                max_bytes=SAVED_CONFIG_BYTE_LIMIT)
            result = read_saved_configuration()
            _config_cache = normalize_config(result.document)
        except Exception:
            _config_cache = None  # Never retain obsolete dispatch authority.
            raise
    clear_agent_cache_if_loaded()
    return result


def _configuration_mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        global _config_cache
        with _CONFIG_LOCK:
            # Legacy UI/tool setters participate in the same fresh RMW owner.
            current = read_saved_configuration()
            _config_cache = normalize_config(current.document)
            return function(*args, **kwargs)
    return guarded


def _retain_unknown_fields(normalized: dict, current: dict) -> dict:
    result = _safe_copy(current)
    result.update(normalized)
    result.pop("_client_publication", None)
    result["servers"] = {}
    for name, server in normalized.get("servers", {}).items():
        prior = current.get("servers", {}).get(name, {})
        retained = _safe_copy(prior) if type(prior) is dict else {}
        retained.update(server)
        result["servers"][name] = retained
    return result



def clear_agent_cache_if_loaded() -> None:
    """Invalidate agent graphs without importing the heavyweight agent module."""
    seen_modules: set[int] = set()
    for module_name in ("row_bot.agent", "agent"):
        agent_mod = sys.modules.get(module_name)
        if agent_mod is None or id(agent_mod) in seen_modules:
            continue
        seen_modules.add(id(agent_mod))
        clear_cache = getattr(agent_mod, "clear_agent_cache", None)
        if not callable(clear_cache):
            continue
        try:
            clear_cache()
        except Exception:
            pass


def _safe_copy(data: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(data)


def _normalize_transport(value: str | None) -> str:
    transport = (value or "stdio").strip().lower()
    if transport == "streamable-http":
        transport = "streamable_http"
    if transport == "http":
        transport = "streamable_http"
    if transport not in VALID_TRANSPORTS:
        transport = "stdio"
    return transport


def normalize_server_config(name: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    transport = _normalize_transport(raw.get("transport") or ("streamable_http" if raw.get("url") else "stdio"))
    cfg: dict[str, Any] = {
        "name": str(raw.get("name") or name),
        "enabled": bool(raw.get("enabled", False)),
        "transport": transport,
        "command": str(raw.get("command") or ""),
        "args": list(raw.get("args") or []),
        "cwd": raw.get("cwd") or None,
        "env": dict(raw.get("env") or {}),
        "url": str(raw.get("url") or ""),
        "headers": dict(raw.get("headers") or {}),
        "connect_timeout": float(raw.get("connect_timeout", 30) or 30),
        "tool_timeout": float(raw.get("tool_timeout", 120) or 120),
        "output_limit": int(raw.get("output_limit", 24000) or 24000),
        "trust_level": str(raw.get("trust_level") or "standard"),
        "requirements": list(raw.get("requirements") or []),
        "tools": dict(raw.get("tools") or {}),
        "source": dict(raw.get("source") or {}),
    }
    tools_cfg = cfg["tools"]
    tools_cfg["enabled"] = dict(tools_cfg.get("enabled") or {})
    tools_cfg["require_approval"] = list(tools_cfg.get("require_approval") or [])
    tools_cfg["include"] = list(tools_cfg.get("include") or [])
    tools_cfg["exclude"] = list(tools_cfg.get("exclude") or [])
    tools_cfg["resources_enabled"] = bool(tools_cfg.get("resources_enabled", False))
    tools_cfg["prompts_enabled"] = bool(tools_cfg.get("prompts_enabled", False))
    return cfg


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = _safe_copy(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return cfg
    cfg["version"] = CURRENT_VERSION
    cfg["enabled"] = bool(raw.get("enabled", cfg["enabled"]))
    if isinstance(raw.get("marketplace"), dict):
        cfg["marketplace"].update(raw["marketplace"])
    servers = raw.get("servers", {})
    if isinstance(servers, dict):
        cfg["servers"] = {
            str(name): normalize_server_config(str(name), value)
            for name, value in servers.items()
            if str(name).strip()
        }
    return cfg


def load_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _safe_copy(_config_cache)
    if not CONFIG_PATH.exists():
        _config_cache = normalize_config({})
        return _safe_copy(_config_cache)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        _config_cache = normalize_config(raw)
    except Exception as exc:
        log_event("mcp.config.load_failed", level=30, path=str(CONFIG_PATH), error=str(exc))
        _config_cache = normalize_config({})
    return _safe_copy(_config_cache)


def save_config(config: dict[str, Any]) -> None:
    """Keep the legacy setter contract on the canonical contained publication."""
    from row_bot.runtime import admissions
    with _CONFIG_LOCK:
        require_configuration_write_available()
        current = read_saved_configuration()
        normalized = _retain_unknown_fields(normalize_config(config), current.document)
        command_id = str(uuid.uuid4())
        owner_id = "mcp:legacy-settings"
        command = {"command_id": command_id, "type": "mcp.configuration.legacy_save",
                   "intent_digest": admissions.keyed_digest(normalized),
                   "expected_revision": current.digest}
        admissions.claim_command(owner_id, command_id, command, "settings:mcp")
        progress = {"command_id": command_id, "status": "admitting"}
        normalized["_client_publication"] = {"owner_id": owner_id, "key": command_id,
                                             "command_id": command_id}

        def checkpoint(proof):
            progress["_mcp_configuration"] = {"publication": asdict(proof), "server_ids": []}
            admissions.command_progress(owner_id, command_id, progress)

        try:
            publish_saved_configuration(normalized, expected_digest=current.digest,
                command_id=command_id, persist_recovery=checkpoint)
        except Exception:
            # A failure before a durable checkpoint has no publication effects.
            if "_mcp_configuration" not in progress:
                admissions.reject_command(owner_id, command_id, "mcp_configuration_save_failed")
            raise
        admissions.complete_command(owner_id, command_id,
            {"command_id": command_id, "status": "completed"})
    log_event("mcp.config.saved", servers=len(normalized.get("servers", {})))


def get_config() -> dict[str, Any]:
    return load_config()


def get_cached_enablement(tool_names: dict[str, tuple[str, ...]]) -> dict[str, Any] | None:
    """Project requested loaded toggles without reading config, env or secrets."""
    if _config_cache is None or get_row_bot_data_dir(create=False) != DATA_DIR:
        return None
    servers = _config_cache.get("servers")
    if type(servers) is not dict:
        return None
    result = {}
    for name, names in tool_names.items():
        requested_names = set(names)
        server = servers.get(name)
        if type(server) is not dict:
            continue
        tools = server.get("tools")
        tools = tools if type(tools) is dict else {}
        enabled = tools.get("enabled")
        approvals = tools.get("require_approval")
        result[name] = {
            "enabled": server.get("enabled"),
            "tools": {key: enabled[key] if type(enabled[key]) is bool else None for key in names
                      if key in enabled} if type(enabled) is dict else {},
            "require_approval": tuple(value for value in approvals if type(value) is str and value in requested_names)
            if type(approvals) in (list, tuple) else (),
        }
    return {"enabled": _config_cache.get("enabled"), "servers": result}


def is_globally_enabled() -> bool:
    return bool(load_config().get("enabled", False))


def set_global_enabled(enabled: bool) -> None:
    enabled = bool(enabled)
    with _CONFIG_LOCK:
        cfg = normalize_config(read_saved_configuration().document)
        cfg["enabled"] = enabled
        save_config(cfg)
    try:
        from row_bot.tools import registry
        if registry.get_tool("mcp") is not None and registry.is_enabled("mcp") != enabled:
            registry.set_enabled("mcp", enabled)
    except Exception:
        pass
    try:
        from row_bot.mcp_client import runtime
        if enabled:
            runtime.discover_enabled_servers()
        else:
            runtime.shutdown()
    except Exception as exc:
        log_event("mcp.config.runtime_toggle_failed", level=30, enabled=enabled, error=str(exc))


def get_servers(*, enabled_only: bool = False) -> dict[str, dict[str, Any]]:
    servers = load_config().get("servers", {})
    if enabled_only:
        return {name: cfg for name, cfg in servers.items() if cfg.get("enabled")}
    return servers


@_configuration_mutation
def upsert_server(name: str, server_config: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    normalized = normalize_server_config(name, server_config)
    cfg.setdefault("servers", {})[name] = normalized
    save_config(cfg)
    return normalized


@_configuration_mutation
def delete_server(name: str) -> None:
    cfg = load_config()
    cfg.setdefault("servers", {}).pop(name, None)
    save_config(cfg)


@_configuration_mutation
def set_server_enabled(name: str, enabled: bool) -> None:
    cfg = load_config()
    if name in cfg.get("servers", {}):
        cfg["servers"][name]["enabled"] = bool(enabled)
        save_config(cfg)


@_configuration_mutation
def set_tool_enabled(server_name: str, tool_name: str, enabled: bool) -> None:
    cfg = load_config()
    server = cfg.get("servers", {}).get(server_name)
    if not server:
        return
    tools_cfg = server.setdefault("tools", {})
    tools_cfg.setdefault("enabled", {})[tool_name] = bool(enabled)
    save_config(cfg)


@_configuration_mutation
def set_tool_requires_approval(server_name: str, tool_name: str, requires: bool) -> None:
    cfg = load_config()
    server = cfg.get("servers", {}).get(server_name)
    if not server:
        return
    tools_cfg = server.setdefault("tools", {})
    approvals = set(tools_cfg.get("require_approval") or [])
    if requires:
        approvals.add(tool_name)
    else:
        approvals.discard(tool_name)
    tools_cfg["require_approval"] = sorted(approvals)
    save_config(cfg)


@_configuration_mutation
def set_server_utility_enabled(server_name: str, utility: str, enabled: bool) -> None:
    cfg = load_config()
    server = cfg.get("servers", {}).get(server_name)
    if not server:
        return
    if utility not in {"resources_enabled", "prompts_enabled"}:
        raise ValueError(f"Unknown MCP utility toggle: {utility}")
    server.setdefault("tools", {})[utility] = bool(enabled)
    save_config(cfg)


def masked_config() -> dict[str, Any]:
    return mask_mapping(load_config())
