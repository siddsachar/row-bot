"""Explicit bounded workspace editing over existing resource and file owners.

Reads never initialize a sandbox. Browser values are intent, not authority: the
application supplies its durable command receipt and current grant validator.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
from pathlib import Path
from typing import Literal
import uuid

from row_bot.developer import change_ledger, client_workspace, edits, sandbox_runtime

INLINE_JSON_LIMIT = 200 * 1024
EditTarget = Literal["workspace", "sandbox_shadow"]


@dataclass(frozen=True)
class WorkspaceEditableFile:
    resource_id: str
    conversation_id: str
    relative_path: str
    resource_revision: str
    binding_id: str
    binding_revision: str
    target: EditTarget
    status: Literal["text", "missing", "binary", "too_large", "denied", "sandbox_unprepared"]
    content: str = ""
    digest: str = ""
    size_bytes: int = 0
    code: str = ""
    schema_version: int = 1
    review_token: str = ""


@dataclass(frozen=True)
class WorkspaceEditResult:
    resource_id: str
    conversation_id: str
    relative_path: str
    resource_revision: str
    binding_id: str
    binding_revision: str
    target: EditTarget
    status: Literal["saved", "unchanged", "pending_import", "conflict", "denied", "partial"]
    digest: str = ""
    change_set_id: str | None = None
    pending_change_id: str | None = None
    file_saved: bool = False
    ledger_saved: bool = False
    code: str = ""
    schema_version: int = 1


@dataclass(frozen=True)
class WorkspaceEditRecovery:
    """Private application-owned receipt, never accepted directly from a client."""
    command_id: str
    resource_id: str
    conversation_id: str
    relative_path: str
    resource_revision: str
    binding_id: str
    binding_revision: str
    target: EditTarget
    file: edits.FileEditRecovery
    review_token: str = ""
    parent_identity: str = ""


def _scope(resource_id: str, conversation_id: str):
    from row_bot.conversation_resources import list_bindings
    workspace = client_workspace._query_workspace(resource_id, conversation_id)
    bindings = list_bindings(conversation_id)
    matching = [item for item in bindings.bindings if item.kind == "workspace" and item.resource_id == resource_id]
    if len(matching) != 1:
        raise ValueError("resource_binding_revoked")
    return workspace, matching[0]


def _target(workspace) -> tuple[Path, EditTarget]:
    if workspace.execution_mode == "docker":
        return sandbox_runtime.prepared_shadow_workspace(workspace), "sandbox_shadow"
    return Path(workspace.path), "workspace"


def _inline(content: str) -> bool:
    return len(json.dumps(content, ensure_ascii=True).encode("ascii")) <= INLINE_JSON_LIMIT


def _capture_identity(root: Path, relative_path: str) -> tuple[bytes | None, dict[str, str]]:
    """Capture complete bytes and private authority under the existing parent guard."""
    path = edits._edit_path(root, relative_path)
    parent = client_workspace._directory_identity(path.parent, parent=True)
    with client_workspace._empty_parent_guard(path.parent, parent) as descriptor:
        if descriptor is None:
            data, digest, identity, _ = edits.read_edit_bytes(root, relative_path)
            metadata = edits.file_edit_metadata_digest(path) if data is not None else ""
        else:
            data, digest, identity, _ = edits._read_edit_at(descriptor, path.name)
            metadata = edits._edit_metadata_at(descriptor, path.name) if data is not None else ""
    return data, {"digest": digest, "identity": identity, "metadata": metadata, "parent_identity": parent}


def _review_token(workspace, binding, conversation_id: str, relative_path: str, target: EditTarget,
                  approval_mode: str, capture: dict[str, str]) -> str:
    from row_bot.runtime.admissions import keyed_digest
    return keyed_digest({"operation": "workspace.edit.initial-review.v1", "workspace": workspace.to_dict(),
        "conversation_id": conversation_id, "binding_id": binding.binding_id, "binding_revision": binding.revision,
        "relative_path": relative_path, "target": target, "approval_mode": approval_mode, "file": capture}, read_only=True)


def get_workspace_editable_file(resource_id: str, conversation_id: str, relative_path: str) -> WorkspaceEditableFile:
    from row_bot.threads import _get_thread_approval_mode
    workspace, binding = _scope(resource_id, conversation_id)
    target: EditTarget = "sandbox_shadow" if workspace.execution_mode == "docker" else "workspace"
    result = WorkspaceEditableFile(resource_id, conversation_id, relative_path, workspace.updated_at,
                                   binding.binding_id, binding.revision, target, "denied")
    try:
        root, _ = _target(workspace)
        approval_mode = _get_thread_approval_mode(conversation_id)
        data, capture = _capture_identity(root, relative_path)
        digest = capture["digest"]
        try:
            text = data.decode("utf-8") if data is not None else ""
        except UnicodeError:
            return replace(result, status="binary", digest=digest, size_bytes=len(data), code="file_not_text")
        if data is not None and b"\0" in data:
            return replace(result, status="binary", digest=digest, size_bytes=len(data), code="file_not_text")
        if not _inline(text):
            return replace(result, status="too_large", digest=digest, size_bytes=len(data), code="inline_edit_too_large")
        current, current_binding = _scope(resource_id, conversation_id)
        if (current.to_dict() != workspace.to_dict() or current_binding != binding
                or _get_thread_approval_mode(conversation_id) != approval_mode):
            raise ValueError("resource_revision_conflict")
        token = _review_token(workspace, binding, conversation_id, relative_path, target, approval_mode, capture)
        return replace(result, status="missing" if data is None else "text", content=text, digest=digest,
            size_bytes=len(data) if data is not None else 0, review_token=token)
    except (OSError, ValueError) as exc:
        code = str(exc)
        if code == "sandbox_unprepared":
            return replace(result, status="sandbox_unprepared", code=code)
        if code == "file_too_large":
            return replace(result, status="too_large", code="inline_edit_too_large")
        if code == "command_metadata_unavailable":
            return replace(result, code=code)
        return replace(result, code="file_revision_conflict" if code == "file_revision_conflict" else "workspace_path_denied")


@contextmanager
def _owned_edit_run(workspace, conversation_id: str, command_id: str, approval_mode: str,
                    outcome: dict) -> Iterator[Callable[[], None]]:
    from row_bot import agent_runs
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-edit:" + command_id).hex
    lock_key = f"developer:{workspace.id}"
    existing = agent_runs.get_agent_run(run_id)
    if existing is not None:
        saved = existing.get("result_json") or {}
        if (not isinstance(saved, dict) or saved.get("command_id") != command_id or saved.get("operation") != "workspace.edit"
                or existing.get("workspace_id") != workspace.id or existing.get("thread_id") != conversation_id
                or existing.get("write_lock_key") != lock_key):
            raise edits.FileEditError("edit_recovery_conflict")
    created = existing is not None
    try:
        if existing is None:
            agent_runs.create_agent_run(run_id=run_id, kind="workflow", status="running",
                thread_id=conversation_id, parent_thread_id=conversation_id, workspace_id=workspace.id,
                workspace_path=workspace.path, workspace_mode="single_writer", write_lock_key=lock_key,
                approval_mode=approval_mode, display_name="Workspace file edit", prompt="",
                result_json={"command_id": command_id, "operation": "workspace.edit"})
            created = True
        elif existing.get("stop_requested"):
            raise edits.FileEditError("edit_cancelled")
        lease = agent_runs.get_agent_write_lock(lock_key)
        owns_lease = lease and lease.get("run_id") == run_id
        if owns_lease and (lease.get("workspace_id") != workspace.id or lease.get("thread_id") != conversation_id):
            raise edits.FileEditError("edit_recovery_conflict")
        if not owns_lease and not agent_runs.acquire_agent_write_lock(lock_key, run_id, thread_id=conversation_id,
                workspace_id=workspace.id, workspace_path=workspace.path,
                metadata_json={"command_id": command_id, "operation": "workspace.edit"}):
            raise edits.FileEditError("workspace_writer_busy")
        if existing is not None:
            agent_runs.start_agent_run(run_id)
        def validate_run():
            run = agent_runs.get_agent_run(run_id)
            lock = agent_runs.get_agent_write_lock(lock_key)
            if (run is None or run.get("status") != "running" or run.get("stop_requested")
                    or run.get("workspace_id") != workspace.id or run.get("thread_id") != conversation_id
                    or not lock or lock.get("run_id") != run_id or lock.get("workspace_id") != workspace.id
                    or lock.get("thread_id") != conversation_id):
                raise edits.FileEditError("edit_cancelled")
        validate_run()
        yield validate_run
    except Exception as exc:
        outcome.update(run_status="failed", file_saved=bool(getattr(exc, "file_saved", False)),
                       code=getattr(exc, "code", "workspace_edit_failed"))
        raise
    finally:
        # Admission-event failures can occur after the SQL lock was committed.
        # Releasing only this exact, registered original-command run never releases a
        # different agent's workspace lease.
        try:
            lease = agent_runs.get_agent_write_lock(lock_key)
            if lease and lease.get("run_id") == run_id:
                if lease.get("workspace_id") != workspace.id or lease.get("thread_id") != conversation_id:
                    raise edits.FileEditError("edit_recovery_conflict")
                agent_runs.release_agent_write_lock(run_id=run_id)
            remaining = agent_runs.get_agent_write_lock(lock_key)
            if remaining and remaining.get("run_id") == run_id:
                raise edits.FileEditError("workspace_writer_busy")
        finally:
            if created or agent_runs.get_agent_run(run_id) is not None:
                current = agent_runs.get_agent_run(run_id)
                final_status = "stopped" if current and current.get("stop_requested") else outcome.get("run_status", "failed")
                agent_runs.finish_agent_run(run_id, final_status,
                    summary="Workspace file edit", result_json={"command_id": command_id,
                    "operation": "workspace.edit", "file_saved": outcome.get("file_saved", False)},
                    error=outcome.get("code", ""))


def save_workspace_text(resource_id: str, conversation_id: str, relative_path: str, content: str, *,
                        expected_resource_revision: str, expected_binding_id: str, expected_binding_revision: str,
                        expected_digest: str, command_id: str, expected_review_token: str,
                        persist_recovery: Callable[[WorkspaceEditRecovery], None],
                        recovery: WorkspaceEditRecovery | None = None,
                        validate: Callable[[], None] | None = None) -> WorkspaceEditResult:
    """Save explicit inline text, keeping file and history progress truthful."""
    from row_bot.threads import _get_thread_approval_mode
    workspace, binding = _scope(resource_id, conversation_id)
    target: EditTarget = "sandbox_shadow" if workspace.execution_mode == "docker" else "workspace"
    result = WorkspaceEditResult(resource_id, conversation_id, relative_path, workspace.updated_at,
                                 binding.binding_id, binding.revision, target, "denied")
    outcome = {}
    saved_recovery = recovery
    try:
        if str(uuid.UUID(command_id)) != command_id or not callable(persist_recovery):
            raise edits.FileEditError("invalid_edit")
        if type(content) is not str or not _inline(content) or len(content.encode("utf-8")) > edits.CLIENT_EDIT_BYTE_LIMIT:
            raise edits.FileEditError("inline_edit_too_large")
        approval_mode = _get_thread_approval_mode(conversation_id)
        if edits.ordinary_edit_decision(approval_mode).decision != "allow":
            raise edits.FileEditError("edit_policy_denied")
        root, _ = _target(workspace)
        def validate_scope():
            if validate:
                validate()
            current, current_binding = _scope(resource_id, conversation_id)
            if (current.to_dict() != workspace.to_dict() or current.updated_at != expected_resource_revision
                    or current_binding.binding_id != expected_binding_id
                    or current_binding.revision != expected_binding_revision or _target(current)[0] != root):
                raise edits.FileEditError("resource_revision_conflict")
            if edits.ordinary_edit_decision(_get_thread_approval_mode(conversation_id)).decision != "allow":
                raise edits.FileEditError("edit_policy_denied")
            if _get_thread_approval_mode(conversation_id) != approval_mode:
                raise edits.FileEditError("file_review_conflict")
        validate_scope()
        scope_values = (command_id, resource_id, conversation_id, relative_path,
                        expected_resource_revision, expected_binding_id, expected_binding_revision, target)
        if recovery is not None and (recovery.command_id, recovery.resource_id, recovery.conversation_id,
                recovery.relative_path, recovery.resource_revision, recovery.binding_id, recovery.binding_revision, recovery.target) != scope_values:
            raise edits.FileEditError("edit_recovery_conflict")
        data, capture = _capture_identity(root, relative_path)
        digest = capture["digest"]
        if data is not None:
            try:
                if b"\0" in data:
                    raise UnicodeError
                before_text = data.decode("utf-8")
            except UnicodeError:
                raise edits.FileEditError("file_not_text") from None
            if not _inline(before_text):
                raise edits.FileEditError("inline_edit_too_large")
        if recovery is None and digest != expected_digest:
            raise edits.FileEditError("file_revision_conflict")
        if recovery is not None:
            if recovery.review_token != expected_review_token or not recovery.parent_identity:
                raise edits.FileEditError("edit_recovery_conflict")
            capture = {"digest": recovery.file.before_digest, "identity": recovery.file.original_identity,
                "metadata": recovery.file.metadata_digest if recovery.file.before_digest != "missing" else "",
                "parent_identity": recovery.parent_identity}
        token = _review_token(workspace, binding, conversation_id, relative_path, target, approval_mode, capture)
        if (type(expected_review_token) is not str or len(expected_review_token) != 64
                or not hmac.compare_digest(token, expected_review_token)):
            raise edits.FileEditError("file_review_conflict")
        if recovery is None and data == content.encode("utf-8"):
            return replace(result, status="unchanged", digest=digest)
        if target == "workspace":
            change_ledger.validate_client_ledger()
        else:
            sandbox_runtime.validate_client_pending()
        def persist(file_recovery):
            nonlocal saved_recovery
            saved_recovery = WorkspaceEditRecovery(*scope_values, file_recovery, expected_review_token, capture["parent_identity"])
            persist_recovery(saved_recovery)
        with _owned_edit_run(workspace, conversation_id, command_id, approval_mode, outcome) as validate_run:
            def admitted():
                validate_run()
                validate_scope()
                if not hmac.compare_digest(_review_token(workspace, binding, conversation_id, relative_path,
                        target, approval_mode, capture), expected_review_token):
                    raise edits.FileEditError("file_review_conflict")
            admitted()
            constraints = dict(expected_identity=capture["identity"], expected_metadata=capture["metadata"],
                expected_parent_identity=capture["parent_identity"])
            if target == "sandbox_shadow":
                value = sandbox_runtime.write_file_in_docker_sandbox(workspace, relative_path, content,
                    thread_id=conversation_id, prepared_only=True, expected_digest=expected_digest,
                    command_id=command_id, persist_recovery=persist, recovery=recovery.file if recovery else None,
                    validate=admitted, **constraints)
                result = replace(result, status="pending_import" if value.pending_change_id else "unchanged",
                    digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    pending_change_id=value.pending_change_id or None,
                    file_saved=bool(value.pending_change_id), ledger_saved=bool(value.pending_change_id))
                if value.pending_change_id:
                    pending = sandbox_runtime.get_pending_change(value.pending_change_id)
                    if pending is not None and pending.imported:
                        result = replace(result, status="saved", code="sandbox_change_already_imported")
            else:
                publication = edits.publish_text_revision(root, relative_path, content,
                    expected_digest=expected_digest, command_id=command_id, persist_recovery=persist,
                    recovery=recovery.file if recovery else None, validate=admitted, **constraints)
                result = replace(result, digest=publication.digest, file_saved=publication.changed,
                                 status="saved" if publication.changed else "unchanged")
                if publication.changed:
                    try:
                        change = change_ledger.record_change_set(workspace_id=workspace.id, thread_id=conversation_id,
                            summary=f"Edit {relative_path}", command_id=command_id, files=[change_ledger.FileChange(
                                path=relative_path, action="create" if publication.before_text is None else "update",
                                before_hash=change_ledger.text_hash(publication.before_text),
                                after_hash=change_ledger.text_hash(content), before_text=publication.before_text)])
                        result = replace(result, change_set_id=change.id, ledger_saved=True)
                    except Exception:
                        raise edits.FileEditError("change_ledger_incomplete", publication.recovery, file_saved=True) from None
            outcome.update(run_status="completed", file_saved=result.file_saved)
        return result
    except Exception as exc:
        safe = {"invalid_edit", "inline_edit_too_large", "file_too_large", "file_not_text", "edit_policy_denied",
                "sandbox_unprepared", "resource_revision_conflict", "resource_binding_revoked", "workspace_path_denied",
                "edit_recovery_conflict", "file_revision_conflict", "workspace_writer_busy", "edit_cancelled",
                "change_ledger_unavailable", "sandbox_history_unavailable", "change_ledger_incomplete",
                "sandbox_history_incomplete", "edit_receipt_unconfirmed", "file_publication_incomplete",
                "sandbox_patch_unavailable",
                "file_metadata_unavailable", "file_review_conflict", "command_metadata_unavailable", "folder_selection_denied",
                "file_publication_unavailable", "capability_revoked", "action_denied"}
        code = getattr(exc, "code", str(exc))
        code = code if type(code) is str and code in safe else "file_publication_incomplete"
        file_saved = result.file_saved or bool(getattr(exc, "file_saved", False))
        partial = file_saved or saved_recovery is not None
        conflict = code in {"resource_revision_conflict", "file_revision_conflict", "file_review_conflict", "edit_recovery_conflict", "workspace_writer_busy"}
        return replace(result, status="partial" if partial else "conflict" if conflict else "denied",
                       file_saved=file_saved, code=code)
