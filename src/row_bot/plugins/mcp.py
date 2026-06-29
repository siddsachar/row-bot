"""Helpers for plugin-owned MCP server declarations.

Plugin MCP servers are an in-memory overlay on top of the user's normal MCP
configuration.  They are owned by plugin enablement and are not persisted into
``mcp_servers.json``.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any


def plugin_mcp_server_name(plugin_id: str, server_id: str) -> str:
    return f"plugin_{_safe_part(plugin_id)}_{_safe_part(server_id)}"[:96].rstrip("_")


def plugin_mcp_servers() -> dict[str, dict[str, Any]]:
    """Return enabled plugin-owned MCP server configs keyed by runtime name."""

    from row_bot.plugins import registry as plugin_registry
    from row_bot.plugins import state as plugin_state

    servers: dict[str, dict[str, Any]] = {}
    for manifest in plugin_registry.get_loaded_manifests():
        plugin_id = str(manifest.id)
        if not plugin_state.is_plugin_enabled(plugin_id):
            continue
        for entry in getattr(manifest.provides, "mcp_servers", []) or []:
            if not isinstance(entry, dict):
                continue
            server_id = str(entry.get("id") or "").strip()
            if not server_id:
                continue
            name = plugin_mcp_server_name(plugin_id, server_id)
            servers[name] = _server_config_from_entry(manifest, entry, server_id)
    return servers


def with_plugin_mcp_servers(config: dict[str, Any]) -> dict[str, Any]:
    """Return *config* plus enabled plugin-owned MCP server overlays."""

    cfg = copy.deepcopy(config)
    base_enabled = bool(cfg.get("enabled"))
    cfg.setdefault("servers", {})
    if not isinstance(cfg["servers"], dict):
        cfg["servers"] = {}

    # If the global MCP switch is off, keep user-configured MCP servers inert
    # while still allowing explicitly enabled plugin MCP servers to run.
    if not base_enabled:
        for server_cfg in cfg["servers"].values():
            if isinstance(server_cfg, dict):
                server_cfg["enabled"] = False

    plugin_servers = plugin_mcp_servers()
    cfg["servers"].update(plugin_servers)
    cfg["enabled"] = base_enabled or bool(plugin_servers)
    return cfg


def _server_config_from_entry(manifest: Any, entry: dict[str, Any], server_id: str) -> dict[str, Any]:
    plugin_id = str(manifest.id)
    plugin_path = Path(getattr(manifest, "path", "") or ".")
    transport = str(entry.get("transport") or ("streamable_http" if entry.get("url") else "stdio"))
    cfg = {
        "name": plugin_mcp_server_name(plugin_id, server_id),
        "enabled": True,
        "transport": transport,
        "command": str(entry.get("command") or ""),
        "args": [str(arg) for arg in entry.get("args", []) if str(arg)],
        "cwd": str(entry.get("cwd") or plugin_path),
        "env": _resolve_mapping(plugin_id, entry.get("env", {})),
        "url": str(_resolve_value(plugin_id, entry.get("url", "")) or ""),
        "headers": _resolve_mapping(plugin_id, entry.get("headers", {})),
        "connect_timeout": float(entry.get("connect_timeout", 30) or 30),
        "tool_timeout": float(entry.get("tool_timeout", 120) or 120),
        "output_limit": int(entry.get("output_limit", 24000) or 24000),
        "trust_level": str(entry.get("trust_level") or "standard"),
        "requirements": [],
        "tools": dict(entry.get("tools") or {}),
        "source": {
            "kind": "plugin",
            "plugin_id": plugin_id,
            "plugin_name": str(getattr(manifest, "name", "") or plugin_id),
            "server_id": server_id,
        },
    }
    cfg["tools"].setdefault("enabled", {})
    cfg["tools"].setdefault("require_approval", [])
    cfg["tools"].setdefault("include", [])
    cfg["tools"].setdefault("exclude", [])
    return cfg


def _resolve_mapping(plugin_id: str, raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    resolved: dict[str, str] = {}
    for key, value in raw.items():
        out = _resolve_value(plugin_id, value)
        if out not in (None, ""):
            resolved[str(key)] = str(out)
    return resolved


def _resolve_value(plugin_id: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith("setting:"):
        from row_bot.plugins import state as plugin_state

        key = value.split(":", 1)[1]
        return plugin_state.get_plugin_config(plugin_id, key, "")
    if value.startswith("secret:"):
        from row_bot.plugins import state as plugin_state

        key = value.split(":", 1)[1]
        return plugin_state.get_plugin_secret(plugin_id, key) or ""
    return value


def _safe_part(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip().lower()).strip("_") or "plugin"
