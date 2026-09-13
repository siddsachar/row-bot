"""Explicit saved native MCP exposure; never connect or construct a tool on read."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.developer.edits import FileEditRecovery

from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import os
import re
import sys
from uuid import UUID

from row_bot import tool_configuration as configuration

_KEY = os.urandom(32)
_REVISION = re.compile(r"[0-9a-f]{64}\Z")


class NativeMcpError(ValueError):
    def __init__(self, code: str, current_revision: str | None = None):
        self.code, self.current_revision = code, current_revision
        super().__init__(code)


@dataclass(frozen=True)
class NativeMcpState:
    schema_version: int
    resource_revision: str | None
    availability: str
    saved_enabled: bool | None
    effective_enabled: bool | None
    registered: bool


def _registered():
    registry = sys.modules.get("row_bot.tools.registry")
    if registry is None or vars(registry).get("_active_config_path") != configuration.configuration_path():
        return None, None
    tools = vars(registry).get("_tools")
    tool = tools.get("mcp") if type(tools) is dict else None
    return registry, tool


def _boolean(value):
    return value if type(value) is bool else None


def _revision(saved, registry, tool):
    value = [str(configuration.configuration_path().absolute()), saved.digest, id(registry), id(tool)]
    return hmac.new(_KEY, json.dumps(value).encode(), hashlib.sha256).hexdigest()


def _state(saved, *, recovery=False):
    registry, tool = _registered()
    enabled = vars(registry).get("_enabled", {}) if registry else {}
    return NativeMcpState(1, _revision(saved, registry, tool),
        "recovery_required" if recovery else "registration_unavailable" if tool is None else
        "available" if saved.exists else "missing",
        _boolean(configuration.tools_map(saved.document).get("mcp", False)),
        _boolean(enabled.get("mcp")) if type(enabled) is dict and tool is not None else None, tool is not None)


def read_native_mcp_state(*, validate: Callable[[], None] = lambda: None) -> NativeMcpState:
    validate()
    try:
        saved = configuration.read_saved()
        recovery = configuration.recovery_required()
        result = _state(saved, recovery=recovery)
    except configuration.ToolConfigurationError:
        result = NativeMcpState(1, None, "unavailable", None, None, False)
    validate()
    return result


def _payload(resource_revision, enabled):
    if (type(resource_revision) is not str or not _REVISION.fullmatch(resource_revision)
            or type(enabled) is not bool):
        raise NativeMcpError("invalid_command")


def _review(resource_revision, enabled):
    from row_bot.runtime import admissions
    _payload(resource_revision, enabled)
    return {"resource_revision": resource_revision, "enabled": enabled,
        "action_digest": admissions.keyed_digest({"resource_revision": resource_revision, "enabled": enabled})}


def review_native_mcp_command(resource_revision: str, enabled: bool, *, validate: Callable[[], None]) -> dict:
    validate()
    review = _review(resource_revision, enabled)
    with configuration.transaction():
        configuration.require_write_available()
        saved = configuration.read_saved()
        current = _state(saved)
        if current.resource_revision != resource_revision:
            raise NativeMcpError("revision_conflict", current.resource_revision)
        if not current.registered:
            raise NativeMcpError("native_mcp_unavailable")
        validate()
    return review


def public_receipt(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "_native_tool_configuration"}


def _invalidate_loaded():
    # Existing graphs/inference caches cannot exist before their owners load.
    for module, name in (("row_bot.agent", "clear_agent_cache"), ("row_bot.tasks", "invalidate_keyword_map_cache")):
        owner = sys.modules.get(module)
        callback = vars(owner).get(name) if owner is not None else None
        if callback is not None:
            callback()


def execute_native_mcp_command(*, owner_id: str, key: str, command: dict,
                               validate: Callable[[], None], validate_review: Callable[[dict], None]) -> dict:
    """Save once; original retries only reconcile publication/cache completion."""
    from row_bot.runtime import admissions
    validate()
    payload = command.get("payload")
    if (command.get("type") != "mcp.facade.control" or type(payload) is not dict
            or set(payload) != {"resource_revision", "enabled"}):
        raise NativeMcpError("invalid_command")
    review = _review(**payload)
    try:
        if str(UUID(command["command_id"])) != command["command_id"]:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NativeMcpError("invalid_command") from None
    mapped = {**command, "wire_expected_revision": command.get("expected_revision"),
              "expected_revision": payload["resource_revision"]}
    progress = {"command_id": command["command_id"], "status": "admitting"}

    def partial() -> dict:
        validate()
        value = {**progress, "status": "partial", "native_mcp": {
            "schema_version": 1, "status": "partial", "resource_revision": None,
            "saved_enabled": None, "effective_enabled": None, "code": "native_mcp_unconfirmed"}}
        try:
            admissions.command_progress(owner_id, key, value)
        except Exception:
            pass
        return public_receipt(value)

    def finish(saved: configuration.SavedToolConfiguration) -> dict:
        validate()
        state = _state(saved)
        private = progress.get("_native_tool_configuration", {})
        registry, tool = _registered()
        if state.saved_enabled != payload["enabled"] or tool is None:
            return partial()
        # Only the captured live owner may receive the cache mutation. On a
        # restart/replacement, observe its independently loaded state instead.
        if private.get("registration") == _registration(registry, tool):
            registry._enabled["mcp"] = payload["enabled"]
        elif state.effective_enabled != payload["enabled"]:
            return partial()
        try:
            _invalidate_loaded()
            state = _state(saved)
            value = {**progress, "status": "completed", "native_mcp": {
                "schema_version": 1, "status": "saved", "resource_revision": state.resource_revision,
                "saved_enabled": state.saved_enabled, "effective_enabled": state.effective_enabled, "code": None}}
            return public_receipt(admissions.complete_command(owner_id, key, value))
        except Exception:
            return partial()

    with configuration.transaction():
        validate()
        try:
            replay = admissions.claim_command(owner_id, key, mapped, "settings:tools")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise NativeMcpError(str(error), error.current_revision) from None
            retained = admissions.receipt(owner_id, command["command_id"])
            if type(retained) is not dict:
                return partial()
            progress = retained
            private = retained.get("_native_tool_configuration", {})
            try:
                saved = configuration.read_saved()
            except configuration.ToolConfigurationError:
                return partial()
            if (type(private) is dict and type(private.get("publication")) is dict
                    and configuration.confirmed_publication(saved, private["publication"],
                        owner_id=owner_id, key=key, command_id=command["command_id"])):
                return finish(saved)
            return partial()
        if replay is not None:
            validate()
            return public_receipt(replay)
        registry, tool = _registered()

        def authority() -> None:
            validate()
            validate_review(review)
            current_registry, current_tool = _registered()
            if tool is None or current_registry is not registry or current_tool is not tool:
                raise NativeMcpError("native_mcp_unavailable")

        try:
            configuration.require_write_available(excluding=(owner_id, key))
            saved = configuration.read_saved()
            current = _revision(saved, registry, tool)
            if current != payload["resource_revision"]:
                raise NativeMcpError("revision_conflict", current)
            authority()
        except Exception as error:
            admissions.reject_command(owner_id, key, getattr(error, "code", "action_denied"))
            raise
        document = configuration.editable_document(saved)
        configuration.tools_map(document)["mcp"] = payload["enabled"]
        document["_client_publication"] = {"owner_id": owner_id, "key": key, "command_id": command["command_id"]}
        private = {"registration": _registration(registry, tool)}
        progress["_native_tool_configuration"] = private
        admissions.command_progress(owner_id, key, progress)

        def checkpoint(proof: FileEditRecovery) -> None:
            private["publication"] = asdict(proof)
            admissions.command_progress(owner_id, key, progress)

        try:
            result = configuration.publish_saved(document, expected_digest=saved.digest,
                command_id=command["command_id"], persist_recovery=checkpoint, validate=authority)
        except Exception as error:
            if "publication" not in private:
                admissions.reject_command(owner_id, key, getattr(error, "code", "tool_configuration_save_failed"))
                raise
            return partial()
        return finish(result)


def _registration(registry, tool):
    # Key rotation after restart prevents accidental CPython address reuse.
    return hmac.new(_KEY, f"{id(registry)}:{id(tool)}".encode(), hashlib.sha256).hexdigest()
