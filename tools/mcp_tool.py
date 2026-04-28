"""Native parent tool for external MCP servers."""

from __future__ import annotations

import logging

from tools.base import BaseTool
from tools import registry

logger = logging.getLogger(__name__)


class McpTool(BaseTool):
    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "External MCP Tools"

    @property
    def description(self) -> str:
        return "Use tools exposed by configured external Model Context Protocol servers."

    @property
    def enabled_by_default(self) -> bool:
        return False

    @property
    def destructive_tool_names(self) -> set[str]:
        try:
            from mcp_client.runtime import get_destructive_tool_names
            return get_destructive_tool_names()
        except Exception as exc:
            logger.debug("MCP destructive tool lookup failed: %s", exc)
            return set()

    @property
    def inference_keywords(self) -> list[str]:
        return ["mcp", "model context protocol", "external tool", "external server"]

    def execute(self, query: str) -> str:
        try:
            from mcp_client.runtime import get_status_summary
            summary = get_status_summary()
            return (
                "MCP is managed through dynamic MCP server tools. "
                f"Servers: {summary['enabled_server_count']} enabled, "
                f"{summary['connected_server_count']} connected. "
                f"Tools: {summary['enabled_tool_count']} enabled."
            )
        except Exception as exc:
            return f"MCP status unavailable: {exc}"

    def as_langchain_tools(self) -> list:
        try:
            from mcp_client.runtime import get_langchain_tools
            return get_langchain_tools()
        except Exception as exc:
            logger.warning("MCP dynamic tool injection skipped: %s", exc, exc_info=True)
            return []


registry.register(McpTool())