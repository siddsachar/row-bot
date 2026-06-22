"""Row-Bot tools package - import this module to discover all tools.

Each tool module auto-registers itself with the registry on import.
To add a new tool, create a new ``*_tool.py`` module in this package
that subclasses ``BaseTool`` and calls ``registry.register()``.
"""

from __future__ import annotations

import importlib
import logging

from row_bot.tools import registry  # noqa: F401 - make registry accessible as tools.registry

logger = logging.getLogger(__name__)


def _import_tool_module(module_name: str, *, optional: bool = False) -> None:
    try:
        importlib.import_module(module_name)
    except ImportError as exc:
        if optional:
            logger.info("Optional tool module skipped: %s (%s)", module_name, exc)
            return
        raise


_CORE_TOOL_MODULES = (
    "row_bot.tools.documents_tool",
    "row_bot.tools.wikipedia_tool",
    "row_bot.tools.arxiv_tool",
    "row_bot.tools.web_search_tool",
    "row_bot.tools.duckduckgo_tool",
    "row_bot.tools.filesystem_tool",
    "row_bot.tools.gmail_tool",
    "row_bot.tools.calendar_tool",
    "row_bot.tools.url_reader_tool",
    "row_bot.tools.youtube_tool",
    "row_bot.tools.calculator_tool",
    "row_bot.tools.wolfram_tool",
    "row_bot.tools.weather_tool",
    "row_bot.tools.vision_tool",
    "row_bot.tools.memory_tool",
    "row_bot.tools.conversation_search_tool",
    "row_bot.tools.system_info_tool",
    "row_bot.tools.tracker_tool",
    "row_bot.tools.shell_tool",
    "row_bot.tools.task_tool",
    "row_bot.tools.custom_tool_builder_tool",
    "row_bot.tools.image_gen_tool",
    "row_bot.tools.video_gen_tool",
    "row_bot.tools.wiki_tool",
    "row_bot.tools.x_tool",
    "row_bot.tools.row_bot_status_tool",
    "row_bot.tools.agent_tool",
    "row_bot.tools.goal_tool",
    "row_bot.tools.updater_tool",
)

_OPTIONAL_TOOL_MODULES = (
    "row_bot.tools.chart_tool",
    "row_bot.tools.mcp_tool",
    "row_bot.tools.developer_tool",
    "row_bot.designer.tool",
    "row_bot.tools.browser_tool",
)

for _module_name in _CORE_TOOL_MODULES:
    _import_tool_module(_module_name)

for _module_name in _OPTIONAL_TOOL_MODULES:
    _import_tool_module(_module_name, optional=True)
