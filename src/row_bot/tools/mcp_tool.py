"""Native parent tool for external MCP servers."""

from __future__ import annotations

import logging
from functools import wraps

from row_bot.tools.base import BaseTool
from row_bot.tools import registry

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
            from row_bot.mcp_client.runtime import get_destructive_tool_names
            return get_destructive_tool_names()
        except Exception as exc:
            logger.debug("MCP destructive tool lookup failed: %s", exc)
            return set()

    @property
    def inference_keywords(self) -> list[str]:
        return ["mcp", "model context protocol", "external tool", "external server"]

    def execute(self, query: str) -> str:
        try:
            from row_bot.mcp_client.runtime import get_status_summary
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
            from row_bot.mcp_client.runtime import get_langchain_tools
            return self.bind_langchain_tools(get_langchain_tools())
        except Exception as exc:
            logger.warning("MCP dynamic tool injection skipped: %s", exc, exc_info=True)
            return []

    def bind_langchain_tools(self, tools: list) -> list:
        """Guard an already-collected snapshot without refreshing its owner."""
        from row_bot.tool_configuration import configuration_path
        scope = configuration_path()

        def validate():
            if (configuration_path() != scope or registry._active_config_path != scope
                    or registry._tools.get("mcp") is not self or registry._enabled.get("mcp") is not True):
                raise RuntimeError("Native MCP capability was revoked or replaced")

        def sync(function):
            @wraps(function)
            def guarded(*args, **kwargs):
                validate()
                return function(*args, **kwargs)
            return guarded

        def asynchronous(function):
            @wraps(function)
            async def guarded(*args, **kwargs):
                validate()
                return await function(*args, **kwargs)
            return guarded

        result = []
        for tool in tools:
            updates = {}
            if getattr(tool, "func", None) is not None:
                updates["func"] = sync(tool.func)
            else:
                updates["_run"] = sync(tool._run)
            if getattr(tool, "coroutine", None) is not None:
                updates["coroutine"] = asynchronous(tool.coroutine)
            else:
                updates["_arun"] = asynchronous(tool._arun)
            result.append(tool.model_copy(update=updates))
        return result


registry.register(McpTool())
