import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _fresh_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.developer.storage",
        "row_bot.developer.worktrees",
    ):
        sys.modules.pop(name, None)
    import row_bot.tasks as tasks
    import row_bot.threads as threads
    import row_bot.developer.storage as storage
    import row_bot.developer.worktrees as worktrees

    return (
        importlib.reload(tasks),
        importlib.reload(threads),
        importlib.reload(storage),
        importlib.reload(worktrees),
    )


def _run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _git_out(repo, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(repo, message="initial"):
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Row Bot Test",
            "-c",
            "user.email=row-bot-test@example.invalid",
            "commit",
            "-m",
            message,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _create_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for local worktree allocation")
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _run_git(repo, "add", "README.md", ".gitignore")
    _commit(repo)
    return repo


def test_allocate_thread_worktree_creates_hidden_workspace_and_preserves_metadata(tmp_path, monkeypatch):
    _tasks, threads, storage, worktrees = _fresh_modules(tmp_path, monkeypatch)
    repo = _create_repo(tmp_path)

    parent = storage.add_or_update_local_workspace(str(repo))
    thread_id = threads.create_thread(
        "Developer thread",
        thread_type="code",
        developer_workspace_id=parent.id,
        project_workspace_id=parent.id,
    )
    allocated = worktrees.allocate_thread_worktree(
        thread_id,
        parent.id,
        objective="Review local change",
        seed_mode="last_commit",
    )

    worktree_path = (
        tmp_path
        / ".row-bot-worktrees"
        / parent.id
        / allocated["branch_name"].replace("/", "-")
    )
    assert allocated["owner_kind"] == "thread"
    assert allocated["owner_id"] == thread_id
    assert allocated["status"] == "active"
    assert allocated["project_workspace_id"] == parent.id
    assert allocated["worktree_path"] == str(worktree_path.resolve())
    assert allocated["cleanup_state"] == "preserve"
    assert allocated["metadata_json"]["source_dirty"] is False
    assert allocated["base_commit"]
    assert worktree_path.exists()
    child = storage.get_workspace(allocated["worktree_workspace_id"])
    assert child is not None
    assert child.hidden is True
    assert child.approval_mode == parent.approval_mode

    summary = worktrees.worktree_diff_summary("thread", thread_id)
    assert summary["ok"] is True
    assert summary["worktree"]["owner_id"] == thread_id

    preserved = worktrees.mark_worktree_preserved("thread", thread_id, reason="test complete")
    assert preserved["status"] == "preserved"
    assert worktree_path.exists()


def test_dirty_current_changes_seed_into_worktree_without_mutating_parent(tmp_path, monkeypatch):
    _tasks, _threads, storage, worktrees = _fresh_modules(tmp_path, monkeypatch)
    repo = _create_repo(tmp_path)
    (repo / "README.md").write_text("staged\nunstaged\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    (repo / "README.md").write_text("staged\nunstaged later\n", encoding="utf-8")
    (repo / "notes.md").write_text("untracked\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    parent = storage.add_or_update_local_workspace(str(repo))
    allocated = worktrees.allocate_thread_worktree(
        "dirty-thread",
        parent.id,
        objective="Inspect dirty parent",
    )
    worktree_path = allocated["worktree_path"]

    assert allocated["status"] == "active"
    assert allocated["metadata_json"]["source_dirty"] is True
    assert allocated["metadata_json"]["seeded_from_current_changes"] is True
    assert allocated["metadata_json"]["seeded_staged_diff"] is True
    assert allocated["metadata_json"]["seeded_unstaged_diff"] is True
    assert allocated["metadata_json"]["seeded_untracked_files"] == ["notes.md"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "staged\nunstaged later\n"
    assert (repo / "ignored.txt").exists()
    assert (repo / "notes.md").exists()
    assert (repo / ".git").exists()
    assert _git_out(repo, "status", "--porcelain").splitlines() == [
        "MM README.md",
        "?? notes.md",
    ]

    worktree = Path(worktree_path)
    assert (worktree / "README.md").read_text(encoding="utf-8") == "staged\nunstaged later\n"
    assert (worktree / "notes.md").read_text(encoding="utf-8") == "untracked\n"
    assert not (worktree / "ignored.txt").exists()
    worktree_status = _git_out(worktree, "status", "--porcelain").splitlines()
    assert "MM README.md" in worktree_status
    assert "?? notes.md" in worktree_status


def test_child_worktree_derives_from_dirty_parent_worktree(tmp_path, monkeypatch):
    _tasks, _threads, storage, worktrees = _fresh_modules(tmp_path, monkeypatch)
    repo = _create_repo(tmp_path)
    parent = storage.add_or_update_local_workspace(str(repo))
    thread_wt = worktrees.allocate_thread_worktree("parent-thread", parent.id, objective="Parent")
    parent_worktree = storage.get_workspace(thread_wt["worktree_workspace_id"])
    assert parent_worktree is not None

    parent_path = Path(parent_worktree.path)
    (parent_path / "README.md").write_text("dirty parent worktree\n", encoding="utf-8")
    (parent_path / "child-note.md").write_text("child seed\n", encoding="utf-8")
    child = worktrees.allocate_agent_worktree(
        "run-child",
        parent_worktree.id,
        objective="Child sees parent state",
        parent_thread_id="parent-thread",
    )
    child_path = Path(child["worktree_path"])

    assert child["status"] == "active"
    assert child["project_workspace_id"] == parent.id
    assert child["metadata_json"]["source_workspace_id"] == parent_worktree.id
    assert child["metadata_json"]["parent_owner_kind"] == "thread"
    assert child["metadata_json"]["parent_owner_id"] == "parent-thread"
    assert (child_path / "README.md").read_text(encoding="utf-8") == "dirty parent worktree\n"
    assert (child_path / "child-note.md").read_text(encoding="utf-8") == "child seed\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello\n"


def test_allocate_worktree_requires_git_repo(tmp_path, monkeypatch):
    _tasks, _threads, storage, worktrees = _fresh_modules(tmp_path, monkeypatch)
    folder = tmp_path / "plain"
    folder.mkdir()
    parent = storage.add_or_update_local_workspace(str(folder))

    with pytest.raises(ValueError, match="Worktree requires a git repository"):
        worktrees.allocate_agent_worktree("nogit", parent.id, objective="Try")


def test_git_summary_distinguishes_nested_folder_from_repo_root(tmp_path, monkeypatch):
    _tasks, _threads, storage, _worktrees = _fresh_modules(tmp_path, monkeypatch)
    repo = _create_repo(tmp_path)
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    root_summary = storage.detect_git_summary(str(repo))
    nested_summary = storage.detect_git_summary(str(nested))
    plain_summary = storage.detect_git_summary(str(plain))

    assert root_summary["is_git"] is True
    assert root_summary["is_repo_root"] is True
    assert root_summary["repo_root"] == str(repo.resolve())
    assert nested_summary["is_git"] is True
    assert nested_summary["is_repo_root"] is False
    assert nested_summary["repo_root"] == str(repo.resolve())
    assert plain_summary["is_git"] is False
    assert plain_summary["is_repo_root"] is False
    assert plain_summary["repo_root"] == ""
    assert plain_summary["branch"] == ""
    assert plain_summary["error"] == ""
