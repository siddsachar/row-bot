"""Durable, exact workspace process intents over existing command/run owners."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import time
from typing import Any
import uuid

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

_TYPES = {"workspace.process.start", "workspace.process.stop", "workspace.process.recover"}


@contextmanager
def _readonly(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Canonical configured store only; no creation, migration or live probing."""
    path = Path(path).absolute()
    try:
        for leaf in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            for component in (*reversed(leaf.parents), leaf):
                try:
                    info = component.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            deadline, steps = time.monotonic() + 2, 0
            def interrupted() -> bool:
                nonlocal steps
                steps += 1000
                return steps >= 10_000_000 or time.monotonic() >= deadline
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
            conn.set_progress_handler(interrupted, 1000)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.close()
    except ClientPlatformError:
        raise
    except (OSError, ValueError, sqlite3.Error):
        raise ClientPlatformError("process_history_unavailable") from None


def _table(conn: sqlite3.Connection, name: str) -> bool:
    # Only constant internal names reach this helper.
    matching = [row for row in conn.execute("PRAGMA table_list") if row[0] == "main" and row[1] == name]
    ordinary = bool(matching and matching[0][2] == "table")
    if matching and not ordinary:
        raise ClientPlatformError("process_history_unavailable")
    if ordinary and any(row[6] != 0 for row in conn.execute(f'PRAGMA table_xinfo("{name}")')):
        raise ClientPlatformError("process_history_unavailable")
    return ordinary


def _scope(resource_id: str, conversation_id: str, validate: Callable[[], None]):
    from row_bot import threads
    from row_bot.conversation_resources import _read
    from row_bot.developer.storage import get_workspace
    from row_bot.developer.review import scoped_workspace_path
    from row_bot.approval_policy import normalize_approval_mode
    validate()
    with _readonly(threads.DB_PATH) as conn:
        if not _table(conn, "thread_meta"):
            raise ClientPlatformError("not_found")
        metadata = conn.execute("SELECT client_revision,approval_mode FROM thread_meta WHERE thread_id=?", (conversation_id,)).fetchone()
        if metadata is None:
            raise ClientPlatformError("not_found")
        bindings = _read(conn, conversation_id).bindings
    binding = [item for item in bindings if item.kind == "workspace" and item.resource_id == resource_id]
    if len(binding) != 1:
        raise ClientPlatformError("resource_binding_revoked")
    workspace = get_workspace(resource_id)
    if workspace is None:
        raise ClientPlatformError("resource_unavailable")
    scoped_workspace_path(Path(workspace.path))
    validate()
    return workspace, binding[0], str(metadata["client_revision"]), normalize_approval_mode(metadata["approval_mode"], threads.DEFAULT_APPROVAL_MODE)


def _uuid(value: str) -> str:
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ClientPlatformError("invalid_command") from None
    return value


def _run_id(process_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-process:" + process_id).hex


def _cleanup_requested(owner_id: str, process_id: str, target: dict, conversation_id: str) -> bool:
    """A durable Stop received before launch also cancels that reserved Start."""
    from row_bot import tasks
    with _readonly(tasks._DB_PATH) as conn:
        if not _table(conn, "client_commands"):
            return False
        return conn.execute("""SELECT 1 FROM client_commands
            WHERE owner_id=? AND target=? AND type IN ('workspace.process.stop','workspace.process.recover')
              AND json_valid(result_json)
              AND json_extract(result_json,'$.workspace_process_phase')='cleanup_requested'
              AND json_extract(result_json,'$.workspace_process_id')=?
              AND json_extract(result_json,'$.resource_id')=?
              AND json_extract(result_json,'$.binding_id')=?
              AND json_extract(result_json,'$.binding_revision')=? LIMIT 1""",
            (owner_id, conversation_id, process_id, target["resource_id"],
             target["binding_id"], target["binding_revision"])).fetchone() is not None


def get_workspace_process_review(resource_id: str, conversation_id: str, command_id: str,
                                 command: str, *, validate: Callable[[], None]) -> dict:
    """Pure review state; only the route's nonce callback can grant execution."""
    from row_bot.developer import runtime
    from row_bot.developer.sandbox import decide_action
    _uuid(command_id)
    try:
        if (type(command) is not str or not command.strip() or "\0" in command or len(command.encode("utf-8")) > 4096):
            raise ValueError
        if not runtime.split_command(command) or runtime.has_shell_control_operator(command):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ClientPlatformError("process_command_invalid") from None
    workspace, binding, revision, mode = _scope(resource_id, conversation_id, validate)
    action = runtime.classify_command_action(command)
    if action == "run_safe_command":
        action = "start_server"
    decision = runtime._apply_docker_network_policy(workspace, action, decide_action(mode, action))
    policy = {"mode": mode, "execution_mode": workspace.execution_mode,
              "sandbox_network": workspace.sandbox_network, "sandbox_image": workspace.sandbox_image,
              "action": action, "decision": decision.decision}
    policy_revision = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"resource_id": resource_id, "conversation_id": conversation_id,
              "binding_id": binding.binding_id, "binding_revision": binding.revision,
              "resource_revision": workspace.updated_at, "conversation_revision": revision,
              "command_id": command_id, "command": command, "policy_revision": policy_revision,
              "policy_decision": decision.decision, "approval_required": decision.requires_approval}
    # Chat-only revision advances must not revoke a running owned process.
    # Initial command admission still checks expected conversation revision.
    action_state = {key: value for key, value in result.items() if key != "conversation_revision"}
    result["action_digest"] = hashlib.sha256(json.dumps(action_state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return result


def _safe_info(info) -> dict:
    from row_bot.developer.client_processes import _CODES
    value = asdict(info)
    # Keep the canonical admission store content-free. The live runtime/session
    # owns command display text; receipts retain only owned identifiers/results.
    value["command"] = ""
    value["code"] = value.get("code") if value.get("code") in _CODES else "process_start_failed"
    return value


def _completed_owner(process_id: str, target: dict, conversation_id: str, text: str) -> dict | None:
    from row_bot import tasks
    from row_bot.developer.client_processes import _CODES
    with _readonly(tasks._DB_PATH) as conn:
        if not _table(conn, "agent_runs") or not _table(conn, "agent_write_locks"):
            return None
        row = conn.execute("SELECT result_json FROM agent_runs WHERE id=?", (_run_id(process_id),)).fetchone()
        if not row:
            return None
        saved = json.loads(row["result_json"])
        expected = {"operation": "workspace.process", "command_id": process_id, "run_id": _run_id(process_id),
            "resource_id": target["resource_id"], "conversation_id": conversation_id,
            "binding_id": target["binding_id"], "binding_revision": target["binding_revision"],
            "resource_revision": target["resource_revision"],
            "command_digest": hashlib.sha256(text.encode()).hexdigest()}
        if type(saved) is not dict or any(saved.get(key) != value for key, value in expected.items()):
            raise ClientPlatformError("operation_uncertain")
        if saved.get("quiesced") is not True or conn.execute("SELECT 1 FROM agent_write_locks WHERE run_id=?", (_run_id(process_id),)).fetchone():
            return None
        code, exit_code = saved.get("code", ""), saved.get("exit_code")
        if code not in _CODES or (exit_code is not None and type(exit_code) is not int):
            raise ClientPlatformError("operation_uncertain")
        return {"process_id": process_id, "command_id": process_id, "run_id": _run_id(process_id), "command": "",
                "state": "failed" if code else "exited", "quiesced": True, "exit_code": exit_code, "code": code}


def execute_workspace_process_command(service: Any, command: dict, conversation_id: str, *,
        owner_id: str, key: str, validate: Callable[[], None],
        validate_approval: Callable[[dict], None] | None = None) -> dict:
    """Call outside shared Start locks. Cleanup uses authenticated owner authority.

    For Stop/Recover, the injected validator must allow cleanup of this owned
    target when new-start capability is withdrawn; exact current binding checks
    still apply. It must not grant new Start authority as a cleanup side effect.
    """
    from row_bot.developer import client_processes
    from row_bot.thread_cleanup import is_thread_deleting
    if command.get("type") not in _TYPES:
        raise ClientPlatformError("invalid_command")
    command_id = _uuid(command["command_id"])
    payload, kind = command["payload"], command["type"]
    target = payload["target"]
    if target.get("kind") != "workspace":
        raise ClientPlatformError("invalid_command")
    process_id = command_id if kind.endswith(".start") else _uuid(payload["process_id"])
    validate()
    start_returned = False
    approval_checked = False

    def authority(*, initial: bool = False) -> dict | None:
        workspace, binding, revision, _ = _scope(target["resource_id"], conversation_id, validate)
        if binding.binding_id != target["binding_id"] or binding.revision != target["binding_revision"]:
            raise ClientPlatformError("resource_binding_revoked")
        if is_thread_deleting(conversation_id, initialized_read=True):
            raise ClientPlatformError("conversation_deleting")
        if kind.endswith(".start"):
            if not start_returned and _cleanup_requested(owner_id, process_id, target, conversation_id):
                raise ClientPlatformError("process_revoked")
            if workspace.updated_at != target["resource_revision"]:
                raise ClientPlatformError("resource_revision_conflict")
            if initial and revision != command["expected_revision"]:
                raise ClientPlatformError("revision_conflict", revision)
            review = get_workspace_process_review(target["resource_id"], conversation_id, command_id,
                                                   payload["command"], validate=validate)
            if review["policy_revision"] != payload["policy_revision"] or review["action_digest"] != payload["action_digest"]:
                raise ClientPlatformError("process_review_stale")
            if review["policy_decision"] == "block":
                raise ClientPlatformError("process_policy_denied")
            if approval_checked and not start_returned and validate_approval is not None:
                validate_approval(review)
            return review
        return None

    # Existing claim HMAC binds target, command bytes, policy, approval nonce and
    # command identity. A duplicate in-flight Start is never dispatched again.
    retrying = False
    try:
        prior = admissions.claim_command(owner_id, key, command, conversation_id)
    except admissions.AdmissionError as exc:
        if str(exc) != "operation_uncertain":
            raise ClientPlatformError(str(exc), exc.current_revision) from exc
        prior, retrying = admissions.receipt(owner_id, command_id), True
    if prior:
        # Current authentication/binding is still required for historical data,
        # but a later conversation/policy edit cannot turn receipt reads into a
        # new Start or prevent an exact already-owned cleanup request.
        workspace, binding, _, _ = _scope(target["resource_id"], conversation_id, validate)
        if binding.binding_id != target["binding_id"] or binding.revision != target["binding_revision"]:
            raise ClientPlatformError("resource_binding_revoked")
        if prior.get("status") == "completed":
            return prior
    if retrying and kind.endswith(".start"):
        completed = _completed_owner(process_id, target, conversation_id, payload["command"])
        if completed:
            return admissions.complete_command(owner_id, key, {**(prior or {}), "status": "completed", "workspace_process": completed})
        return {**(prior or {}), "command_id": command_id, "conversation_id": conversation_id,
                "resource_id": target["resource_id"], "workspace_process_id": process_id,
                "workspace_process_run_id": _run_id(process_id), "status": "partial", "code": "process_start_unconfirmed"}
    progress = {"command_id": command_id, "conversation_id": conversation_id,
                "resource_id": target["resource_id"], "workspace_process_id": process_id,
                "binding_id": target["binding_id"], "binding_revision": target["binding_revision"],
                "workspace_process_run_id": _run_id(process_id), "status": "admitting"}
    reserved = False
    try:
        review = authority(initial=not retrying)
        if kind.endswith(".start"):
            if validate_approval is None:
                raise ClientPlatformError("action_denied")
            validate_approval(review)
            approval_checked = True
            authority(initial=True)
            progress["workspace_process_phase"] = "start_reserved"
            admissions.command_progress(owner_id, key, progress)
            reserved = True
            outcome = client_processes.start_workspace_process(target["resource_id"], conversation_id,
                payload["command"], command_id=process_id, expected_resource_revision=target["resource_revision"],
                expected_binding_id=target["binding_id"], expected_binding_revision=target["binding_revision"],
                confirmed=True, validate=authority)
            start_returned = True
        else:
            progress["workspace_process_phase"] = "cleanup_requested"
            admissions.command_progress(owner_id, key, progress)
            reserved = True
            operation = client_processes.stop_workspace_process if kind.endswith(".stop") else client_processes.recover_workspace_process
            outcome = operation(target["resource_id"], conversation_id, process_id, validate=authority)
        if outcome.process_id != process_id or outcome.command_id != process_id or outcome.run_id != _run_id(process_id):
            raise ClientPlatformError("operation_uncertain")
        progress.update(workspace_process=_safe_info(outcome), status="partial" if outcome.state == "cleanup_incomplete" or
                        (outcome.state == "failed" and not outcome.quiesced) else "completed")
        if progress["status"] == "completed":
            return admissions.complete_command(owner_id, key, progress)
        admissions.command_progress(owner_id, key, progress)
        return progress
    except Exception as exc:
        if reserved:
            progress.update(status="partial", code="process_start_unconfirmed" if kind.endswith(".start") else "process_cleanup_unconfirmed")
            try:
                admissions.command_progress(owner_id, key, progress)
            except admissions.AdmissionError:
                pass
            return progress
        code = exc.code if isinstance(exc, ClientPlatformError) else "process_admission_failed"
        if getattr(exc, "code", None) in {"approval_expired", "approval_already_resolved", "session_revoked", "action_denied"}:
            code = exc.code
        admissions.reject_command(owner_id, key, code)
        raise ClientPlatformError(code) from exc


def list_durable_workspace_processes(resource_id: str, conversation_id: str, *, binding_id: str,
        binding_revision: str, validate: Callable[[], None], cursor: str | None = None, limit: int = 32) -> dict:
    """Read bounded recovery candidates from existing AgentRun rows only.

    States are historical and explicitly unconfirmed unless the canonical owner
    recorded quiescence. This never probes a process/container or releases locks.
    """
    from row_bot import tasks
    workspace, binding, _, _ = _scope(resource_id, conversation_id, validate)
    if binding.binding_id != binding_id or binding.revision != binding_revision:
        raise ClientPlatformError("resource_binding_revoked")
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ClientPlatformError("invalid_command")
    fingerprint = hashlib.sha256(json.dumps([conversation_id, resource_id, binding_id, binding_revision]).encode()).hexdigest()[:32]
    before = 2**63 - 1
    if cursor:
        try:
            if type(cursor) is not str or len(cursor) > 64:
                raise ValueError
            marker, number = cursor.split(":")
            before = int(number)
            if len(cursor) > 64 or marker != fingerprint or not 0 < before < 2**63 or str(before) != number:
                raise ValueError
        except (ValueError, TypeError):
            raise ClientPlatformError("cursor_expired") from None
    if not Path(tasks._DB_PATH).is_file():
        return {"items": [], "next_cursor": None}
    with _readonly(tasks._DB_PATH) as conn:
        if not _table(conn, "agent_runs"):
            return {"items": [], "next_cursor": None}
        rows = conn.execute("""SELECT rowid,id,result_json FROM agent_runs
            WHERE rowid<? AND workspace_id=? AND parent_thread_id=? AND json_valid(result_json)
              AND json_extract(result_json,'$.operation')='workspace.process'
              AND json_extract(result_json,'$.binding_id')=? AND json_extract(result_json,'$.binding_revision')=?
              AND json_extract(result_json,'$.quiesced') IS NOT 1
            ORDER BY rowid DESC LIMIT ?""", (before, workspace.id, conversation_id, binding_id, binding_revision, limit + 1)).fetchall()
    items = []
    for row in rows[:limit]:
        try:
            saved = json.loads(row["result_json"])
            identifier = _uuid(saved["command_id"])
            if (row["id"] != _run_id(identifier) or saved.get("run_id") != row["id"] or
                    saved.get("resource_id") != resource_id or saved.get("conversation_id") != conversation_id):
                raise ValueError
            items.append({"process_id": identifier, "command_id": identifier, "run_id": row["id"],
                          "command": "", "state": "cleanup_incomplete", "exit_code": None,
                          "quiesced": False, "code": "process_owner_lost"})
        except (KeyError, TypeError, ValueError, ClientPlatformError):
            raise ClientPlatformError("process_history_unavailable") from None
    validate()
    return {"items": items, "next_cursor": f"{fingerprint}:{rows[limit - 1]['rowid']}" if len(rows) > limit else None}
