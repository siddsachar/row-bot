"""Explicit repository clone into one locally selected Developer parent."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from row_bot.developer import storage
from row_bot.developer.client_workspace import (
    AuthorizedWorkspaceFolder, EmptyWorkspaceRecovery, WorkspaceRegistration,
    _choice, _directory_identity, _empty_folder_name, _empty_parent,
    create_empty_workspace,
)


@dataclass(frozen=True)
class CloneRecovery:
    command_id: str
    source: str
    empty: dict
    stage: str = 'empty_created'


class CloneCreationError(ValueError):
    def __init__(self, code: str, recovery: CloneRecovery | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.recovery = recovery


def source_name(repo_url: str) -> tuple[str, str]:
    """Allow only an explicit remote Git URL, without inline credentials."""
    source = str(repo_url or '').strip()
    if (not 1 <= len(source) <= 2048 or source != repo_url
            or any(ord(char) < 33 or ord(char) == 127 for char in source)):
        raise CloneCreationError('clone_source_invalid')
    if source.startswith('git@'):
        if not re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9._~/\-]+', source):
            raise CloneCreationError('clone_source_invalid')
    else:
        parsed = urlsplit(source)
        if (parsed.scheme not in {'https', 'http', 'ssh', 'git'} or not parsed.hostname
                or parsed.username not in {None, 'git'} or parsed.password is not None
                or parsed.query or parsed.fragment or not parsed.path.strip('/')):
            raise CloneCreationError('clone_source_invalid')
    try:
        name = _empty_folder_name(storage.suggested_clone_name(source))
    except ValueError:
        raise CloneCreationError('clone_source_invalid') from None
    return source, name


def _verified_clone(target: Path, source: str, directory_identity: str) -> bool:
    try:
        git_dir = target / '.git'
        git_stat = git_dir.lstat()
        if (_directory_identity(target) != directory_identity
                or not stat.S_ISDIR(git_stat.st_mode)
                or git_dir.is_symlink()
                or bool(getattr(git_stat, 'st_file_attributes', 0) & 0x400)):
            return False
        result = subprocess.run(
            ['git', '-C', str(target), 'config', '--local', '--get', 'remote.origin.url'],
            check=True, capture_output=True, text=True, timeout=10,
            env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'},
        )
        return result.stdout.strip() == source
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def clone_selected_repository(
    selection: AuthorizedWorkspaceFolder, repo_url: str, *, command_id: str,
    persist: Callable[[CloneRecovery], None],
    recovery: CloneRecovery | None = None,
    validate: Callable[[], None],
) -> WorkspaceRegistration:
    """Create one child and clone once; continuation only inspects exact evidence."""
    source, name = source_name(repo_url)
    if recovery is not None and (recovery.command_id != command_id or recovery.source != source):
        raise CloneCreationError('workspace_recovery_conflict', recovery)
    if recovery is None:
        saved: CloneRecovery | None = None
        def created(empty: EmptyWorkspaceRecovery) -> None:
            nonlocal saved
            saved = CloneRecovery(command_id, source, asdict(empty))
            persist(saved)

        try:
            create_empty_workspace(selection, name, command_id=command_id,
                                   persist_created=created, validate=validate)
        except Exception as exc:
            if isinstance(exc, CloneCreationError):
                raise
            code = getattr(exc, 'code', 'workspace_clone_unconfirmed')
            raise CloneCreationError(code, saved) from None
        if saved is None:
            raise CloneCreationError('workspace_clone_unconfirmed')
        recovery = saved
    empty = EmptyWorkspaceRecovery(**recovery.empty)
    parent = _empty_parent(selection)
    target = parent / name
    if (empty.folder_name != name or empty.command_id != command_id
            or empty.parent_identity != _directory_identity(parent, parent=True)
            or empty.directory_identity != _directory_identity(target)
            or empty.resource_id != storage._workspace_id_for_path(target)):
        raise CloneCreationError('workspace_recovery_conflict', recovery)
    validate()
    if recovery.stage not in {'empty_created', 'clone_started', 'cloned'}:
        raise CloneCreationError('workspace_recovery_conflict', recovery)
    if recovery.stage == 'empty_created':
        if storage.get_workspace(empty.resource_id) is None:
            try:
                create_empty_workspace(selection, name, command_id=command_id,
                                       persist_created=lambda _value: None,
                                       recovery=empty, validate=validate)
            except Exception:
                raise CloneCreationError('workspace_recovery_conflict', recovery) from None
        try:
            if next(target.iterdir(), None) is not None:
                raise CloneCreationError('workspace_clone_unconfirmed', recovery)
        except OSError:
            raise CloneCreationError('workspace_recovery_conflict', recovery) from None
        recovery = CloneRecovery(command_id, source, recovery.empty, 'clone_started')
        try:
            persist(recovery)
        except Exception:
            raise CloneCreationError('workspace_clone_unconfirmed', recovery) from None
        try:
            subprocess.run(
                ['git', '-c', 'protocol.ext.allow=never', '-c', 'protocol.file.allow=never',
                 'clone', '--', source, str(target)],
                cwd=str(parent), check=True, capture_output=True, text=True, timeout=600,
                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'},
            )
        except (OSError, subprocess.SubprocessError):
            raise CloneCreationError('workspace_clone_unconfirmed', recovery) from None
        if not _verified_clone(target, source, empty.directory_identity):
            raise CloneCreationError('workspace_clone_unconfirmed', recovery)
        recovery = CloneRecovery(command_id, source, recovery.empty, 'cloned')
        try:
            persist(recovery)
        except Exception:
            raise CloneCreationError('workspace_clone_unconfirmed', recovery) from None
    elif recovery.stage == 'clone_started':
        # A lost response may follow a completed clone. Inspect it, but never
        # rerun network work under the same command identity.
        if not _verified_clone(target, source, empty.directory_identity):
            raise CloneCreationError('workspace_clone_unconfirmed', recovery)
        recovery = CloneRecovery(command_id, source, recovery.empty, 'cloned')
        try:
            persist(recovery)
        except Exception:
            raise CloneCreationError('workspace_clone_unconfirmed', recovery) from None
    if not _verified_clone(target, source, empty.directory_identity):
        raise CloneCreationError('workspace_recovery_conflict', recovery)
    validate()
    with storage.workspace_transaction():
        workspace = storage.get_workspace(empty.resource_id)
        if workspace is None or Path(workspace.path).absolute() != target.absolute():
            raise CloneCreationError('workspace_recovery_conflict', recovery)
        if workspace.repo_url and workspace.repo_url != source:
            raise CloneCreationError('workspace_recovery_conflict', recovery)
        if not workspace.repo_url:
            workspace.repo_url = source
            storage.save_workspace(workspace)
        return WorkspaceRegistration(_choice(workspace), True)
