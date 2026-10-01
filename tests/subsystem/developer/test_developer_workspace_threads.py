from __future__ import annotations

import importlib
import sqlite3
import sys


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


def test_latest_workspace_thread_follows_updated_at(tmp_path, monkeypatch):
    threads, storage = _fresh_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))
    first = storage.ensure_workspace_thread(workspace.id)
    second = threads.create_thread(
        "Thread Jun 03, 20:45",
        thread_type="code",
        developer_workspace_id=workspace.id,
        project_workspace_id=workspace.id,
    )

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:00' WHERE thread_id = ?", (first,))
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:01' WHERE thread_id = ?", (second,))
        conn.commit()
    assert storage.latest_workspace_thread(workspace.id) == second

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET updated_at = '2026-06-03T10:00:02' WHERE thread_id = ?", (first,))
        conn.commit()
    assert storage.latest_workspace_thread(workspace.id) == first
