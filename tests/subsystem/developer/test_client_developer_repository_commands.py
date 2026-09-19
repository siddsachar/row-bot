"""Developer repository client commands retain exact local authority."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import developer_repository_commands as commands


pytestmark = pytest.mark.subsystem
REVISION = "a" * 64


class FakeBackend:
    def __init__(self, root: Path, *, approval_mode: str = "approve") -> None:
        self.workspace = SimpleNamespace(
            id="workspace-1",
            path=str(root),
            updated_at="1",
            name="Disposable repository",
            trusted=True,
            execution_mode="local",
            sandbox_network="off",
            sandbox_image="row-bot-sandbox:latest",
        )
        self.binding = SimpleNamespace(binding_id="binding-1", revision="1")
        self.approval_mode = approval_mode
        self.repository_state = {
            "state": "ready",
            "is_git": True,
            "is_root": True,
            "branch": "main",
            "detached": False,
            "dirty": True,
            "remote_configured": True,
            "tracking_summary": "## main",
        }
        self.worktree_rows: list[dict] = []
        self.sandbox_state = {
            "execution_mode": "local",
            "network": "off",
            "image": "row-bot-sandbox:latest",
            "pending_imports": 0,
            "owned_processes": 0,
            "runtime_status": "not_probed",
        }
        self.effects: list[tuple[str, dict, str]] = []
        self.fail = False

    def resolve(self, resource_id, conversation_id, validate):
        validate()
        assert (resource_id, conversation_id) == ("workspace-1", "conversation-1")
        return self.workspace, self.binding, "conversation-revision", self.approval_mode

    def repository(self, workspace):
        assert workspace is self.workspace
        public = deepcopy(self.repository_state)
        return public, {**public, "directory_identity": "disposable:1", "repo_root_matches": True}

    def worktrees(self, resource_id, conversation_id):
        assert (resource_id, conversation_id) == ("workspace-1", "conversation-1")
        public = deepcopy(self.worktree_rows)
        return public, [{**row, "owner_kind": "thread", "owner_id": conversation_id,
                         "path": "<private>", "metadata": {}} for row in public]

    def sandbox(self, workspace, conversation_id):
        assert workspace is self.workspace and conversation_id == "conversation-1"
        return deepcopy(self.sandbox_state), deepcopy(self.sandbox_state)

    def execute(self, action, normalized, context, command_id):
        assert context["resource_id"] == "workspace-1"
        if self.fail:
            raise OSError("synthetic lost outcome")
        self.effects.append((action, deepcopy(normalized), command_id))
        if action == "developer.repository.commit":
            self.repository_state["dirty"] = False
        elif action == "developer.repository.branch.create":
            self.repository_state.update(branch=normalized["branch"], dirty=False)
        elif action == "developer.repository.sandbox.configure":
            self.sandbox_state.update(
                execution_mode=normalized["execution_mode"],
                network=normalized["sandbox_network"],
                image=normalized["sandbox_image"],
            )
        self.workspace.updated_at = str(int(self.workspace.updated_at) + 1)
        return {}


@pytest.fixture
def domain(tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.runtime import admissions

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(tasks, "_DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    admissions.instance_identity()
    return FakeBackend(root)


def snapshot(backend: FakeBackend):
    return commands.read_developer_repository(
        "workspace-1", "conversation-1", validate=lambda: None, backend=backend
    )


def review(backend: FakeBackend, action: str, payload: dict):
    return commands.review_developer_repository_command(
        action,
        payload,
        "workspace-1",
        "conversation-1",
        validate=lambda: None,
        backend=backend,
    )


def execute(backend: FakeBackend, action: str, payload: dict, *, command_id=None,
            validate_action=lambda _action: None, validate_review=lambda _command, _review: None):
    command_id = command_id or str(uuid4())
    result = commands.execute_developer_repository_command(
        {"command_id": command_id, "type": action, "payload": {**payload, "nonce": "review-nonce"}},
        "workspace-1",
        "conversation-1",
        owner_id="owner-1",
        authority_id="authority-1",
        key=f"repository:{command_id}",
        validate=lambda: None,
        validate_action=validate_action,
        validate_review=validate_review,
        backend=backend,
    )
    return result, command_id


def test_snapshot_is_bounded_path_free_and_exposes_closed_availability(domain):
    result = snapshot(domain)
    assert result["repository"]["branch"] == "main"
    assert result["availability"]["developer.repository.commit"] == {
        "available": True,
        "code": None,
    }
    assert result["availability"]["developer.repository.branch.switch"]["code"] == "clean_git_root_required"
    assert result["availability"]["developer.repository.delete"] == {
        "available": False,
        "code": "no_recoverable_repository_delete_owner",
    }
    encoded = json.dumps(result)
    assert str(domain.workspace.path) not in encoded
    assert "directory_identity" not in encoded


def test_review_rejects_stale_or_smuggled_commands_and_confined_paths(domain):
    current = snapshot(domain)["revision"]
    with pytest.raises(commands.DeveloperRepositoryError, match="developer_repository_revision_conflict"):
        review(domain, "developer.repository.commit", {"revision": REVISION, "message": "save", "paths": []})
    with pytest.raises(commands.DeveloperRepositoryError, match="invalid_developer_repository_command"):
        review(domain, "developer.repository.commit", {"revision": current, "message": "save", "paths": [], "shell": "git push"})
    with pytest.raises(commands.DeveloperRepositoryError, match="workspace_path_denied"):
        review(domain, "developer.repository.commit", {"revision": current, "message": "save", "paths": ["../outside.txt"]})
    with pytest.raises(commands.DeveloperRepositoryError, match="invalid_branch_name"):
        review(domain, "developer.repository.branch.create", {"revision": current, "branch": "feature..lock"})
    with pytest.raises(commands.DeveloperRepositoryError, match="use_workspace_process_review"):
        review(domain, "developer.repository.network", {"revision": current})


def test_review_preserves_policy_and_explicit_remote_disclosure(domain):
    current = snapshot(domain)["revision"]
    approved = review(domain, "developer.repository.push", {"revision": current})
    assert approved["policy_action"] == "git_push"
    assert approved["policy_decision"] == "ask"
    assert approved["approval_required"] is True
    assert approved["disclosures"] == [
        "The current branch will be sent to its configured remote."
    ]
    domain.approval_mode = "block"
    blocked_revision = snapshot(domain)["revision"]
    blocked = review(domain, "developer.repository.push", {"revision": blocked_revision})
    assert blocked["policy_decision"] == "block"
    with pytest.raises(commands.DeveloperRepositoryError, match="developer_repository_action_denied"):
        execute(domain, "developer.repository.push", {"revision": blocked_revision})
    assert domain.effects == []


def test_completed_command_uses_exact_review_and_retries_only_its_receipt(domain):
    (Path(domain.workspace.path) / "src").mkdir()
    current = snapshot(domain)["revision"]
    payload = {"revision": current, "message": "Save focused changes", "paths": ["src/file.py"]}
    actions: list[str] = []
    reviews: list[tuple[dict, dict]] = []
    result, command_id = execute(
        domain,
        "developer.repository.commit",
        payload,
        validate_action=actions.append,
        validate_review=lambda command, prepared: reviews.append((deepcopy(command), deepcopy(prepared))),
    )
    assert result["status"] == "completed" and result["revision"]
    assert actions == ["git_commit", "git_commit", "git_commit"]
    assert len(reviews) == 3
    assert len({item[1]["action_digest"] for item in reviews}) == 1
    assert domain.effects == [
        (
            "developer.repository.commit",
            {"revision": current, "message": "Save focused changes", "paths": ["src/file.py"]},
            command_id,
        )
    ]
    repeated, _ = execute(domain, "developer.repository.commit", payload, command_id=command_id)
    assert repeated == result
    assert len(domain.effects) == 1


def test_lost_effect_outcome_stays_partial_and_is_never_replayed(domain):
    current = snapshot(domain)["revision"]
    domain.fail = True
    payload = {"revision": current, "branch": "feat/synthetic"}
    first, command_id = execute(domain, "developer.repository.branch.create", payload)
    assert first["status"] == "partial"
    assert first["code"] == "developer_repository_outcome_uncertain"
    domain.fail = False
    repeated, _ = execute(domain, "developer.repository.branch.create", payload, command_id=command_id)
    assert repeated == first
    assert domain.effects == []


def test_canonical_repository_read_uses_disposable_repo_without_exposing_path(tmp_path, monkeypatch):
    from row_bot.developer import review as repository_review

    root = tmp_path / "canonical-repo"
    root.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(root)], check=True,
                   capture_output=True, text=True, timeout=10)
    monkeypatch.setattr(repository_review, "workspace_has_custom_read_hooks", lambda _path: False)
    workspace = SimpleNamespace(path=str(root))
    public, private = commands.CanonicalDeveloperRepositoryBackend().repository(workspace)
    assert public["is_git"] and public["is_root"] and public["branch"] == "main"
    assert str(root) not in json.dumps(public)
    assert private["repo_root_matches"] and private["directory_identity"]


def test_canonical_effect_refuses_repository_change_after_admission(tmp_path, monkeypatch):
    from row_bot.developer import git

    backend = commands.CanonicalDeveloperRepositoryBackend()
    current = {"directory_identity": "directory:1", "branch": "main", "dirty": False}
    monkeypatch.setattr(backend, "repository", lambda _workspace: ({}, current))
    monkeypatch.setattr(git, "create_branch", lambda *_args: pytest.fail("stale effect ran"))
    checks: list[None] = []
    context = {
        "workspace": SimpleNamespace(path=str(tmp_path)),
        "validate": lambda: checks.append(None),
        "snapshot_proof": {"repository": {**current, "branch": "reviewed"}},
        "resource_id": "workspace-1",
        "conversation_id": "conversation-1",
    }
    with pytest.raises(commands.DeveloperRepositoryError, match="developer_repository_revision_conflict"):
        backend.execute(
            "developer.repository.branch.create",
            {"branch": "feat/synthetic"},
            context,
            str(uuid4()),
        )
    assert checks == [None]
