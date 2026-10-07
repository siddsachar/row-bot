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
_READ_RE = re.compile(r"^(read|get|list|search|find|inspect|describe|count|query|fetch|status|lookup)(_|$)", re.IGNORECASE)
# What a read's description may admit it also does to data. Reads describe themselves with running ("Execute a
# SELECT query", "Run a search") and with nouns such as order, post or comment, so those never count (B306).
_DATA_CHANGE_RE = re.compile(
    r"(^|_)(delete|remove|destroy|drop|purge|erase|wipe|truncate|revoke|overwrite|send|publish|deploy|transfer|"
    r"create|update|edit|write|modify|insert|append|save|rename|move|patch)(_|$)",
    re.IGNORECASE,
)
HINTS = ("readOnlyHint", "destructiveHint")  # The annotations Row-Bot weighs; a saved catalog keeps them (B307).

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
_BROWSER_READS = {"browser_console_messages", "browser_network_requests", "browser_snapshot",
                  "browser_take_screenshot", "browser_wait_for"}


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


def hints(tool: Any) -> dict[str, bool]:
    """The annotations of ``tool`` that Row-Bot weighs, as its saved catalog keeps them."""
    return {key: value for key in HINTS if type(value := _annotation_value(tool, key)) is bool}


def saved_hints(value: Any) -> dict[str, bool] | None:
    """Saved annotations as :func:`hints` wrote them, or None when they aren't."""
    valid = type(value) is dict and set(value) <= set(HINTS) and all(type(hint) is bool for hint in value.values())
    return value if valid else None


def _classify(tool_name: str, description: str, tool: Any) -> str:
    """``high_impact``, ``mutation``, ``read_only``, ``interaction`` or ``unknown``.

    Trusted in this order (B307, B306): a ``destructiveHint``; a high-impact name; a change named as one (which
    its description can still make high impact); a ``readOnlyHint``; a read verb in the name; and only then the
    description. A read-only hint never outweighs a name, and a description never outweighs either of them,
    except that a read whose description says it changes data asks first."""
    name = sanitize_name_component(tool_name)
    words = sanitize_name_component(description or "")
    if (_annotation_value(tool, "destructiveHint") is True or _DESTRUCTIVE_RE.search(name)
            or _sensitive_change(name, description)):
        return "high_impact"
    if name in _BROWSER_READS:
        return "read_only"
    if name in _BROWSER_SESSION_SAFE_TOOLS:
        return "interaction"
    if _changes(name):
        return "high_impact" if _DESTRUCTIVE_RE.search(words) else "mutation"
    read_only = _annotation_value(tool, "readOnlyHint")
    if read_only is True:
        return "read_only"
    if read_only is not False and _READ_RE.match(name):
        return "unknown" if _DATA_CHANGE_RE.search(words) else "read_only"
    return "high_impact" if _DESTRUCTIVE_RE.search(words) else "unknown"


def is_destructive_tool(tool_name: str, description: str = "", tool_obj: Any = None) -> bool:
    """Whether a tool is destructive or high impact, so it asks in every access preset."""
    return _classify(tool_name, description, tool_obj) == "high_impact"


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
    found = _classify(tool_name, description, tool_obj)
    return "mutation" if found == "high_impact" else found
