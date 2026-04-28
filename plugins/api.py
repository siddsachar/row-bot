"""Plugin API and PluginTool base class.

This module defines the interface that plugin authors use — it is the ONLY
module plugins are allowed to import from Thoth core.  Everything else
(tools/, ui/, agent.py, etc.) is off-limits.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# PluginAPI — the object passed to register()
# ═════════════════════════════════════════════════════════════════════════════
class PluginAPI:
    """Bridge between a plugin and Thoth's plugin infrastructure.

    An instance is created per-plugin and passed to the plugin's
    ``register(api)`` function.  The plugin uses it to:

    - Register tools and skills
    - Read/write its own configuration
    - Read its own secrets (API keys)
    - Get its plugin ID and data directory
    """

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: "Any",       # pathlib.Path — avoid import for type stub
        state_backend: "Any",    # plugins.state module
    ):
        self._plugin_id = plugin_id
        self._plugin_dir = plugin_dir
        self._state = state_backend
        self._registered_tools: list[PluginTool] = []
        self._registered_skills: list[dict] = []

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def plugin_dir(self) -> Any:
        """Path to the plugin's installation directory."""
        return self._plugin_dir

    # ── Tool & Skill Registration ────────────────────────────────────────
    def register_tool(self, tool: "PluginTool") -> None:
        """Register a tool with Thoth. Called inside register()."""
        self._registered_tools.append(tool)
        logger.debug("Plugin '%s' registered tool: %s", self._plugin_id, tool.name)

    def register_skill(self, skill_info: dict) -> None:
        """Register a skill dict with Thoth. Usually auto-discovered from skills/."""
        self._registered_skills.append(skill_info)
        logger.debug("Plugin '%s' registered skill: %s",
                      self._plugin_id, skill_info.get("name", "?"))

    # ── Configuration ────────────────────────────────────────────────────
    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a configuration value for this plugin."""
        return self._state.get_plugin_config(self._plugin_id, key, default)

    def set_config(self, key: str, value: Any) -> None:
        """Write a configuration value for this plugin."""
        self._state.set_plugin_config(self._plugin_id, key, value)

    # ── Secrets ──────────────────────────────────────────────────────────
    def get_secret(self, key: str) -> str | None:
        """Read a secret (API key) for this plugin."""
        return self._state.get_plugin_secret(self._plugin_id, key)

    def set_secret(self, key: str, value: str) -> None:
        """Write a secret (API key) for this plugin."""
        self._state.set_plugin_secret(self._plugin_id, key, value)

    # ── Runtime Context ─────────────────────────────────────────────────
    def is_background_workflow(self) -> bool:
        """Return True when the current tool call is running from a background task."""
        try:
            from agent import is_background_workflow
            return bool(is_background_workflow())
        except Exception as exc:
            logger.debug("Plugin background workflow check unavailable: %s", exc)
            return False

    def get_allowed_recipients(self) -> list[str]:
        """Return email recipients approved for the current background task."""
        try:
            from agent import _task_allowed_recipients_var
            recipients = _task_allowed_recipients_var.get() or []
            return [str(recipient) for recipient in recipients]
        except Exception as exc:
            logger.debug("Plugin allowed recipients unavailable: %s", exc)
            return []


# ═════════════════════════════════════════════════════════════════════════════
# PluginTool — base class for plugin tools
# ═════════════════════════════════════════════════════════════════════════════
class PluginTool:
    """Base class for plugin-provided tools.

    Mirrors the core ``BaseTool`` interface but lives entirely in the
    plugin namespace.  Plugin authors subclass this and register
    instances via ``api.register_tool(MyTool(api))``.
    """

    def __init__(self, plugin_api: PluginAPI):
        self.plugin_api = plugin_api

    # ── Identity (override in subclass) ──────────────────────────────────
    @property
    def name(self) -> str:
        """Internal unique identifier. Must be lowercase with underscores."""
        raise NotImplementedError

    @property
    def display_name(self) -> str:
        """Human-readable label shown in Settings and agent output."""
        raise NotImplementedError

    @property
    def description(self) -> str:
        """One-line description passed to the agent for tool selection."""
        return ""

    # ── Destructive / background permissions ────────────────────────────
    @property
    def destructive_tool_names(self) -> set[str]:
        """Names of sub-tools requiring user confirmation before execution.

        Override in subclasses that expose multiple LangChain tools via
        ``as_langchain_tools()`` and need some of them gated by a
        LangGraph ``interrupt()`` (e.g. send_email, delete_event).
        """
        return set()

    @property
    def background_allowed_tool_names(self) -> set[str]:
        """Destructive sub-tools allowed in background tasks.

        These tools must implement their own runtime permission checks
        (e.g. validating recipients against ``_task_allowed_recipients_var``).
        Only meaningful for tools that are also in ``destructive_tool_names``.
        """
        return set()

    # ── Execution ────────────────────────────────────────────────────────
    def execute(self, query: str) -> str:
        """Run the tool and return a text result.

        Must handle its own errors gracefully — return error messages
        instead of raising exceptions.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement execute()")

    # ── Rich return helpers ──────────────────────────────────────────────
    @staticmethod
    def image_result(b64: str, text: str = "") -> str:
        """Wrap a base64-encoded image so the UI renders it inline."""
        return f"__IMAGE__:{b64}\n\n{text}" if text else f"__IMAGE__:{b64}"

    @staticmethod
    def html_result(html: str, text: str = "") -> str:
        """Wrap HTML content so the UI renders it inline."""
        return f"__HTML__:{html}\n\n{text}" if text else f"__HTML__:{html}"

    @staticmethod
    def chart_result(plotly_json: str, text: str = "") -> str:
        """Wrap a Plotly figure JSON so the UI renders an interactive chart."""
        return f"__CHART__:{plotly_json}\n\n{text}" if text else f"__CHART__:{plotly_json}"

    # ── LangChain Bridge ─────────────────────────────────────────────────
    def as_langchain_tool(self) -> StructuredTool:
        """Convert to a LangChain StructuredTool for the agent."""
        tool_instance = self

        def _run(query: str) -> str:
            try:
                return tool_instance.execute(query)
            except Exception as exc:
                logger.error("Plugin tool '%s' error: %s", tool_instance.name,
                             exc, exc_info=True)
                return f"Error in {tool_instance.display_name}: {exc}"

        return StructuredTool.from_function(
            func=_run,
            name=self.name,
            description=f"{self.display_name}: {self.description}",
        )

    def as_langchain_tools(self) -> list:
        """Return one or more LangChain tools. Override for multi-tool plugins."""
        return [self.as_langchain_tool()]
