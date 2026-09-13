"""Independent adversarial checks of the passive knowledge projection."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from row_bot import knowledge_views as views
from tests.subsystem.knowledge_graph.test_client_read_views import document_store as document_store


def _entities(path: Path, *, wal=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        if wal:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE entities (
            id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, subject TEXT NOT NULL,
            description TEXT, aliases TEXT, tags TEXT, properties TEXT,
            source TEXT, created_at TEXT, updated_at TEXT)""")
        connection.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?)",
                           ("safe-id", "fact", "Saved fixture", "Saved description", "", "",
                            '{"private":"never-project"}', "/private/never-project", "created", "updated"))
    connection.close()


def test_computed_entity_view_is_rejected_without_evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE VIEW entities AS SELECT
            'safe-id' id, 'fact' entity_type, 'Saved fixture' subject,
            review_tripwire() description, '' aliases, '' tags, 'updated' updated_at""")
    calls = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        result = original(*args, **kwargs)
        result.create_function("review_tripwire", 0, lambda: calls.append(True) or "Computed fixture")
        return result

    monkeypatch.setattr(views.sqlite3, "connect", connect)
    result = views.list_saved_entities()
    assert calls == [], "A noncanonical schema expression executed during passive GET"
    assert result.availability == "unavailable" and result.total is None and not result.items


def test_document_completed_content_mismatch_is_partial(document_store):
    service, jobs = document_store
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("INSERT INTO document_records VALUES(?,?,?,?,?,?,?,?)",
                           (jobs[0].id, jobs[0].original_name, jobs[0].stored_name, jobs[0].staged_path,
                            "different-saved-content", 999, "saved", "done"))
    result = views.list_saved_documents(status="completed")
    assert len(result.items) == 1
    assert result.items[0].record_state == "partial"
    assert "different-saved-content" not in json.dumps(asdict(result))


def test_linked_document_store_cannot_cross_data_owner(tmp_path, monkeypatch, document_store):
    service, jobs = document_store
    owned, outside = tmp_path / "owned", service.root
    owned.mkdir()
    # Use a real directory junction on Windows (no symlink privilege required).
    link = owned / "document_ingestion"
    if os.name == "nt":
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                   capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("UPDATE document_jobs SET original_name='outside-private-label' WHERE id=?", (jobs[0].id,))
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(owned))
    result = views.list_saved_documents()
    assert result.availability == "unavailable" and not result.items
    assert "outside-private-label" not in json.dumps(asdict(result))


def test_fresh_interpreter_read_does_not_import_effectful_owners(tmp_path):
    _entities(tmp_path / "memory.db")
    script = """
import json, sys
from row_bot import knowledge_views
result = knowledge_views.list_saved_entities()
assert result.availability == 'available'
assert result.total == 1
assert not {'row_bot.knowledge_graph', 'row_bot.document_jobs', 'row_bot.documents',
            'row_bot.embedding_providers', 'row_bot.wiki_vault'}.intersection(sys.modules)
print(json.dumps({'available': result.availability, 'count': result.total}))
"""
    environment = dict(os.environ, ROW_BOT_DATA_DIR=str(tmp_path))
    completed = subprocess.run([sys.executable, "-c", script], env=environment,
                               capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(completed.stdout) == {"available": "available", "count": 1}


@pytest.mark.parametrize("field,value", [("id", None), ("description", sqlite3.Binary(b"\xff")),
                                         ("updated_at", None)])
def test_malformed_saved_fields_fail_closed_without_raw_data(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "memory.db"
    _entities(path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"UPDATE entities SET {field}=?", (value,))
    result = views.list_saved_entities()
    assert result.availability == "unavailable" and not result.items and result.total is None
    assert "never-project" not in json.dumps(asdict(result))


def test_expensive_sql_is_interrupted_by_owner_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    _entities(tmp_path / "memory.db")
    original = sqlite3.connect
    observed = {"installed": False, "owner_interrupted": False, "guardian": False}

    class GuardedConnection(sqlite3.Connection):
        def set_progress_handler(self, callback, interval):
            observed["installed"] = True
            calls = 0

            def guarded():
                nonlocal calls
                calls += 1
                if callback():
                    observed["owner_interrupted"] = True
                    return 1
                if calls * interval >= 40_000_000:
                    observed["guardian"] = True
                    return 1
                return 0

            return super().set_progress_handler(guarded, interval)

        def execute(self, sql, parameters=()):
            if "FROM entities ORDER BY id" in sql:
                if not observed["installed"]:
                    observed["guardian"] = True
                    super().set_progress_handler(lambda: 1, 1000)
                # Exercise real SQLite VM work on the owner's read connection;
                # the test guardian bounds regressions without relying on sleeps.
                return super().execute("""WITH RECURSIVE numbers(n) AS (
                    SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<1000000000)
                    SELECT sum(n) FROM numbers""")
            return super().execute(sql, parameters)

    monkeypatch.setattr(views.sqlite3, "connect", lambda *a, **kw: original(*a, factory=GuardedConnection, **kw))
    result = views.list_saved_entities()
    assert observed == {"installed": True, "owner_interrupted": True, "guardian": False}
    assert result.availability == "unavailable" and result.total is None and not result.items


def test_oversized_saved_value_fails_without_partial_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "memory.db"
    _entities(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE entities SET description=?", ("x" * (17 * 1024 * 1024),))
    result = views.list_saved_entities(query="absent")
    assert result.availability == "unavailable" and result.total is None and not result.items


def test_live_uncheckpointed_wal_commit_remains_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "memory.db"
    _entities(path, wal=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("UPDATE entities SET subject='Committed only in live WAL'")
        connection.commit()
        assert (tmp_path / "memory.db-wal").stat().st_size > 0
        before = path.read_bytes()
        result = views.list_saved_entities(query="Committed only in live WAL")
        assert result.availability == "available" and result.total == 1
        assert result.items[0].subject == "Committed only in live WAL"
        assert path.read_bytes() == before
    finally:
        connection.close()


def test_failed_finalize_is_not_reported_completed(document_store):
    service, jobs = document_store
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("UPDATE document_jobs SET status='failed',stage='finalize' WHERE id=?", (jobs[0].id,))
    result = views.list_saved_documents(status="failed")
    item = next(item for item in result.items if item.id == jobs[0].id)
    assert item.status == "failed" and item.stage == "finalize"
    assert item.searchability == "unknown"


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_linked_coordination_sidecar_is_rejected_before_sqlite_open(tmp_path, monkeypatch, suffix):
    owned = tmp_path / "owned"
    _entities(owned / "memory.db", wal=True)
    target = tmp_path / "outside"
    target.mkdir()
    link = owned / ("memory.db" + suffix)
    if os.name == "nt":
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr
    else:
        link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(owned))
    opened = []
    original = sqlite3.connect

    def connect(*args, **kwargs):
        opened.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(views.sqlite3, "connect", connect)
    result = views.list_saved_entities()
    assert result.availability == "unavailable" and not result.items
    assert opened == []
