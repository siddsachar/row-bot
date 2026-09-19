"""Reviewed byte-exact Undo over canonical imported-file retention and writer leases."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import copy
import json
from pathlib import Path
import uuid

from row_bot.developer import change_ledger, client_edits, client_imports, edits
from row_bot.developer.sandbox import decide_action
from row_bot.runtime import admissions


@dataclass(frozen=True)
class WorkspaceUndoReview:
    resource_id: str
    conversation_id: str
    resource_revision: str
    binding_id: str
    binding_revision: str
    change_set_id: str
    change_set_revision: str
    host_revision: str
    policy_revision: str
    policy_decision: str
    approval_required: bool
    files: tuple[str, ...]
    directories_retained: tuple[str, ...]
    action_digest: str


@dataclass(frozen=True)
class WorkspaceUndoResult:
    command_id: str
    resource_id: str
    conversation_id: str
    change_set_id: str
    status: str
    files_restored: tuple[str, ...]
    ledger_saved: bool
    reverted: bool
    code: str = ""


def _digest(value):
    return admissions.keyed_digest(value, read_only=True)


def _source(resource_id, conversation_id, change_set_id):
    workspace, binding = client_edits._scope(resource_id, conversation_id)
    change, revision = change_ledger.read_change_set(change_set_id)
    if change.workspace_id != resource_id or change.thread_id != conversation_id:
        raise ValueError("change_set_unavailable")
    saved = change.guarded_import
    try:
        paths = [item.path for item in change.files]
        if (not isinstance(saved, dict) or saved.get("kind") != "workspace.import.v1"
                or not 1 <= len(paths) <= 100 or len(set(paths)) != len(paths)
                or set(saved["files"]) != set(paths) or set(saved["parents"]) != set(paths)
                or len(json.dumps(saved, ensure_ascii=True).encode()) > client_imports.RECOVERY_BYTES
                or change.id != uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:client-edit:" + saved["command_id"]).hex):
            raise ValueError
        for path in paths:
            proof = edits.FileEditRecovery(**saved["files"][path])
            if (proof.relative_path != path or proof.command_id != str(uuid.uuid5(uuid.UUID(saved["command_id"]), "sandbox-import:" + path))
                    or not isinstance(saved["parents"][path], str) or not saved["parents"][path]):
                raise ValueError
        if (not isinstance(saved["directories"], list) or len(saved["directories"]) > 100
                or any(not isinstance(path, str) for path in saved["directories"])):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError("workspace_undo_proof_unavailable") from None
    return workspace, binding, change, revision


def _hosts(change):
    return {path: {"digest": value["after_digest"],
        "identity": value["candidate_identity"] if value["after_digest"] != "missing" else "",
        "metadata": value["metadata_digest"] if value["after_digest"] != "missing" else "",
        "parent_identity": change.guarded_import["parents"][path]}
        for path, value in change.guarded_import["files"].items()}


def _policy(workspace, conversation_id):
    from row_bot.threads import _get_thread_approval_mode
    return {"approval_mode": _get_thread_approval_mode(conversation_id), "workspace": workspace.to_dict()}


def review_workspace_undo(resource_id: str, conversation_id: str, change_set_id: str, *,
                          validate: Callable[[], None] = lambda: None) -> WorkspaceUndoReview:
    validate()
    workspace, binding, change, revision = _source(resource_id, conversation_id, change_set_id)
    if change.reverted:
        raise ValueError("change_set_already_reverted")
    total = 0
    for path, expected in _hosts(change).items():
        validate()
        _, actual = client_edits._capture_identity(Path(workspace.path), path)
        if actual != expected:
            raise ValueError("file_revision_conflict")
        data = edits.read_retained_edit_before(Path(workspace.path), path,
            edits.FileEditRecovery(**change.guarded_import["files"][path]))
        total += len(data) if data is not None else 0
        if total > client_imports.IMPORT_BYTES:
            raise ValueError("workspace_undo_too_large")
    policy = _policy(workspace, conversation_id)
    decision = decide_action(policy["approval_mode"], "edit")
    fields = dict(resource_id=resource_id, conversation_id=conversation_id, resource_revision=workspace.updated_at,
        binding_id=binding.binding_id, binding_revision=binding.revision, change_set_id=change_set_id,
        change_set_revision=revision, host_revision=_digest(_hosts(change)), policy_revision=_digest(policy),
        policy_decision=decision.decision, approval_required=decision.requires_approval,
        files=tuple(item.path for item in change.files), directories_retained=tuple(change.guarded_import["directories"]))
    result = WorkspaceUndoReview(**fields, action_digest=_digest(fields))
    if len(json.dumps(asdict(result), ensure_ascii=True).encode()) > client_imports.REVIEW_BYTES:
        raise ValueError("workspace_undo_too_large")
    current, current_binding, _, current_revision = _source(resource_id, conversation_id, change_set_id)
    if current.to_dict() != workspace.to_dict() or current_binding != binding or current_revision != revision or _policy(current, conversation_id) != policy:
        raise ValueError("change_set_revision_conflict")
    validate()
    return result


@contextmanager
def _writer(workspace, conversation_id, command_id, mode, outcome, borrowed_run_id):
    if borrowed_run_id is None:
        with client_imports._writer(workspace, conversation_id, command_id, mode, outcome, "workspace.undo") as guard:
            yield guard
        return
    # Only an executing tool can borrow its own existing writer. A browser
    # cannot choose another run, and this operation never releases that lease.
    from row_bot import agent, agent_runs, conversation_resources
    def guard():
        runtime = agent.get_active_runtime_context()
        context = conversation_resources.current_execution_context()
        binding = context.resolve("workspace") if context else None
        run = agent_runs.get_agent_run(borrowed_run_id)
        lease = agent_runs.get_agent_write_lock("developer:" + workspace.id)
        if (runtime.get("agent_run_id") != borrowed_run_id or not context or context.conversation_id != conversation_id
                or not binding or binding.resource_id != workspace.id or not run or run.get("stop_requested")
                or run.get("thread_id") != conversation_id or run.get("workspace_id") != workspace.id
                or not lease or lease.get("run_id") != borrowed_run_id
                or lease.get("thread_id") != conversation_id or lease.get("workspace_id") != workspace.id):
            raise ValueError("edit_cancelled")
    guard()
    yield guard


def undo_workspace_change(review: WorkspaceUndoReview, *, command_id: str, confirmed: bool,
                           persist_recovery: Callable[[dict], None], recovery: dict | None = None,
                           validate: Callable[[], None] = lambda: None,
                           borrowed_run_id: str | None = None) -> WorkspaceUndoResult:
    """Restore only captured originals; an uncertain retry keeps its original command."""
    original = json.loads(json.dumps(asdict(review)))
    progress = copy.deepcopy(recovery) if recovery else {"review": original, "command_id": command_id, "files": {}, "completed": []}
    completed, reverted, outcome = (), False, {}
    def result(status, code=""):
        return WorkspaceUndoResult(command_id, review.resource_id, review.conversation_id, review.change_set_id,
            status, completed, reverted, reverted, code)
    try:
        if str(uuid.UUID(command_id)) != command_id or not callable(persist_recovery):
            raise ValueError("invalid_edit")
        fields = asdict(review)
        fields.pop("action_digest")
        if _digest(fields) != review.action_digest:
            raise ValueError("edit_recovery_conflict")
        if (progress.get("review") != original or progress.get("command_id") != command_id
                or set(progress.get("files", {})) - set(review.files)
                or len(json.dumps(progress, ensure_ascii=True).encode()) > client_imports.RECOVERY_BYTES):
            raise ValueError("edit_recovery_conflict")
        completed = tuple(progress.get("completed", ()))
        validate()
        workspace, binding, change, revision = _source(review.resource_id, review.conversation_id, review.change_set_id)
        policy = _policy(workspace, review.conversation_id)
        decision = decide_action(policy["approval_mode"], "edit")
        if decision.decision == "block" or decision.requires_approval and not confirmed:
            raise ValueError("workspace_undo_approval_required" if decision.requires_approval else "edit_policy_denied")
        if (workspace.updated_at != review.resource_revision or binding.binding_id != review.binding_id
                or binding.revision != review.binding_revision or _digest(policy) != review.policy_revision
                or _digest(_hosts(change)) != review.host_revision):
            raise ValueError("resource_revision_conflict")
        if change.reverted:
            if change.undo_command_id != command_id:
                raise ValueError("change_set_revision_conflict")
            reverted, completed = True, tuple(review.files)
            outcome["reverted"] = True
            validate()
            if borrowed_run_id is None:
                client_imports._finish_writer(workspace, review.conversation_id, command_id, outcome, "workspace.undo")
            return result("undone")
        if revision != review.change_set_revision:
            raise ValueError("change_set_revision_conflict")
        def authority():
            validate()
            current, current_binding, _, current_revision = _source(review.resource_id, review.conversation_id, review.change_set_id)
            if current.to_dict() != workspace.to_dict() or current_binding != binding or current_revision != revision or _policy(current, review.conversation_id) != policy:
                raise ValueError("change_set_revision_conflict")
        root = Path(workspace.path)
        with _writer(workspace, review.conversation_id, command_id, policy["approval_mode"], outcome, borrowed_run_id) as writer:
            def admitted():
                writer()
                authority()
            admitted()
            sources, total = {}, 0
            for path in review.files:
                admitted()
                if path not in progress["files"]:
                    _, capture = client_edits._capture_identity(root, path)
                    if capture != _hosts(change)[path]:
                        raise ValueError("file_revision_conflict")
                proof = edits.FileEditRecovery(**change.guarded_import["files"][path])
                data = edits.read_retained_edit_before(root, path, proof)
                total += len(data) if data is not None else 0
                if total > client_imports.IMPORT_BYTES:
                    raise ValueError("workspace_undo_too_large")
                sources[path] = (proof, data)
            def save():
                if len(json.dumps(progress, ensure_ascii=True).encode()) > client_imports.RECOVERY_BYTES:
                    raise ValueError("workspace_undo_too_large")
                persist_recovery(copy.deepcopy(progress))
            for path in review.files:
                admitted()
                proof, data = sources[path]
                expected = _hosts(change)[path]
                old = progress["files"].get(path)
                def persist(value, path=path):
                    progress["files"][path] = asdict(value)
                    save()
                arguments = dict(command_id=str(uuid.uuid5(uuid.UUID(command_id), "workspace-undo:" + path)),
                    expected_digest=expected["digest"], expected_identity=expected["identity"], expected_metadata=expected["metadata"],
                    expected_parent_identity=expected["parent_identity"], recovery=edits.FileEditRecovery(**old) if old else None,
                    persist_recovery=persist, validate=admitted)
                if data is None:
                    edits.publish_file_removal(root, path, _import_bytes=True, **arguments)
                else:
                    edits.publish_text_revision(root, path, "", _import_bytes=data, _retained_source=proof, **arguments)
                if path not in progress["completed"]:
                    progress["completed"].append(path)
                completed = tuple(path for path in review.files if path in progress["completed"])
                progress["completed"] = list(completed)
                save()
            admitted()
            change_ledger.mark_reverted(change.id, expected_revision=revision, command_id=command_id, validate=admitted)
            reverted = True
            outcome["reverted"] = True
        return result("undone")
    except Exception as error:
        code = getattr(error, "code", str(error))
        safe = {"invalid_edit", "edit_recovery_conflict", "file_revision_conflict", "resource_revision_conflict",
            "change_set_revision_conflict", "change_set_unavailable", "workspace_undo_proof_unavailable", "workspace_undo_too_large",
            "workspace_undo_approval_required", "edit_policy_denied", "resource_binding_revoked", "capability_revoked",
            "action_denied", "folder_selection_denied", "workspace_writer_busy", "edit_cancelled", "workspace_path_denied",
            "file_metadata_unavailable", "file_publication_incomplete", "edit_receipt_unconfirmed", "change_ledger_unavailable"}
        code = code if isinstance(code, str) and code in safe else "workspace_undo_unconfirmed"
        outcome["code"] = code
        return result("partial" if progress.get("files") or reverted else "conflict" if "conflict" in code else "denied", code)


def prepare_retained_undo(resource_id: str, conversation_id: str, change_set_id: str, *,
                           validate: Callable[[], None]) -> tuple[WorkspaceUndoReview, str]:
    """NiceGUI/tool adapter: recover an original review from the existing receipt."""
    validate()
    client_edits._scope(resource_id, conversation_id)
    owner = "developer-undo:" + conversation_id
    target = "workspace-undo:" + resource_id + ":" + change_set_id
    pending = admissions.read_unfinished_target_commands(target, limit=2)
    if pending["overflow"] or len(pending["items"]) > 1:
        raise ValueError("workspace_undo_unconfirmed")
    if pending["items"]:
        item = pending["items"][0]
        if item["owner_id"] != owner or item["type"] != "workspace.undo.retained":
            raise ValueError("workspace_undo_unconfirmed")
        saved = admissions.read_command_receipt(owner, item["command_id"])
        if not saved or not isinstance(saved.get("review"), dict):
            raise ValueError("workspace_undo_unconfirmed")
        fields = dict(saved["review"])
        fields["files"] = tuple(fields["files"])
        fields["directories_retained"] = tuple(fields["directories_retained"])
        review = WorkspaceUndoReview(**fields)
        if (review.resource_id, review.conversation_id, review.change_set_id) != (resource_id, conversation_id, change_set_id):
            raise ValueError("edit_recovery_conflict")
        validate()
        return review, item["command_id"]
    review = review_workspace_undo(resource_id, conversation_id, change_set_id, validate=validate)
    # Fresh explicit review may follow a cancelled/rejected attempt. Once
    # reserved, this exact ID is recovered above across interrupt/remount.
    command = str(uuid.uuid4())
    return review, command


def execute_retained_undo(review: WorkspaceUndoReview, command_id: str, *, confirmed: bool,
                           validate: Callable[[], None], borrowed_run_id: str | None = None) -> WorkspaceUndoResult:
    """Persist recovery before each effect; errors never become blind legacy reverts."""
    validate()
    owner = "developer-undo:" + review.conversation_id
    target = "workspace-undo:" + review.resource_id + ":" + review.change_set_id
    command = {"command_id": command_id, "type": "workspace.undo.retained", "review": asdict(review)}
    saved = None
    try:
        saved = admissions.claim_command(owner, command_id, command, target, exclusive_target=True)
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise
        saved = admissions.read_command_receipt(owner, command_id)
    if saved and saved.get("result", {}).get("reverted") and saved.get("status") == "completed":
        values = dict(saved["result"])
        values["files_restored"] = tuple(values["files_restored"])
        return WorkspaceUndoResult(**values)
    def persist(progress):
        admissions.command_progress(owner, command_id, {"review": asdict(review), "recovery": progress})
    previous = saved.get("recovery") if saved else None
    # An existing empty admission has no host publication authority. Retain
    # its original intent before entering the shared writer owner.
    if previous is None:
        persist(None)
    result = undo_workspace_change(review, command_id=command_id, confirmed=confirmed,
        persist_recovery=persist, recovery=previous, validate=validate, borrowed_run_id=borrowed_run_id)
    current = admissions.read_command_receipt(owner, command_id) or {}
    payload = {"review": asdict(review), "recovery": current.get("recovery"), "result": asdict(result)}
    if result.status == "undone":
        admissions.complete_command(owner, command_id, payload)
    elif result.status in {"denied", "conflict"} and not (current.get("recovery") or {}).get("files"):
        admissions.reject_command(owner, command_id, result.code)
    else:
        admissions.command_progress(owner, command_id, payload)
    return result


def reserve_retained_undo_review(review: WorkspaceUndoReview, command_id: str, *, validate: Callable[[], None]) -> None:
    """Keep a tool's exact review across approval-interrupt replay before effects."""
    validate()
    owner = "developer-undo:" + review.conversation_id
    target = "workspace-undo:" + review.resource_id + ":" + review.change_set_id
    command = {"command_id": command_id, "type": "workspace.undo.retained", "review": asdict(review)}
    try:
        saved = admissions.claim_command(owner, command_id, command, target, exclusive_target=True)
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise
        return
    if saved is None:
        admissions.command_progress(owner, command_id, {"review": asdict(review), "recovery": None})
