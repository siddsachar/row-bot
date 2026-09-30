from __future__ import annotations

import importlib
import sqlite3
import sys


def _fresh_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in ("threads", "row_bot.threads"):
        sys.modules.pop(name, None)
    import row_bot.threads as threads

    return importlib.reload(threads)


def _fresh_developer_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "row_bot.threads",
        "row_bot.developer.storage",
        "row_bot.developer.worktrees",
        "row_bot.tasks",
    ):
        sys.modules.pop(name, None)
    import row_bot.threads as threads
    import row_bot.developer.storage as storage

    return importlib.reload(threads), importlib.reload(storage)


def test_thread_pin_metadata_migrates_and_preserves_updated_at(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Thread Jun 03, 20:45")

    with sqlite3.connect(threads.DB_PATH) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(thread_meta)").fetchall()}
        conn.execute(
            "UPDATE thread_meta SET updated_at = '2026-06-03T10:00:00' WHERE thread_id = ?",
            (thread_id,),
        )
        conn.commit()
    assert "pinned_at" in columns

    pinned_at = threads.pin_thread(thread_id)

    assert pinned_at
    row = next(item for item in threads._list_threads(include_details=True) if item[0] == thread_id)
    assert row[12] == ""
    assert row[13] == pinned_at
    with sqlite3.connect(threads.DB_PATH) as conn:
        stored_updated_at = conn.execute(
            "SELECT updated_at FROM thread_meta WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
    assert stored_updated_at == "2026-06-03T10:00:00"

    threads.unpin_thread(thread_id)

    row = next(item for item in threads._list_threads(include_details=True) if item[0] == thread_id)
    assert row[13] == ""
    with sqlite3.connect(threads.DB_PATH) as conn:
        stored_updated_at = conn.execute(
            "SELECT updated_at FROM thread_meta WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()[0]
    assert stored_updated_at == "2026-06-03T10:00:00"


def test_thread_pin_helpers_reject_missing_or_empty_thread_ids(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    for thread_id in ("", "missing"):
        try:
            threads.pin_thread(thread_id)
        except ValueError as exc:
            assert "thread" in str(exc).lower()
        else:
            raise AssertionError(f"pinning {thread_id!r} should fail")


def test_developer_workspace_thread_rows_append_pin_state(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)
    thread_id = threads.create_thread(
        "Developer thread",
        thread_type="code",
        developer_workspace_id="workspace_1",
        project_workspace_id="workspace_1",
    )

    pinned_at = threads.pin_thread(thread_id)
    row = threads.list_developer_workspace_threads("workspace_1")[0]

    assert row[10] == "workspace_1"
    assert row[11] == pinned_at


def test_developer_latest_thread_remains_recency_based_when_older_thread_is_pinned(tmp_path, monkeypatch):
    threads, storage = _fresh_developer_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = storage.add_or_update_local_workspace(str(repo))
    older = storage.ensure_workspace_thread(workspace.id)
    newer = threads.create_thread(
        "Thread Jun 03, 20:45",
        thread_type="code",
        developer_workspace_id=workspace.id,
        project_workspace_id=workspace.id,
    )

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute(
            "UPDATE thread_meta SET updated_at = '2026-06-03T10:00:00' WHERE thread_id = ?",
            (older,),
        )
        conn.execute(
            "UPDATE thread_meta SET updated_at = '2026-06-03T10:00:01' WHERE thread_id = ?",
            (newer,),
        )
        conn.commit()
    pinned_at = threads.pin_thread(older)

    rows = storage.list_workspace_threads(workspace.id)

    assert storage.latest_workspace_thread(workspace.id) == newer
    assert [row[0] for row in rows[:2]] == [newer, older]
    assert next(row for row in rows if row[0] == older)[11] == pinned_at
