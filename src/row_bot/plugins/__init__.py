"""Row-Bot Plugin System — total separation from core.

Plugins are discovered from ``~/.row-bot/installed_plugins/<id>/`` and loaded
at startup.  Each plugin provides tools and/or skills that are injected into
the agent alongside (but separate from) built-in tools and skills.

The plugin system intentionally avoids importing anything from ``tools/``,
``skills.py``, or any other core module.  The only integration points are:

- ``agent.py``  appends plugin LangChain tools + skills prompt
- ``app.py``    calls ``refresh_plugin_runtime()`` at startup
- ``ui/settings.py``  adds a Plugins tab
"""

from row_bot.plugins.loader import get_load_summary, load_plugins, refresh_plugin_runtime

__all__ = ["load_plugins", "get_load_summary", "refresh_plugin_runtime"]
