"""Action-level read-only profile limits at canonical tool dispatch.

These limits complement existing approval and sandbox owners. They are not an
OS sandbox, nor a promise that observational tools never update local caches.
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
import sys
import typing
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
    "browser": {"browser_extract", "browser_wait_for"},
    "row_bot_status": {"row_bot_status", "row_bot_docs"},
    "conversation_search": {"search_conversations", "list_conversations"},
    "calculator": {"calculate"},
    "system_info": {"get_system_info"},
    "wiki": {"wiki_read", "wiki_stats"},
    "youtube": {"youtube_search", "youtube_transcript"},
    "url_reader": {"read_url"},
    "agents": {"agent_status", "agent_wait", "agent_profiles"},
    "weather": {"get_current_weather", "get_weather_forecast"},
    "pages": {"page_read"},
    "x": {"x_accounts"},
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
# The read halves of the mail and calendar tools: a proactive preparation (a meeting brief, a drafted
# reply) may read the person's mail and events, and nothing else they offer.
_MAIL_CALENDAR_READS = {
    "gmail": {"search_gmail", "get_gmail_message", "get_gmail_thread"},
    "calendar": {"search_events", "get_current_datetime"},
    "outlook_mail": {"outlook_search_mail", "outlook_read_mail"},
    "outlook_calendar": {"outlook_list_events", "outlook_find_free_times"},
}
READ_ONLY_SURFACE_REFUSAL = (
    "BLOCKED: This is Row-Bot's background preparation, which only reads: this action can't run here. "
    "Don't try another way; write what you would suggest instead."
)


_RUN_PARAMETERS = frozenset({"callbacks", "run_manager"})


def _run_parameters(function: Any) -> set[str]:
    """What LangChain itself hands a tool that takes it: its callbacks, its run manager and the
    run's config (to the parameter typed ``RunnableConfig``). They belong to the run, not the call."""
    try:
        from langchain_core.runnables import RunnableConfig

        hints = typing.get_type_hints(function)
    except Exception:
        hints = {}
    return set(_RUN_PARAMETERS) | {name for name, hint in hints.items() if hint is RunnableConfig}


def call_arguments(function: Any, args: tuple, kwargs: dict) -> dict | None:
    """What one call was made with, positional calls bound without guessing a composite action argument.

    Never the run's own values (``_run_parameters``): a card can't be saved with them in the run's
    checkpoint, and they change each time the call runs, so an approval bound to them never held (B417).
    """
    try:
        arguments = dict(inspect.signature(function).bind_partial(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        if args:
            return None
        arguments = dict(kwargs)
    run_parameters = _run_parameters(function)
    return {name: value for name, value in arguments.items() if name not in run_parameters}


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
    if parent == "browser" and name == "browser_tabs":
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


def read_only_refusal(name: str, arguments: dict | None, *, source: str, parent: str) -> str | None:
    """A runtime that only reads (Proactive Row-Bot's background preparation): refuse anything but a
    reviewed read, before any approval. Unknown is refused."""
    if action_effect(name, arguments, source=source, parent=parent) == "read_only":
        return None
    if source == "core" and arguments is not None and name in _MAIL_CALENDAR_READS.get(parent, ()):
        return None
    return READ_ONLY_SURFACE_REFUSAL


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
    # A profile lists no tools (F31): what it may do is its capability and the approval level.
    policy = profile.get("tool_policy_json")
    policy = policy if isinstance(policy, Mapping) else {}
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
