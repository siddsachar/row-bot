"""Tool registry — discovers, stores, and manages all retrieval tools.

Usage
-----
    from row_bot.tools import registry

    for tool in registry.get_enabled_tools():
        results = tool.get_retriever().invoke(query)
"""

from __future__ import annotations

import json
import logging
import copy
import os
import pathlib
from typing import TYPE_CHECKING

from row_bot.data_paths import get_row_bot_data_dir
from row_bot import tool_configuration

if TYPE_CHECKING:
    from row_bot.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Persist enabled / disabled state alongside other Row-Bot data.  The path is
# resolved dynamically because tests and channel runtimes may isolate
# ROW_BOT_DATA_DIR after module import.
def _data_dir() -> pathlib.Path:
    data_dir = get_row_bot_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _config_path() -> pathlib.Path:
    return _data_dir() / "tools_config.json"


DATA_DIR = _data_dir()
_CONFIG_PATH = _config_path()
_active_config_path = _CONFIG_PATH

# ── Internal storage ─────────────────────────────────────────────────────────────
_tools: dict[str, "BaseTool"] = {}          # name → tool instance
_enabled: dict[str, bool] = {}              # name → enabled flag (runtime cache)
_tool_configs: dict[str, dict] = {}         # name → {key: value} (tool-specific config)
_CONFIG_LOCK = tool_configuration.LOCK


# ── Config persistence ───────────────────────────────────────────────────────
def _read_config(path: pathlib.Path) -> dict:
    """Load the persisted config from disk."""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load tools config from %s", path, exc_info=True)
            return {}
    return {}


def _load_config() -> dict:
    return _read_config(_config_path())


def _write_config_atomic(path: pathlib.Path, payload: dict, *, expected_digest: str | None = None) -> None:
    tool_configuration.legacy_publish(path, payload, expected_digest=expected_digest)


def _apply_saved_config(tool: "BaseTool", saved: dict) -> None:
    # Support new format {"tools": {...}} and old flat format
    tools_map = saved.get("tools", saved) if isinstance(saved.get("tools"), dict) else saved
    if tool.name in tools_map:
        _enabled[tool.name] = bool(tools_map[tool.name])
    else:
        _enabled[tool.name] = tool.enabled_by_default

    # Restore persisted tool-specific config
    saved_configs = saved.get("tool_configs", {})
    if tool.name in saved_configs and isinstance(saved_configs.get(tool.name), dict):
        _tool_configs[tool.name] = dict(saved_configs[tool.name])
        # Auto-migrate: if the schema adds new default operations that didn't
        # exist before, merge them in so existing users get new sub-tools.
        # We track which options were known at last save via _known_options.
        for key, spec in tool.config_schema.items():
            if spec.get("type") != "multicheck":
                continue
            known_key = f"_{key}_known"
            current_options = set(spec.get("options", []))
            previously_known = set(_tool_configs[tool.name].get(known_key, []))
            if previously_known:
                new_ops = current_options - previously_known
                default_ops = set(spec.get("default", []))
                to_add = new_ops & default_ops  # only auto-enable if in defaults
                if to_add and key in _tool_configs[tool.name]:
                    saved_ops = set(_tool_configs[tool.name][key])
                    _tool_configs[tool.name][key] = list(saved_ops | to_add)
                    logger.info("Auto-enabled new operations for %s: %s", tool.name, to_add)
            # Always update the known set to current options
            _tool_configs[tool.name][known_key] = list(current_options)
    else:
        # Initialise from schema defaults
        _tool_configs.setdefault(tool.name, {})
        for key, spec in tool.config_schema.items():
            if key not in _tool_configs[tool.name]:
                _tool_configs[tool.name][key] = spec.get("default")


def _ensure_config_scope() -> None:
    """Reload tool settings if ROW_BOT_DATA_DIR changed after module import."""
    global DATA_DIR, _CONFIG_PATH, _active_config_path
    current = _config_path()
    if current == _active_config_path:
        return
    logger.info("Tool config scope changed: %s -> %s", _active_config_path, current)
    DATA_DIR = current.parent
    _CONFIG_PATH = current
    _active_config_path = current
    _enabled.clear()
    _tool_configs.clear()
    saved = _read_config(current)
    for tool in _tools.values():
        _apply_saved_config(tool, saved)


def _save_config():
    """Persist the current enabled/disabled map and tool configs to disk."""
    with _CONFIG_LOCK:
        _ensure_config_scope()
        saved = tool_configuration.read_saved(_config_path())
        document = tool_configuration.editable_document(saved)
        tool_configuration.tools_map(document).update(copy.deepcopy(_enabled))
        configs = document.setdefault("tool_configs", {})
        for name, values in _tool_configs.items():
            if name in configs and type(configs[name]) is not dict:
                raise tool_configuration.ToolConfigurationError("tool_configuration_unavailable")
            configs.setdefault(name, {}).update(copy.deepcopy(values))
        _write_config_atomic(_config_path(), document, expected_digest=saved.digest)


# ── Public API ───────────────────────────────────────────────────────────────
def register(tool: "BaseTool") -> None:
    """Register a tool instance.  Called by each tool module at import time."""
    logger.debug("Registering tool: %s", tool.name)
    with _CONFIG_LOCK:
        _ensure_config_scope()
        _tools[tool.name] = tool
        # If the user already toggled this tool, honour that; otherwise use default
        saved = _load_config()
        _apply_saved_config(tool, saved)


def get_all_tools() -> list["BaseTool"]:
    """Return all registered tools (enabled + disabled), sorted by name."""
    _ensure_config_scope()
    return [_tools[n] for n in sorted(_tools)]


def get_enabled_tools() -> list["BaseTool"]:
    """Return only the tools the user has enabled."""
    enabled = [t for t in get_all_tools() if is_enabled(t.name)]
    if os.environ.get("ROW_BOT_LIVE_CHAT_PARITY") == "1":
        # The owner-profile parity run is authorized to exercise only the
        # statically verified, local, read-only calculator.  This ephemeral
        # restriction never rewrites the owner's saved tool configuration.
        return [tool for tool in enabled if tool.name == "calculator"]
    return enabled


def is_enabled(name: str) -> bool:
    _ensure_config_scope()
    return _enabled.get(name, False)


def set_enabled(name: str, value: bool) -> None:
    with _CONFIG_LOCK:
        _ensure_config_scope()
        tool = get_tool(name)
        if tool is None:
            raise KeyError(f"Unknown tool '{name}'")
        saved = tool_configuration.read_saved(_config_path())
        document = tool_configuration.editable_document(saved)
        tool_configuration.tools_map(document)[tool.name] = value
        _write_config_atomic(_config_path(), document, expected_digest=saved.digest)
        _enabled[tool.name] = value
    logger.info("Tool '%s' %s", tool.name, "enabled" if value else "disabled")
    _invalidate_agent_cache()
    # Also invalidate the task tool-inference keyword map
    try:
        from row_bot.tasks import invalidate_keyword_map_cache
        invalidate_keyword_map_cache()
    except ImportError:
        pass


def get_tool(name: str) -> "BaseTool | None":
    return _tools.get(name)


def get_passive_tool_records() -> list[dict]:
    """Copy registered metadata without defaults, scope migration or tool code."""
    from row_bot.agent_tool_catalog import _passive_tool_fields
    from itertools import islice

    same_scope = get_row_bot_data_dir(create=False) / "tools_config.json" == _active_config_path
    enabled = _enabled.copy() if same_scope else {}
    return [
        {**_passive_tool_fields(name, tool), "enabled": enabled.get(name)}
        for name, tool in islice(_tools.copy().items(), 10001)
    ]


def read_policy_snapshot() -> dict:
    """Observe saved policy and loaded registrations without readiness probes."""
    import inspect

    path = get_row_bot_data_dir(create=False) / "tools_config.json"
    saved = tool_configuration.read_saved(path)
    with _CONFIG_LOCK:
        if len(_tools) > 10000:
            raise ValueError("tool_policy_unavailable")
        same_scope = path == _active_config_path
        registrations = []
        for name, tool in sorted(_tools.items()):
            destructive = inspect.getattr_static(tool, "destructive_tool_names", None)
            if type(destructive) in (set, frozenset):
                if len(destructive) > 1000 or any(type(item) is not str or len(item) > 256 for item in destructive):
                    raise ValueError("tool_policy_unavailable")
                effects = sorted(destructive)
            else:
                # A dynamic descriptor may depend on saved policy, but reading
                # it here must not construct tools or discover providers.
                effects = {"descriptor": id(destructive)}
            registrations.append((name, id(tool), _enabled.get(name) if same_scope else None, effects))
        return {"saved_revision": saved.digest, "registrations": registrations,
            "tool_configs": copy.deepcopy(_tool_configs) if same_scope else {},
            "global_config": copy.deepcopy(_global_config) if same_scope else {}}


def get_all_required_api_keys() -> dict[str, str]:
    """Aggregate ``required_api_keys`` from *all* registered tools.
    Returns ``{UI label: ENV_VAR_NAME}``.
    """
    keys: dict[str, str] = {}
    for tool in get_all_tools():
        keys.update(tool.required_api_keys)
    return keys


def get_tool_config(tool_name: str, key: str, default=None):
    """Read a persisted config value for a tool."""
    _ensure_config_scope()
    return _tool_configs.get(tool_name, {}).get(key, default)


def set_tool_config(tool_name: str, key: str, value):
    """Write a config value for a tool and persist."""
    with _CONFIG_LOCK:
        _ensure_config_scope()
        saved = tool_configuration.read_saved(_config_path())
        document = tool_configuration.editable_document(saved)
        values = document.setdefault("tool_configs", {}).setdefault(tool_name, {})
        if type(values) is not dict:
            raise tool_configuration.ToolConfigurationError("tool_configuration_unavailable")
        values[key] = copy.deepcopy(value)
        _write_config_atomic(_config_path(), document, expected_digest=saved.digest)
        _tool_configs.setdefault(tool_name, {})[key] = value
    logger.info("Tool config updated: %s.%s", tool_name, key)
    _invalidate_agent_cache()


def _invalidate_agent_cache():
    """Clear cached agent graphs when tool settings change."""
    try:
        from row_bot.agent import clear_agent_cache
        clear_agent_cache()
    except ImportError:
        pass


# ── Global (non-tool-specific) config ────────────────────────────────────────
_global_config: dict = {}

def _load_global_config():
    """Bootstrap global config from the persisted file."""
    global _global_config
    _ensure_config_scope()
    saved = _load_config()
    _global_config = saved.get("global", {})


def reload_saved_config() -> None:
    """Refresh loaded tool caches after another canonical settings writer."""

    global _global_config
    with _CONFIG_LOCK:
        _ensure_config_scope()
        saved = _load_config()
        _enabled.clear()
        _tool_configs.clear()
        for tool in _tools.values():
            _apply_saved_config(tool, saved)
        global_config = saved.get("global", {})
        _global_config = (
            copy.deepcopy(global_config) if isinstance(global_config, dict) else {}
        )
    _invalidate_agent_cache()

def get_global_config(key: str, default=None):
    """Read a global (non-tool-specific) config value."""
    if not _global_config:
        _load_global_config()
    return _global_config.get(key, default)

def set_global_config(key: str, value) -> None:
    """Write a global config value and persist."""
    global _global_config
    with _CONFIG_LOCK:
        _ensure_config_scope()
        saved = tool_configuration.read_saved(_config_path())
        document = tool_configuration.editable_document(saved)
        values = document.setdefault("global", {})
        values[key] = copy.deepcopy(value)
        _write_config_atomic(_config_path(), document, expected_digest=saved.digest)
        _global_config = copy.deepcopy(values)
    _invalidate_agent_cache()


def get_external_tool_loading_mode() -> str:
    """Return the normalized external-tool binding mode."""

    value = str(get_global_config("external_tool_loading_mode", "auto") or "auto").strip().lower()
    return value if value in {"auto", "eager"} else "auto"


def get_langchain_tools() -> list:
    """Return LangChain-compatible tool wrappers for all enabled tools.
    Uses ``as_langchain_tools()`` (plural) so tools contributing multiple
    operations are handled correctly."""
    tools = []
    for t in get_enabled_tools():
        tools.extend(t.as_langchain_tools())
    return tools
