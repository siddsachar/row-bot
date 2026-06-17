"""Row-Bot tools package — import this module to discover all tools.

Each tool module auto-registers itself with the registry on import.
To add a new tool, create a new ``*_tool.py`` module in this package
that subclasses ``BaseTool`` and calls ``registry.register()``.
"""

from row_bot.tools import registry  # noqa: F401 — make registry accessible as tools.registry

# Import every tool module so they self-register.
# When you add a new tool, add its import here.
from row_bot.tools import documents_tool   # noqa: F401
from row_bot.tools import wikipedia_tool   # noqa: F401
from row_bot.tools import arxiv_tool       # noqa: F401
from row_bot.tools import web_search_tool  # noqa: F401
from row_bot.tools import duckduckgo_tool  # noqa: F401
from row_bot.tools import filesystem_tool  # noqa: F401
from row_bot.tools import gmail_tool       # noqa: F401
from row_bot.tools import calendar_tool   # noqa: F401
from row_bot.tools import url_reader_tool # noqa: F401
from row_bot.tools import youtube_tool    # noqa: F401
from row_bot.tools import calculator_tool  # noqa: F401
from row_bot.tools import wolfram_tool     # noqa: F401
from row_bot.tools import weather_tool     # noqa: F401
from row_bot.tools import vision_tool      # noqa: F401
from row_bot.tools import memory_tool      # noqa: F401
from row_bot.tools import conversation_search_tool  # noqa: F401
from row_bot.tools import system_info_tool  # noqa: F401
from row_bot.tools import chart_tool        # noqa: F401
from row_bot.tools import tracker_tool      # noqa: F401
from row_bot.tools import shell_tool        # noqa: F401
from row_bot.tools import task_tool         # noqa: F401
from row_bot.tools import custom_tool_builder_tool  # noqa: F401
from row_bot.tools import image_gen_tool     # noqa: F401
from row_bot.tools import video_gen_tool     # noqa: F401
from row_bot.tools import wiki_tool          # noqa: F401
from row_bot.tools import x_tool             # noqa: F401
from row_bot.tools import row_bot_status_tool  # noqa: F401
from row_bot.tools import agent_tool         # noqa: F401
from row_bot.tools import goal_tool          # noqa: F401
from row_bot.tools import updater_tool       # noqa: F401
from row_bot.tools import mcp_tool           # noqa: F401
from row_bot.tools import developer_tool     # noqa: F401
import row_bot.designer.tool # noqa: F401 — designer tool self-registers
from row_bot import designer
try:                                # browser_tool is still in development
    from row_bot.tools import browser_tool  # noqa: F401
except ImportError:
    pass
