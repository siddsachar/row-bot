from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path


REQUIRED_TABLES = {
    "tasks",
    "task_runs",
    "pipeline_state",
    "workflow_drafts",
    "approval_requests",
    "approval_channel_refs",
}


def _fresh_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path))
    sys.modules.pop("tasks", None)
    import row_bot.tasks as tasks

    return importlib.reload(tasks)


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }


def test_empty_data_dir_creates_required_task_tables(tmp_path, monkeypatch):
    tasks = _fresh_tasks(tmp_path, monkeypatch)

    assert tasks.diagnose_task_schema()["ok"] is True
    assert REQUIRED_TABLES <= _tables(tmp_path / "tasks.db")


def test_partial_tasks_db_repairs_missing_task_runs_and_preserves_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tasks ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, prompts TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tasks (id, name, prompts, created_at) VALUES (?, ?, ?, ?)",
            ("task-1", "Keep Me", '["hello"]', "2026-01-01T00:00:00"),
        )
        conn.commit()

    tasks = _fresh_tasks(tmp_path, monkeypatch)
    rows = tasks.list_tasks()

    assert REQUIRED_TABLES <= _tables(db_path)
    assert any(row["id"] == "task-1" and row["name"] == "Keep Me" for row in rows)


def test_get_recent_runs_repairs_missing_task_runs(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tasks ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, prompts TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.commit()

    tasks = _fresh_tasks(tmp_path, monkeypatch)

    assert tasks.get_recent_runs() == []
    assert "task_runs" in _tables(db_path)


def test_corrupt_tasks_db_is_backed_up_and_recreated(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    db_path.write_bytes(b"not a sqlite database")
    (tmp_path / "tasks.db-wal").write_text("wal", encoding="utf-8")
    (tmp_path / "tasks.db-shm").write_text("shm", encoding="utf-8")

    tasks = _fresh_tasks(tmp_path, monkeypatch)
    diag = tasks.diagnose_task_schema()
    backups = list((tmp_path / "recovery").glob("tasks-db-corrupt-*"))

    assert diag["ok"] is True
    assert backups
    assert (backups[0] / "tasks.db").exists()
    assert (backups[0] / "tasks.db-wal").exists()
    assert (backups[0] / "tasks.db-shm").exists()


def test_workflow_migration_skips_malformed_rows_after_schema_exists(tmp_path, monkeypatch):
    old_db = tmp_path / "workflows.db"
    with sqlite3.connect(old_db) as conn:
        conn.execute(
            "CREATE TABLE workflows ("
            "id TEXT, name TEXT, description TEXT, icon TEXT, prompts TEXT, "
            "schedule TEXT, enabled INTEGER, last_run TEXT, created_at TEXT, "
            "sort_order INTEGER)"
        )
        conn.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("good", "Good", "", "*", '["ok"]', None, 1, None, "2026-01-01", 0),
        )
        conn.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("bad", "Bad", "", "*", None, None, 1, None, "2026-01-01", 1),
        )
        conn.commit()

    tasks = _fresh_tasks(tmp_path, monkeypatch)
    rows = tasks.list_tasks()

    assert any(row["id"] == "good" for row in rows)
    assert not any(row["id"] == "bad" for row in rows)
    assert (tmp_path / ".workflows_migrated").exists()


def test_launcher_recovery_args_and_backup_family(tmp_path, monkeypatch):
    import row_bot.launcher as launcher

    args = launcher._build_arg_parser().parse_args(["--reset-tasks-db"])
    assert args.reset_tasks_db is True
    args = launcher._build_arg_parser().parse_args(["--reset-db"])
    assert args.reset_db is True
    args = launcher._build_arg_parser().parse_args(["--restore-data"])
    assert args.restore_data == "latest"

    db_path = tmp_path / "tasks.db"
    db_path.write_text("db", encoding="utf-8")
    Path(str(db_path) + "-wal").write_text("wal", encoding="utf-8")
    Path(str(db_path) + "-shm").write_text("shm", encoding="utf-8")
    moved = launcher._backup_family(db_path, tmp_path / "backup")

    assert len(moved) == 3
    assert not db_path.exists()
    assert (tmp_path / "backup" / "tasks.db").exists()
    assert (tmp_path / "backup" / "tasks.db-wal").exists()
    assert (tmp_path / "backup" / "tasks.db-shm").exists()
