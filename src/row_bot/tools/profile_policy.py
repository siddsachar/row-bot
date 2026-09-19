"""Action-level read-only profile limits at canonical tool dispatch.

These limits complement existing approval and sandbox owners. They are not an
OS sandbox, nor a promise that observational tools never update local caches.
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
import sys
from typing import Any

# Reviewed read halves of the existing built-in composite tools. No prefix or
# user-supplied description can turn an unknown action into a read operation.
_READ_OPERATIONS = {
    "memory": {"search_memory", "list_memories", "explore_connections"},
    "filesystem": {
        "workspace_read_file",
        "workspace_list_directory",
        "workspace_file_search",
    },
    "developer": {
        "developer_workspace_info",
        "developer_list_files",
        "developer_read_file",
        "developer_search",
        "developer_git_status",
        "developer_get_diff",
        "developer_preview_patch",
        "developer_list_agent_changes",
    },
    "browser": {"browser_snapshot"},
    "row_bot_status": {"row_bot_status"},
    "conversation_search": {"search_conversations", "list_conversations"},
    "calculator": {"calculate"},
    "system_info": {"get_system_info"},
    "wiki": {"wiki_read", "wiki_stats"},
    "youtube": {"youtube_search", "youtube_transcript"},
    "url_reader": {"read_url"},
    "agents": {"agent_status", "agent_wait", "agent_profiles"},
    "weather": {"get_current_weather", "get_weather_forecast"},
}
_RETRIEVAL_PARENTS = {
    "arxiv",
    "documents",
    "duckduckgo",
    "web_search",
    "wikipedia",
    "wolfram_alpha",
}
_BRIDGE_READS = {"tool_search", "skill_search", "skill_load"}


def call_arguments(function: Any, args: tuple, kwargs: dict) -> dict | None:
    """Bind positional calls without guessing a composite action argument."""
    try:
        return dict(inspect.signature(function).bind_partial(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        return dict(kwargs) if not args else None


def _mcp_effect(runtime_name: str) -> str:
    # Read the already-owned classification; never synchronize config, resolve
    # plugin secrets, connect, discover or trust worker-provided metadata here.
    runtime = sys.modules.get("row_bot.mcp_client.runtime")
    if runtime is None:
        return "unknown"
    with runtime._runtime_lock:
        matches = [
            info
            for tools in runtime._catalog.values()
            for info in tools.values()
            if info.prefixed_name == runtime_name
        ]
        if len(matches) != 1:
            return "unknown"
        info = matches[0]
        if info.destructive:
            return "mutation"
        return info.effect or "unknown"


def action_effect(
    name: str, arguments: dict | None, *, source: str, parent: str
) -> str:
    """Classify only the exact registered source and existing operation."""
    if arguments is None:
        return "unknown"
    if source == "bridge":
        return "read_only" if name in _BRIDGE_READS else "unknown"
    if source == "mcp" or ":mcp:" in source:
        return _mcp_effect(name)
    if source != "core":
        return "unknown"
    if parent in _RETRIEVAL_PARENTS and name == parent:
        return "read_only"
    if parent == "browser" and name == "browser_tab":
        return (
            "read_only" if arguments.get("action", "list") == "list" else "interaction"
        )
    if parent == "developer" and name == "developer_search":
        query = arguments.get("query")
        if type(query) is not str or query.startswith("-"):
            return "unknown"  # The existing rg caller must never consume options.
    if name in _READ_OPERATIONS.get(parent, ()):
        return "read_only"
    return "unknown"


def dispatch_refusal(
    profile: Mapping[str, Any] | None,
    name: str,
    arguments: dict | None,
    *,
    source: str,
    parent: str,
    allowlist: tuple[str, ...] | None = None,
) -> str | None:
    """Return a refusal before approval/effects, without weakening other gates."""
    if source != "bridge" and allowlist is not None:
        allowed = set(allowlist)
        plugin = (
            source.split(":")[1] if source.startswith(("plugin:", "custom:")) else ""
        )
        if name not in allowed and parent not in allowed and plugin not in allowed:
            return "BLOCKED: This tool is outside the current runtime tool allowlist."
    if not profile:
        return None
    if profile.get("enabled", True) is not True:
        return "BLOCKED: The selected Agent Profile is unavailable."
    policy = profile.get("tool_policy_json")
    policy = policy if isinstance(policy, Mapping) else {}
    if source != "bridge" and policy.get("deny_tools"):
        denied = policy["deny_tools"]
        if not isinstance(denied, (list, tuple)) or any(
            type(item) is not str for item in denied
        ):
            return "BLOCKED: The selected Agent Profile tool policy is unavailable."
        plugin = (
            source.split(":")[1] if source.startswith(("plugin:", "custom:")) else ""
        )
        if name in denied or parent in denied or plugin in denied:
            return "BLOCKED: This tool is denied by the selected Agent Profile."
    if source != "bridge" and policy.get("allow_tools"):
        selected = policy["allow_tools"]
        if not isinstance(selected, (list, tuple)) or any(
            type(item) is not str for item in selected
        ):
            return "BLOCKED: The selected Agent Profile tool policy is unavailable."
        plugin = (
            source.split(":")[1] if source.startswith(("plugin:", "custom:")) else ""
        )
        if name not in selected and parent not in selected and plugin not in selected:
            return "BLOCKED: This tool is outside the selected Agent Profile allowlist."
    if (
        name == "delegate_work"
        and source == "core"
        and not policy.get("allow_delegation")
    ):
        return "BLOCKED: This Agent Profile does not allow delegation."
    capability = policy.get("capability", "read_only")
    if capability in {"write_capable", "orchestrator"}:
        return None
    if (
        capability != "read_only"
        or action_effect(name, arguments, source=source, parent=parent) != "read_only"
    ):
        return "BLOCKED: This action is unavailable in a read-only Agent Profile."
    return None
