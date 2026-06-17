"""Plugin registry — separate from tools.registry.

Stores loaded plugin tools and skills, provides LangChain-compatible
tool wrappers and skills prompt text for injection into the agent.

This registry is completely independent of the core tools/registry.py.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.plugins.api import PluginTool
    from row_bot.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

# ── Internal storage ─────────────────────────────────────────────────────────
_plugin_tools: dict[str, "PluginTool"] = {}       # tool_name → tool instance
_plugin_skills: dict[str, dict] = {}              # skill_name → skill info dict
_loaded_manifests: dict[str, "PluginManifest"] = {}  # plugin_id → manifest
_tool_to_plugin: dict[str, str] = {}              # tool_name → plugin_id
_skill_to_plugin: dict[str, str] = {}             # skill_name → plugin_id


# ── Registration (called by loader) ──────────────────────────────────────────
def register_plugin(manifest: "PluginManifest",
                     tools: list["PluginTool"],
                     skills: list[dict]) -> list[str]:
    """Register a loaded plugin's tools and skills.

    Returns a list of warnings (e.g. name collisions with built-in tools).
    """
    warnings: list[str] = []
    _loaded_manifests[manifest.id] = manifest

    for tool in tools:
        # Check for collision with built-in tools
        if _name_collides_with_builtin(tool.name):
            warnings.append(
                f"Plugin '{manifest.id}' tool '{tool.name}' collides with a "
                f"built-in tool — skipped"
            )
            continue
        # Check for collision with other plugin tools
        if tool.name in _plugin_tools:
            other = _tool_to_plugin.get(tool.name, "unknown")
            warnings.append(
                f"Plugin '{manifest.id}' tool '{tool.name}' collides with "
                f"plugin '{other}' — skipped"
            )
            continue
        _plugin_tools[tool.name] = tool
        _tool_to_plugin[tool.name] = manifest.id

    for skill in skills:
        skill_name = skill.get("name", "")
        if not skill_name:
            continue
        if skill_name in _plugin_skills:
            other = _skill_to_plugin.get(skill_name, "unknown")
            warnings.append(
                f"Plugin '{manifest.id}' skill '{skill_name}' collides with "
                f"plugin '{other}' — skipped"
            )
            continue
        _plugin_skills[skill_name] = skill
        _skill_to_plugin[skill_name] = manifest.id

    return warnings


# ── Public API ───────────────────────────────────────────────────────────────
def _allow_set(allow_names: Iterable[str] | None) -> set[str] | None:
    if allow_names is None:
        return None
    return {str(name) for name in allow_names if str(name or "").strip()}


def _plugin_runtime_allowed(
    *,
    allow: set[str] | None,
    runtime_name: str,
    parent_name: str,
    plugin_id: str,
) -> bool:
    if allow is None:
        return True
    return runtime_name in allow or parent_name in allow or plugin_id in allow


def get_langchain_tools(allow_names: Iterable[str] | None = None) -> list:
    """Return LangChain tool wrappers for all tools from enabled plugins."""
    from row_bot.plugins import state

    allow = _allow_set(allow_names)
    tools = []
    for tool_name, tool in _plugin_tools.items():
        plugin_id = _tool_to_plugin.get(tool_name)
        if plugin_id and state.is_plugin_enabled(plugin_id):
            for lc_tool in tool.as_langchain_tools():
                runtime_name = str(getattr(lc_tool, "name", "") or tool_name)
                if _plugin_runtime_allowed(
                    allow=allow,
                    runtime_name=runtime_name,
                    parent_name=tool_name,
                    plugin_id=plugin_id,
                ):
                    tools.append(lc_tool)
    return tools


def get_skills_prompt() -> str:
    """Return skills prompt text from all enabled plugins."""
    from row_bot.plugins import state

    parts: list[str] = []
    for skill_name, skill_info in _plugin_skills.items():
        plugin_id = _skill_to_plugin.get(skill_name)
        if plugin_id and state.is_plugin_enabled(plugin_id):
            instructions = skill_info.get("instructions", "")
            if instructions:
                icon = skill_info.get("icon", "🔌")
                display = skill_info.get("display_name", skill_name)
                parts.append(f"\n### {icon} {display} (plugin)\n{instructions}\n")

    if not parts:
        return ""

    return (
        "## Plugin Skills\n\n"
        "The following skills are provided by installed plugins.\n"
        + "\n".join(parts)
    )


def get_plugin_tool_names() -> list[str]:
    """Return names of all registered plugin tools (enabled or not)."""
    return list(_plugin_tools.keys())


def get_destructive_names(allow_names: Iterable[str] | None = None) -> set[str]:
    """Return destructive sub-tool names from all enabled plugin tools."""
    from row_bot.plugins import state

    allow = _allow_set(allow_names)
    names: set[str] = set()
    for tool_name, tool in _plugin_tools.items():
        plugin_id = _tool_to_plugin.get(tool_name)
        if plugin_id and state.is_plugin_enabled(plugin_id):
            if allow is None or tool_name in allow or plugin_id in allow:
                names.update(tool.destructive_tool_names)
            else:
                names.update(name for name in tool.destructive_tool_names if name in allow)
    return names


def get_background_allowed_names() -> set[str]:
    """Return background-allowed destructive sub-tool names from all enabled plugin tools."""
    from row_bot.plugins import state

    names: set[str] = set()
    for tool_name, tool in _plugin_tools.items():
        plugin_id = _tool_to_plugin.get(tool_name)
        if plugin_id and state.is_plugin_enabled(plugin_id):
            names.update(tool.background_allowed_tool_names)
    return names


def get_enabled_plugin_tool_names() -> list[str]:
    """Return names of tools from enabled plugins only."""
    from row_bot.plugins import state
    return [
        name for name, tool in _plugin_tools.items()
        if state.is_plugin_enabled(_tool_to_plugin.get(name, ""))
    ]


def get_enabled_plugin_tool_records() -> list[dict]:
    """Return enabled plugin runtime tool metadata for Agent Profile cataloging."""
    from row_bot.plugins import state

    records: list[dict] = []
    for tool_name, tool in _plugin_tools.items():
        plugin_id = _tool_to_plugin.get(tool_name, "")
        if not plugin_id or not state.is_plugin_enabled(plugin_id):
            continue
        manifest = _loaded_manifests.get(plugin_id)
        tags = list(getattr(manifest, "tags", []) or []) if manifest else []
        plugin_name = str(getattr(manifest, "name", "") or plugin_id)
        manifest_description = str(getattr(manifest, "description", "") or "")
        try:
            lc_tools = list(tool.as_langchain_tools())
        except Exception as exc:
            logger.debug("Plugin tool catalog skipped %s: %s", tool_name, exc, exc_info=True)
            continue
        destructive = set(getattr(tool, "destructive_tool_names", set()) or set())
        for lc_tool in lc_tools:
            runtime_name = str(getattr(lc_tool, "name", "") or tool_name)
            label = str(getattr(tool, "display_name", "") or runtime_name)
            if runtime_name != tool_name:
                label = f"{label} / {runtime_name}"
            records.append({
                "runtime_name": runtime_name,
                "parent_name": tool_name,
                "plugin_id": plugin_id,
                "plugin_name": plugin_name,
                "tags": tags,
                "label": label,
                "description": str(
                    getattr(lc_tool, "description", "")
                    or getattr(tool, "description", "")
                    or manifest_description
                    or ""
                ),
                "destructive": runtime_name in destructive,
            })
    return records


def get_loaded_manifests() -> list["PluginManifest"]:
    """Return manifests of all loaded plugins."""
    return list(_loaded_manifests.values())


def get_manifest(plugin_id: str) -> "PluginManifest | None":
    return _loaded_manifests.get(plugin_id)


def get_plugin_tools(plugin_id: str) -> list["PluginTool"]:
    """Return all tools belonging to a specific plugin."""
    return [
        tool for name, tool in _plugin_tools.items()
        if _tool_to_plugin.get(name) == plugin_id
    ]


def get_plugin_skills(plugin_id: str) -> list[dict]:
    """Return all skills belonging to a specific plugin."""
    return [
        info for name, info in _plugin_skills.items()
        if _skill_to_plugin.get(name) == plugin_id
    ]


# ── Unregister ───────────────────────────────────────────────────────────────
def unregister_plugin(plugin_id: str) -> None:
    """Remove all tools and skills for a plugin."""
    tool_names = [n for n, pid in _tool_to_plugin.items() if pid == plugin_id]
    for name in tool_names:
        _plugin_tools.pop(name, None)
        _tool_to_plugin.pop(name, None)

    skill_names = [n for n, pid in _skill_to_plugin.items() if pid == plugin_id]
    for name in skill_names:
        _plugin_skills.pop(name, None)
        _skill_to_plugin.pop(name, None)

    _loaded_manifests.pop(plugin_id, None)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _name_collides_with_builtin(tool_name: str) -> bool:
    """Check if a plugin tool name collides with a built-in tool."""
    try:
        from row_bot.tools import registry as core_registry
        return core_registry.get_tool(tool_name) is not None
    except ImportError:
        return False


# ── Reset (for testing) ─────────────────────────────────────────────────────
def _reset():
    """Clear all plugin registrations. For testing only."""
    _plugin_tools.clear()
    _plugin_skills.clear()
    _loaded_manifests.clear()
    _tool_to_plugin.clear()
    _skill_to_plugin.clear()
