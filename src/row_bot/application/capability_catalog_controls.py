"""Explicit acceptance of an exact completed MCP Test's retained tool catalog."""
from __future__ import annotations

import base64
from collections.abc import Callable
import copy
from dataclasses import dataclass
import hmac
import json
import math
from uuid import UUID

from row_bot.application import capability_configuration_controls as configuration
from row_bot.application import capability_policy_controls as policy
from row_bot.mcp_client import config
from row_bot.mcp_client.conflicts import requires_manual_tool_selection
from row_bot.mcp_client.safety import is_destructive_tool
from row_bot.runtime import admissions

Error = configuration.CapabilityConfigurationError
_SNAPSHOT_BYTES = 128 * 1024  # Leaves the existing 256 KiB receipt budget intact.
_TOOL_LIMIT = 1000


def _bounded_json(value):
    remaining, nodes = _SNAPSHOT_BYTES, 50000
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes -= 1
        if nodes < 0 or depth > 24:
            raise ValueError
        if type(item) is str:
            if len(item) > remaining:
                raise ValueError
            remaining -= len(item.encode("utf-8"))
        elif type(item) in (dict, list):
            if len(item) > nodes:
                raise ValueError
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError
                stack.extend((part, depth + 1) for pair in item.items() for part in pair)
            else:
                stack.extend((part, depth + 1) for part in item)
        elif type(item) in (int, float):
            if type(item) is float and not math.isfinite(item) or type(item) is int and item.bit_length() > 256:
                raise ValueError
            remaining -= 80
        elif item is not None and type(item) is not bool:
            raise ValueError
        if remaining < 0:
            raise ValueError
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > _SNAPSHOT_BYTES:
        raise ValueError
    return encoded


def capture_tested_catalog(tested: dict) -> dict:
    """Copy bounded normalized metadata; unavailable metadata never blocks cleanup."""
    try:
        if type(tested) is not dict or tested.get("ok") is not True:
            raise ValueError
        tools = tested.get("tools")
        if type(tools) is not list or len(tools) > _TOOL_LIMIT:
            raise ValueError
        rows, names, runtime_names = [], set(), set()
        for tool in tools:
            if type(tool) is not dict:
                raise ValueError
            name, runtime_name = tool.get("name"), tool.get("prefixed_name")
            description, schema = tool.get("description", ""), tool.get("input_schema", {})
            if (type(name) is not str or not name or len(name) > 512 or "\0" in name or name in names
                    or type(runtime_name) is not str or not runtime_name or len(runtime_name) > 1024 or runtime_name in runtime_names
                    or type(description) is not str or len(description) > 16384 or type(schema) is not dict
                    or type(tool.get("destructive")) is not bool or type(tool.get("requires_approval")) is not bool):
                raise ValueError
            names.add(name)
            runtime_names.add(runtime_name)
            effect = tool.get("effect")
            if type(effect) is not str or effect not in {"read_only", "mutation", "interaction", "unknown"}:
                effect = "unknown"
            destructive = tool["destructive"] or is_destructive_tool(name, description)
            # Recorded safety is never lowered by a catalog edit or a missing hint.
            rows.append({"name": name, "description": description, "input_schema": schema,
                "destructive": destructive, "requires_approval": tool["requires_approval"] or destructive or effect == "unknown",
                "effect": effect})
        result = {"availability": "available", "tools": rows}
        return json.loads(_bounded_json(result))
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return {"availability": "unavailable", "tools": []}


@dataclass(frozen=True)
class McpCatalogTool:
    tool_id: str
    name: str
    enabled_after_accept: bool | None
    requires_approval: bool
    destructive: bool


@dataclass(frozen=True)
class McpTestedCatalogPage:
    schema_version: int
    configuration_revision: str | None
    server_id: str
    test_command_id: str
    availability: str
    manual_selection_required: bool | None
    items: tuple[McpCatalogTool, ...]
    total: int | None
    next_cursor: str | None


def _ids(server_id, test_command_id):
    policy._identity(server_id)
    try:
        if type(test_command_id) is not str or str(UUID(test_command_id)) != test_command_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise Error("invalid_command") from None


def _captured(owner_id, server_id, test_command_id, saved):
    metadata = admissions.read_command_metadata(owner_id, test_command_id)
    receipt = admissions.read_command_receipt(owner_id, test_command_id)
    if (metadata is None or metadata.get("type") != "mcp.runtime.control"
            or metadata.get("target") != "settings:mcp-runtime:" + server_id or receipt is None
            or metadata.get("status") not in {"admitting", "completed"}):
        raise Error("mcp_catalog_unavailable")
    private = receipt.get("_mcp_runtime")
    if type(private) is not dict or private.get("operation") != "test" or private.get("server_id") != server_id:
        raise Error("mcp_catalog_unavailable")
    proof = private.get("completion")
    if (type(proof) is not dict or proof.get("state") != "tested" or proof.get("operation") != "test"
            or proof.get("session_quiesced") is not True
            or proof.get("runtime_id") != private.get("runtime_id") or proof.get("server_id") != server_id):
        raise Error("mcp_catalog_unavailable")
    if private.get("configuration_digest") != saved.digest:
        raise Error("mcp_catalog_stale")
    catalog = private.get("tested_catalog")
    if type(catalog) is not dict or catalog.get("availability") != "available":
        raise Error("mcp_catalog_unavailable")
    # A stored private receipt is still parsed strictly before use as settings.
    rows = catalog.get("tools")
    if type(rows) is not list or len(rows) > _TOOL_LIMIT:
        raise Error("mcp_catalog_unavailable")
    normalized = capture_tested_catalog({"ok": True, "tools": [
        {**row, "prefixed_name": str(index)} if type(row) is dict else row for index, row in enumerate(rows)]})
    if normalized != catalog:
        raise Error("mcp_catalog_unavailable")
    return catalog


def _document(saved, server_id, captured):
    name, server = policy._server(saved, server_id)
    if name is None:
        raise Error("not_found")
    document = copy.deepcopy(saved.document)
    target = document["servers"][name]
    tools = target.setdefault("tools", {})
    if type(tools) is not dict:
        raise Error("mcp_policy_unavailable")
    policy._tool_policies(server_id, tools)  # Retain strict existing safety shapes.
    enabled, catalog = tools.setdefault("enabled", {}), tools.setdefault("catalog", {})
    approvals = set(tools.get("require_approval", []))
    manual = requires_manual_tool_selection(name, server)
    for row in captured["tools"]:
        tool_name = row["name"]
        old = catalog.get(tool_name, {})
        if type(old) is not dict:
            raise Error("mcp_policy_unavailable")
        updated = {**old, **row}
        updated["requires_approval"] = (row["requires_approval"] or old.get("requires_approval") is not False and "requires_approval" in old
            or old.get("destructive") is not False and "destructive" in old or tool_name in approvals)
        updated["destructive"] = row["destructive"] or old.get("destructive") is True
        catalog[tool_name] = updated
        if updated["requires_approval"]:
            approvals.add(tool_name)
        if tool_name not in enabled:
            enabled[tool_name] = not (manual or updated["destructive"] or row["effect"] == "unknown")
    tools["require_approval"] = sorted(approvals)
    return document, (name,), manual


def read_tested_mcp_catalog(*, owner_id: str, server_id: str, test_command_id: str,
                            query: str = "", cursor: str | None = None, limit: int = 25,
                            validate: Callable[[], None] = lambda: None) -> McpTestedCatalogPage:
    validate()
    _ids(server_id, test_command_id)
    if type(query) is not str or len(query) > 128 or type(limit) is not int or not 1 <= limit <= 50:
        raise Error("invalid_query")
    query = query.strip().casefold()
    revision = None
    try:
        saved = config.read_saved_configuration()
        revision = configuration._revision(saved)
        captured = _captured(owner_id, server_id, test_command_id, saved)
        document, names, manual = _document(saved, server_id, captured)
        rows = policy._tool_policies(server_id, document["servers"][names[0]]["tools"])
        matches = sorted((McpCatalogTool(rows[row["name"]].tool_id, rows[row["name"]].name,
            rows[row["name"]].enabled, bool(rows[row["name"]].requires_approval), bool(rows[row["name"]].destructive))
            for row in captured["tools"] if query in rows[row["name"]].name.casefold()), key=lambda row: (row.name.casefold(), row.tool_id))
        availability = "recovery_required" if config.configuration_recovery_required() else "available"
    except (Error, config.McpConfigurationError, admissions.AdmissionError) as error:
        validate()
        if cursor is not None:
            raise Error("cursor_expired") from None
        return McpTestedCatalogPage(1, revision, server_id, test_command_id,
            "stale" if getattr(error, "code", "") == "mcp_catalog_stale" else "unavailable", None, (), None, None)
    fingerprint = configuration._digest([owner_id, server_id, test_command_id, captured])
    offset = 0
    if cursor is not None:
        try:
            if type(cursor) is not str or len(cursor) > 2048:
                raise ValueError
            value, signature = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
            if (type(value) is not list or len(value) != 5 or value[:3] != [revision, fingerprint, query]
                    or type(value[3]) is not int or not 0 <= value[3] <= _TOOL_LIMIT or value[4] != limit
                    or type(signature) is not str or not hmac.compare_digest(signature, configuration._digest(value))):
                raise ValueError
            offset = value[3]
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Error("cursor_expired") from None
    items = tuple(matches[offset:offset + limit])
    next_cursor = None
    if offset + len(items) < len(matches):
        value = [revision, fingerprint, query, offset + len(items), limit]
        next_cursor = base64.urlsafe_b64encode(json.dumps([value, configuration._digest(value)], separators=(",", ":")).encode()).decode().rstrip("=")
    validate()
    return McpTestedCatalogPage(1, revision, server_id, test_command_id, availability, manual, items, len(matches), next_cursor)


def _intent(server_id, test_command_id):
    _ids(server_id, test_command_id)
    return {"operation": "accept_catalog", "server_id": server_id, "test_command_id": test_command_id}


def review_mcp_catalog_command(*, owner_id: str, configuration_revision: str, server_id: str,
                               test_command_id: str, validate: Callable[[], None]) -> dict:
    validate()
    intent = _intent(server_id, test_command_id)
    policy._identity(configuration_revision)
    with config.configuration_transaction():
        config.require_configuration_write_available()
        saved = config.read_saved_configuration()
        current = configuration._revision(saved)
        if current != configuration_revision:
            raise Error("revision_conflict", current)
        captured = _captured(owner_id, server_id, test_command_id, saved)
        _next, _names, manual = _document(saved, server_id, captured)
        validate()
        return {"configuration_revision": current, "server_id": server_id, "test_command_id": test_command_id,
            "operation": "accept_catalog", "action_digest": admissions.keyed_digest({"revision": current, "intent": intent}),
            "tool_count": len(captured["tools"]), "manual_selection_required": manual, "saved_disabled": None}


def execute_mcp_catalog_command(*, owner_id: str, key: str, command: dict, validate: Callable[[], None],
                                validate_review: Callable[[dict], None]) -> dict:
    validate()
    payload = command.get("payload")
    if (command.get("type") != "mcp.catalog.accept" or type(payload) is not dict
            or set(payload) != {"configuration_revision", "server_id", "test_command_id"}):
        raise Error("invalid_command")
    intent = _intent(payload["server_id"], payload["test_command_id"])
    mapped = {**command, "payload": {"configuration_revision": payload["configuration_revision"], "intent": intent}}
    def next_document(saved: config.SavedMcpConfiguration, _intent: dict) -> tuple[dict, tuple[str, ...]]:
        captured = _captured(owner_id, payload["server_id"], payload["test_command_id"], saved)
        document, names, _manual = _document(saved, payload["server_id"], captured)
        return document, names
    return configuration._execute_saved_change(owner_id=owner_id, key=key, command=mapped, validate=validate,
        validate_review=validate_review, command_type="mcp.catalog.accept", next_document=next_document, saved_disabled=None)


public_receipt = configuration.public_receipt
