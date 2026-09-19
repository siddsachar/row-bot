"""Bound sandbox import review over saved pending changes and canonical writers."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from contextlib import contextmanager
import uuid

from row_bot.developer import client_edits, edits, sandbox_runtime
from row_bot.developer.sandbox import decide_action
from row_bot.runtime import admissions

PATCH_BYTES = 1024 * 1024
IMPORT_FILES = 100
IMPORT_BYTES = 8 * 1024 * 1024
RECOVERY_BYTES = 160 * 1024
REVIEW_BYTES = 32 * 1024


@dataclass(frozen=True)
class WorkspaceImportSummary:
    pending_change_id: str
    revision: str
    file_count: int
    imported: bool
    created_at: str


@dataclass(frozen=True)
class WorkspaceImportPage:
    items: tuple[WorkspaceImportSummary, ...]
    snapshot_revision: str
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class WorkspaceImportReview:
    resource_id: str
    conversation_id: str
    resource_revision: str
    binding_id: str
    binding_revision: str
    pending_change_id: str
    pending_revision: str
    patch_digest: str
    host_revision: str
    git_policy_revision: str
    policy_revision: str
    policy_decision: str
    approval_required: bool
    files: tuple[str, ...]
    action_digest: str
    directories: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceImportPatch:
    pending_change_id: str
    revision: str
    text: str
    next_offset: int | None


@dataclass(frozen=True)
class WorkspaceImportResult:
    command_id: str
    resource_id: str
    conversation_id: str
    pending_change_id: str
    status: str
    files_applied: tuple[str, ...]
    change_set_id: str | None
    ledger_saved: bool
    imported: bool
    code: str = ""


def _matching_run(workspace, conversation_id, command_id, operation="workspace.import"):
    from row_bot import agent_runs
    if operation not in {"workspace.import", "workspace.undo"}:
        raise ValueError("edit_recovery_conflict")
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:" + operation.replace(".", "-") + ":" + command_id).hex
    existing = agent_runs.get_agent_run(run_id)
    if existing is not None:
        saved = existing.get("result_json") or {}
        if (not isinstance(saved, dict) or saved.get("command_id") != command_id or saved.get("operation") != operation
                or existing.get("workspace_id") != workspace.id or existing.get("thread_id") != conversation_id
                or existing.get("write_lock_key") != "developer:" + workspace.id):
            raise ValueError("edit_recovery_conflict")
    return run_id, existing


def _finish_writer(workspace, conversation_id, command_id, outcome, operation="workspace.import"):
    from row_bot import agent_runs
    run_id, existing = _matching_run(workspace, conversation_id, command_id, operation)
    if existing is None:
        raise ValueError("edit_recovery_conflict")
    key = "developer:" + workspace.id
    completion = "reverted" if operation == "workspace.undo" else "imported"
    label = "Undo imported workspace changes" if operation == "workspace.undo" else "Import sandbox changes"
    try:
        lease = agent_runs.get_agent_write_lock(key)
        if lease and lease.get("run_id") == run_id:
            if lease.get("workspace_id") != workspace.id or lease.get("thread_id") != conversation_id:
                raise ValueError("edit_recovery_conflict")
            agent_runs.release_agent_write_lock(run_id=run_id)
            remaining = agent_runs.get_agent_write_lock(key)
            if remaining and remaining.get("run_id") == run_id:
                raise ValueError("workspace_writer_busy")
    finally:
        agent_runs.finish_agent_run(run_id, "completed" if outcome.get(completion) else "failed",
            summary=label, result_json={"command_id": command_id, "operation": operation,
            completion: outcome.get(completion, False)}, error=outcome.get("code", ""))


@contextmanager
def _writer(workspace, conversation_id, command_id, mode, outcome, operation="workspace.import"):
    from row_bot import agent_runs
    run_id, existing = _matching_run(workspace, conversation_id, command_id, operation)
    key = "developer:" + workspace.id
    try:
        if existing is None:
            agent_runs.create_agent_run(run_id=run_id, kind="workflow", status="running", thread_id=conversation_id,
                parent_thread_id=conversation_id, workspace_id=workspace.id, workspace_path=workspace.path,
                workspace_mode="single_writer", write_lock_key=key, approval_mode=mode,
                display_name="Undo imported workspace changes" if operation == "workspace.undo" else "Import sandbox changes",
                prompt="", result_json={"command_id": command_id, "operation": operation})
        lease = agent_runs.get_agent_write_lock(key)
        owns_lease = lease and lease.get("run_id") == run_id
        if owns_lease and (lease.get("workspace_id") != workspace.id or lease.get("thread_id") != conversation_id):
            raise ValueError("edit_recovery_conflict")
        if not owns_lease and not agent_runs.acquire_agent_write_lock(key, run_id, thread_id=conversation_id,
                workspace_id=workspace.id, workspace_path=workspace.path):
            raise ValueError("workspace_writer_busy")
        def guard():
            _, current = _matching_run(workspace, conversation_id, command_id, operation)
            lease = agent_runs.get_agent_write_lock(key)
            if (current is None or current.get("stop_requested") or not lease or lease.get("run_id") != run_id
                    or lease.get("workspace_id") != workspace.id or lease.get("thread_id") != conversation_id):
                raise ValueError("edit_cancelled")
        yield guard
    finally:
        if agent_runs.get_agent_run(run_id) is not None:
            _finish_writer(workspace, conversation_id, command_id, outcome, operation)


def _cursor(cursor: str | None, scope: str, revision: str) -> int:
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError
        saved_scope, saved_revision, offset = json.loads(base64.urlsafe_b64decode(cursor))
        if saved_scope != scope or saved_revision != revision or type(offset) is not int or offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("snapshot_revision_conflict") from None


def _saved(resource_id: str, conversation_id: str, validate: Callable[[], None]):
    validate()
    try:
        workspace, binding = client_edits._scope(resource_id, conversation_id)
    except OSError:
        raise ValueError("workspace_path_denied") from None
    rows = sandbox_runtime.read_pending_import_rows(resource_id, conversation_id)
    rows.sort(key=lambda row: (str(row.get("created_at", "")), row["id"]), reverse=True)
    validate()
    return workspace, binding, rows


def list_workspace_imports(resource_id: str, conversation_id: str, *, cursor: str | None = None,
                           limit: int = 50, validate: Callable[[], None] = lambda: None) -> WorkspaceImportPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
    _workspace, _binding, rows = _saved(resource_id, conversation_id, validate)
    revision = hashlib.sha256(json.dumps([(row["id"], sandbox_runtime.pending_change_revision(row)) for row in rows]).encode()).hexdigest()
    scope = resource_id + ":" + conversation_id
    offset = _cursor(cursor, scope, revision)
    page = tuple(WorkspaceImportSummary(row["id"], sandbox_runtime.pending_change_revision(row), len(row["files"]),
        row.get("imported", False), str(row.get("created_at", ""))[:80]) for row in rows[offset:offset + limit])
    next_offset = offset + len(page)
    continuation = base64.urlsafe_b64encode(json.dumps([scope, revision, next_offset]).encode()).decode() if next_offset < len(rows) else None
    validate()
    return WorkspaceImportPage(page, revision, continuation, len(rows))


def _pending(rows: list[dict], identity: str) -> dict:
    value = next((row for row in rows if row["id"] == identity), None)
    if value is None:
        raise ValueError("sandbox_change_unavailable")
    return value


def _bounded_patch(row: dict) -> str:
    try:
        patch = row["patch"]
        if not patch or len(patch.encode("utf-8")) > PATCH_BYTES or "\0" in patch:
            raise ValueError
        unsupported = ("GIT binary patch", "Binary files ", "old mode ", "new mode ",
                       "rename from ", "rename to ", "copy from ", "copy to ")
        if any(line.startswith(unsupported) or line.startswith("new file mode ") and line != "new file mode 100644"
                for line in patch.splitlines()):
            raise ValueError("sandbox_import_format_unavailable")
        return patch
    except ValueError as exc:
        if str(exc) == "sandbox_import_format_unavailable":
            raise
        raise ValueError("sandbox_patch_unavailable") from None
    except UnicodeError:
        raise ValueError("sandbox_patch_unavailable") from None


def read_workspace_import_patch(resource_id: str, conversation_id: str, pending_change_id: str, *,
                                expected_revision: str, offset: int = 0, limit: int = 16384,
                                validate: Callable[[], None] = lambda: None) -> WorkspaceImportPatch:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16384:
        raise ValueError("invalid_limit")
    _, _, rows = _saved(resource_id, conversation_id, validate)
    row = _pending(rows, pending_change_id)
    revision = sandbox_runtime.pending_change_revision(row)
    if revision != expected_revision:
        raise ValueError("sandbox_change_revision_conflict")
    patch = _bounded_patch(row)
    text = patch[offset:offset + limit]
    end = offset + len(text)
    validate()
    return WorkspaceImportPatch(pending_change_id, revision, text, end if end < len(patch) else None)


def _capture(workspace, row):
    from row_bot.developer.client_workspace import _directory_identity, _empty_parent_guard
    patch = _bounded_patch(row)
    root = Path(workspace.path)
    try:
        paths = edits.paths_from_patch(root, patch)
    except (ValueError, OSError):
        raise ValueError("workspace_path_denied") from None
    if (not paths or len(paths) > IMPORT_FILES or len({path.casefold() for path in paths}) != len(paths)
            or set(paths) != set(row["files"])):
        raise ValueError("sandbox_patch_unavailable")
    # Names occur in public review, private preimages, and each file proof. This
    # exact escaped budget leaves room for all three inside an admission receipt.
    if len(json.dumps(paths, ensure_ascii=True).encode()) > 8192:
        raise ValueError("sandbox_import_too_large")
    captured, total = {}, 0
    for path in paths:
        # Inspect each existing prefix through the same contained path owner.
        # A missing prefix is a reviewed creation intent, not path authority.
        parts = path.split("/")
        if len(parts) > 32:
            raise ValueError("sandbox_import_too_large")
        from row_bot.developer.client_workspace import _empty_folder_name
        for part in parts:
            _empty_folder_name(part)
            if part.casefold() in {".git", ".row-bot-edit-recovery"}:
                raise ValueError("workspace_path_denied")
        missing = []
        nearest = root
        for index in range(1, len(parts)):
            relative = "/".join(parts[:index])
            if missing:
                missing.append(relative)
                continue
            parent = edits._edit_path(root, relative)
            if not os.path.lexists(parent):
                missing.append(relative)
            elif not parent.is_dir():
                raise ValueError("workspace_path_denied")
            else:
                nearest = parent
        if missing:
            nearest_identity = _directory_identity(nearest, parent=True)
            with _empty_parent_guard(nearest, nearest_identity):
                if os.path.lexists(nearest / parts[len(parts) - len(missing) - 1]):
                    raise ValueError("file_revision_conflict")
            captured[path] = {"data": None, "digest": "missing", "identity": "", "metadata": "",
                "parent_identity": "", "nearest_parent": nearest.relative_to(root).as_posix(),
                "nearest_identity": nearest_identity, "missing_parents": missing}
            continue
        target = edits._edit_path(root, path)
        parent_identity = _directory_identity(target.parent, parent=True)
        with _empty_parent_guard(target.parent, parent_identity) as descriptor:
            if descriptor is None:
                data, digest, identity, _ = edits.read_edit_bytes(root, path, max_bytes=PATCH_BYTES)
                metadata = edits.file_edit_metadata_digest(target) if data is not None else ""
            else:
                data, digest, identity, _ = edits._read_edit_at(descriptor, target.name, max_bytes=PATCH_BYTES)
                metadata = edits._edit_metadata_at(descriptor, target.name) if data is not None else ""
        if data is not None:
            if b"\0" in data[:4096]:
                raise ValueError("sandbox_import_format_unavailable")
            total += len(data)
        if total > IMPORT_BYTES:
            raise ValueError("sandbox_import_too_large")
        captured[path] = {"data": data, "digest": digest, "identity": identity, "metadata": metadata,
            "parent_identity": parent_identity}
    directories = sorted({path for item in captured.values() for path in item.get("missing_parents", ())})
    if len(directories) > 100 or len(json.dumps([paths, directories], ensure_ascii=True).encode()) > 8192:
        raise ValueError("sandbox_import_too_large")
    return patch, captured


def _verify_parent_plan(root, host, proofs, command_id):
    from row_bot.developer.client_workspace import _empty_parent_guard
    nearest = root if host["nearest_parent"] == "." else edits._edit_path(root, host["nearest_parent"])
    with _empty_parent_guard(nearest, host["nearest_identity"]):
        pass
    expected = host["nearest_identity"]
    for path in host["missing_parents"]:
        target = edits._edit_path(root, path)
        if not os.path.lexists(target):
            return None
        proof = proofs.get(path)
        if (not proof or proof["parent_identity"] != expected or proof["relative_path"] != path
                or proof["root_identity"] != edits._edit_identity(root.stat())
                or proof["command_id"] != str(uuid.uuid5(uuid.UUID(command_id), "sandbox-import-directory:" + path))):
            raise ValueError("file_revision_conflict")
        expected = proof["directory_identity"]
        with _empty_parent_guard(target, expected):
            pass
    return expected


def _git_policy(root: Path, paths: list[str]) -> dict:
    try:
        return edits.capture_patch_git_policy(root, paths)
    except Exception as error:
        code = str(error)
        if code not in {"sandbox_import_format_unavailable", "sandbox_import_too_large", "sandbox_git_policy_unavailable"}:
            code = "sandbox_git_policy_unavailable"
        raise ValueError(code) from None


def _check_recovery_budget(review, hosts, git_policy):
    """Reserve the complete escaped private receipt before offering review."""
    identity = "x" * 64
    command = "x" * 36
    files = {path: asdict(edits.FileEditRecovery(command, path, identity, identity, identity,
        identity, identity, identity)) for path in review.files}
    directories = {path: asdict(edits.DirectoryEditRecovery(command, path, identity, identity, identity))
        for path in review.directories}
    envelope = {"review": asdict(review), "command_id": command, "hosts": hosts, "git_policy": git_policy,
        "directories": directories,
        "patch": {"command_id": command, "patch_digest": identity, "files": files, "completed": review.files}}
    if len(json.dumps(envelope, ensure_ascii=True).encode()) > RECOVERY_BYTES:
        raise ValueError("sandbox_import_too_large")


def review_workspace_import(resource_id: str, conversation_id: str, pending_change_id: str, *,
                             validate: Callable[[], None] = lambda: None) -> WorkspaceImportReview:
    from row_bot.threads import _get_thread_approval_mode
    workspace, binding, rows = _saved(resource_id, conversation_id, validate)
    row = _pending(rows, pending_change_id)
    if row.get("imported"):
        raise ValueError("sandbox_change_already_imported")
    patch, captured = _capture(workspace, row)
    policy = {"approval_mode": _get_thread_approval_mode(conversation_id), "workspace": workspace.to_dict()}
    git_policy = _git_policy(Path(workspace.path), list(captured))
    decision = decide_action(policy["approval_mode"], "edit")
    # Contents remain server-only. Public review binds hashes and relative names.
    hosts = {path: {key: value for key, value in item.items() if key != "data"} for path, item in captured.items()}
    fields = dict(resource_id=resource_id, conversation_id=conversation_id, resource_revision=workspace.updated_at,
        binding_id=binding.binding_id, binding_revision=binding.revision, pending_change_id=pending_change_id,
        pending_revision=sandbox_runtime.pending_change_revision(row), patch_digest=hashlib.sha256(patch.encode()).hexdigest(),
        host_revision=admissions.keyed_digest(hosts), git_policy_revision=admissions.keyed_digest(git_policy), policy_revision=admissions.keyed_digest(policy),
        policy_decision=decision.decision, approval_required=decision.requires_approval, files=tuple(captured),
        directories=tuple(sorted({path for item in captured.values() for path in item.get("missing_parents", ())})))
    result = WorkspaceImportReview(**fields, action_digest=admissions.keyed_digest(fields))
    if len(json.dumps(asdict(result), ensure_ascii=True).encode()) > REVIEW_BYTES:
        raise ValueError("sandbox_import_too_large")
    _check_recovery_budget(result, hosts, git_policy)
    validate()
    return result


def import_workspace_change(review: WorkspaceImportReview, *, command_id: str, confirmed: bool,
                             persist_recovery: Callable[[dict], None], recovery: dict | None = None,
                             validate: Callable[[], None] = lambda: None) -> WorkspaceImportResult:
    """Import one exact reviewed pending patch; partial work keeps original proof."""
    from row_bot.threads import _get_thread_approval_mode
    import copy
    original = json.loads(json.dumps(asdict(review)))
    progress = copy.deepcopy(recovery) if recovery else {"review": original, "command_id": command_id, "hosts": {}, "patch": None}
    applied, change_id, ledger_saved, outcome = (), None, False, {}
    def result(status, code="", imported=False):
        outcome.update(code=code, imported=imported)
        return WorkspaceImportResult(command_id, review.resource_id, review.conversation_id, review.pending_change_id,
            status, applied, change_id, ledger_saved, imported, code)
    try:
        if str(uuid.UUID(command_id)) != command_id or not callable(persist_recovery):
            raise ValueError("invalid_edit")
        if progress.get("review") != original or progress.get("command_id") != command_id:
            raise ValueError("edit_recovery_conflict")
        if len(json.dumps(progress, ensure_ascii=True).encode()) > RECOVERY_BYTES:
            raise ValueError("edit_recovery_conflict")
        applied = tuple((progress.get("patch") or {}).get("completed", ()))
        workspace, binding, rows = _saved(review.resource_id, review.conversation_id, validate)
        row = _pending(rows, review.pending_change_id)
        policy = {"approval_mode": _get_thread_approval_mode(review.conversation_id), "workspace": workspace.to_dict()}
        decision = decide_action(policy["approval_mode"], "edit")
        if decision.decision == "block" or decision.requires_approval and not confirmed:
            raise ValueError("sandbox_import_approval_required" if decision.requires_approval else "edit_policy_denied")
        if (workspace.updated_at != review.resource_revision or binding.binding_id != review.binding_id or
                binding.revision != review.binding_revision or admissions.keyed_digest(policy) != review.policy_revision):
            raise ValueError("resource_revision_conflict")
        if row.get("imported") and row.get("import_command_id") == command_id:
            from row_bot.developer import change_ledger
            change_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:client-edit:" + command_id).hex
            applied = tuple(review.files)
            outcome["imported"] = True
            saved = [item for item in change_ledger._load_client_ledger()["change_sets"] if item.get("id") == change_id]
            if len(saved) != 1 or saved[0].get("workspace_id") != review.resource_id or saved[0].get("thread_id") != review.conversation_id or tuple(file.get("path") for file in saved[0].get("files", [])) != tuple(review.files):
                raise ValueError("change_ledger_unavailable")
            ledger_saved = True
            validate()
            # A prior result can contain all file/history proofs while writer
            # release or run finalization failed. Retry only that exact owner;
            # never report completion while its workspace lease is stranded.
            _finish_writer(workspace, review.conversation_id, command_id, outcome)
            return result("imported", imported=True)
        if sandbox_runtime.pending_change_revision(row) != review.pending_revision:
            raise ValueError("sandbox_change_revision_conflict")
        patch = _bounded_patch(row)
        if hashlib.sha256(patch.encode()).hexdigest() != review.patch_digest:
            raise ValueError("sandbox_change_revision_conflict")
        root = Path(workspace.path)
        attribute_recovery = bool(recovery and any(Path(path).name.casefold() == ".gitattributes" for path in review.files))
        try:
            git_policy = _git_policy(root, list(review.files))
        except ValueError as error:
            if not attribute_recovery or str(error) != "sandbox_import_format_unavailable":
                raise
            git_policy = None
        completion_only = git_policy is None or admissions.keyed_digest(git_policy) != review.git_policy_revision
        if completion_only:
            saved_policy = progress.get("git_policy")
            if (not attribute_recovery or not isinstance(saved_policy, dict)
                    or admissions.keyed_digest(saved_policy) != review.git_policy_revision
                    or edits.capture_patch_git_configuration(root) != saved_policy.get("config")):
                raise ValueError("sandbox_git_policy_conflict")
            git_policy = saved_policy
        def authority():
            validate()
            current, current_binding, current_rows = _saved(review.resource_id, review.conversation_id, validate)
            if (current.to_dict() != workspace.to_dict() or current_binding != binding or
                    _get_thread_approval_mode(review.conversation_id) != policy["approval_mode"]):
                raise ValueError("resource_revision_conflict")
            current_row = _pending(current_rows, review.pending_change_id)
            if sandbox_runtime.pending_change_revision(current_row) != review.pending_revision:
                raise ValueError("sandbox_change_revision_conflict")
        with _writer(workspace, review.conversation_id, command_id, policy["approval_mode"], outcome) as validate_writer:
            def admitted():
                validate_writer()
                authority()
            admitted()
            _, captured = _capture(workspace, row)
            if recovery:
                saved_hosts = progress.get("hosts")
                file_progress = (progress.get("patch") or {}).get("files", {})
                if not isinstance(saved_hosts, dict) or set(saved_hosts) != set(review.files):
                    raise ValueError("edit_recovery_conflict")
                for path, value in captured.items():
                    data = value["data"]
                    if path in file_progress:
                        data = edits.read_retained_edit_before(root, path, edits.FileEditRecovery(**file_progress[path]))
                    if saved_hosts[path].get("missing_parents"):
                        _verify_parent_plan(root, saved_hosts[path], progress.get("directories", {}), command_id)
                        if path not in file_progress and data is not None:
                            raise ValueError("file_revision_conflict")
                        value.clear()
                        value.update(saved_hosts[path], data=data)
                    elif path in file_progress:
                        value.update(saved_hosts[path], data=data)
            hosts = {path: {key: value for key, value in item.items() if key != "data"} for path, item in captured.items()}
            if admissions.keyed_digest(hosts) != review.host_revision:
                raise ValueError("file_revision_conflict")
            progress["hosts"] = hosts
            progress["git_policy"] = git_policy
            if completion_only:
                for host in captured.values():
                    if host.get("missing_parents"):
                        proven_parent = _verify_parent_plan(root, host, progress.get("directories", {}), command_id)
                        if proven_parent is None:
                            raise ValueError("edit_recovery_conflict")
                        host["parent_identity"] = proven_parent
                outputs = edits.confirm_import_publications(root, captured, (progress.get("patch") or {}).get("files", {}),
                    command_id=command_id, validate=admitted)
            else:
                outputs = edits.prepare_reviewed_patch(patch, captured, git_policy, validate=admitted)
                if _git_policy(root, list(captured)) != git_policy:
                    raise ValueError("sandbox_git_policy_conflict")
            def save_progress():
                if len(json.dumps(progress, ensure_ascii=True).encode()) > RECOVERY_BYTES:
                    raise ValueError("sandbox_import_too_large")
                persist_recovery(copy.deepcopy(progress))
            directory_proofs = progress.setdefault("directories", {})
            for host in captured.values():
                if host.get("missing_parents") and not completion_only:
                    _verify_parent_plan(root, host, directory_proofs, command_id)
                    expected = host["nearest_identity"]
                    for directory in host["missing_parents"]:
                        admitted()
                        old = directory_proofs.get(directory)
                        def persist_directory(proof, directory=directory):
                            directory_proofs[directory] = asdict(proof)
                            save_progress()
                        published = edits.publish_import_directory(root, directory,
                            command_id=str(uuid.uuid5(uuid.UUID(command_id), "sandbox-import-directory:" + directory)),
                            expected_parent_identity=expected, persist_recovery=persist_directory,
                            recovery=edits.DirectoryEditRecovery(**old) if old else None, validate=admitted)
                        expected = published.directory_identity
                    host["parent_identity"] = expected
            def persist(value):
                nonlocal applied
                progress["patch"] = copy.deepcopy(value)
                applied = tuple(value.get("completed", ()))
                save_progress()
            change, _ = edits.apply_patch_to_workspace(workspace_id=workspace.id, thread_id=review.conversation_id,
                patch=patch, approval_mode=policy["approval_mode"], summary="Import sandbox changes", confirmed=confirmed,
                prepared_import={"captured": captured, "outputs": outputs, "completion_only": completion_only}, command_id=command_id,
                persist_recovery=persist, recovery=progress.get("patch"), validate=admitted)
            if change is None:
                raise ValueError("sandbox_import_approval_required")
            change_id, ledger_saved = change.id, True
            admitted()
            sandbox_runtime.mark_pending_change_imported(review.pending_change_id, expected_revision=review.pending_revision,
                command_id=command_id, validate=admitted)
            outcome["imported"] = True
        return result("imported", imported=True)
    except Exception as exc:
        code = getattr(exc, "code", str(exc))
        allowed = {"invalid_edit", "edit_recovery_conflict", "sandbox_import_approval_required", "edit_policy_denied",
            "resource_revision_conflict", "resource_binding_revoked", "folder_selection_denied", "sandbox_change_revision_conflict", "sandbox_change_unavailable",
            "sandbox_git_policy_conflict", "sandbox_git_policy_unavailable", "workspace_writer_busy", "edit_cancelled",
            "file_revision_conflict", "file_metadata_unavailable", "file_not_text", "workspace_path_denied",
            "sandbox_patch_conflict", "sandbox_import_format_unavailable", "sandbox_import_too_large", "sandbox_history_unavailable",
            "change_ledger_unavailable", "capability_revoked", "action_denied", "edit_receipt_unconfirmed", "file_publication_incomplete"}
        safe = code if type(code) is str and code in allowed else "sandbox_import_unconfirmed"
        partial = bool((progress.get("patch") or {}).get("files")) or bool(progress.get("directories")) or bool(outcome.get("imported"))
        return result("partial" if partial else "conflict" if "conflict" in safe else "denied", safe,
            imported=bool(outcome.get("imported")))
