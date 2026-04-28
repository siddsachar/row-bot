"""Core MCP client support for Thoth.

The package is deliberately defensive: optional dependencies, remote directory
failures, bad server configs, and broken MCP servers must degrade to status
entries and logs instead of breaking Thoth startup or chat.
"""

from __future__ import annotations

from mcp_client.config import get_config, is_globally_enabled
from mcp_client.runtime import (
    discover_enabled_servers,
    get_langchain_tools,
    get_status_summary,
    get_destructive_tool_names,
    shutdown,
)

__all__ = [
    "discover_enabled_servers",
    "get_config",
    "get_destructive_tool_names",
    "get_langchain_tools",
    "get_status_summary",
    "is_globally_enabled",
    "shutdown",
]