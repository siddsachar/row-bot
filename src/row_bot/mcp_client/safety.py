"""Safety classification and naming helpers for MCP tools."""

from __future__ import annotations

import re
from typing import Any

# High impact: destroys, reaches other people, runs code, moves money or grants access. Always asks.
_DESTRUCTIVE_RE = re.compile(
    r"(^|_)(delete|remove|destroy|drop|purge|erase|wipe|truncate|revoke|uninstall|kill|terminate|cancel|refund|"
    r"send|post|reply|forward|comment|invite|share|publish|deploy|merge|commit|push|upload|"
    r"run|exec|execute|shell|command|payment|pay|charge|transfer|withdraw|trade|buy|sell|order|book|"
    r"grant|permission|permissions|reset|overwrite|eval)(_|$)",
    re.IGNORECASE,
)
# A change to these reaches other people, publishes, runs code or grants access: high impact. A repository
# is one too: a repository write (create_or_update_file) is a commit, as in Row-Bot's own git rules.
_SENSITIVE_RE = re.compile(
    r"(^|_)(emails?|messages?|releases?|scripts?|passwords?|roles?|members?|collaborators?|tokens?|secrets?|keys?|"
    r"admins?|owners?|webhooks?|pull_requests?|users?|visibility|memberships?|repository|repositories|repos?)(_|$)",
    re.IGNORECASE,
)
# A routine change the description places in a repository ("a file in a GitHub repository") is a commit too;
# the same verb on a file on this computer stays a routine change.
_REPOSITORY_RE = re.compile(r"(^|_)(repository|repositories|repo|repos)(_|$)", re.IGNORECASE)
# Routine changes inside the app: ask unless the user chose Full access for the tool.
_ROUTINE_RE = re.compile(
    r"(^|_)(create|add|update|edit|write|set|rename|move|put|patch|modify|insert|append|save|assign|duplicate)(_|$)",
    re.IGNORECASE,
)
# Words that are also nouns are changes only as the name's verb: tag_issue, not get_tag.
_ROUTINE_FIRST_RE = re.compile(r"^(tag|label|mark|archive|close|star|link|attach|copy)(_|$)", re.IGNORECASE)

_BROWSER_SESSION_SAFE_TOOLS = {
    "browser_click",
    "browser_close",
    "browser_console_messages",
    "browser_drag",
    "browser_fill_form",
    "browser_hover",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_requests",
    "browser_press_key",
    "browser_resize",
    "browser_select_option",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_wait_for",
}


def sanitize_name_component(value: str) -> str:
    """Return a function-call safe identifier component."""
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text or "unnamed"


def prefixed_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp_{sanitize_name_component(server_name)}_{sanitize_name_component(tool_name)}"


def _annotation_value(tool: Any, key: str) -> Any:
    annotations = tool.get("annotations") if isinstance(tool, dict) else getattr(tool, "annotations", None)
    if annotations is None:
        return None
    if isinstance(annotations, dict):
        return annotations.get(key)
    return getattr(annotations, key, None)


def is_destructive_tool(tool_name: str, description: str = "", tool_obj: Any = None) -> bool:
    """Whether a tool is destructive or high impact, so it asks in every access preset.

    Its name, its description or a ``destructiveHint`` can make it high impact; a
    ``readOnlyHint`` never outweighs a high-impact name."""
    normalized_name = sanitize_name_component(tool_name)
    if tool_obj is not None:
        destructive_hint = _annotation_value(tool_obj, "destructiveHint")
        read_only_hint = _annotation_value(tool_obj, "readOnlyHint")
        if destructive_hint is True:
            return True
        if _DESTRUCTIVE_RE.search(normalized_name) or _sensitive_change(normalized_name, description):
            return True
        if read_only_hint is True:
            return False
    if normalized_name in _BROWSER_SESSION_SAFE_TOOLS:
        return False
    if _sensitive_change(normalized_name, description):
        return True
    haystack = f"{tool_name} {description or ''}"
    normalized = sanitize_name_component(haystack)
    return bool(_DESTRUCTIVE_RE.search(normalized))


def _changes(name: str) -> bool:
    return bool(_ROUTINE_RE.search(name) or _ROUTINE_FIRST_RE.match(name))


def _sensitive_change(name: str, description: str = "") -> bool:
    """create_release, add_collaborator, set_password: a routine verb on something high impact, or on
    something its description puts in a repository."""
    return _changes(name) and bool(_SENSITIVE_RE.search(name) or _REPOSITORY_RE.search(sanitize_name_component(description)))


def tool_enabled_by_default(is_destructive: bool) -> bool:
    """Default selection rule after a successful server test/discovery."""
    return not is_destructive


def asks_first(name: str, destructive: bool, effect: str, approvals, allowed) -> bool:
    """The invocation gate. High-impact and unknown tools always ask; a routine change asks
    unless the user allowed it to run without asking (Full access, or that one tool)."""
    return bool(destructive or effect == "unknown" or name in approvals or (effect == "mutation" and name not in allowed))


def classify_tool_effect(tool_name: str, description: str = "", tool_obj: Any = None) -> str:
    """Describe known effects without treating an unrecognized name as read-only.

    Reviewed browser interaction retains its existing approval policy; its
    classification remains distinct from an observational/read-only operation.
    Server annotations are hints, never evidence of an operating-system sandbox.
    """
    name = sanitize_name_component(tool_name)
    if is_destructive_tool(tool_name, description, tool_obj):
        return "mutation"
    if name in {
        "browser_console_messages", "browser_network_requests", "browser_snapshot",
        "browser_take_screenshot", "browser_wait_for",
    }:
        return "read_only"
    if name in _BROWSER_SESSION_SAFE_TOOLS:
        return "interaction"
    # A change named as one stays a change whatever its hints say; unknown names stay unknown.
    if _changes(name):
        return "mutation"
    read_only_hint = _annotation_value(tool_obj, "readOnlyHint") if tool_obj is not None else None
    if read_only_hint is True:
        return "read_only"
    if _ROUTINE_RE.search(sanitize_name_component(description or "")):
        return "unknown"  # Only its description says it changes something: it always asks.
    if read_only_hint is False:
        return "unknown"
    if re.match(r"^(read|get|list|search|find|inspect|describe|count|query|fetch|status|lookup)(_|$)", name):
        return "read_only"
    return "unknown"
