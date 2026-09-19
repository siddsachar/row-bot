"""Explicit reviewed MCP lifecycle commands over canonical runtime/admissions.

Reads never load a runtime. Unknown launch results never cause a second launch.
Session cleanup proof is separate from saved enablement and OS containment.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import sys
from uuid import UUID

from row_bot.application import capability_configuration_controls as configuration
from row_bot.mcp_client import config
from row_bot.runtime import admissions


class CapabilityRuntimeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class McpRuntimeState:
    schema_version: int
    server_id: str
    configuration_revision: str | None
    cleanup_revision: str | None
    availability: str
    runtime_id: str | None
    state: str
    session_quiesced: bool | None


def _identity(value: str, *, uuid: bool = False) -> None:
    try:
        valid = type(value) is str and (str(UUID(value)) == value if uuid else bool(configuration._IDENTITY.fullmatch(value)))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise CapabilityRuntimeError("invalid_command")


def _owned(server_id: str, expected_runtime_id: str | None = None):
    module = sys.modules.get("row_bot.mcp_client.runtime")
    if module is None:
        return None
    with module._runtime_lock:
        if len(module._servers) > 10000:
            raise CapabilityRuntimeError("mcp_runtime_unavailable")
        for name, owner in module._servers.items():
            if configuration._server_id(name) == server_id:
                if expected_runtime_id is not None and owner.runtime_id != expected_runtime_id:
                    raise CapabilityRuntimeError("mcp_runtime_identity_changed")
                return owner
    return None


def _cleanup_revision(server_id: str, runtime_id: str) -> str:
    return configuration._digest(["mcp-owned-cleanup", server_id, runtime_id])


def read_mcp_runtime_state(server_id: str, *, expected_runtime_id: str | None = None,
                           validate: Callable[[], None] = lambda: None) -> McpRuntimeState:
    validate()
    _identity(server_id)
    if expected_runtime_id is not None:
        _identity(expected_runtime_id, uuid=True)
    try:
        saved = config.read_saved_configuration()
        revision = configuration._revision(saved)
        availability = "recovery_required" if config.configuration_recovery_required() else "available" if saved.exists else "missing"
    except config.McpConfigurationError:
        revision, availability = None, "unavailable"
    owner = _owned(server_id, expected_runtime_id)
    validate()
    if owner is None:
        return McpRuntimeState(1, server_id, revision, None, availability, None, "missing", None)
    state = owner.state if owner.state in {"not_started", "connecting", "connected", "stopping", "stopped", "failed", "dependency_missing", "cleanup_incomplete"} else "unknown"
    if owner.cleanup_complete and not owner._release_confirmed:
        state = "cleanup_incomplete"
    return McpRuntimeState(1, server_id, revision, _cleanup_revision(server_id, owner.runtime_id), availability, owner.runtime_id, state,
                           owner.cleanup_complete and owner._finished.is_set())


def _review(revision: str, server_id: str, operation: str, runtime_id: str | None,
             validate: Callable[[], None]) -> tuple[dict, str, dict]:
    validate()
    _identity(revision)
    _identity(server_id)
    if type(operation) is not str or operation not in {"connect", "test", "disconnect"}:
        raise CapabilityRuntimeError("invalid_command")
    if runtime_id is not None:
        _identity(runtime_id, uuid=True)
    if operation == "disconnect":
        if runtime_id is None:
            raise CapabilityRuntimeError("invalid_command")
        owner = _owned(server_id, runtime_id)
        if owner is None:
            raise CapabilityRuntimeError("mcp_runtime_missing")
        if revision != _cleanup_revision(server_id, runtime_id):
            raise CapabilityRuntimeError("revision_conflict")
        name, server = owner.name, {}
        action = {"operation": operation, "server_id": server_id, "runtime_id": runtime_id}
    else:
        if runtime_id is not None:
            raise CapabilityRuntimeError("invalid_command")
        config.require_configuration_write_available()
        saved = config.read_saved_configuration()
        if configuration._revision(saved) != revision:
            raise CapabilityRuntimeError("revision_conflict")
        name = next((name for name in saved.document.get("servers", {}) if configuration._server_id(name) == server_id), None)
        if name is None:
            raise CapabilityRuntimeError("not_found")
        try:
            raw = saved.document["servers"][name]
            configuration._fields({key: value for key, value in raw.items() if key in configuration._FIELDS})
            server = config.normalize_server_config(name, raw)
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise CapabilityRuntimeError("mcp_configuration_unavailable") from None
        if operation == "connect" and (saved.document.get("enabled") is not True or saved.document["servers"][name].get("enabled") is not True):
            raise CapabilityRuntimeError("mcp_runtime_disabled")
        action = {"operation": operation, "server_id": server_id, "revision": revision, "server": server}
    validate()
    return {"resource_revision": revision, "server_id": server_id, "operation": operation,
            "runtime_id": runtime_id, "action_digest": admissions.keyed_digest(action)}, name, server


def review_mcp_runtime_command(resource_revision: str, server_id: str, operation: str,
                               expected_runtime_id: str | None, *, validate: Callable[[], None]) -> dict:
    """Review an explicit connection effect; this performs no runtime import/IO."""
    return _review(resource_revision, server_id, operation, expected_runtime_id, validate)[0]


def public_receipt(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "_mcp_runtime"}


def _persist_progress(owner_id: str, key: str, incoming: dict) -> dict:
    """Merge progress and exact completion atomically in the existing receipt.

    A late HTTP response or a competing original retry must never erase a
    lifetime callback's proof or downgrade an already completed command.
    """
    with admissions.transaction() as conn:
        row = conn.execute("SELECT status,result_json,type FROM client_commands WHERE owner_id=? AND key=?",
                           (owner_id, key)).fetchone()
        if row is None or row["type"] != "mcp.runtime.control":
            raise CapabilityRuntimeError("mcp_runtime_unconfirmed")
        current = json.loads(row["result_json"])
        if type(current) is not dict:
            raise CapabilityRuntimeError("mcp_runtime_unconfirmed")
        if row["status"] == "completed":
            return {**current, "status": "completed"}
        if row["status"] != "admitting":
            raise CapabilityRuntimeError("mcp_runtime_unconfirmed")
        # Public terminal evidence is immutable even if a prior caller failed
        # between writing that evidence and marking the old receipt complete.
        merged = dict(current) if current.get("status") == "completed" else {**current, **incoming}
        before, after = current.get("_mcp_runtime"), incoming.get("_mcp_runtime")
        if before is not None or after is not None:
            if (before is not None and type(before) is not dict) or (after is not None and type(after) is not dict):
                raise CapabilityRuntimeError("mcp_runtime_unconfirmed")
            if before and after and any(before.get(field) != after.get(field) for field in ("runtime_id", "operation")):
                raise CapabilityRuntimeError("mcp_runtime_identity_changed")
            # Latest persisted proof wins; an older local checkpoint contains
            # only the same immutable identity and cannot remove that proof.
            merged["_mcp_runtime"] = {**(after or {}), **(before or {})}
        status = "completed" if merged.get("status") == "completed" else "admitting"
        conn.execute("UPDATE client_commands SET status=?,result_json=? WHERE owner_id=? AND key=?",
                     (status, json.dumps(merged, separators=(",", ":")), owner_id, key))
        return merged


def _record_completion(server_id: str, runtime_owner) -> None:
    """Checkpoint exact physical completion for bounded matching original intents.

    This is bookkeeping for already authorized owners, not additional effects.
    All rows must refer to this exact runtime, including pending Stop requests.
    """
    if not runtime_owner.cleanup_complete:
        raise CapabilityRuntimeError("mcp_cleanup_incomplete")
    pending = admissions.read_unfinished_target_commands("settings:mcp-runtime:" + server_id)
    if pending["overflow"]:
        raise CapabilityRuntimeError("mcp_runtime_recovery_required")
    for row in pending["items"]:
        if row["type"] != "mcp.runtime.control":
            continue
        retained = admissions.receipt(row["owner_id"], row["command_id"])
        private = retained.get("_mcp_runtime") if type(retained) is dict else None
        if type(private) is not dict or private.get("runtime_id") != runtime_owner.runtime_id:
            continue
        operation = private.get("operation")
        if type(operation) is not str or operation not in {"connect", "test", "disconnect"}:
            raise CapabilityRuntimeError("mcp_runtime_unconfirmed")
        state, code = "stopped", None
        if operation == "test":
            tested = runtime_owner._probe_result
            state = "tested" if type(tested) is dict and tested.get("ok") is True else "failed"
            code = None if state == "tested" else "mcp_connection_failed"
        elif operation == "connect" and runtime_owner.state in {"failed", "dependency_missing"}:
            state, code = runtime_owner.state, "mcp_connection_failed"
        proof = {"runtime_id": runtime_owner.runtime_id, "server_id": server_id, "operation": operation,
                 "state": state, "session_quiesced": True, "code": code}
        completed = {"runtime_id": runtime_owner.runtime_id, "operation": operation, "completion": proof}
        if operation == "test":
            from row_bot.application.capability_catalog_controls import capture_tested_catalog
            completed["tested_catalog"] = capture_tested_catalog(tested)
        _persist_progress(row["owner_id"], row["key"],
            {"command_id": row["command_id"], "_mcp_runtime": completed})


def execute_mcp_runtime_command(*, owner_id: str, key: str, command: dict,
                                validate: Callable[[], None], validate_review: Callable[[dict], None],
                                validate_admitted_review: Callable[[dict, str], None] | None = None,
                                observe_seconds: float = 5) -> dict:
    """Perform one exact launch/cleanup, or reconcile its original receipt only."""
    validate()
    if type(observe_seconds) not in (int, float) or not 0 <= observe_seconds <= 5:
        raise CapabilityRuntimeError("invalid_command")
    payload = command.get("payload")
    if command.get("type") != "mcp.runtime.control" or type(payload) is not dict or set(payload) != {
            "resource_revision", "server_id", "operation", "expected_runtime_id"}:
        raise CapabilityRuntimeError("invalid_command")
    _identity(command.get("command_id"), uuid=True)
    revision, server_id = payload["resource_revision"], payload["server_id"]
    operation, expected_id = payload["operation"], payload["expected_runtime_id"]
    _identity(revision)
    _identity(server_id)
    if type(operation) is not str or operation not in {"test", "connect", "disconnect"}:
        raise CapabilityRuntimeError("invalid_command")
    if expected_id is not None:
        _identity(expected_id, uuid=True)
    target = "settings:mcp-runtime:" + server_id
    mapped = {**command, "wire_expected_revision": command.get("expected_revision"), "expected_revision": revision}
    progress = {"command_id": command["command_id"], "status": "admitting"}

    def outcome(state: str, runtime_id: str | None, quiesced: bool | None, *, terminal: bool, code: str | None = None) -> dict:
        nonlocal progress
        value = {"schema_version": 1, "operation": operation, "server_id": server_id, "runtime_id": runtime_id,
                 "state": state, "session_quiesced": quiesced, "code": code}
        progress = {**progress, "status": "completed" if terminal else "partial", "mcp_runtime": value}
        progress = _persist_progress(owner_id, key, progress)
        return public_receipt(progress)

    def reconcile(retained: dict, *, release_checked: bool = False) -> dict:
        nonlocal progress
        progress = retained
        known = retained.get("mcp_runtime")
        if retained.get("status") == "completed" and type(known) is dict:
            return public_receipt(_persist_progress(owner_id, key, retained))
        private = retained.get("_mcp_runtime")
        identity = private.get("runtime_id") if type(private) is dict else None
        if type(identity) is not str:
            return outcome("unknown", None, None, terminal=False, code="mcp_runtime_unconfirmed")
        try:
            _identity(identity, uuid=True)
        except CapabilityRuntimeError:
            return outcome("unknown", None, None, terminal=False, code="mcp_runtime_unconfirmed")
        completion = private.get("completion")
        if (type(completion) is dict and completion.get("runtime_id") == identity
                and completion.get("server_id") == server_id and completion.get("operation") == operation
                and completion.get("session_quiesced") is True
                and type(completion.get("state")) is str
                and completion.get("state") in ({"stopped"} if operation == "disconnect" else
                    {"tested", "failed"} if operation == "test" else {"stopped", "failed", "dependency_missing"})
                and completion.get("code") in (None, "mcp_connection_failed")):
            return outcome(completion["state"], identity, True, terminal=True, code=completion.get("code"))
        try:
            runtime_owner = _owned(server_id, identity)
        except CapabilityRuntimeError as error:
            if error.code != "mcp_runtime_identity_changed":
                raise
            runtime_owner = None
        if runtime_owner is not None:
            if operation == "connect" and runtime_owner._connected_admitted and not runtime_owner._stop_requested.is_set():
                return outcome("connected", identity, False, terminal=True)
            if not release_checked and runtime_owner.cleanup_complete and runtime_owner._finished.is_set():
                from row_bot.mcp_client import runtime
                released = runtime.reconcile_server_release_owned(runtime_owner.name, identity)
                if released["receipt_confirmed"]:
                    return reconcile(admissions.receipt(owner_id, command["command_id"]) or retained, release_checked=True)
        return outcome("unknown", identity, None, terminal=False, code="mcp_runtime_unconfirmed")

    try:
        replay = admissions.claim_command(owner_id, key, mapped, target)
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise CapabilityRuntimeError(str(error)) from None
        validate()
        return reconcile(admissions.receipt(owner_id, command["command_id"]) or progress)
    if replay is not None:
        validate()
        return public_receipt(replay)
    try:
        reviewed, name, server = _review(revision, server_id, operation, expected_id, validate)
        validate_review(reviewed)
        if operation != "disconnect":
            pending = admissions.read_unfinished_target_commands(target)
            if pending["overflow"] or any((row["owner_id"], row["key"]) != (owner_id, key) for row in pending["items"]):
                raise CapabilityRuntimeError("mcp_runtime_recovery_required")
    except Exception as error:
        admissions.reject_command(owner_id, key, getattr(error, "code", "action_denied"))
        raise

    def authority() -> None:
        current, _, _ = _review(revision, server_id, operation, expected_id, validate)
        if current != reviewed:
            raise CapabilityRuntimeError("mcp_runtime_review_stale")
        identity = progress.get("_mcp_runtime", {}).get("runtime_id")
        if operation != "disconnect" and identity is not None and validate_admitted_review is not None:
            validate_admitted_review(current, identity)
        else:
            validate_review(current)

    def checkpoint(runtime_id: str) -> None:
        progress["_mcp_runtime"] = {"runtime_id": runtime_id, "operation": operation}
        if operation == "test":
            progress["_mcp_runtime"].update(server_id=server_id,
                configuration_digest=config.read_saved_configuration().digest)
        _persist_progress(owner_id, key, progress)

    from row_bot.mcp_client import runtime
    if operation == "disconnect":
        checkpoint(expected_id)
        authority()
        with runtime._runtime_lock:
            captured = runtime._servers.get(name)
            if captured is None or captured.runtime_id != expected_id:
                raise CapabilityRuntimeError("mcp_runtime_identity_changed")
            if captured._before_release is None:
                captured._before_release = lambda owned: _record_completion(server_id, owned)
            # A cleanup command may arrive while an earlier completion callback
            # is writing receipts. Its older snapshot cannot confirm this intent.
            captured._release_epoch += 1
            captured._release_confirmed = False
        stopped = runtime.stop_server_owned(name, expected_id, wait_seconds=observe_seconds)
        terminal = stopped["session_quiesced"] and stopped["receipt_confirmed"]
        return outcome(stopped["state"], expected_id, stopped["session_quiesced"], terminal=terminal,
                       code=None if terminal else "mcp_cleanup_incomplete")
    try:
        launched = runtime.launch_server_owned(name, server, before_start=checkpoint, validate=authority,
            temporary=operation == "test", before_release=lambda owned: _record_completion(server_id, owned))
    except Exception:
        identity = progress.get("_mcp_runtime", {}).get("runtime_id")
        return outcome("unknown", identity, None, terminal=False, code="mcp_runtime_unconfirmed")
    launched._ready.wait(observe_seconds)
    if operation == "connect" and launched._connected_admitted and not launched._stop_requested.is_set():
        return outcome("connected", launched.runtime_id, False, terminal=True)
    if launched.cleanup_complete and launched._finished.is_set():
        if not launched._release_confirmed:
            return outcome("cleanup_incomplete", launched.runtime_id, True, terminal=False, code="mcp_runtime_unconfirmed")
        if operation == "test":
            tested = launched._probe_result or {"ok": False}
            return outcome("tested" if tested.get("ok") is True else "failed", launched.runtime_id, True, terminal=True,
                           code=None if tested.get("ok") is True else "mcp_connection_failed")
        return outcome(launched.state, launched.runtime_id, True, terminal=True,
                       code="mcp_connection_failed" if launched.state in {"failed", "dependency_missing"} else None)
    return outcome(launched.state, launched.runtime_id, False, terminal=False, code="mcp_runtime_unconfirmed")
