"""Core MCP client support for Row-Bot.

The package is deliberately defensive: optional dependencies, remote directory
failures, bad server configs, and broken MCP servers must degrade to status
entries and logs instead of breaking Row-Bot startup or chat.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "discover_enabled_servers",
    "get_config",
    "get_destructive_tool_names",
    "get_langchain_tools",
    "get_status_summary",
    "is_globally_enabled",
    "shutdown",
]


def __getattr__(name: str):
    """Retain public imports without starting optional runtime imports on reads."""
    if name not in __all__:
        raise AttributeError(name)
    owner = "config" if name in {"get_config", "is_globally_enabled"} else "runtime"
    value = getattr(import_module(f"row_bot.mcp_client.{owner}"), name)
    globals()[name] = value
    return value
