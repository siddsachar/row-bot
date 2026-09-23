"""Explicit managed-runtime resolution/install over canonical command receipts.

Only these explicit commands schedule work. Passive reads never resolve latest,
download, launch an MCP server or execute a runtime. A cancellation request does
not prove that an in-flight network read or installer thread has returned.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
import os
import threading
from uuid import UUID

import psutil

from row_bot.mcp_client import requirements
from row_bot.runtime import admissions


class RuntimeInstallationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RuntimeArchiveView:
    version: str
    url: str
    sha256: str
    size_bytes: int
    system: str
    arch: str
    asset_name: str


@dataclass(frozen=True)
class RuntimeInstallationReview:
    schema_version: int
    runtime_id: str
    operation: str
    resource_revision: str
    action_digest: str
    source_command_id: str | None
    plan: RuntimeArchiveView | None
    disclosures: tuple[str, ...]
    network_required: bool = True
    executes_runtime: bool = False


@dataclass(frozen=True)
class RuntimeInstallationSnapshot:
    schema_version: int
    runtime_id: str
    resource_revision: str | None
    availability: str
    installed: bool | None
    active_command_id: str | None
    quiesced: bool | None


@dataclass
class _Operation:
    owner_id: str
    command_id: str
    runtime_id: str
    operation: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    outcome: dict | None = None


_LOCK = threading.RLock()
# Physical ownership only, shared by service instances. Never a second journal.
_OPERATIONS: dict[str, _Operation] = {}
_TYPES = {"mcp.runtime.resolve", "mcp.runtime.install", "mcp.runtime.install.cancel"}
_PRIVATE = "_runtime_installation"


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise RuntimeInstallationError("invalid_command") from None


def _runtime(value):
    if type(value) is not str or value not in {"node", "uv"}:
        raise RuntimeInstallationError("runtime_installation_unavailable")
    return value


def _bounded(value):
    try:
        if len(json.dumps(value, allow_nan=False).encode()) > 32768:
            raise ValueError
        return deepcopy(value)
    except (ValueError, TypeError, RecursionError):
        raise RuntimeInstallationError("runtime_installation_unavailable") from None


def _target(runtime_id):
    return "settings:mcp-runtime:" + _runtime(runtime_id)


def _view(plan):
    return RuntimeArchiveView(**{key: getattr(plan, key) for key in RuntimeArchiveView.__dataclass_fields__})


def public_receipt(value: dict) -> dict:
    return {key: deepcopy(value[key]) for key in ("command_id", "status", "code", "installation") if key in value}


def _merge(owner: str, command_id: str, changes: dict, private: dict | None = None, *, terminal=False) -> dict:
    """Preserve concurrent proof updates and never downgrade terminal receipts."""
    with admissions.transaction() as conn:
        row = conn.execute("SELECT type,status,result_json FROM client_commands WHERE owner_id=? AND command_id=? AND key=?",
                           (owner, command_id, command_id)).fetchone()
        if row is None or row["type"] not in _TYPES:
            raise RuntimeInstallationError("runtime_installation_unconfirmed")
        saved = json.loads(row["result_json"] or "{}")
        if row["status"] in {"completed", "rejected"}:
            return saved
        if row["status"] != "admitting":
            raise RuntimeInstallationError("runtime_installation_unconfirmed")
        saved.update(deepcopy(changes))
        if private:
            saved.setdefault(_PRIVATE, {}).update(deepcopy(private))
        saved = _bounded(saved)
        conn.execute("UPDATE client_commands SET result_json=?,status=? WHERE owner_id=? AND command_id=?",
                     (json.dumps(saved, separators=(",", ":")), "completed" if terminal else "admitting", owner, command_id))
        return saved


def _saved(owner_id, command_id, runtime_id):
    _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    value = admissions.read_command_receipt(owner_id, command_id)
    if (not metadata or metadata["target"] != _target(runtime_id) or metadata["type"] not in _TYPES
            or not value or type(value.get(_PRIVATE)) is not dict):
        raise RuntimeInstallationError("runtime_installation_command_unavailable")
    return metadata, value


def _source_plan(owner_id, command_id, runtime_id):
    metadata, value = _saved(owner_id, command_id, runtime_id)
    if metadata["type"] != "mcp.runtime.resolve" or value["status"] != "completed":
        raise RuntimeInstallationError("runtime_plan_unavailable")
    try:
        data = deepcopy(value[_PRIVATE]["plan"])
        data["executable_candidates"] = tuple(data["executable_candidates"])
        plan = requirements.ArchiveRuntimePlan(**data)
        if plan.runtime_id != runtime_id:
            raise ValueError
        requirements._validated_plan(plan)
        return plan
    except (KeyError, TypeError, ValueError, RuntimeError):
        raise RuntimeInstallationError("runtime_plan_changed") from None


def _policy(read_policy, operation):
    value = _bounded(read_policy(operation))
    if type(value) is not dict:
        raise RuntimeInstallationError("runtime_installation_policy_changed")
    return admissions.keyed_digest(value)


def _dead_owner(private: dict) -> bool:
    """Only observed PID birth mismatch/absence proves the saved thread owner died."""
    pid, birth = private.get("owner_pid"), private.get("owner_birth")
    if type(pid) is not int or pid <= 0 or type(birth) not in {int, float} or not math.isfinite(birth) or birth <= 0:
        return False
    try:
        # Match the process-birth tolerance used by admission recovery. Some
        # platforms round repeated create_time() reads by a few microseconds.
        return abs(psutil.Process(pid).create_time() - birth) >= 0.001
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError):
        return False


class McpRuntimeInstallationService:
    """Bounded explicit workers; root supplies current policy/session authority."""

    def snapshot(self, runtime_id: str, *, validate: Callable[[], None], owner_id: str | None = None) -> RuntimeInstallationSnapshot:
        validate()
        _runtime(runtime_id)
        try:
            revision = requirements.runtime_install_revision(runtime_id)
            present = bool(requirements._read_manifest(runtime_id))
            installed = requirements._managed_bin_dir(runtime_id) is not None if present else False
            availability = "available" if installed else "unavailable" if present else "missing"
        except (OSError, ValueError, RuntimeError):
            revision, installed, availability = None, None, "unavailable"
        with _LOCK:
            active = _OPERATIONS.get(runtime_id)
            active_id = active.command_id if active and active.owner_id == owner_id else None
            quiesced = not active.thread.is_alive() if active and active.thread else (False if active else True)
        unfinished = admissions.read_unfinished_target_commands(_target(runtime_id), limit=1)
        if unfinished["items"] or unfinished["overflow"]:
            availability = "recovery_required"
            if active is None:
                quiesced = None
                if not unfinished["overflow"] and unfinished["items"][0]["owner_id"] == owner_id:
                    try:
                        active_id = _uuid(unfinished["items"][0]["command_id"])
                    except RuntimeInstallationError:
                        active_id = None  # Preserve recovery status without exposing corrupt identifiers.
        validate()
        return RuntimeInstallationSnapshot(1, runtime_id, revision, availability, installed, active_id, quiesced)

    def review(self, *, owner_id: str, runtime_id: str, operation: str, resource_revision: str,
               source_command_id: str | None, validate: Callable[[], None],
               read_policy: Callable[[str], dict]) -> RuntimeInstallationReview:
        validate()
        _runtime(runtime_id)
        if operation not in {"resolve", "install"} or (operation == "resolve") != (source_command_id is None):
            raise RuntimeInstallationError("invalid_command")
        if requirements.runtime_install_revision(runtime_id) != resource_revision:
            raise RuntimeInstallationError("runtime_plan_changed")
        plan = _source_plan(owner_id, source_command_id, runtime_id) if source_command_id else None
        policy = _policy(read_policy, operation)
        action = {"runtime_id": runtime_id, "operation": operation, "resource_revision": resource_revision,
                  "source_command_id": source_command_id, "plan": asdict(plan) if plan else None, "policy": policy}
        digest = admissions.keyed_digest(action)
        disclosures = (("Contact the runtime publisher to resolve an exact version, download URL, checksum and byte size.",)
            if operation == "resolve" else ("Download and publish only the reviewed pinned archive.",
                                           "Previous and incomplete managed generations are retained for recovery."))
        validate()
        return RuntimeInstallationReview(1, runtime_id, operation, resource_revision, digest, source_command_id,
            _view(plan) if plan else None, (*disclosures, "This action does not run the runtime or connect an MCP server."))

    def _finish(self, active):
        with _LOCK:
            if (_OPERATIONS.get(active.runtime_id) is not active or active.thread is None or active.thread.is_alive()
                    or active.outcome is None):
                return
            outcome = {**active.outcome, "quiesced": True, "cancel_requested": active.cancelled.is_set()}
            successful = outcome["stage"] in {"resolved", "installed", "cancelled"}
            _merge(active.owner_id, active.command_id,
                {"status": "completed" if successful else "partial", "installation": outcome},
                {"observed_quiescence": True}, terminal=successful)
            if successful:
                _OPERATIONS.pop(active.runtime_id, None)

    def receipt(self, *, owner_id: str, runtime_id: str, command_id: str,
                validate: Callable[[], None]) -> dict:
        validate()
        with _LOCK:
            active = _OPERATIONS.get(_runtime(runtime_id))
        if active and active.owner_id == owner_id and active.command_id == command_id:
            self._finish(active)
        _metadata, value = _saved(owner_id, command_id, runtime_id)
        if value["status"] not in {"completed", "rejected"}:
            private = value[_PRIVATE]
            matching = active is not None and active.owner_id == owner_id and active.command_id == command_id
            quiesced = (active.thread is not None and not active.thread.is_alive()) if matching else _dead_owner(private)
            if quiesced and private.get("publication") and private.get("generation"):
                verified = requirements.read_runtime_installation_proof(runtime_id, command_id, private, validate=validate)
                if verified:
                    installation = {**value["installation"], "installed": True, "stage": "installed"}
                    if quiesced:
                        installation["quiesced"] = True
                        value = _merge(owner_id, command_id, {"status": "completed", "installation": installation},
                                       {"observed_quiescence": True}, terminal=True)
                        with _LOCK:
                            if _OPERATIONS.get(runtime_id) is active:
                                _OPERATIONS.pop(runtime_id)
            if value["status"] not in {"completed", "rejected"} and quiesced:
                outcome = private.get("finished_outcome")
                if type(outcome) is dict and outcome.get("stage") in {"resolved", "cancelled"}:
                    value = _merge(owner_id, command_id, {"status": "completed", "installation": {
                        **outcome, "quiesced": True}}, {"observed_quiescence": True}, terminal=True)
                else:
                    value = _merge(owner_id, command_id, {"status": "partial", "code": "runtime_installation_unconfirmed",
                        "installation": {**value["installation"], "quiesced": True}}, {"observed_quiescence": True})
            elif value["status"] not in {"completed", "rejected"} and not matching:
                value = {**value, "status": "partial", "code": "runtime_installation_owner_unavailable",
                         "installation": {**value["installation"], "quiesced": None}}
        validate()
        return public_receipt(value)

    def execute(self, command: dict, *, owner_id: str, key: str, validate: Callable[[], None],
                read_policy: Callable[[str], dict], validate_review: Callable[[RuntimeInstallationReview], None]) -> dict:
        validate()
        command = _bounded(command)
        if type(command) is not dict:
            raise RuntimeInstallationError("invalid_command")
        identity = _uuid(command.get("command_id"))
        kind, payload = command.get("type"), command.get("payload")
        if key != identity or kind not in _TYPES or type(payload) is not dict:
            raise RuntimeInstallationError("invalid_command")
        runtime_id = _runtime(payload.get("runtime_id"))
        cancelling = kind == "mcp.runtime.install.cancel"
        fields = {"runtime_id", "source_command_id"} if cancelling else {
            "runtime_id", "source_command_id", "resource_revision", "action_digest"}
        if payload.keys() != fields:
            raise RuntimeInstallationError("invalid_command")
        previous = admissions.read_command_metadata(owner_id, identity)
        if previous:
            try:
                admissions.claim_command(owner_id, key, command, _target(runtime_id))
            except admissions.AdmissionError as error:
                if str(error) != "operation_uncertain":
                    raise
            return self.receipt(owner_id=owner_id, runtime_id=runtime_id, command_id=identity, validate=validate)
        if cancelling:
            return self._cancel(command, owner_id, validate)
        operation = "resolve" if kind == "mcp.runtime.resolve" else "install"
        policy = _policy(read_policy, operation)
        review = self.review(owner_id=owner_id, runtime_id=runtime_id, operation=operation,
            resource_revision=payload["resource_revision"], source_command_id=payload["source_command_id"],
            validate=validate, read_policy=read_policy)
        if review.action_digest != payload["action_digest"] or _policy(read_policy, operation) != policy:
            raise RuntimeInstallationError("runtime_review_changed")
        validate_review(review)
        plan = _source_plan(owner_id, payload["source_command_id"], runtime_id) if operation == "install" else None
        with _LOCK:
            unfinished = admissions.read_unfinished_target_commands(_target(runtime_id), limit=1)
            if runtime_id in _OPERATIONS or unfinished["items"] or unfinished["overflow"]:
                raise RuntimeInstallationError("runtime_installation_pending")
            admissions.claim_command(owner_id, key, command, _target(runtime_id), exclusive_target=True)
            active = _Operation(owner_id, identity, runtime_id, operation)
            _OPERATIONS[runtime_id] = active
        installation = {"runtime_id": runtime_id, "operation": operation, "stage": "admitted",
                        "cancel_requested": False, "quiesced": False, "installed": None, "plan": asdict(_view(plan)) if plan else None}
        try:
            _merge(owner_id, identity, {"command_id": identity, "status": "accepted", "installation": installation},
                   {"runtime_id": runtime_id, "operation": operation,
                    "owner_pid": os.getpid(), "owner_birth": psutil.Process(os.getpid()).create_time()})
        except Exception:
            with _LOCK:
                _OPERATIONS.pop(runtime_id, None)
            raise

        def authority() -> None:
            validate()
            if active.cancelled.is_set():
                raise RuntimeInstallationError("runtime_installation_cancelled")
            if _policy(read_policy, operation) != policy:
                raise RuntimeInstallationError("runtime_installation_policy_changed")
            validate_review(review)

        def checkpoint(stage: str, proof: dict) -> None:
            authority()
            _merge(owner_id, identity, {"installation": {**installation, "stage": stage}}, proof)

        def worker() -> None:
            outcome = dict(installation)
            try:
                authority()
                if operation == "resolve":
                    resolved = requirements.resolve_managed_runtime_plan(runtime_id, validate=authority, cancelled=active.cancelled.is_set)
                    authority()
                    _merge(owner_id, identity, {}, {"plan": asdict(resolved)})
                    outcome.update(stage="resolved", plan=asdict(_view(resolved)))
                else:
                    result = requirements.install_runtime_plan(plan, validate=authority, cancelled=active.cancelled.is_set,
                                                               command_id=identity, checkpoint=checkpoint)
                    outcome.update(stage="installed" if result.ok else "cancelled", installed=True if result.ok else None)
            except Exception:
                # Retained effects and uncertainty stay private; never expose raw paths/errors.
                outcome.update(stage="cancelled" if active.cancelled.is_set() else "needs_attention")
            finally:
                active.outcome = outcome
                try:
                    _merge(owner_id, identity, {}, {"finished_outcome": outcome})
                except Exception:
                    pass  # Original owner remains retained for exact bookkeeping recovery.
        active.thread = threading.Thread(target=worker, name="mcp-runtime-" + runtime_id, daemon=True)
        try:
            active.thread.start()
        except Exception:
            if active.thread.ident is None and not active.thread.is_alive():
                active.outcome = {**installation, "stage": "needs_attention"}
            raise RuntimeInstallationError("runtime_installation_unconfirmed") from None
        return self.receipt(owner_id=owner_id, runtime_id=runtime_id, command_id=identity, validate=validate)

    def _cancel(self, command, owner_id, validate):
        identity = command["command_id"]
        runtime_id, source = command["payload"]["runtime_id"], _uuid(command["payload"]["source_command_id"])
        metadata, saved = _saved(owner_id, source, runtime_id)
        if metadata["type"] not in {"mcp.runtime.resolve", "mcp.runtime.install"}:
            raise RuntimeInstallationError("runtime_installation_command_unavailable")
        with _LOCK:
            active = _OPERATIONS.get(runtime_id)
            if saved.get("status") == "completed" and saved["installation"].get("quiesced") is True:
                validate()
                admissions.claim_command(owner_id, identity, command, _target(runtime_id))
                receipt = {"command_id": identity, "status": "completed", "installation": {
                    **saved["installation"], "stage": "already_quiesced", "cancel_requested": False},
                    _PRIVATE: {"source_command_id": source}}
                admissions.complete_command(owner_id, identity, receipt)
                return public_receipt(receipt)
            if not active or active.command_id != source or active.owner_id != owner_id:
                raise RuntimeInstallationError("runtime_installation_owner_unavailable")
            validate()
            admissions.claim_command(owner_id, identity, command, _target(runtime_id))
            validate()
            _merge(owner_id, source, {}, {"cancel_requested": True})
            active.cancelled.set()
            receipt = {"command_id": identity, "status": "completed", "installation": {
                **saved["installation"], "stage": "cancellation_requested", "cancel_requested": True,
                "quiesced": not active.thread.is_alive() if active.thread else False}, _PRIVATE: {"source_command_id": source}}
            admissions.complete_command(owner_id, identity, receipt)
        validate()
        return public_receipt(receipt)

    def close(self, *, timeout: float = 1.0) -> bool:
        """Request cancellation, observe bounded joins; never claim a killed thread."""
        if not 0 <= timeout <= 5:
            raise ValueError("Invalid runtime shutdown timeout")
        with _LOCK:
            owned = list(_OPERATIONS.values())
            for active in owned:
                active.cancelled.set()
        for active in owned:
            if active.thread and active.thread.ident is not None:
                active.thread.join(timeout)
            self._finish(active)
        return all(active.thread is not None and not active.thread.is_alive() for active in owned)
