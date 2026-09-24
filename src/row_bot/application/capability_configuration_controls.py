"""Passive MCP configuration and explicit Save Disabled over canonical owners.

Launch values are write-only. This adapter never tests, connects, discovers or
installs a server, and its status observations never grant execution authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Never
    from row_bot.developer.edits import FileEditRecovery

import base64
from collections.abc import Callable
import copy
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import math
import os
import re
import sys
from typing import Any

from row_bot.mcp_client import config

_PUBLIC_KEY = os.urandom(32)  # Restart expires public snapshots; no extra store.
_WIRE_LIMIT = 128 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _().-]{0,127}\Z")
_IDENTITY = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {
    "name",
    "transport",
    "command",
    "args",
    "cwd",
    "url",
    "env",
    "headers",
    "connect_timeout",
    "tool_timeout",
    "output_limit",
}
_PRIVATE_FIELDS = ("command", "args", "cwd", "url", "env", "headers")


class CapabilityConfigurationError(ValueError):
    def __init__(self, code: str, current_revision: str | None = None):
        self.code, self.current_revision = code, current_revision
        super().__init__(code)


@dataclass(frozen=True)
class McpServerSummary:
    server_id: str
    name: str
    transport: str
    enabled: bool | None
    runtime_status: str | None
    configured_fields: tuple[str, ...]
    tool_count: int | None
    connection_present: bool | None
    requirements: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class McpConfigurationPage:
    schema_version: int
    revision: str | None
    availability: str
    enabled: bool | None
    items: tuple[McpServerSummary, ...]
    total: int | None
    next_cursor: str | None


def _digest(value: Any) -> str:
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()
    return hmac.new(_PUBLIC_KEY, data, hashlib.sha256).hexdigest()


def _revision(saved: config.SavedMcpConfiguration) -> str:
    return _digest([str(config.CONFIG_PATH.absolute()), saved.digest])


def _server_id(name: str) -> str:
    return hashlib.sha256(
        ("mcp-server:" + json.dumps(name, ensure_ascii=True)).encode()
    ).hexdigest()


def _label(name: str) -> str:
    if not _NAME.fullmatch(name) or re.search(
        r"(?:sk-|Bearer|token[=:]|secret[=:])", name, re.I
    ):
        return "MCP server"
    return name


def _cursor(revision: str, query: str, offset: int, limit: int) -> str:
    value = [revision, query, offset, limit]
    return (
        base64.urlsafe_b64encode(
            json.dumps([value, _digest(value)], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def read_mcp_configuration(
    *,
    query: str = "",
    cursor: str | None = None,
    limit: int = 25,
    validate: Callable[[], None] = lambda: None,
) -> McpConfigurationPage:
    """Read a bounded page from the full saved library, with no cold runtime load."""
    validate()
    if (
        type(query) is not str
        or len(query) > 128
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise CapabilityConfigurationError("invalid_query")
    query = query.strip().casefold()
    try:
        saved = config.read_saved_configuration()
        recovery_required = config.configuration_recovery_required()
    except config.McpConfigurationError:
        validate()
        if cursor is not None:
            raise CapabilityConfigurationError("cursor_expired") from None
        return McpConfigurationPage(1, None, "unavailable", None, (), None, None)
    revision = _revision(saved)
    offset = 0
    if cursor is not None:
        try:
            if type(cursor) is not str or len(cursor) > 2048:
                raise ValueError
            value, signature = json.loads(
                base64.b64decode(
                    cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
                )
            )
            if (
                type(value) is not list
                or len(value) != 4
                or type(signature) is not str
                or not hmac.compare_digest(signature, _digest(value))
                or value[0] != revision
                or value[1] != query
                or value[3] != limit
                or type(value[2]) is not int
                or not 0 <= value[2] <= 10000
            ):
                raise ValueError
            offset = value[2]
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise CapabilityConfigurationError("cursor_expired") from None
    servers = saved.document.get("servers", {})
    matches = sorted(
        (name for name in servers if query in _label(name).casefold()),
        key=lambda name: (_label(name).casefold(), _server_id(name)),
    )
    names = matches[offset : offset + limit]
    runtime = sys.modules.get("row_bot.mcp_client.runtime")
    statuses = (
        runtime.get_passive_server_statuses(tuple(names)) if runtime is not None else {}
    )
    items = []
    for name in names:
        server = servers[name]
        transport = server.get(
            "transport", "streamable_http" if server.get("url") else "stdio"
        )
        transport = (
            {"http": "streamable_http", "streamable-http": "streamable_http"}.get(
                transport, transport
            )
            if type(transport) is str
            else "unknown"
        )
        if transport not in {"stdio", "streamable_http", "sse"}:
            transport = "unknown"
        tools = server.get("tools")
        catalog = tools.get("catalog") if type(tools) is dict else None
        status = statuses.get(name, {})
        from row_bot.mcp_client.requirements import check_server_requirements

        try:
            checks = check_server_requirements(server)[:8]
            requirements = tuple(
                {
                    "id": check.requirement.id
                    if check.requirement.id in {"node", "uv", "playwright-chrome"}
                    else "other",
                    "label": check.requirement.label[:96]
                    if check.requirement.id in {"node", "uv", "playwright-chrome"}
                    else "Other runtime",
                    "available": check.available,
                    "managed": check.requirement.managed,
                    "installable": check.installable
                    and check.requirement.id in {"node", "uv"},
                    "source": check.source
                    if check.source in {"system", "managed", "environment", "missing"}
                    else "unknown",
                }
                for check in checks
            )
        except (OSError, ValueError, RuntimeError):
            requirements = (
                {
                    "id": "other",
                    "label": "Requirements",
                    "available": False,
                    "managed": False,
                    "installable": False,
                    "source": "unknown",
                },
            )
        items.append(
            McpServerSummary(
                _server_id(name),
                _label(name),
                transport,
                server.get("enabled", False)
                if type(server.get("enabled", False)) is bool
                else None,
                status.get("status"),
                tuple(field for field in _PRIVATE_FIELDS if server.get(field)),
                len(catalog) if type(catalog) is dict else status.get("tool_count"),
                status.get("connection_present"),
                requirements,
            )
        )
    validate()
    return McpConfigurationPage(
        1,
        revision,
        "recovery_required"
        if recovery_required
        else "available"
        if saved.exists
        else "missing",
        saved.document.get("enabled", False)
        if type(saved.document.get("enabled", False)) is bool
        else None,
        tuple(items),
        len(matches),
        _cursor(revision, query, offset + len(items), limit)
        if offset + len(items) < len(matches)
        else None,
    )


def _text(value: Any, maximum: int = 16384, *, empty: bool = True) -> str:
    try:
        valid = (
            type(value) is str
            and len(value.encode("utf-8")) <= maximum
            and "\0" not in value
            and (empty or bool(value.strip()))
        )
    except UnicodeError:
        valid = False
    if not valid:
        raise CapabilityConfigurationError("invalid_command")
    return value


def _name(value: Any) -> str:
    if type(value) is not str or not _NAME.fullmatch(value) or value != value.strip():
        raise CapabilityConfigurationError("invalid_command")
    return value


def _fields(raw: Any) -> dict:
    if type(raw) is not dict or set(raw) - _FIELDS:
        raise CapabilityConfigurationError("invalid_command")
    fields = copy.deepcopy(raw)
    for key, value in fields.items():
        if key == "name":
            _name(value)
        elif key == "transport":
            if type(value) is not str or value not in {
                "stdio",
                "streamable_http",
                "sse",
            }:
                raise CapabilityConfigurationError("invalid_command")
        elif key in {"command", "url", "cwd"}:
            if value is not None or key != "cwd":
                _text(value)
        elif key == "args":
            if type(value) is not list or len(value) > 128:
                raise CapabilityConfigurationError("invalid_command")
            for item in value:
                _text(item)
        elif key in {"env", "headers"}:
            if type(value) is not dict or len(value) > 128:
                raise CapabilityConfigurationError("invalid_command")
            for label, item in value.items():
                _text(label, 256, empty=False)
                _text(item)
        elif (
            type(value) not in {int, float}
            or not math.isfinite(value)
            or not 1 <= value <= (1_000_000 if key == "output_limit" else 3600)
            or key == "output_limit"
            and type(value) is not int
        ):
            raise CapabilityConfigurationError("invalid_command")
    return fields


def _server(base: dict, fields: dict, name: str) -> dict:
    result = copy.deepcopy(base)
    result.update(fields)
    result.update(name=name, enabled=False)
    transport = result.get("transport") or (
        "streamable_http" if result.get("url") else "stdio"
    )
    if type(transport) is not str:
        raise CapabilityConfigurationError("invalid_command")
    transport = {"http": "streamable_http", "streamable-http": "streamable_http"}.get(
        transport, transport
    )
    if transport not in {"stdio", "streamable_http", "sse"}:
        raise CapabilityConfigurationError("invalid_command")
    result["transport"] = transport
    if transport == "stdio":
        _text(result.get("command"), empty=False)
    else:
        from urllib.parse import urlsplit

        value = _text(result.get("url"), empty=False)
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError
        except ValueError:
            raise CapabilityConfigurationError("invalid_command") from None
    return result


def _next_document(
    saved: config.SavedMcpConfiguration, intent: dict
) -> tuple[dict, tuple[str, ...]]:
    """Compute the complete proposed map before any file or receipt publication."""
    if type(intent) is not dict or set(intent) - {
        "operation",
        "server_id",
        "fields",
        "import_json",
    }:
        raise CapabilityConfigurationError("invalid_command")
    try:
        size = len(json.dumps(intent, ensure_ascii=True, allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        raise CapabilityConfigurationError("invalid_command") from None
    if size > _WIRE_LIMIT:
        raise CapabilityConfigurationError("invalid_command")
    operation = intent.get("operation")
    document = copy.deepcopy(saved.document)
    servers = document.setdefault("servers", {})
    affected = []
    if operation == "import":
        if set(intent) != {"operation", "import_json"}:
            raise CapabilityConfigurationError("invalid_command")
        raw = _text(intent["import_json"], _WIRE_LIMIT, empty=False)
        try:

            def invalid_constant(_value: str) -> Never:
                raise ValueError

            imported = json.loads(
                raw,
                object_pairs_hook=config._strict_object,
                parse_constant=invalid_constant,
            )
        except (ValueError, RecursionError):
            raise CapabilityConfigurationError("invalid_command") from None
        if type(imported) is not dict:
            raise CapabilityConfigurationError("invalid_command")
        imported = imported.get("mcpServers", imported.get("servers", imported))
        if type(imported) is not dict or not 1 <= len(imported) <= 64:
            raise CapabilityConfigurationError("invalid_command")
        for name, value in imported.items():
            _name(name)
            if name in servers:
                raise CapabilityConfigurationError("mcp_server_collision")
            if type(value) is not dict:
                raise CapabilityConfigurationError("invalid_command")
            fields = _fields({key: value[key] for key in value if key in _FIELDS})
            servers[name] = _server(value, fields, name)
            affected.append(name)
    elif operation == "delete":
        if set(intent) != {"operation", "server_id"}:
            raise CapabilityConfigurationError("invalid_command")
        identity = intent["server_id"]
        if type(identity) is not str or not _IDENTITY.fullmatch(identity):
            raise CapabilityConfigurationError("invalid_command")
        name = next((name for name in servers if _server_id(name) == identity), None)
        if name is None:
            raise CapabilityConfigurationError("not_found")
        del servers[name]
        affected.append(name)
    elif operation in {"add", "edit", "rename"}:
        if "import_json" in intent:
            raise CapabilityConfigurationError("invalid_command")
        fields = _fields(intent.get("fields"))
        if operation == "add":
            if "server_id" in intent:
                raise CapabilityConfigurationError("invalid_command")
            name = _name(fields.get("name"))
            if name in servers:
                raise CapabilityConfigurationError("mcp_server_collision")
            servers[name] = _server({}, fields, name)
        else:
            identity = intent.get("server_id")
            if type(identity) is not str or not _IDENTITY.fullmatch(identity):
                raise CapabilityConfigurationError("invalid_command")
            name = next(
                (name for name in servers if _server_id(name) == identity), None
            )
            if name is None:
                raise CapabilityConfigurationError("not_found")
            destination = fields.get("name", name)
            if operation == "edit" and destination != name:
                raise CapabilityConfigurationError("invalid_command")
            if destination != name and destination in servers:
                raise CapabilityConfigurationError("mcp_server_collision")
            server = _server(servers[name], fields, destination)
            del servers[name]
            servers[destination] = server
            name = destination
        affected.append(name)
    else:
        raise CapabilityConfigurationError("invalid_command")
    return document, tuple(affected)


def review_mcp_configuration_command(
    configuration_revision: str, intent: dict, *, validate: Callable[[], None]
) -> dict:
    """Review explicit local configuration only; never test a launch target."""
    from row_bot.runtime import admissions

    validate()
    config.require_configuration_write_available()
    saved = config.read_saved_configuration()
    current = _revision(saved)
    if configuration_revision != current:
        raise CapabilityConfigurationError("revision_conflict", current)
    _document, affected = _next_document(saved, intent)
    validate()
    return {
        "configuration_revision": current,
        "operation": intent["operation"],
        "action_digest": admissions.keyed_digest(
            {"revision": current, "intent": intent}
        ),
        "server_ids": [_server_id(name) for name in affected],
        "saved_disabled": True,
    }


def public_receipt(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "_mcp_configuration"}


def _confirmed_publication(
    saved: config.SavedMcpConfiguration,
    publication: dict,
    owner_id: str,
    key: str,
    command_id: str,
) -> bool:
    """Equal bytes alone never prove ownership of a possibly interrupted save."""
    from row_bot.file_ownership import confirmed_edit_publication

    return saved.exists and confirmed_edit_publication(
        config.CONFIG_PATH,
        saved.digest,
        saved.identity,
        publication,
        owner_id=owner_id,
        key=key,
        command_id=command_id,
        max_bytes=8 * 1024 * 1024,
    )


def execute_mcp_configuration_command(
    *,
    owner_id: str,
    key: str,
    command: dict,
    validate: Callable[[], None],
    validate_review: Callable[[dict], None],
) -> dict:
    """Save disabled once; retry reconciles exact proof and never blindly writes."""
    return _execute_saved_change(
        owner_id=owner_id,
        key=key,
        command=command,
        validate=validate,
        validate_review=validate_review,
        command_type="mcp.configuration.save",
        next_document=_next_document,
        saved_disabled=True,
    )


def _execute_saved_change(
    *,
    owner_id: str,
    key: str,
    command: dict,
    validate: Callable[[], None],
    validate_review: Callable[[dict], None],
    command_type: str,
    next_document: Callable,
    saved_disabled: bool | None,
) -> dict:
    """Shared MCP configuration publication; callers own explicit typed intents."""
    from uuid import UUID
    from row_bot.runtime import admissions

    validate()
    payload = command.get("payload")
    if (
        command.get("type") != command_type
        or type(payload) is not dict
        or set(payload) != {"configuration_revision", "intent"}
        or type(payload.get("configuration_revision")) is not str
        or not _IDENTITY.fullmatch(payload["configuration_revision"])
    ):
        raise CapabilityConfigurationError("invalid_command")
    try:
        if str(UUID(command["command_id"])) != command["command_id"]:
            raise ValueError
        intent = copy.deepcopy(payload["intent"])
        if (
            type(intent) is not dict
            or len(json.dumps(intent, ensure_ascii=True, allow_nan=False).encode())
            > _WIRE_LIMIT
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, RecursionError):
        raise CapabilityConfigurationError("invalid_command") from None
    revision = payload["configuration_revision"]
    review = {
        "configuration_revision": revision,
        "operation": intent.get("operation"),
        "action_digest": admissions.keyed_digest(
            {"revision": revision, "intent": intent}
        ),
    }
    mapped = {
        **command,
        "wire_expected_revision": command.get("expected_revision"),
        "expected_revision": revision,
    }
    progress: dict = {"command_id": command["command_id"], "status": "admitting"}

    def authority() -> None:
        validate()
        validate_review(review)

    def partial() -> dict:
        validate()
        value = {
            **progress,
            "status": "partial",
            "mcp_configuration": {
                "schema_version": 1,
                "status": "partial",
                "revision": None,
                "server_ids": [],
                "saved_disabled": None,
                "runtime_cleanup": "not_requested",
                "code": "mcp_configuration_unconfirmed",
            },
        }
        try:
            admissions.command_progress(owner_id, key, value)
        except Exception:
            pass  # Keep any earlier private proof; no false completion.
        return public_receipt(value)

    def complete(
        saved: config.SavedMcpConfiguration, ids: list[str], names: list[str]
    ) -> dict:
        nonlocal progress
        validate()
        cleanup = "not_requested"
        if intent.get("operation") == "delete":
            from row_bot.mcp_client import runtime

            if len(names) != 1 or len(ids) != 1 or _server_id(names[0]) != ids[0]:
                return partial()
            try:
                lifecycle = runtime.get_server_lifecycle(names[0])
                runtime_id = lifecycle.get("runtime_id")
                if runtime_id:
                    stopped = runtime.stop_server_owned(names[0], runtime_id)
                    cleanup = (
                        "stopped"
                        if stopped["state"] == "stopped"
                        else "cleanup_incomplete"
                    )
                else:
                    cleanup = "not_running"
            except (OSError, RuntimeError, ValueError):
                cleanup = "cleanup_incomplete"
            if cleanup == "cleanup_incomplete":
                progress = {
                    **progress,
                    "status": "partial",
                    "mcp_configuration": {
                        "schema_version": 1,
                        "status": "partial",
                        "revision": _revision(saved),
                        "server_ids": ids,
                        "saved_disabled": True,
                        "runtime_cleanup": cleanup,
                        "code": "mcp_cleanup_incomplete",
                    },
                }
                admissions.command_progress(owner_id, key, progress)
                return public_receipt(progress)
        outcome = {
            "schema_version": 1,
            "status": "saved",
            "revision": _revision(saved),
            "server_ids": ids,
            "saved_disabled": saved_disabled,
            "runtime_cleanup": cleanup,
            "code": None,
        }
        progress = {**progress, "status": "completed", "mcp_configuration": outcome}
        admissions.command_progress(owner_id, key, progress)
        return public_receipt(admissions.complete_command(owner_id, key, progress))

    with config.configuration_transaction():
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, mapped, "settings:mcp")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise CapabilityConfigurationError(
                    str(error), error.current_revision
                ) from None
            retained = admissions.receipt(owner_id, command["command_id"])
            if type(retained) is not dict:
                return partial()
            progress = retained
            outcome = retained.get("mcp_configuration")
            if type(outcome) is dict and outcome.get("status") == "saved":
                validate()
                return public_receipt(
                    admissions.complete_command(owner_id, key, progress)
                )
            private = retained.get("_mcp_configuration")
            if type(private) is not dict:
                return partial()
            publication = private.get("publication")
            ids = private.get("server_ids")
            try:
                saved = config.read_saved_configuration()
            except config.McpConfigurationError:
                return partial()
            if (
                type(publication) is dict
                and type(ids) is list
                and len(ids) <= 64
                and all(
                    type(identity) is str and _IDENTITY.fullmatch(identity)
                    for identity in ids
                )
                and _confirmed_publication(
                    saved, publication, owner_id, key, command["command_id"]
                )
            ):
                names = private.get("affected_names")
                if intent.get("operation") == "delete" and (
                    type(names) is not list or len(names) != len(ids)
                ):
                    return partial()
                return complete(saved, ids, names if type(names) is list else [])
            return partial()
        if replay is not None:
            validate()
            return public_receipt(replay)
        try:
            config.require_configuration_write_available(excluding=(owner_id, key))
            saved = config.read_saved_configuration()
            current = _revision(saved)
            if current != revision:
                raise CapabilityConfigurationError("revision_conflict", current)
            document, names = next_document(saved, intent)
            ids = [_server_id(name) for name in names]
            authority()
        except (CapabilityConfigurationError, config.McpConfigurationError) as error:
            admissions.reject_command(
                owner_id, key, error.code, getattr(error, "current_revision", None)
            )
            raise
        except Exception:
            admissions.reject_command(owner_id, key, "action_denied")
            raise
        private = {"server_ids": ids, "affected_names": list(names)}
        progress["_mcp_configuration"] = private
        admissions.command_progress(owner_id, key, progress)
        document["_client_publication"] = {
            "owner_id": owner_id,
            "key": key,
            "command_id": command["command_id"],
        }

        def checkpoint(proof: FileEditRecovery) -> None:
            private["publication"] = asdict(proof)
            admissions.command_progress(owner_id, key, progress)

        try:
            result = config.publish_saved_configuration(
                document,
                expected_digest=saved.digest,
                command_id=command["command_id"],
                persist_recovery=checkpoint,
                validate=authority,
            )
        except Exception:
            return partial()
        return complete(result, ids, list(names))
