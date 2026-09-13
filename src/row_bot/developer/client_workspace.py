"""Workspace queries and explicit setup over existing Developer owners.

The application validates grants, session authority and bindings before calling
this module. Domain checks repeat containment and revision validation. No helper
here creates a conversation, allocates a worktree or changes execution policy.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from row_bot.approval_policy import ApprovalMode
from row_bot.developer import storage
from row_bot.developer.review import (
    ChangedFile, DiffStats, DiffPreview, DirectoryPage, FilePreview, list_directory_page,
    read_bounded_diff, read_bounded_file, scoped_workspace_path, workspace_has_custom_read_hooks,
)
from row_bot.developer.state import DeveloperWorkspace


@dataclass(frozen=True)
class AuthorizedWorkspaceFolder:
    path: Path
    scope_root: Path
    selection_id: str


@dataclass(frozen=True)
class WorkspacePolicy:
    execution_mode: Literal["local", "docker"]
    approval_mode: ApprovalMode
    sandbox_network: Literal["off", "ask", "on"]
    read_only: bool = True


@dataclass(frozen=True)
class WorkspaceAssociation:
    status: Literal["origin", "latest", "default", "unassociated", "repair_required"]
    conversation_id: str | None = None


@dataclass(frozen=True)
class WorkspaceChoice:
    resource_id: str
    project_workspace_id: str
    execution_workspace_id: str
    name: str
    revision: str
    policy: WorkspacePolicy
    association: WorkspaceAssociation
    available: bool


@dataclass(frozen=True)
class WorkspaceRegistration:
    workspace: WorkspaceChoice
    created: bool


@dataclass(frozen=True)
class EmptyWorkspaceRecovery:
    """Private server receipt; never accept this evidence from a renderer."""

    command_id: str
    folder_name: str
    resource_id: str
    parent_identity: str
    directory_identity: str


class EmptyWorkspaceCreationError(ValueError):
    def __init__(self, code: str, recovery: EmptyWorkspaceRecovery | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.recovery = recovery


@dataclass(frozen=True)
class WorkspaceChoicePage:
    items: tuple[WorkspaceChoice, ...]
    next_cursor: str | None
    revision: str


@dataclass(frozen=True)
class WorkspaceCommandStatus:
    label: str
    kind: str
    status: Literal["not_run"] = "not_run"


@dataclass(frozen=True)
class WorkspaceProcessStatus:
    pid: int
    status: Literal["running", "stopped"]


@dataclass(frozen=True)
class WorkspaceTodo:
    id: str
    label: str
    status: str


@dataclass(frozen=True)
class WorkspaceInspector:
    resource_id: str
    project_workspace_id: str
    execution_workspace_id: str
    conversation_id: str
    name: str
    policy: WorkspacePolicy
    snapshot_revision: str
    status: Literal["ready", "stale", "unavailable"]
    is_git: bool
    branch: str
    dirty: bool
    changed_total: int
    diff_stats: DiffStats | None
    commands: tuple[WorkspaceCommandStatus, ...]
    processes: tuple[WorkspaceProcessStatus, ...]
    todos: tuple[WorkspaceTodo, ...]
    error: str = ""


@dataclass(frozen=True)
class ChangedFilePage:
    items: tuple[ChangedFile, ...]
    next_cursor: str | None
    snapshot_revision: str
    total: int


@dataclass(frozen=True)
class WorkspaceChangeSet:
    id: str
    summary: str
    reviewed: bool
    reverted: bool
    file_count: int


@dataclass(frozen=True)
class WorkspaceChangeSetPage:
    items: tuple[WorkspaceChangeSet, ...]
    next_cursor: str | None
    snapshot_revision: str
    total: int


@dataclass(frozen=True)
class WorkspaceChangeSetFile:
    path: str
    action: str


@dataclass(frozen=True)
class WorkspaceChangeSetFiles:
    items: tuple[WorkspaceChangeSetFile, ...]
    next_cursor: str | None
    snapshot_revision: str
    total: int
    change_set_id: str


def _workspace(resource_id: str) -> DeveloperWorkspace:
    from row_bot.conversation_resources import _identifier
    _identifier(resource_id)
    workspace = storage.get_workspace(resource_id)
    if workspace is None:
        raise ValueError("resource_unavailable")
    return workspace


def _policy(workspace: DeveloperWorkspace) -> WorkspacePolicy:
    return WorkspacePolicy(workspace.execution_mode, workspace.approval_mode, workspace.sandbox_network)


def _conversation_available(conversation_id: str) -> bool:
    from row_bot.threads import _thread_exists, _thread_write_blocked
    return _thread_exists(conversation_id) and not _thread_write_blocked(conversation_id)


def _association(workspace: DeveloperWorkspace) -> WorkspaceAssociation:
    if workspace.origin_conversation_id:
        return WorkspaceAssociation("origin" if _conversation_available(workspace.origin_conversation_id)
                                    else "repair_required", workspace.origin_conversation_id)
    latest = storage.latest_workspace_thread(workspace.id)
    if latest and _conversation_available(latest):
        return WorkspaceAssociation("latest", latest)
    if workspace.default_thread_id:
        return WorkspaceAssociation("default" if _conversation_available(workspace.default_thread_id)
                                    else "repair_required", workspace.default_thread_id)
    return WorkspaceAssociation("unassociated")


def _choice(workspace: DeveloperWorkspace) -> WorkspaceChoice:
    association = _association(workspace)
    execution_id = workspace.id
    if association.conversation_id and association.status in {"latest", "default"}:
        from row_bot.threads import _get_thread_developer_workspace
        execution_id = _get_thread_developer_workspace(association.conversation_id) or workspace.id
    execution_workspace = storage.get_workspace(execution_id) if execution_id != workspace.id else workspace
    try:
        available = execution_workspace is not None and scoped_workspace_path(Path(execution_workspace.path)).is_dir()
    except (OSError, ValueError):
        available = False
    return WorkspaceChoice(workspace.id, workspace.id, execution_id, workspace.name,
                           workspace.updated_at, _policy(execution_workspace or workspace), association, available)


def register_existing_folder(selection: AuthorizedWorkspaceFolder) -> WorkspaceRegistration:
    """Register one already-authorized existing folder, changing metadata only."""
    if not selection.selection_id:
        raise ValueError("folder_selection_required")
    try:
        root = selection.scope_root.absolute()
        path = selection.path.absolute()
        relative = path.relative_to(root).as_posix()
        verified = scoped_workspace_path(root, "" if relative == "." else relative)
        if not verified.is_dir():
            raise ValueError
    except (OSError, ValueError):
        raise ValueError("folder_selection_denied") from None
    with storage.workspace_transaction():
        workspace_id = storage._workspace_id_for_path(verified)
        existing = storage.get_workspace(workspace_id)
        if existing is not None:
            try:
                if not Path(existing.path).samefile(verified):
                    raise ValueError("workspace_identity_conflict")
            except OSError:
                if Path(existing.path).absolute() != verified.absolute():
                    raise ValueError("workspace_identity_conflict") from None
        # Revalidate immediately before the metadata commit.
        scoped_workspace_path(root, "" if relative == "." else relative)
        if existing is not None and not existing.hidden:
            return WorkspaceRegistration(_choice(existing), False)
        workspace = storage.add_or_update_local_workspace(str(verified))
        return WorkspaceRegistration(_choice(workspace), existing is None)


def _empty_folder_name(name: str) -> str:
    # Apply portable Windows component rules even on Unix: names travel between
    # supported clients, and neither alternate streams nor devices are folders.
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    reserved.update(f"{prefix}{suffix}" for prefix in ("com", "lpt")
                    for suffix in "123456789¹²³")
    if (not isinstance(name, str) or not name or name != name.strip()
            or name.endswith(".") or name.casefold() in {".", "..", ".git"}
            or any(ord(char) < 32 or ord(char) == 127 or char in '<>:"/\\|?*' for char in name)
            or name.split(".", 1)[0].casefold() in reserved):
        raise EmptyWorkspaceCreationError("workspace_name_invalid")
    try:
        if len(name.encode("utf-8")) > 255:
            raise ValueError
    except (UnicodeError, ValueError):
        raise EmptyWorkspaceCreationError("workspace_name_invalid") from None
    return name


def _empty_parent(selection: AuthorizedWorkspaceFolder) -> Path:
    if not selection.selection_id:
        raise EmptyWorkspaceCreationError("folder_selection_required")
    try:
        root = selection.scope_root.absolute()
        relative = selection.path.absolute().relative_to(root).as_posix()
        parent = scoped_workspace_path(root, "" if relative == "." else relative)
        if not parent.is_dir():
            raise ValueError
        return parent
    except (OSError, ValueError):
        raise EmptyWorkspaceCreationError("folder_selection_denied") from None


def _directory_identity(path: Path, *, parent: bool = False) -> str:
    from row_bot.file_ownership import directory_identity
    return directory_identity(path, parent=parent)


@contextmanager
def _empty_parent_guard(parent: Path, identity: str) -> Iterator[int | None]:
    """Compatibility adapter for the shared native directory ownership guard."""
    from row_bot.file_ownership import DirectoryOwnershipError, guard_directory
    try:
        with guard_directory(parent, identity) as descriptor:
            yield descriptor
    except DirectoryOwnershipError:
        raise EmptyWorkspaceCreationError("folder_selection_denied") from None


def _validate_empty_authority(validate: Callable[[], None] | None,
                              recovery: EmptyWorkspaceRecovery | None = None) -> None:
    if validate is None:
        return
    try:
        validate()
    except Exception as error:
        if recovery is None:
            raise
        # Preserve already-persisted progress when authority expires after
        # mkdir. Only stable public authorization codes escape this boundary.
        code = getattr(error, "code", "action_denied")
        if code not in {"capability_revoked", "action_denied", "resource_revision_conflict",
                        "conversation_unavailable", "folder_selection_denied"}:
            code = "action_denied"
        raise EmptyWorkspaceCreationError(code, recovery) from None


def create_empty_workspace(
    selection: AuthorizedWorkspaceFolder, folder_name: str, *, command_id: str,
    persist_created: Callable[[EmptyWorkspaceRecovery], None],
    recovery: EmptyWorkspaceRecovery | None = None,
    validate: Callable[[], None] | None = None,
) -> WorkspaceRegistration:
    """Create one explicitly named child, then register it without execution.

    The application owns admission, grants and durable command identity. It
    must save the private callback evidence before attempting later setup
    stages and supply only its own saved evidence on explicit continuation.
    mkdir and that receipt cannot be atomic: an interruption between them is
    unconfirmed, never permission to adopt an existing directory on retry.
    No file, directory, prior record, policy or association is overwritten.
    """
    name = _empty_folder_name(folder_name)
    try:
        if str(uuid.UUID(command_id)) != command_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise EmptyWorkspaceCreationError("invalid_command") from None
    with storage.workspace_transaction():
        _validate_empty_authority(validate, recovery)
        parent = _empty_parent(selection)
        parent_identity = _directory_identity(parent, parent=True)
        with _empty_parent_guard(parent, parent_identity) as parent_fd:
            target = parent / name
            resource_id = storage._workspace_id_for_path(target)
            if recovery is not None and (
                recovery.command_id != command_id or recovery.folder_name != name
                or recovery.resource_id != resource_id or recovery.parent_identity != parent_identity
            ):
                raise EmptyWorkspaceCreationError("workspace_recovery_conflict")
            existing = storage.get_workspace(resource_id)
            if recovery is None:
                # A hidden/stale registry identity is not authority to replace it.
                if existing is not None:
                    raise EmptyWorkspaceCreationError("workspace_identity_conflict")
                if os.path.lexists(target):
                    raise EmptyWorkspaceCreationError("workspace_destination_exists")
                if _directory_identity(_empty_parent(selection), parent=True) != parent_identity:
                    raise EmptyWorkspaceCreationError("folder_selection_denied")
                _validate_empty_authority(validate)
                try:
                    if parent_fd is None:
                        target.mkdir(exist_ok=False)
                    else:
                        os.mkdir(name, dir_fd=parent_fd)
                except FileExistsError:
                    raise EmptyWorkspaceCreationError("workspace_destination_exists") from None
                except OSError:
                    raise EmptyWorkspaceCreationError("workspace_creation_denied") from None
                try:
                    scoped_workspace_path(parent, name)
                    if _directory_identity(_empty_parent(selection), parent=True) != parent_identity:
                        raise ValueError
                    recovery = EmptyWorkspaceRecovery(command_id, name, resource_id,
                                                      parent_identity, _directory_identity(target))
                    persist_created(recovery)
                except Exception:
                    # Never remove a directory as rollback; receipt failure means
                    # its creation cannot be safely inferred after a restart.
                    raise EmptyWorkspaceCreationError("workspace_creation_unconfirmed") from None
            try:
                verified = scoped_workspace_path(_empty_parent(selection), name)
                if (not verified.is_dir() or _directory_identity(verified) != recovery.directory_identity
                        or _directory_identity(parent, parent=True) != recovery.parent_identity):
                    raise ValueError
            except (OSError, ValueError):
                raise EmptyWorkspaceCreationError("workspace_recovery_conflict", recovery) from None
            _validate_empty_authority(validate, recovery)
            if existing is not None:
                if Path(existing.path).absolute() != verified.absolute():
                    raise EmptyWorkspaceCreationError("workspace_identity_conflict", recovery)
                return WorkspaceRegistration(_choice(existing), False)
            try:
                if next(verified.iterdir(), None) is not None:
                    raise EmptyWorkspaceCreationError("workspace_destination_not_empty", recovery)
            except OSError:
                raise EmptyWorkspaceCreationError("workspace_creation_denied", recovery) from None
            _validate_empty_authority(validate, recovery)
            try:
                # Persist only the existing Developer record shape. In particular,
                # no legacy helper may allocate a thread/worktree or infer Git.
                workspace = DeveloperWorkspace(id=resource_id, name=name, path=str(verified))
                storage.save_workspace(workspace)
            except Exception:
                raise EmptyWorkspaceCreationError("workspace_registration_failed", recovery) from None
            return WorkspaceRegistration(_choice(workspace), True)


def _page_cursor(cursor: str | None, scope: str, revision: str) -> int:
    if cursor is None:
        return 0
    try:
        if len(cursor) > 2048:
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if value[0] != scope or value[1] != revision:
            raise ValueError
        offset = value[2]
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, IndexError, KeyError, UnicodeError):
        raise ValueError("cursor_revision_conflict") from None


def _next_cursor(scope: str, revision: str, offset: int, total: int) -> str | None:
    if offset >= total:
        return None
    return base64.urlsafe_b64encode(json.dumps([scope, revision, offset]).encode()).decode()


def list_workspace_choices(cursor: str | None = None, limit: int = 50) -> WorkspaceChoicePage:
    """Read saved identities with complete revision-pinned continuation."""
    if not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
    workspaces = sorted(storage.list_workspaces(), key=lambda item: item.id)
    revision = hashlib.sha256(json.dumps([(w.id, w.updated_at) for w in workspaces]).encode()).hexdigest()
    offset = _page_cursor(cursor, "workspaces", revision)
    items = tuple(_choice(item) for item in workspaces[offset:offset + limit])
    return WorkspaceChoicePage(items, _next_cursor("workspaces", revision, offset + len(items), len(workspaces)), revision)


def resolve_workspace_open(resource_id: str, expected_revision: str) -> WorkspaceChoice:
    workspace = _workspace(resource_id)
    if workspace.updated_at != expected_revision:
        raise ValueError("resource_revision_conflict")
    return _choice(workspace)


def associate_workspace(resource_id: str, conversation_id: str, expected_revision: str,
                        expected_origin_id: str | None, repair: bool = False) -> WorkspaceChoice:
    """CAS a resume association. Binding and deletion authority remain separate."""
    with storage.workspace_transaction():
        workspace = _workspace(resource_id)
        if workspace.updated_at != expected_revision or workspace.origin_conversation_id != (expected_origin_id or ""):
            raise ValueError("resource_revision_conflict")
        if not _conversation_available(conversation_id):
            raise ValueError("conversation_unavailable")
        if workspace.origin_conversation_id == conversation_id:
            return _choice(workspace)
        if not repair and (workspace.origin_conversation_id
                           or _association(workspace).status != "unassociated"):
            raise ValueError("origin_repair_required")
        workspace.origin_conversation_id = conversation_id
        workspace.touch()
        storage.save_workspace(workspace)
        return _choice(workspace)


def _query_workspace(resource_id: str, conversation_id: str) -> DeveloperWorkspace:
    """Repeat binding checks so a cached Inspector cannot outlive revocation."""
    from row_bot.conversation_resources import list_bindings
    if not _conversation_available(conversation_id):
        raise ValueError("conversation_unavailable")
    if not any(b.kind == "workspace" and b.resource_id == resource_id
               for b in list_bindings(conversation_id).bindings):
        raise ValueError("resource_binding_revoked")
    workspace = _workspace(resource_id)
    scoped_workspace_path(Path(workspace.path))
    return workspace


async def get_workspace_inspector(resource_id: str, conversation_id: str,
                                  refresh: bool = False) -> WorkspaceInspector:
    from row_bot.developer import inspector_snapshot as owner
    from row_bot.developer import runtime
    from row_bot.threads import _get_thread_project_workspace, _get_thread_approval_mode
    workspace = _query_workspace(resource_id, conversation_id)
    snapshot = owner.get_snapshot(resource_id, conversation_id)
    if snapshot is None or refresh:
        if await asyncio.to_thread(workspace_has_custom_read_hooks, workspace.path):
            raise ValueError("workspace_read_hooks_unavailable")
        owner.request_snapshot_refresh(resource_id, conversation_id, debounce=0)
        snapshot = await owner.wait_for_snapshot_refresh(resource_id, conversation_id)
    workspace = _query_workspace(resource_id, conversation_id)
    if snapshot is None:
        raise ValueError("inspector_unavailable")
    processes = tuple(WorkspaceProcessStatus(p.pid, "running" if p.poll() is None else "stopped")
                      for p in runtime._ACTIVE_PROCESSES.get(str(Path(workspace.path).resolve()), ())[:100])
    policy = WorkspacePolicy(workspace.execution_mode, _get_thread_approval_mode(conversation_id), workspace.sandbox_network)
    refresh_error = owner.get_snapshot_refresh_error(resource_id, conversation_id)
    return WorkspaceInspector(resource_id, _get_thread_project_workspace(conversation_id) or resource_id,
        resource_id, conversation_id, workspace.name, policy, str(snapshot.version),
        "stale" if snapshot.error or refresh_error else "ready", bool(snapshot.git_summary.get("is_git")),
        str(snapshot.git_summary.get("branch") or ""), bool(snapshot.git_summary.get("dirty")),
        len(snapshot.changed_files), snapshot.diff_stats,
        tuple(WorkspaceCommandStatus(c.label, c.kind) for c in snapshot.command_specs[:100]), processes,
        tuple(WorkspaceTodo(t.id[:256], t.label[:4096], t.status[:80]) for t in snapshot.todos[:100]),
        "inspector_refresh_failed" if snapshot.error or refresh_error else "")


def list_inspector_changes(resource_id: str, conversation_id: str, cursor: str | None = None,
                           limit: int = 50, expected_snapshot_revision: str | None = None) -> ChangedFilePage:
    from row_bot.developer.inspector_snapshot import get_snapshot
    _query_workspace(resource_id, conversation_id)
    if not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
    snapshot = get_snapshot(resource_id, conversation_id)
    if snapshot is None:
        raise ValueError("inspector_unavailable")
    revision = str(snapshot.version)
    if expected_snapshot_revision is not None and revision != expected_snapshot_revision:
        raise ValueError("snapshot_revision_conflict")
    scope = f"changes:{resource_id}:{conversation_id}"
    offset = _page_cursor(cursor, scope, revision)
    changes = sorted(snapshot.changed_files, key=lambda item: item.path)
    items = tuple(changes[offset:offset + limit])
    return ChangedFilePage(items, _next_cursor(scope, revision, offset + len(items), len(changes)), revision, len(changes))


def list_workspace_directory(resource_id: str, directory: str = "", cursor: str | None = None,
                             limit: int = 50, expected_revision: str | None = None) -> DirectoryPage:
    workspace = _workspace(resource_id)
    return list_directory_page(workspace.path, directory, cursor=cursor, limit=limit, expected_revision=expected_revision)


def list_inspector_change_sets(resource_id: str, conversation_id: str, cursor: str | None = None,
                               limit: int = 50, expected_snapshot_revision: str | None = None) -> WorkspaceChangeSetPage:
    from row_bot.developer.inspector_snapshot import get_snapshot
    _query_workspace(resource_id, conversation_id)
    if not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
    snapshot = get_snapshot(resource_id, conversation_id)
    if snapshot is None:
        raise ValueError("inspector_unavailable")
    revision = str(snapshot.version)
    if expected_snapshot_revision is not None and revision != expected_snapshot_revision:
        raise ValueError("snapshot_revision_conflict")
    scope = f"ledger:{resource_id}:{conversation_id}"
    offset = _page_cursor(cursor, scope, revision)
    items = tuple(WorkspaceChangeSet(item.id, item.summary[:4096], item.reviewed, item.reverted, len(item.files))
                  for item in snapshot.agent_changes[offset:offset + limit])
    return WorkspaceChangeSetPage(items, _next_cursor(scope, revision, offset + len(items), len(snapshot.agent_changes)),
                                  revision, len(snapshot.agent_changes))


def list_inspector_change_set_files(resource_id: str, conversation_id: str, change_set_id: str,
                                    cursor: str | None = None, limit: int = 50,
                                    expected_snapshot_revision: str | None = None) -> WorkspaceChangeSetFiles:
    from row_bot.developer.inspector_snapshot import get_snapshot
    _query_workspace(resource_id, conversation_id)
    if not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
    snapshot = get_snapshot(resource_id, conversation_id)
    if snapshot is None:
        raise ValueError("inspector_unavailable")
    revision = str(snapshot.version)
    if expected_snapshot_revision is not None and revision != expected_snapshot_revision:
        raise ValueError("snapshot_revision_conflict")
    change = next((item for item in snapshot.agent_changes if item.id == change_set_id), None)
    if change is None:
        raise ValueError("change_set_unavailable")
    scope = f"ledger-files:{resource_id}:{conversation_id}:{change_set_id}"
    offset = _page_cursor(cursor, scope, revision)
    items = tuple(WorkspaceChangeSetFile(item.path, item.action) for item in change.files[offset:offset + limit])
    return WorkspaceChangeSetFiles(items, _next_cursor(scope, revision, offset + len(items), len(change.files)),
                                   revision, len(change.files), change.id)


def read_workspace_file(resource_id: str, relative_path: str, offset: int = 0,
                        limit_bytes: int = 32768, expected_revision: str | None = None) -> FilePreview:
    workspace = _workspace(resource_id)
    return read_bounded_file(workspace.path, relative_path, offset=offset,
                             limit_bytes=limit_bytes, expected_revision=expected_revision)


def read_workspace_diff(resource_id: str, conversation_id: str, relative_path: str,
                        expected_snapshot_revision: str, offset: int = 0,
                        limit_bytes: int = 32768, expected_revision: str | None = None) -> DiffPreview:
    from row_bot.developer.inspector_snapshot import get_snapshot
    workspace = _query_workspace(resource_id, conversation_id)
    snapshot = get_snapshot(resource_id, conversation_id)
    if snapshot is None or str(snapshot.version) != expected_snapshot_revision:
        raise ValueError("snapshot_revision_conflict")
    changed = next((item for item in snapshot.changed_files if item.path == relative_path), None)
    if changed is not None and changed.status == "??":
        preview = read_bounded_file(workspace.path, relative_path, offset=offset,
                                    limit_bytes=min(limit_bytes, 32768), expected_revision=expected_revision)
        if preview.status != "text":
            return DiffPreview("unavailable", "", preview.revision)
        # Untracked files have no Git diff. Show their addition as bounded source
        # pages, retaining byte offsets in the original file for continuation.
        text = "\n".join("+" + line for line in preview.text.splitlines())
        return DiffPreview("text", text, preview.revision, preview.next_offset, preview.next_offset is not None)
    return read_bounded_diff(workspace.path, relative_path, offset=offset,
                             limit_bytes=limit_bytes, expected_revision=expected_revision)
