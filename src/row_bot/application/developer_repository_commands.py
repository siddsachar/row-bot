"""Path-free Developer repository, worktree, and sandbox client commands.

The browser never receives a host path or a shell authority.  Every effect is
reviewed against a bound workspace snapshot, admitted exactly once, and routed
through the existing Developer owners.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
from typing import Any
import uuid

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer.sandbox import action_needs_explicit_user_intent, decide_action
from row_bot.runtime import admissions


_ACTIONS = frozenset(
    {
        "developer.repository.branch.create",
        "developer.repository.branch.switch",
        "developer.repository.commit",
        "developer.repository.push",
        "developer.repository.pull_request",
        "developer.repository.worktree.create",
        "developer.repository.worktree.preserve",
        "developer.repository.sandbox.configure",
        "developer.repository.sandbox.rebuild",
        "developer.repository.sandbox.cleanup",
    }
)
_UNAVAILABLE = {
    "developer.repository.clone": "use_workspace_setup",
    "developer.repository.install": "use_workspace_process_review",
    "developer.repository.network": "use_workspace_process_review",
    "developer.repository.delete": "no_recoverable_repository_delete_owner",
}
_POLICY_ACTION = {
    "developer.repository.branch.create": "git_branch",
    "developer.repository.branch.switch": "git_branch",
    "developer.repository.commit": "git_commit",
    "developer.repository.push": "git_push",
    "developer.repository.pull_request": "git_pr",
    "developer.repository.worktree.create": "git_worktree",
    "developer.repository.worktree.preserve": "git_worktree",
    "developer.repository.sandbox.configure": "run_safe_command",
    "developer.repository.sandbox.rebuild": "delete",
    "developer.repository.sandbox.cleanup": "delete",
}
_REVISION = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.RLock()


class DeveloperRepositoryError(ClientPlatformError):
    """Stable, client-safe Developer repository error."""


def _error(code: str, revision: str | None = None) -> DeveloperRepositoryError:
    return DeveloperRepositoryError(code, current_revision=revision)


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if type(value) is not str:
        raise _error("invalid_developer_repository_command")
    try:
        if len(value.encode("utf-8")) > maximum or any(
            ord(char) < 32 and char not in "\n\t" for char in value
        ):
            raise ValueError
    except (UnicodeError, ValueError):
        raise _error("invalid_developer_repository_command") from None
    value = value.strip()
    if required and not value:
        raise _error("invalid_developer_repository_command")
    return value


def _uuid(value: object) -> str:
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise _error("invalid_developer_repository_command") from None
    return value


def _directory_identity(path: Path) -> str:
    from row_bot.developer.review import scoped_workspace_path

    root = scoped_workspace_path(path.absolute())
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise _error("workspace_path_denied")
    return f"{info.st_dev}:{info.st_ino}:{getattr(info, 'st_birthtime_ns', info.st_ctime_ns)}"


def _safe_pathspec(root: Path, value: object) -> str:
    from row_bot.developer.review import scoped_workspace_path

    text = _text(value, 1024, required=True).replace("\\", "/")
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in pure.parts)
        or ":" in text
    ):
        raise _error("workspace_path_denied")
    try:
        target = root.joinpath(*pure.parts)
        if target.exists():
            scoped_workspace_path(root, text)
        else:
            parent = "/".join(pure.parts[:-1])
            scoped_workspace_path(root, parent)
    except (OSError, ValueError):
        raise _error("workspace_path_denied") from None
    return pure.as_posix()


def _safe_file(path: Path, *, maximum: int = 1024 * 1024) -> object | None:
    """Read one bounded ordinary JSON file without creating its parent."""

    try:
        for component in (*reversed(path.parents), path):
            try:
                info = component.lstat()
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError
        with path.open("rb") as handle:
            current = os.fstat(handle.fileno())
            data = handle.read(maximum + 1)
        if len(data) > maximum or (current.st_dev, current.st_ino, current.st_mtime_ns) != (
            info.st_dev,
            info.st_ino,
            info.st_mtime_ns,
        ):
            raise ValueError
        return json.loads(data)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise _error("developer_runtime_status_unavailable") from None


class CanonicalDeveloperRepositoryBackend:
    """Thin access to the current Developer domain owners."""

    def resolve(
        self,
        resource_id: str,
        conversation_id: str,
        validate: Callable[[], None],
    ) -> tuple[Any, Any, str, str]:
        from row_bot.application.workspace_process_commands import _scope

        return _scope(resource_id, conversation_id, validate)

    def repository(self, workspace: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        from row_bot.developer import git, review

        root = Path(workspace.path).absolute()
        identity = _directory_identity(root)
        try:
            if review.workspace_has_custom_read_hooks(str(root)):
                raise _error("git_read_hooks_unavailable")
            status = git.get_git_status(str(root))
        except DeveloperRepositoryError:
            raise
        except Exception:
            raise _error("git_status_unavailable") from None
        if status.error:
            raise _error("git_status_unavailable")
        branch = _text(status.branch or "", 256)
        public = {
            "state": "ready" if status.is_git else "plain_folder",
            "is_git": bool(status.is_git),
            "is_root": bool(status.is_repo_root),
            "branch": branch,
            "detached": bool(status.is_git and not branch),
            "dirty": bool(status.dirty),
            "remote_configured": bool(status.remote),
            "tracking_summary": _text(status.ahead_behind or "", 512),
        }
        private = {
            **public,
            "directory_identity": identity,
            "repo_root_matches": bool(
                status.repo_root and Path(status.repo_root).absolute() == root
            ),
        }
        return public, private

    def worktrees(self, resource_id: str, conversation_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        from row_bot import tasks
        from row_bot.application.workspace_process_commands import _readonly, _table

        public: list[dict[str, Any]] = []
        private: list[dict[str, Any]] = []
        with _readonly(tasks._DB_PATH) as conn:  # noqa: SLF001
            if not _table(conn, "developer_worktrees"):
                return public, private
            rows = conn.execute(
                "SELECT id,owner_kind,owner_id,project_workspace_id,worktree_workspace_id,"
                "worktree_path,branch_name,status,cleanup_state,error,metadata_json "
                "FROM developer_worktrees WHERE project_workspace_id=? OR worktree_workspace_id=? "
                "ORDER BY updated_at DESC LIMIT 33",
                (resource_id, resource_id),
            ).fetchmany(33)
        if len(rows) > 32:
            raise _error("developer_worktree_status_unavailable")
        for row in rows:
            if any(not isinstance(row[name], str) or len(row[name]) > 65536 for name in row.keys()):
                raise _error("developer_worktree_status_unavailable")
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (ValueError, TypeError, json.JSONDecodeError):
                raise _error("developer_worktree_status_unavailable") from None
            current = row["worktree_workspace_id"] == resource_id
            owned = row["owner_kind"] == "thread" and row["owner_id"] == conversation_id
            item = {
                "worktree_id": row["id"],
                "branch": _text(row["branch_name"], 256),
                "status": row["status"] if row["status"] in {"active", "failed", "preserved", "archived"} else "failed",
                "cleanup_state": row["cleanup_state"] if row["cleanup_state"] in {"preserve", "requested", "completed", "failed"} else "failed",
                "current": current,
                "owned_by_conversation": owned,
                "source_dirty": metadata.get("source_dirty") is True,
                "seeded_current_changes": metadata.get("seeded_from_current_changes") is True,
                "has_error": bool(row["error"]),
            }
            public.append(item)
            private.append({**item, "owner_kind": row["owner_kind"], "owner_id": row["owner_id"], "path": row["worktree_path"], "metadata": metadata})
        return public, private

    def sandbox(self, workspace: Any, conversation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        from row_bot.developer import sandbox_runtime

        pending_payload = _safe_file(sandbox_runtime.PENDING_CHANGES_PATH)
        pending_rows = [] if pending_payload is None else pending_payload.get("changes") if isinstance(pending_payload, dict) else None
        if not isinstance(pending_rows, list) or len(pending_rows) > 4096:
            raise _error("sandbox_history_unavailable")
        pending = 0
        for row in pending_rows:
            if not isinstance(row, dict):
                raise _error("sandbox_history_unavailable")
            if row.get("workspace_id") == workspace.id and row.get("thread_id") == conversation_id and row.get("imported") is not True:
                pending += 1
                if pending > 100:
                    raise _error("sandbox_history_unavailable")
        sessions_payload = _safe_file(sandbox_runtime.SESSIONS_PATH)
        processes = 0
        if sessions_payload is not None:
            raw = sessions_payload.get("processes") if isinstance(sessions_payload, dict) else None
            owned = raw.get(workspace.id, []) if isinstance(raw, dict) else None
            if not isinstance(owned, list) or len(owned) > 256:
                raise _error("developer_runtime_status_unavailable")
            processes = len(owned)
        public = {
            "execution_mode": workspace.execution_mode,
            "network": workspace.sandbox_network,
            "image": _text(workspace.sandbox_image, 256),
            "pending_imports": pending,
            "owned_processes": processes,
            "runtime_status": "not_probed",
        }
        return public, deepcopy(public)

    def execute(self, action: str, normalized: dict[str, Any], context: dict[str, Any], command_id: str) -> dict[str, Any]:
        from row_bot.developer import git, github, sandbox_runtime, storage, worktrees

        workspace = context["workspace"]
        validate = context["validate"]
        proof = context["snapshot_proof"]
        validate()
        _repository, repository_private = self.repository(workspace)
        if repository_private != proof["repository"]:
            raise _error("developer_repository_revision_conflict")
        if action.startswith("developer.repository.worktree."):
            _worktrees, worktrees_private = self.worktrees(
                context["resource_id"], context["conversation_id"]
            )
            if worktrees_private != proof["worktrees"]:
                raise _error("developer_repository_revision_conflict")
        if action.startswith("developer.repository.sandbox."):
            _sandbox, sandbox_private = self.sandbox(
                workspace, context["conversation_id"]
            )
            if sandbox_private != proof["sandbox"]:
                raise _error("developer_repository_revision_conflict")
        validate()
        root = str(Path(workspace.path).absolute())
        if action == "developer.repository.branch.create":
            git.create_branch(root, normalized["branch"])
        elif action == "developer.repository.branch.switch":
            git.switch_branch(root, normalized["branch"])
        elif action == "developer.repository.commit":
            git.commit_changes(root, normalized["message"], normalized["paths"] or None)
        elif action == "developer.repository.push":
            result = github.push_current_branch(root, context["approval_mode"], confirmed=True)
            if not result.ran or not result.ok:
                raise _error("git_push_failed")
        elif action == "developer.repository.pull_request":
            result = github.create_pull_request(
                root,
                context["approval_mode"],
                title=normalized["title"],
                body=normalized["body"],
                draft=normalized["draft"],
                confirmed=True,
            )
            if not result.ran or not result.ok:
                raise _error("pull_request_failed")
            return {"external_url": _text(result.url, 2048)}
        elif action == "developer.repository.worktree.create":
            result = worktrees.allocate_worktree(
                "thread",
                context["conversation_id"],
                context["resource_id"],
                objective=normalized["objective"],
                seed_mode=normalized["seed_mode"],
                metadata={"client_command_id": command_id},
            )
            if result.get("status") == "failed":
                raise _error("worktree_create_failed")
            return {"worktree_id": _text(result.get("id", ""), 128)}
        elif action == "developer.repository.worktree.preserve":
            result = worktrees.mark_worktree_preserved(
                "thread", context["conversation_id"], reason=normalized["reason"]
            )
            if result is None:
                raise _error("worktree_unavailable")
            return {"worktree_id": _text(result.get("id", ""), 128)}
        elif action == "developer.repository.sandbox.configure":
            storage.set_workspace_execution_settings(
                workspace.id,
                execution_mode=normalized["execution_mode"],
                sandbox_network=normalized["sandbox_network"],
                sandbox_image=normalized["sandbox_image"],
            )
        elif action == "developer.repository.sandbox.rebuild":
            sandbox_runtime.rebuild_docker_sandbox(workspace)
        elif action == "developer.repository.sandbox.cleanup":
            sandbox_runtime.cleanup_workspace_sandbox(workspace.id)
        else:
            raise _error("developer_repository_action_unavailable")
        return {}


def _backend(value: Any | None) -> Any:
    return value if value is not None else CanonicalDeveloperRepositoryBackend()


def _availability(repository: dict[str, Any], worktrees: list[dict[str, Any]], sandbox: dict[str, Any]) -> dict[str, dict[str, Any]]:
    root = repository["is_git"] and repository["is_root"]
    owned = any(item["owned_by_conversation"] for item in worktrees)
    clear = sandbox["pending_imports"] == 0 and sandbox["owned_processes"] == 0
    values: dict[str, tuple[bool, str | None]] = {
        "developer.repository.branch.create": (root, None if root else "git_root_required"),
        "developer.repository.branch.switch": (root and not repository["dirty"], None if root and not repository["dirty"] else "clean_git_root_required"),
        "developer.repository.commit": (root and repository["dirty"], None if root and repository["dirty"] else "dirty_git_root_required"),
        "developer.repository.push": (root and repository["remote_configured"], None if root and repository["remote_configured"] else "git_remote_required"),
        "developer.repository.pull_request": (root and repository["remote_configured"], None if root and repository["remote_configured"] else "git_remote_required"),
        "developer.repository.worktree.create": (root and not owned, None if root and not owned else "worktree_exists" if owned else "git_root_required"),
        "developer.repository.worktree.preserve": (owned, None if owned else "worktree_unavailable"),
        "developer.repository.sandbox.configure": (clear, None if clear else "sandbox_busy_or_pending_import"),
        "developer.repository.sandbox.rebuild": (clear and sandbox["execution_mode"] == "docker", None if clear and sandbox["execution_mode"] == "docker" else "docker_sandbox_required" if clear else "sandbox_busy_or_pending_import"),
        "developer.repository.sandbox.cleanup": (clear and sandbox["execution_mode"] == "docker", None if clear and sandbox["execution_mode"] == "docker" else "docker_sandbox_required" if clear else "sandbox_busy_or_pending_import"),
    }
    result = {key: {"available": available, "code": code} for key, (available, code) in values.items()}
    result.update({key: {"available": False, "code": code} for key, code in _UNAVAILABLE.items()})
    return result


def _snapshot(resource_id: str, conversation_id: str, validate: Callable[[], None], backend: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    validate()
    workspace, binding, conversation_revision, approval_mode = backend.resolve(resource_id, conversation_id, validate)
    repository, repository_private = backend.repository(workspace)
    worktrees, worktrees_private = backend.worktrees(resource_id, conversation_id)
    sandbox, sandbox_private = backend.sandbox(workspace, conversation_id)
    proof = {
        "resource_id": resource_id,
        "conversation_id": conversation_id,
        "binding_id": binding.binding_id,
        "binding_revision": binding.revision,
        "resource_revision": workspace.updated_at,
        "conversation_revision": conversation_revision,
        "repository": repository_private,
        "worktrees": worktrees_private,
        "sandbox": sandbox_private,
        "approval_mode": approval_mode,
    }
    revision = hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    public = {
        "schema_version": 1,
        "resource_id": resource_id,
        "conversation_id": conversation_id,
        "binding_id": binding.binding_id,
        "binding_revision": binding.revision,
        "resource_revision": workspace.updated_at,
        "revision": revision,
        "workspace_name": _text(workspace.name, 256),
        "trusted": workspace.trusted is True,
        "repository": repository,
        "worktrees": worktrees,
        "sandbox": sandbox,
        "availability": _availability(repository, worktrees, sandbox),
    }
    validate()
    return public, proof, {
        "workspace": workspace,
        "binding": binding,
        "approval_mode": approval_mode,
        "resource_id": resource_id,
        "conversation_id": conversation_id,
        "snapshot_proof": proof,
        "validate": validate,
    }


def read_developer_repository(resource_id: str, conversation_id: str, *, validate: Callable[[], None], backend: Any | None = None) -> dict[str, Any]:
    """Read a bounded path-free Developer repository snapshot."""

    return _snapshot(resource_id, conversation_id, validate, _backend(backend))[0]


def _branch(value: object) -> str:
    from row_bot.developer.git import sanitize_branch_name

    branch = _text(value, 120, required=True)
    if (
        sanitize_branch_name(branch) != branch
        or branch in {"HEAD", "-"}
        or ".." in branch
    ):
        raise _error("invalid_branch_name")
    return branch


def _review(action: str, payload: dict[str, Any], resource_id: str, conversation_id: str, validate: Callable[[], None], backend: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if action in _UNAVAILABLE:
        raise _error(_UNAVAILABLE[action])
    if action not in _ACTIONS or not isinstance(payload, dict) or not _REVISION.fullmatch(str(payload.get("revision", ""))):
        raise _error("invalid_developer_repository_command")
    snapshot, _proof, context = _snapshot(resource_id, conversation_id, validate, backend)
    if payload["revision"] != snapshot["revision"]:
        raise _error("developer_repository_revision_conflict", snapshot["revision"])
    availability = snapshot["availability"][action]
    if not availability["available"]:
        raise _error(availability["code"] or "developer_repository_action_unavailable")
    normalized: dict[str, Any] = {"revision": payload["revision"]}
    root = Path(context["workspace"].path).absolute()
    if action in {"developer.repository.branch.create", "developer.repository.branch.switch"}:
        if set(payload) != {"revision", "branch"}:
            raise _error("invalid_developer_repository_command")
        normalized["branch"] = _branch(payload["branch"])
    elif action == "developer.repository.commit":
        if set(payload) != {"revision", "message", "paths"} or not isinstance(payload.get("paths"), list) or len(payload["paths"]) > 50:
            raise _error("invalid_developer_repository_command")
        paths = [_safe_pathspec(root, item) for item in payload["paths"]]
        if len(paths) != len(set(paths)):
            raise _error("invalid_developer_repository_command")
        normalized.update(message=_text(payload["message"], 512, required=True), paths=paths)
    elif action == "developer.repository.pull_request":
        if set(payload) != {"revision", "title", "body", "draft"} or type(payload.get("draft")) is not bool:
            raise _error("invalid_developer_repository_command")
        normalized.update(title=_text(payload["title"], 256), body=_text(payload["body"], 16384), draft=payload["draft"])
    elif action == "developer.repository.worktree.create":
        if set(payload) != {"revision", "objective", "seed_mode"} or payload.get("seed_mode") != "current_changes":
            raise _error("invalid_developer_repository_command")
        normalized.update(objective=_text(payload["objective"], 512), seed_mode="current_changes")
    elif action == "developer.repository.worktree.preserve":
        if set(payload) != {"revision", "reason"}:
            raise _error("invalid_developer_repository_command")
        normalized["reason"] = _text(payload["reason"], 512)
    elif action == "developer.repository.sandbox.configure":
        if set(payload) != {"revision", "execution_mode", "sandbox_network", "sandbox_image"}:
            raise _error("invalid_developer_repository_command")
        if payload.get("execution_mode") not in {"local", "docker"} or payload.get("sandbox_network") not in {"off", "ask", "on"}:
            raise _error("invalid_developer_repository_command")
        normalized.update(execution_mode=payload["execution_mode"], sandbox_network=payload["sandbox_network"], sandbox_image=_text(payload["sandbox_image"], 256, required=True))
    else:
        if set(payload) != {"revision"}:
            raise _error("invalid_developer_repository_command")
    policy_action = _POLICY_ACTION[action]
    decision = decide_action(context["approval_mode"], policy_action)
    digest = hashlib.sha256(json.dumps({"action": action, "resource_id": resource_id, "conversation_id": conversation_id, "binding_id": snapshot["binding_id"], "binding_revision": snapshot["binding_revision"], "payload": normalized}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    disclosures = {
        "developer.repository.commit": ["Selected host workspace changes will be staged and committed."],
        "developer.repository.push": ["The current branch will be sent to its configured remote."],
        "developer.repository.pull_request": ["GitHub CLI will contact GitHub and create one pull request."],
        "developer.repository.worktree.create": ["A managed Git worktree will be created with the current dirty state preserved."],
        "developer.repository.sandbox.rebuild": ["The existing sandbox container and shadow copy will be replaced."],
        "developer.repository.sandbox.cleanup": ["The sandbox container and shadow copy will be removed."],
    }.get(action, ["This changes the selected Developer workspace configuration."])
    review = {
        "schema_version": 1,
        "action": action,
        "resource_id": resource_id,
        "conversation_id": conversation_id,
        "binding_id": snapshot["binding_id"],
        "binding_revision": snapshot["binding_revision"],
        "resource_revision": snapshot["resource_revision"],
        "revision": snapshot["revision"],
        "policy_action": policy_action,
        "policy_decision": decision.decision,
        "approval_required": decision.requires_approval or action_needs_explicit_user_intent(policy_action),
        "disclosures": disclosures,
        "action_digest": digest,
    }
    return review, normalized, context


def review_developer_repository_command(action: str, payload: dict[str, Any], resource_id: str, conversation_id: str, *, validate: Callable[[], None], backend: Any | None = None) -> dict[str, Any]:
    validate()
    result = _review(action, deepcopy(payload), resource_id, conversation_id, validate, _backend(backend))[0]
    validate()
    return result


def _scope(owner_id: str, authority_id: str, review: dict[str, Any], *, read_only: bool = False) -> str:
    return admissions.keyed_digest(
        {
            "kind": "developer-repository",
            "owner_id": owner_id,
            "authority_id": authority_id,
            "resource_id": review["resource_id"],
            "conversation_id": review["conversation_id"],
            "binding_id": review["binding_id"],
            "binding_revision": review["binding_revision"],
        },
        read_only=read_only,
    )


def _public_receipt(saved: dict[str, Any]) -> dict[str, Any]:
    result = saved.get("result")
    if not isinstance(result, dict) or set(result) != {"schema_version", "command_id", "action", "resource_id", "conversation_id", "status", "code", "revision", "worktree_id", "external_url"}:
        raise _error("developer_repository_receipt_unavailable")
    return deepcopy(result)


def read_developer_repository_receipt(*, owner_id: str, authority_id: str, resource_id: str, conversation_id: str, command_id: str, validate: Callable[[], None], backend: Any | None = None) -> dict[str, Any]:
    validate()
    command_id = _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    saved = admissions.read_command_receipt(owner_id, command_id)
    private = saved.get("_developer_repository") if isinstance(saved, dict) else None
    if not metadata or metadata["target"] != f"developer-repository:{conversation_id}" or metadata["type"] not in _ACTIONS or not isinstance(private, dict):
        raise _error("developer_repository_receipt_unavailable")
    snapshot = read_developer_repository(resource_id, conversation_id, validate=validate, backend=backend)
    review_scope = {
        "resource_id": resource_id,
        "conversation_id": conversation_id,
        "binding_id": snapshot["binding_id"],
        "binding_revision": snapshot["binding_revision"],
    }
    if private.get("scope") != _scope(owner_id, authority_id, review_scope, read_only=True):
        raise _error("developer_repository_receipt_unavailable")
    result = _public_receipt(saved)
    validate()
    return result


def execute_developer_repository_command(command: dict[str, Any], resource_id: str, conversation_id: str, *, owner_id: str, authority_id: str, key: str, validate: Callable[[], None], validate_action: Callable[[str], None], validate_review: Callable[[dict[str, Any], dict[str, Any]], None], backend: Any | None = None) -> dict[str, Any]:
    """Execute exactly one reviewed command; retries only read its receipt."""

    validate()
    backend = _backend(backend)
    command = deepcopy(command)
    command_id = _uuid(command.get("command_id"))
    action, raw = command.get("type"), command.get("payload")
    if action not in _ACTIONS or not isinstance(raw, dict) or type(raw.get("nonce")) is not str:
        raise _error("invalid_developer_repository_command")
    payload = {name: value for name, value in raw.items() if name != "nonce"}
    with _LOCK:
        if admissions.read_command_metadata(owner_id, command_id) is not None:
            try:
                admissions.claim_command(owner_id, key, command, f"developer-repository:{conversation_id}")
            except admissions.AdmissionError as exc:
                if str(exc) != "operation_uncertain":
                    raise _error(str(exc)) from None
            return read_developer_repository_receipt(owner_id=owner_id, authority_id=authority_id, resource_id=resource_id, conversation_id=conversation_id, command_id=command_id, validate=validate, backend=backend)
        review, normalized, context = _review(action, payload, resource_id, conversation_id, validate, backend)

        def authority() -> None:
            validate()
            validate_action(review["policy_action"])
            validate_review(command, review)

        authority()
        if review["policy_decision"] == "block":
            raise _error("developer_repository_action_denied")
        result = {
            "schema_version": 1,
            "command_id": command_id,
            "action": action,
            "resource_id": resource_id,
            "conversation_id": conversation_id,
            "status": "partial",
            "code": "developer_repository_outcome_uncertain",
            "revision": None,
            "worktree_id": None,
            "external_url": None,
        }
        progress = {
            "result": result,
            "_developer_repository": {
                "scope": _scope(owner_id, authority_id, review),
                "action_digest": review["action_digest"],
            },
        }
        try:
            prior = admissions.claim_command(
                owner_id,
                key,
                command,
                f"developer-repository:{conversation_id}",
                exclusive_target=True,
                initial_result=progress,
            )
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise _error(str(exc)) from None
            return read_developer_repository_receipt(owner_id=owner_id, authority_id=authority_id, resource_id=resource_id, conversation_id=conversation_id, command_id=command_id, validate=validate, backend=backend)
        if prior is not None:
            return _public_receipt(prior)
        try:
            authority()
            outcome = backend.execute(action, normalized, context, command_id)
            authority()
            current = read_developer_repository(resource_id, conversation_id, validate=validate, backend=backend)
            result.update(
                status="completed",
                code=None,
                revision=current["revision"],
                worktree_id=outcome.get("worktree_id"),
                external_url=outcome.get("external_url"),
            )
        except DeveloperRepositoryError as exc:
            result.update(status="rejected", code=str(exc))
        except Exception:
            result.update(status="partial", code="developer_repository_outcome_uncertain")
        admissions.complete_command(owner_id, key, progress)
        return deepcopy(result)
