"""Saved MCP authorization controls; no connection or native-tool side effects."""
from __future__ import annotations

import base64
from collections.abc import Callable
import copy
from dataclasses import dataclass
import hashlib
import hmac
import json
import re

from row_bot.application import capability_configuration_controls as configuration
from row_bot.mcp_client import config
from row_bot.mcp_client.safety import classify_tool_effect, is_destructive_tool

Error = configuration.CapabilityConfigurationError
_TOOL_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,127}\Z")


@dataclass(frozen=True)
class McpToolPolicy:
    tool_id: str
    name: str
    enabled: bool | None
    requires_approval: bool | None
    approval_locked: bool
    destructive: bool | None


@dataclass(frozen=True)
class McpPolicyPage:
    schema_version: int
    revision: str | None
    server_id: str | None
    availability: str
    global_enabled: bool | None
    server_enabled: bool | None
    resources_enabled: bool | None
    prompts_enabled: bool | None
    items: tuple[McpToolPolicy, ...]
    total: int | None
    next_cursor: str | None


def _identity(value):
    if type(value) is not str or not configuration._IDENTITY.fullmatch(value):
        raise Error("invalid_command")


def _boolean(value) -> bool | None:
    return value if type(value) is bool else None


def _tool_id(server_id: str, name: str) -> str:
    return hashlib.sha256(json.dumps(["mcp-tool-policy", server_id, name], ensure_ascii=True).encode()).hexdigest()


def _label(name: str) -> str:
    if not _TOOL_LABEL.fullmatch(name) or re.search(r"(?:sk-|Bearer|token|secret)", name, re.I):
        return "MCP tool"
    return name


def _server(saved, server_id):
    return next(((name, value) for name, value in saved.document.get("servers", {}).items()
                 if configuration._server_id(name) == server_id), (None, None))


def _tool_policies(server_id: str, tools: dict) -> dict[str, McpToolPolicy]:
    enabled, catalog = tools.get("enabled", {}), tools.get("catalog", {})
    approvals, included, excluded = (tools.get(field, []) for field in ("require_approval", "include", "exclude"))
    if (type(enabled) is not dict or type(catalog) is not dict or any(type(values) is not list or
            len(values) > 10000 or any(type(name) is not str for name in values)
            for values in (approvals, included, excluded))):
        raise Error("mcp_policy_unavailable")
    names = set(enabled) | set(catalog) | set(approvals) | set(included) | set(excluded)
    if len(names) > 10000 or any(type(name) is not str or not name or len(name) > 512 for name in names):
        raise Error("mcp_policy_unavailable")
    approvals, included, excluded = set(approvals), set(included), set(excluded)
    rows = {}
    for name in names:
        metadata = catalog.get(name, {})
        effect = "unknown"
        if type(metadata) is not dict:
            destructive, declared = None, None
        else:
            description = metadata.get("description", "")
            destructive = _boolean(metadata.get("destructive", False))
            declared = _boolean(metadata.get("requires_approval", False))
            if type(description) is not str or len(description) > 16384:
                destructive = None
            elif is_destructive_tool(name, description):
                destructive = True
            if type(description) is str and len(description) <= 16384:
                effect = classify_tool_effect(name, description)
        locked = destructive is not False or declared is not False or effect == "unknown"
        required = True if destructive is True or declared is True or name in approvals or effect == "unknown" else None if locked else False
        default_enabled = None if destructive is None else not (destructive or effect == "unknown")
        identity, label = _tool_id(server_id, name), _label(name)
        if label == "MCP tool" and label != name:
            label = f"MCP tool ({identity[:12]})"
        rows[name] = McpToolPolicy(identity, label,
            False if name in excluded or (included and name not in included) else _boolean(enabled.get(name, default_enabled)),
            required, locked, destructive)
    return rows


def read_mcp_policy(*, server_id: str | None = None, query: str = "", cursor: str | None = None,
                    limit: int = 25, validate: Callable[[], None] = lambda: None) -> McpPolicyPage:
    """Read the whole saved tool scope before returning one bounded safe page."""
    validate()
    if server_id is not None:
        _identity(server_id)
    if type(query) is not str or len(query) > 128 or type(limit) is not int or not 1 <= limit <= 50:
        raise Error("invalid_query")
    query = query.strip().casefold()
    try:
        saved = config.read_saved_configuration()
        recovery = config.configuration_recovery_required()
    except config.McpConfigurationError:
        if cursor is not None:
            raise Error("cursor_expired") from None
        validate()
        return McpPolicyPage(1, None, server_id, "unavailable", None, None, None, None, (), None, None)
    revision = configuration._revision(saved)
    name, server = _server(saved, server_id) if server_id is not None else (None, None)
    rows, tools = {}, {}
    availability = "recovery_required" if recovery else "available" if saved.exists else "missing"
    if server_id is not None and name is None:
        availability = "not_found"
    elif server is not None:
        tools = server.get("tools", {})
        try:
            if type(tools) is not dict:
                raise Error("mcp_policy_unavailable")
            rows = _tool_policies(server_id, tools)
        except Error:
            validate()
            if cursor is not None:
                raise Error("cursor_expired") from None
            return McpPolicyPage(1, revision, server_id, "recovery_required" if recovery else "partial",
                _boolean(saved.document.get("enabled", False)), _boolean(server.get("enabled", False)),
                None, None, (), None, None)
    offset = 0
    if cursor is not None:
        try:
            if type(cursor) is not str or len(cursor) > 2048:
                raise ValueError
            value, signature = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
            if (type(value) is not list or len(value) != 5 or value[:3] != [revision, server_id, query]
                    or type(value[3]) is not int or not 0 <= value[3] <= 10000 or value[4] != limit
                    or type(signature) is not str or not hmac.compare_digest(signature, configuration._digest(value))):
                raise ValueError
            offset = value[3]
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Error("cursor_expired") from None
    matches = sorted((row for row in rows.values() if query in row.name.casefold()), key=lambda row: (row.name.casefold(), row.tool_id))
    items = tuple(matches[offset:offset + limit])
    next_cursor = None
    if offset + len(items) < len(matches):
        value = [revision, server_id, query, offset + len(items), limit]
        next_cursor = base64.urlsafe_b64encode(json.dumps([value, configuration._digest(value)], separators=(",", ":")).encode()).decode().rstrip("=")
    validate()
    return McpPolicyPage(1, revision, server_id, availability, _boolean(saved.document.get("enabled", False)),
        _boolean(server.get("enabled", False)) if server is not None else None,
        _boolean(tools.get("resources_enabled", False)) if server is not None else None,
        _boolean(tools.get("prompts_enabled", False)) if server is not None else None,
        items, len(matches), next_cursor)


def _next_policy_document(saved, intent):
    if type(intent) is not dict or type(intent.get("operation")) is not str:
        raise Error("invalid_command")
    operation = intent["operation"]
    fields = {"operation", "enabled"}
    if operation != "global_enabled":
        fields.add("server_id")
    if operation in {"tool_enabled", "tool_approval"}:
        fields.add("tool_id")
    if operation == "utility_enabled":
        fields.add("utility")
    if (operation not in {"global_enabled", "server_enabled", "tool_enabled", "tool_approval", "utility_enabled"}
            or set(intent) != fields or type(intent.get("enabled")) is not bool):
        raise Error("invalid_command")
    document = copy.deepcopy(saved.document)
    if operation == "global_enabled":
        document["enabled"] = intent["enabled"]
        return document, ()
    _identity(intent["server_id"])
    name, server = _server(saved, intent["server_id"])
    if name is None:
        raise Error("not_found")
    target = document["servers"][name]
    if operation == "server_enabled":
        target["enabled"] = intent["enabled"]
        return document, (name,)
    tools = target.setdefault("tools", {})
    if type(tools) is not dict:
        raise Error("mcp_policy_unavailable")
    if operation == "utility_enabled":
        if type(intent["utility"]) is not str or intent["utility"] not in {"resources", "prompts"}:
            raise Error("invalid_command")
        tools[intent["utility"] + "_enabled"] = intent["enabled"]
    else:
        _identity(intent["tool_id"])
        rows = _tool_policies(intent["server_id"], tools)
        tool = next((name for name, row in rows.items() if row.tool_id == intent["tool_id"]), None)
        if tool is None:
            raise Error("not_found")
        if operation == "tool_enabled":
            # Enabling an explicitly excluded tool must also remove its saved
            # exclusion, otherwise a successful save would not authorize it.
            tools.setdefault("enabled", {})[tool] = intent["enabled"]
            if intent["enabled"] and tool in tools.get("exclude", []):
                tools["exclude"] = [name for name in tools["exclude"] if name != tool]
            if intent["enabled"] and tools.get("include") and tool not in tools["include"]:
                tools["include"] = [*tools["include"], tool]
        else:
            if not intent["enabled"] and rows[tool].approval_locked:
                raise Error("approval_required")
            approved = set(tools.get("require_approval", []))
            if intent["enabled"]:
                approved.add(tool)
            else:
                approved.discard(tool)
            tools["require_approval"] = sorted(approved)
    return document, (name,)


def review_mcp_policy_command(configuration_revision: str, intent: dict, *, validate: Callable[[], None]) -> dict:
    from row_bot.runtime import admissions
    validate()
    _identity(configuration_revision)
    config.require_configuration_write_available()
    saved = config.read_saved_configuration()
    current = configuration._revision(saved)
    if configuration_revision != current:
        raise Error("revision_conflict", current)
    _document, names = _next_policy_document(saved, intent)
    validate()
    return {"configuration_revision": current, "operation": intent["operation"],
        "action_digest": admissions.keyed_digest({"revision": current, "intent": intent}),
        "server_ids": [configuration._server_id(name) for name in names], "saved_disabled": None}


def execute_mcp_policy_command(*, owner_id: str, key: str, command: dict, validate: Callable[[], None],
                               validate_review: Callable[[dict], None]) -> dict:
    """Save authorization only; original retries reuse the existing proof owner."""
    return configuration._execute_saved_change(owner_id=owner_id, key=key, command=command, validate=validate,
        validate_review=validate_review, command_type="mcp.configuration.control",
        next_document=_next_policy_document, saved_disabled=None)


public_receipt = configuration.public_receipt
