"""Helpers for plugin-owned MCP server declarations.

Plugin MCP servers are an in-memory overlay on top of the user's normal MCP
configuration.  They are owned by plugin enablement and are not persisted into
``mcp_servers.json``.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any


def plugin_mcp_server_name(plugin_id: str, server_id: str) -> str:
    value = f"plugin_{_safe_part(plugin_id)}_{_safe_part(server_id)}"
    if len(value) > 96 or not re.fullmatch(r"[A-Za-z0-9_-]+", server_id):
        value = value[:78] + "_" + hashlib.sha256((plugin_id + "\0" + server_id).encode()).hexdigest()[:16]
    return value


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
        # What runs without asking, and which tools were accepted, are the user's choices, never a manifest's.
        "tools": {key: value for key, value in dict(entry.get("tools") or {}).items()
                  if key not in {"run_without_asking", "catalog", "accepted_names"}},
        "source": {
            "kind": "plugin",
            "plugin_id": plugin_id,
            "plugin_name": str(getattr(manifest, "name", "") or plugin_id),
            "server_id": server_id,
        },
    }
    if entry.get("portable"):
        from row_bot.data_paths import get_row_bot_data_dir
        from row_bot.package_files import contained_path
        from row_bot.plugins.portable import expand

        data = contained_path(get_row_bot_data_dir(create=False), "plugin_data/" + plugin_id)
        cfg["env"] = {key: expand(value, root=plugin_path, data=data) for key, value in entry.get("env", {}).items()}
        cfg["env"].update(PLUGIN_ROOT=str(plugin_path.resolve()), PLUGIN_DATA=str(data.resolve()))
        cfg["args"] = [expand(arg, root=plugin_path, data=data) for arg in entry.get("args", [])]
        command = str(entry.get("command", ""))
        cfg["command"] = str(contained_path(plugin_path, command[2:])) if command.startswith("./") else command
        cwd = expand(str(entry.get("cwd", "${PLUGIN_ROOT}")), root=plugin_path, data=data)
        cfg["cwd"] = str(contained_path(plugin_path, cwd[2:])) if cwd.startswith("./") else cwd
        # Remote values are literal. Native setting:/secret: substitution is
        # intentionally not part of the portable format.
        cfg["url"] = entry.get("url", "")
        cfg["headers"] = dict(entry.get("headers", {}))
        if entry.get("inputs"):  # Filled in only when connecting: plain values here, keys in the keychain.
            cfg["inputs"] = copy.deepcopy(entry["inputs"])
        cfg["environment_mode"] = "minimal"
        cfg["plugin_data"] = str(data)
    if _python_entry(entry) and not entry.get("portable"):
        try:
            prepared = _prepared_python(manifest, entry)
            cfg.update(command=prepared.command, args=list(prepared.args), cwd=prepared.cwd)
            cfg["plugin_prepared"] = {"operation_id": prepared.operation_id,
                "source_revision": prepared.source_revision,
                "environment_revision": prepared.environment_revision}
        except RuntimeError:
            cfg["enabled"] = False
            cfg["plugin_environment_status"] = "unavailable"
    cfg["tools"].setdefault("enabled", {})
    cfg["tools"].setdefault("require_approval", [])
    cfg["tools"].setdefault("include", [])
    cfg["tools"].setdefault("exclude", [])
    from row_bot.plugins.state import get_mcp_child_overrides
    overrides = get_mcp_child_overrides(plugin_id, server_id)
    for key in ("enabled", "tools", "auth", "label", "managed_launch", "input_values"):
        if key in overrides:
            cfg[key] = copy.deepcopy(overrides[key])
    for key in ("env", "headers"):
        cfg[key].update(overrides.get(key, {}))
    if entry.get("portable"):
        cfg["enabled"] = overrides.get("enabled") is True and isinstance(cfg["tools"].get("catalog"), dict)
    return cfg


def read_plugin_mcp_child(plugin_id: str, server_key: str) -> dict:
    """Read an installed declaration for setup even while its parent is off."""
    from row_bot.plugins.installer import _source_for_preparation
    from row_bot.plugins.manifest import parse_manifest
    manifest = parse_manifest(_source_for_preparation(plugin_id))
    if manifest.id != plugin_id:
        raise ValueError("plugin_identity_changed")
    entry = next((s for s in manifest.provides.mcp_servers if s["id"] == server_key), None)
    if entry is None:
        raise ValueError("plugin_child_unavailable")
    return _server_config_from_entry(manifest, entry, server_key)


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


@dataclass(frozen=True)
class PreparedPluginMcpLaunch:
    plugin_id: str
    command: str
    args: tuple[str, ...]
    cwd: str
    declared_env: dict[str, str]
    operation_id: str
    source_revision: str
    environment_revision: str


def _python_entry(entry: dict) -> bool:
    transport = str(entry.get("transport") or ("streamable_http" if entry.get("url") else "stdio"))
    command = str(entry.get("command") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return transport == "stdio" and re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)?)?(?:w)?(?:\.exe)?", command) is not None


def _prepared_python(manifest, entry: dict) -> PreparedPluginMcpLaunch:
    from row_bot.plugins.worker import WorkerError, prepared_worker
    from row_bot.plugins.sandbox import _checked_path
    prepared = prepared_worker(str(manifest.id), Path(manifest.path))
    root = prepared.plugin_dir
    cwd = Path(str(entry.get("cwd") or root))
    if not cwd.is_absolute():
        cwd = root / cwd
    if ".." in cwd.parts:
        raise WorkerError("worker_source_changed")
    try:
        _checked_path(root, cwd)
    except Exception:
        raise WorkerError("worker_source_changed") from None
    args = entry.get("args", [])
    if type(args) is not list or len(args) > 256 or any(type(arg) is not str or len(arg) > 65536 for arg in args):
        raise WorkerError("worker_arguments_invalid")
    # Preserve explicit Python argument boundaries. The trusted bootstrap parses
    # Python's invocation forms; no shell or command-string substitution occurs.
    from row_bot.plugins.mcp_process import invocation
    flags, entry_args = invocation(args)
    command_args = ("-I", "-S", "-B", *flags, str(Path(__file__).with_name("mcp_process.py")),
                    str(prepared.environment), str(root), *entry_args)
    return PreparedPluginMcpLaunch(str(manifest.id), str(prepared.interpreter), command_args,
        str(cwd), _resolve_mapping(str(manifest.id), entry.get("env", {})), prepared.operation_id,
        prepared.source_revision, prepared.environment_revision)


def resolve_prepared_plugin_mcp_launch(server_name: str, config: dict, *, for_setup: bool = False) -> PreparedPluginMcpLaunch | None:
    """Validate a canonical enabled Python contribution at launch/dispatch.

    Config markers never establish ownership. A forged or retired plugin marker
    fails closed; ordinary user and non-Python transports retain their owner.
    """
    from row_bot.plugins import registry, state
    from row_bot.plugins.worker import WorkerError
    if for_setup and config.get("source", {}).get("kind") == "plugin":
        source = config["source"]
        expected = read_plugin_mcp_child(source["plugin_id"], source["server_id"])
        from row_bot.mcp_client.config import normalize_server_config
        if server_name != expected["name"] or normalize_server_config(server_name, expected) != config:
            raise WorkerError("worker_source_changed")
        from row_bot.plugins.installer import _source_for_preparation
        from row_bot.plugins.manifest import parse_manifest
        manifest = parse_manifest(_source_for_preparation(source["plugin_id"]))
        entry = next(s for s in manifest.provides.mcp_servers if s["id"] == source["server_id"])
        return _prepared_python(manifest, entry) if _python_entry(entry) and not entry.get("portable") else None
    matches = []
    for manifest in registry.get_loaded_manifests():
        for entry in manifest.provides.mcp_servers:
            if plugin_mcp_server_name(str(manifest.id), str(entry.get("id", ""))) == server_name:
                matches.append((manifest, entry))
    if not matches:
        if type(config.get("source")) is dict and config["source"].get("kind") == "plugin":
            raise WorkerError("worker_revoked")
        return None
    if len(matches) != 1:
        raise WorkerError("worker_source_changed")
    manifest, entry = matches[0]
    if not state.is_plugin_enabled(str(manifest.id)):
        raise WorkerError("worker_revoked")
    if entry.get("portable") or not _python_entry(entry):
        return None
    launch = _prepared_python(manifest, entry)
    expected = _server_config_from_entry(manifest, entry, str(entry["id"]))
    if any(config.get(key) != expected.get(key) for key in ("command", "args", "cwd", "env", "source", "plugin_prepared")):
        raise WorkerError("worker_source_changed")
    if config.get("enabled") is not True:
        raise WorkerError("worker_revoked")
    return launch
