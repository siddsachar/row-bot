from __future__ import annotations

import importlib
import sqlite3
import sys


def _fresh_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("threads", None)
    import row_bot.threads as threads

    return importlib.reload(threads)


def test_create_thread_sets_auto_name_source(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    tid = threads.create_thread("Thread Jun 03, 20:45")

    assert threads.get_thread_name_source(tid) == "auto"


def test_manual_rename_sets_manual_source_and_blocks_auto_title(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)
    tid = threads.create_thread("Thread Jun 03, 20:45")

    renamed = threads.rename_thread(tid, "Thread but user-written")

    assert renamed == "Thread but user-written"
    assert threads.get_thread_name(tid) == "Thread but user-written"
    assert threads.get_thread_name_source(tid) == "manual"
    assert threads.should_auto_rename_thread(tid) is False


def test_empty_manual_rename_is_rejected(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)
    tid = threads.create_thread("Thread Jun 03, 20:45")

    try:
        threads.rename_thread(tid, "  ")
    except ValueError as exc:
        assert "empty" in str(exc).lower()
    else:
        raise AssertionError("empty rename should fail")


def test_legacy_empty_source_default_title_remains_auto_renamable(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO thread_meta (thread_id, name, created_at, updated_at) "
            "VALUES ('legacy', 'Thread Jun 03, 20:45', '2026-06-03', '2026-06-03')"
        )
        conn.commit()

    assert threads.get_thread_name_source("legacy") == ""
    assert threads.should_auto_rename_thread("legacy") is True


def test_list_threads_detail_indexes_are_append_only(tmp_path, monkeypatch):
    threads = _fresh_threads(tmp_path, monkeypatch)

    tid = threads.create_thread(
        "Code task",
        thread_type="code",
        developer_workspace_id="dev_123",
        project_id="",
        approval_mode="auto_edit",
        model_override="model:test",
        name_source="manual",
    )

    row = next(item for item in threads._list_threads(include_details=True) if item[0] == tid)
    assert row[4] == "model:test"
    assert row[5] == ""
    assert row[6] == "code"
    assert row[7] == "dev_123"
    assert row[8] == "allow_all"
    assert row[9] == "manual"
