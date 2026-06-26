from __future__ import annotations

import importlib
import shutil
import sqlite3
import subprocess
import sys

import pytest


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    for name in [
        "row_bot.threads",
        "row_bot.developer.storage",
        "row_bot.developer.worktrees",
        "row_bot.tasks",
    ]:
        sys.modules.pop(name, None)
    import row_bot.threads as threads
    import row_bot.developer.storage as storage

    return importlib.reload(threads), importlib.reload(storage)


def test_workspace_can_own_multiple_code_threads(tmp_path, monkeypatch):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))

    first = storage.ensure_workspace_thread(workspace.id)
    second = storage.create_workspace_thread(workspace.id, name="Thread Jun 03, 20:45")

    rows = storage.list_workspace_threads(workspace.id)
    ids = {row[0] for row in rows}
    assert {first, second} <= ids
    assert threads.classify_thread("", first, set(), "code", workspace.id) == "code"
    assert threads.classify_thread("", second, set(), "code", workspace.id) == "code"
    assert storage.get_workspace(workspace.id).default_thread_id == first


def test_latest_workspace_thread_follows_updated_at(tmp_path, monkeypatch):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))
    first = storage.ensure_workspace_thread(workspace.id)
    second = storage.create_workspace_thread(workspace.id, name="Thread Jun 03, 20:45")

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:00' WHERE thread_id = ?", (first,))
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:01' WHERE thread_id = ?", (second,))
        conn.commit()
    assert storage.latest_workspace_thread(workspace.id) == second

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:02' WHERE thread_id = ?", (first,))
        conn.commit()
    assert storage.latest_workspace_thread(workspace.id) == first


def test_ensure_workspace_thread_keeps_default_but_latest_can_switch(tmp_path, monkeypatch):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))
    default_thread = storage.ensure_workspace_thread(workspace.id)
    newer_thread = storage.create_workspace_thread(workspace.id, name="Thread Jun 03, 20:45")

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:00' WHERE thread_id = ?", (default_thread,))
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:01' WHERE thread_id = ?", (newer_thread,))
        conn.commit()

    assert storage.ensure_workspace_thread(workspace.id) == default_thread
    assert storage.ensure_latest_workspace_thread(workspace.id) == newer_thread


def test_new_workspace_thread_inherits_workspace_approval_mode(tmp_path, monkeypatch):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")

    thread_id = storage.create_workspace_thread(workspace.id)

    assert threads._get_thread_approval_mode(thread_id) == "allow_all"


def _run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _create_git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for worktree-backed Developer thread tests")
    repo = tmp_path / "git-repo"
    repo.mkdir()
    _run_git(repo, "init")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
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
            "initial",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return repo


def test_git_workspace_thread_defaults_to_worktree_and_groups_under_project(
    tmp_path,
    monkeypatch,
):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = _create_git_repo(tmp_path)
    workspace = storage.add_or_update_local_workspace(str(repo))

    thread_id = storage.create_workspace_thread(workspace.id, name="Worktree thread")

    active_workspace_id = threads._get_thread_developer_workspace(thread_id)
    assert threads._get_thread_project_workspace(thread_id) == workspace.id
    assert active_workspace_id != workspace.id
    active_workspace = storage.get_workspace(active_workspace_id)
    assert active_workspace is not None
    assert active_workspace.hidden is True
    rows = storage.list_workspace_threads(workspace.id)
    assert thread_id in {row[0] for row in rows}


def test_existing_current_folder_thread_can_switch_to_worktree(tmp_path, monkeypatch):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = _create_git_repo(tmp_path)
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = storage.create_workspace_thread(
        workspace.id,
        name="Current folder thread",
        use_worktree=False,
    )

    allocated = storage.create_thread_worktree(thread_id, workspace.id, objective="Move thread")

    assert allocated["status"] == "active"
    assert threads._get_thread_project_workspace(thread_id) == workspace.id
    assert threads._get_thread_developer_workspace(thread_id) == allocated["worktree_workspace_id"]
