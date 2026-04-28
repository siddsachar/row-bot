"""Persistent MCP client configuration.

Stored separately from the built-in tools config so broken MCP settings can be
quarantined without affecting existing tool toggles.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import sys
from typing import Any

from mcp_client.logging import log_event, mask_mapping

DATA_DIR = pathlib.Path(os.environ.get("THOTH_DATA_DIR", pathlib.Path.home() / ".thoth"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    global _config_cache
    normalized = normalize_config(config)
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2)
    tmp_path.replace(CONFIG_PATH)
    _config_cache = normalized
    log_event("mcp.config.saved", servers=len(normalized.get("servers", {})))
    agent_mod = sys.modules.get("agent")
    if agent_mod is not None and hasattr(agent_mod, "clear_agent_cache"):
        try:
            agent_mod.clear_agent_cache()
        except Exception:
            pass


def get_config() -> dict[str, Any]:
    return load_config()


def is_globally_enabled() -> bool:
    return bool(load_config().get("enabled", False))


def set_global_enabled(enabled: bool) -> None:
    enabled = bool(enabled)
    cfg = load_config()
    cfg["enabled"] = enabled
    save_config(cfg)
    try:
        from tools import registry
        if registry.get_tool("mcp") is not None and registry.is_enabled("mcp") != enabled:
            registry.set_enabled("mcp", enabled)
    except Exception:
        pass
    try:
        from mcp_client import runtime
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


def upsert_server(name: str, server_config: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    normalized = normalize_server_config(name, server_config)
    cfg.setdefault("servers", {})[name] = normalized
    save_config(cfg)
    return normalized


def delete_server(name: str) -> None:
    cfg = load_config()
    cfg.setdefault("servers", {}).pop(name, None)
    save_config(cfg)


def set_server_enabled(name: str, enabled: bool) -> None:
    cfg = load_config()
    if name in cfg.get("servers", {}):
        cfg["servers"][name]["enabled"] = bool(enabled)
        save_config(cfg)


def set_tool_enabled(server_name: str, tool_name: str, enabled: bool) -> None:
    cfg = load_config()
    server = cfg.get("servers", {}).get(server_name)
    if not server:
        return
    tools_cfg = server.setdefault("tools", {})
    tools_cfg.setdefault("enabled", {})[tool_name] = bool(enabled)
    save_config(cfg)


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