"""Explicit owned workspace processes over the canonical runtime/run owners."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import sqlite3
import sys
from typing import Callable, Literal
import uuid

from row_bot.developer import client_edits, runtime
from row_bot.developer.sandbox import ApprovalDecision, decide_action

_START_LOCK = threading.RLock()
_OUTPUT_PAGE_LIMIT = 16 * 1024
_CODES = {"", "process_approval_required", "process_policy_denied", "process_command_invalid",
    "process_command_too_large", "process_identity_conflict", "process_owner_lost", "process_limit",
    "process_start_failed", "process_admission_failed", "process_start_timeout", "process_bootstrap_failed",
    "process_output_invalid", "process_cleanup_incomplete", "process_history_incomplete", "process_revoked",
    "workspace_writer_busy", "resource_revision_conflict", "resource_binding_revoked", "sandbox_process_unavailable"}
_CODES |= {"process_containment_unavailable", "process_owner_unavailable", "process_recovery_unavailable",
           "process_control_unavailable", "process_receipt_invalid", "sandbox_container_changed"}


@dataclass(frozen=True)
class WorkspaceProcessInfo:
    process_id: str
    command_id: str
    run_id: str
    command: str
    state: Literal["starting", "running", "stopping", "exited", "failed", "cleanup_incomplete"]
    exit_code: int | None
    quiesced: bool
    code: str = ""


@dataclass(frozen=True)
class WorkspaceProcessSnapshot:
    resource_id: str
    conversation_id: str
    resource_revision: str
    binding_id: str
    binding_revision: str
    processes: tuple[WorkspaceProcessInfo, ...]
    schema_version: int = 1


@dataclass(frozen=True)
class WorkspaceProcessOutputEntry:
    sequence: int
    channel: Literal["stdout", "stderr"]
    text: str


@dataclass(frozen=True)
class WorkspaceProcessOutput:
    process_id: str
    entries: tuple[WorkspaceProcessOutputEntry, ...]
    next_cursor: int
    truncated: bool
    quiesced: bool
    schema_version: int = 1


def _info(state: runtime.TrackedProcess) -> WorkspaceProcessInfo:
    with state.lock:
        finalized = state.metadata.get("_history_finalized") is True
        incomplete = state.quiesced and not finalized
        return WorkspaceProcessInfo(state.process_id, state.metadata["command_id"], state.metadata["run_id"],
            state.command, "cleanup_incomplete" if incomplete else state.state, state.exit_code,
            state.quiesced and finalized, "process_history_incomplete" if incomplete else
            state.code if state.code in _CODES else "process_start_failed")


def _retry_finalization(state: runtime.TrackedProcess) -> None:
    """Retry this owner's ledger only after its original OS cleanup returned."""
    if (state.done.is_set() and state.quiesced and
            state.metadata.get("_history_finalized") is not True and state.on_quiesced):
        try:
            state.on_quiesced(state)
            state.finalization_complete = True
        except Exception:
            with state.lock:
                state.code = "process_history_incomplete"


def _states(workspace, conversation_id, binding):
    return tuple(state for state in runtime.tracked_processes(workspace.path)
        if (state.metadata.get("resource_id"), state.metadata.get("conversation_id"),
            state.metadata.get("binding_id"), state.metadata.get("binding_revision")) ==
            (workspace.id, conversation_id, binding.binding_id, binding.revision))


def list_workspace_processes(resource_id: str, conversation_id: str) -> WorkspaceProcessSnapshot:
    workspace, binding = client_edits._scope(resource_id, conversation_id)
    return WorkspaceProcessSnapshot(resource_id, conversation_id, workspace.updated_at,
        binding.binding_id, binding.revision, tuple(_info(state) for state in _states(workspace, conversation_id, binding)))


def _owned_state(resource_id, conversation_id, process_id):
    workspace, binding = client_edits._scope(resource_id, conversation_id)
    states = [state for state in _states(workspace, conversation_id, binding) if state.process_id == process_id]
    if len(states) != 1:
        raise ValueError("process_unavailable")
    return states[0]


def _mounted_path(value: str) -> str:
    if os.name == "nt":
        for prefix in ("/run/desktop/mnt/host/", "/host_mnt/"):
            if value.startswith(prefix):
                drive, separator, rest = value[len(prefix):].partition("/")
                if len(drive) == 1 and drive.isalpha() and separator:
                    value = drive + ":/" + rest
                    break
        if value.startswith(("\\\\", "//")) or len(value) < 3 or value[1:3] not in {":/", ":\\"}:
            raise ValueError("sandbox_container_changed")
    elif not value.startswith("/"):
        raise ValueError("sandbox_container_changed")
    # Docker-provided paths are compared lexically; never resolve an untrusted
    # UNC/network path while validating a prepared local mount.
    return os.path.normcase(os.path.normpath(value))


def _prepared_container(workspace, expected_id: str | None = None) -> tuple[str, str]:
    from row_bot.developer import sandbox_runtime
    shadow = sandbox_runtime.prepared_shadow_workspace(workspace)
    probe = sandbox_runtime.detect_container_runtime()
    if not probe.available or not probe.binary:
        raise ValueError("sandbox_process_unavailable")
    name = expected_id or sandbox_runtime.sandbox_container_name(workspace.id)
    template = ('{"id":{{json .Id}},"running":{{json .State.Running}},'
        '"network":{{json .HostConfig.NetworkMode}},"image":{{json .Config.Image}},'
        '"workspace_id":{{json (index .Config.Labels "row_bot.developer.workspace_id")}},'
        '"mounts":{{json .Mounts}}}')
    value = json.loads(runtime.run_process_control_query([probe.binary, "inspect", "--format", template, name]))
    if (type(value) is not dict or type(value.get("id")) is not str or len(value["id"]) != 64
            or any(c not in "0123456789abcdef" for c in value["id"]) or value.get("running") is not True
            or value.get("workspace_id") != workspace.id or type(value.get("mounts")) is not list):
        raise ValueError("sandbox_process_unavailable")
    if expected_id and value["id"] != expected_id:
        raise ValueError("sandbox_container_changed")
    matching = [mount for mount in value["mounts"] if type(mount) is dict and mount.get("Destination") == "/workspace"]
    if (len(matching) != 1 or matching[0].get("RW") is not True or type(matching[0].get("Source")) is not str
            or _mounted_path(matching[0]["Source"]) != os.path.normcase(os.path.normpath(str(shadow.resolve())))):
        raise ValueError("sandbox_container_changed")
    if expected_id is None and (value.get("image") != workspace.sandbox_image
            or (value.get("network") == "none") != (workspace.sandbox_network == "off")):
        raise ValueError("sandbox_container_changed")
    return probe.binary, value["id"]


def _receipt_key(identity: dict) -> bytes:
    from row_bot.runtime import admissions
    return bytes.fromhex(admissions.keyed_digest({"domain": "row-bot/workspace-process/receipt/v1",
        "run_id": identity["run_id"], "owner_id": identity["command_id"], "container_id": identity["container_id"]}))


def _verify_receipt(receipt: dict, identity: dict, key: bytes) -> dict:
    from row_bot.developer.process_worker import verify_receipt
    return verify_receipt(receipt, identity["command_id"], identity["container_id"], key)


def _local_supervisor_identity() -> str:
    from row_bot.runtime import admissions
    return admissions.keyed_digest({"domain": "row-bot/workspace-process/local-supervisor/v1"})


def _docker_bootstrap(binary: str, container_id: str, *, recover: bool = False) -> list[str]:
    source = Path(runtime.__file__).with_name("process_worker.py").read_text(encoding="utf-8")
    if len(source.encode("utf-8")) > 24 * 1024:
        raise ValueError("process_containment_unavailable")
    return [binary, "exec", "-i", "-w", "/workspace", container_id,
            "python3", "-I", "-S", "-B", "-u", "-c", source, *(["--recover-owner"] if recover else [])]


def _recover_remote(workspace, identity: dict) -> dict:
    key = _receipt_key(identity)
    if identity.get("process_target") == "local-linux":
        if not sys.platform.startswith("linux") or identity["container_id"] != _local_supervisor_identity():
            raise ValueError("process_recovery_unavailable")
        command = [sys.executable, "-I", "-S", "-B", str(Path(runtime.__file__).with_name("process_worker.py")), "--recover-owner"]
    else:
        binary, container_id = _prepared_container(workspace, identity["container_id"])
        command = _docker_bootstrap(binary, container_id, recover=True)
    value = json.loads(runtime.run_process_control_query(command, {"owner_id": identity["command_id"], "action": "read"}))
    receipt = _verify_receipt(value.get("receipt"), identity, key)
    if not receipt["payload"]["quiesced"]:
        owner = receipt["payload"]
        value = json.loads(runtime.run_process_control_query(command, {"owner_id": identity["command_id"], "action": "stop",
            "supervisor_pid": owner["supervisor_pid"], "start_time": owner["start_time"]}))
        receipt = _verify_receipt(value.get("receipt"), identity, key)
    if not receipt["payload"]["quiesced"]:
        raise ValueError("process_recovery_unavailable")
    return receipt


def get_workspace_process_output(resource_id: str, conversation_id: str, process_id: str,
                                 cursor: int = 0) -> WorkspaceProcessOutput:
    state = _owned_state(resource_id, conversation_id, process_id)
    with state.lock:
        if type(cursor) is not int or cursor < 0 or cursor > state.sequence:
            raise ValueError("process_cursor_invalid")
        first = state.output[0][0] if state.output else state.sequence + 1
        # Include record/envelope overhead, even for thousands of tiny writes.
        entries, cost, next_cursor = [], 512, cursor
        for sequence, channel, text, _ in state.output:
            if sequence <= cursor:
                continue
            size = len(json.dumps({"sequence": sequence, "channel": channel, "text": text}, ensure_ascii=True).encode("ascii")) + 2
            if cost + size > _OUTPUT_PAGE_LIMIT:
                break
            entries.append(WorkspaceProcessOutputEntry(sequence, channel, text))
            next_cursor, cost = sequence, cost + size
        return WorkspaceProcessOutput(process_id, tuple(entries), next_cursor,
            cursor < first - 1 or state.output_incomplete,
            state.quiesced and state.metadata.get("_history_finalized") is True)


def stop_workspace_process(resource_id: str, conversation_id: str, process_id: str, *,
                           validate: Callable[[], None] | None = None) -> WorkspaceProcessInfo:
    if validate:
        validate()
    state = _owned_state(resource_id, conversation_id, process_id)
    runtime.stop_tracked_process(state)
    _retry_finalization(state)
    return _info(state)


def recover_workspace_process(resource_id: str, conversation_id: str, process_id: str, *,
                              validate: Callable[[], None] | None = None) -> WorkspaceProcessInfo:
    """Retry authenticated cleanup of a canonical owner; never take a PID/key."""
    from row_bot import agent_runs
    import psutil
    if validate:
        validate()
    workspace, binding = client_edits._scope(resource_id, conversation_id)
    if str(uuid.UUID(process_id)) != process_id:
        raise ValueError("process_unavailable")
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-process:" + process_id).hex
    with _START_LOCK:
        run = agent_runs.get_agent_run(run_id)
        identity = run.get("result_json") if run else None
        if type(identity) is not dict or any(identity.get(name) != value for name, value in {
                "operation": "workspace.process", "run_id": run_id, "command_id": process_id,
                "resource_id": resource_id, "conversation_id": conversation_id,
                "binding_id": binding.binding_id, "binding_revision": binding.revision}.items()):
            raise ValueError("process_unavailable")
        states = [state for state in runtime.tracked_processes(run["workspace_path"]) if state.process_id == process_id]
        state = states[0] if len(states) == 1 else None
        if state and state.quiesced:
            _retry_finalization(state)
            return _info(state)
        if "container_id" not in identity:
            return WorkspaceProcessInfo(process_id, process_id, run_id, state.command if state else "",
                "cleanup_incomplete", None, False, "process_recovery_unavailable")
        try:
            proof = _recover_remote(workspace, identity)
            if state:
                state.remote_receipt = proof
                state.remote_done.set()
                if not state.done.wait(timeout=10) or not state.host_quiesced or any(thread.is_alive() for thread in state.threads):
                    raise ValueError("process_recovery_unavailable")
                if state.quiesced:
                    return _info(state)
            else:
                owners = [event["payload_json"] for event in agent_runs.get_agent_events(run_id)
                          if event["type"] == "workspace.process.owner"]
                if len(owners) != 1 or type(owners[0].get("launcher_pid")) is not int or type(owners[0].get("launcher_birth")) not in {int, float}:
                    raise ValueError("process_recovery_unavailable")
                try:
                    process = psutil.Process(owners[0]["launcher_pid"])
                    if process.create_time() == owners[0]["launcher_birth"]:
                        raise ValueError("process_recovery_unavailable")
                except psutil.NoSuchProcess:
                    pass
            payload = proof["payload"]
            if state:
                with state.lock:
                    state.quiesced, state.state, state.code = True, "exited", ""
                    state.exit_code = payload["exit_code"]
                if state.on_quiesced:
                    state.on_quiesced(state)
                return _info(state)
            agent_runs.release_agent_write_lock(run_id=run_id)
            final_identity = {**identity, "quiesced": True, "exit_code": payload["exit_code"],
                              "code": "", "completion_receipt": proof}
            status = "completed" if payload["exit_code"] == 0 else "failed"
            if run.get("status") == "stopped" and run.get("stop_requested"):
                status = "stopped"
            finished = agent_runs.finish_agent_run(run_id, status,
                summary="Workspace process cleanup recovered", result_json=final_identity)
            lease = agent_runs.get_agent_write_lock("developer:" + workspace.id)
            if not finished or finished.get("result_json") != final_identity or (lease and lease.get("run_id") == run_id):
                raise ValueError("process_history_incomplete")
            return WorkspaceProcessInfo(process_id, process_id, run_id, "", "exited", payload["exit_code"], True)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, psutil.Error):
            return WorkspaceProcessInfo(process_id, process_id, run_id, state.command if state else "",
                "cleanup_incomplete", None, False, "process_recovery_unavailable")


def start_workspace_process(resource_id: str, conversation_id: str, command: str, *, command_id: str,
        expected_resource_revision: str, expected_binding_id: str, expected_binding_revision: str,
        confirmed: bool = False, validate: Callable[[], None] | None = None) -> WorkspaceProcessInfo:
    """Start a confirmed exact command; retain its writer until actual cleanup.

    `confirmed` and `validate` come from the server's durable approval/admission
    owner, never directly from an unchecked browser boolean. The validator stays
    attached to the process lifetime so capability revocation stops owned work.
    """
    from row_bot import agent_runs
    from row_bot.threads import _get_thread_approval_mode
    workspace, binding = client_edits._scope(resource_id, conversation_id)
    process_id = command_id
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-process:" + str(command_id)).hex
    def failure(code, quiesced=True):
        return WorkspaceProcessInfo(process_id, command_id, run_id,
            command if type(command) is str and len(command) <= 4096 else "", "failed", None, quiesced, code)
    try:
        if (str(uuid.UUID(command_id)) != command_id or type(command) is not str or not command.strip()
                or "\0" in command or len(command.encode("utf-8")) > 4096):
            return failure("process_command_invalid")
        argv = runtime.split_command(command)
        if not argv or runtime.has_shell_control_operator(command):
            return failure("process_command_invalid")
    except (ValueError, TypeError, AttributeError):
        return failure("process_command_invalid")
    identity = {"operation": "workspace.process", "command_id": command_id, "run_id": run_id,
        "resource_id": resource_id, "conversation_id": conversation_id,
        "resource_revision": expected_resource_revision, "binding_id": expected_binding_id,
        "binding_revision": expected_binding_revision, "command_digest": hashlib.sha256(command.encode()).hexdigest()}
    key = "developer:" + workspace.id
    def policy():
        mode = _get_thread_approval_mode(conversation_id)
        action = runtime.classify_command_action(command)
        if action == "run_safe_command":
            action = "start_server"
        decision = decide_action(mode, action)
        decision = runtime._apply_docker_network_policy(workspace, action, decision)
        if decision.requires_approval and confirmed:
            decision = ApprovalDecision("allow", "Server confirmed this exact process command.")
        if not decision.allowed:
            raise ValueError("process_approval_required" if decision.requires_approval else "process_policy_denied")
        return mode
    def scope():
        if validate:
            validate()
        current, current_binding = client_edits._scope(resource_id, conversation_id)
        if (current.to_dict() != workspace.to_dict() or current.updated_at != expected_resource_revision
                or current_binding.binding_id != expected_binding_id or current_binding.revision != expected_binding_revision):
            raise ValueError("resource_revision_conflict")
        policy()
    def guard():
        scope()
        run, lease = agent_runs.get_agent_run(run_id), agent_runs.get_agent_write_lock(key)
        if not run or run.get("status") != "running" or run.get("stop_requested") or not lease or lease["run_id"] != run_id:
            raise ValueError("process_revoked")
    finalization_lock = threading.RLock()
    def finish(state):
        with finalization_lock:
            if state.metadata.get("_history_finalized") is True:
                return
            original_code = state.metadata.setdefault("_history_original_code", state.code)
            saved_run = agent_runs.get_agent_run(run_id)
            saved_identity = saved_run.get("result_json") if saved_run else None
            if not isinstance(saved_identity, dict) or any(saved_identity.get(name) != value for name, value in identity.items()):
                raise ValueError("process_identity_conflict")
            agent_runs.release_agent_write_lock(run_id=run_id)
            final_identity = {**identity, "exit_code": state.exit_code,
                    "quiesced": state.quiesced, "code": original_code,
                    **({"completion_receipt": state.remote_receipt} if state.remote_receipt else {})}
            status = "failed" if original_code or state.exit_code not in {0, None} else "completed"
            if saved_run.get("status") == "stopped" and saved_run.get("stop_requested"):
                status = "stopped"
            finished = agent_runs.finish_agent_run(run_id, status,
                summary="Workspace process", result_json=final_identity, error=original_code)
            lease = agent_runs.get_agent_write_lock(key)
            if (not finished or finished.get("result_json") != final_identity or
                    (lease and lease.get("run_id") == run_id)):
                raise ValueError("process_history_incomplete")
            # Public completion means both owned OS quiescence and canonical
            # writer/history completion. Keep the OS proof intact for retry.
            with state.lock:
                state.metadata["_history_finalized"] = True
                state.code = original_code
    def record_owner(state):
        import psutil
        agent_runs.append_agent_event(run_id, "workspace.process.owner", {
            "launcher_pid": state.process.pid, "launcher_birth": psutil.Process(state.process.pid).create_time()},
            visibility="internal")
    creation_attempted = False
    try:
        scope()
        with _START_LOCK:
            existing = agent_runs.get_agent_run(run_id)
            if existing:
                saved = existing.get("result_json") or {}
                if type(saved) is str:
                    saved = json.loads(saved)
                if type(saved) is not dict or any(saved.get(name) != value for name, value in identity.items()):
                    return failure("process_identity_conflict")
                matches = [state for state in _states(workspace, conversation_id, binding) if state.process_id == process_id]
                if matches:
                    return _info(matches[0])
                if saved.get("quiesced") is True:
                    return WorkspaceProcessInfo(process_id, command_id, run_id, command, "exited",
                        saved.get("exit_code"), True, saved.get("code", ""))
                return failure("process_owner_lost", False)
            mode = policy()
            binary = ""
            if workspace.execution_mode == "docker":
                binary, identity["container_id"] = _prepared_container(workspace)
            elif sys.platform.startswith("linux"):
                identity.update(process_target="local-linux", container_id=_local_supervisor_identity())
            elif os.name != "nt":
                raise ValueError("process_containment_unavailable")
            creation_attempted = True
            agent_runs.create_agent_run(run_id=run_id, kind="workflow", status="running", thread_id=conversation_id,
                parent_thread_id=conversation_id, workspace_id=workspace.id, workspace_path=workspace.path,
                workspace_mode="single_writer", write_lock_key=key, approval_mode=mode,
                display_name="Workspace process", prompt="", result_json=identity)
            if not agent_runs.acquire_agent_write_lock(key, run_id, thread_id=conversation_id,
                    workspace_id=workspace.id, workspace_path=workspace.path, metadata_json=identity):
                raise ValueError("workspace_writer_busy")
            guard()
            remote = {}
            if "container_id" in identity:
                secret = _receipt_key(identity)
                remote = {"request_fields": {"owner_id": command_id, "container_id": identity["container_id"], "key": secret.hex()},
                    "verify_receipt": lambda receipt: _verify_receipt(receipt, identity, secret)}
                if binary:
                    remote["bootstrap_argv"] = _docker_bootstrap(binary, identity["container_id"])
            state = runtime.launch_tracked_process(Path(workspace.path), argv, command, process_id=process_id,
                metadata=identity, on_quiesced=finish, validate=guard, on_owner=record_owner, **remote)
            return _info(state)
    except Exception as exc:
        code = str(exc) if str(exc) in _CODES else "process_start_failed"
        # A bootstrap failure may have installed a still-retiring owner. Never
        # release its writer on the assumption that an exception means stopped.
        owned = [state for state in _states(workspace, conversation_id, binding)
                 if state.process_id == process_id and state.metadata.get("run_id") == run_id]
        if owned:
            return _info(owned[0])
        if creation_attempted and agent_runs.get_agent_run(run_id) is not None:
            try:
                agent_runs.release_agent_write_lock(run_id=run_id)
                agent_runs.finish_agent_run(run_id, "failed", summary="Workspace process",
                    result_json={**identity, "quiesced": True, "code": code}, error=code)
            except Exception:
                return failure("process_history_incomplete", False)
        return failure(code)
