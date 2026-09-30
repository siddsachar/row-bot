from __future__ import annotations

import importlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest


pytestmark = pytest.mark.subsystem


def test_legacy_memories_table_migrates_to_entities(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "row-bot-data"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    db_path = data_dir / "memory.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memories ("
        "id TEXT PRIMARY KEY, category TEXT, subject TEXT, content TEXT, "
        "source TEXT, tags TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-1",
            "person",
            "Ada",
            "Ada works on deterministic tests.",
            "legacy",
            "testing,subsystem",
            "2026-01-01T00:00:00",
            "2026-01-02T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    import row_bot.knowledge_graph as kg

    kg = importlib.reload(kg)
    kg._skip_reindex = True
    migrated = kg.get_entity("legacy-1")
    tables_conn = sqlite3.connect(db_path)
    tables = {row[0] for row in tables_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables_conn.close()

    assert migrated is not None
    assert migrated["entity_type"] == "person"
    assert migrated["subject"] == "Ada"
    assert migrated["description"] == "Ada works on deterministic tests."
    assert "memories_v35_backup" in tables
    assert "memories" not in tables


def test_fresh_knowledge_graph_uses_isolated_data_dir(tmp_path, monkeypatch) -> None:
    from tests.fixtures.knowledge_graph import fresh_knowledge_graph

    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    entity = kg.save_entity("project", "Row-Bot", "Local-first assistant", source="test")

    assert kg.DB_PATH.startswith(str(tmp_path))
    assert kg.count_entities() == 1
    assert kg.get_entity(entity["id"])["subject"] == "Row-Bot"


def test_recall_stamps_move_out_of_saved_properties_and_keep_decay_order(tmp_path, monkeypatch) -> None:
    from tests.fixtures.knowledge_graph import fresh_knowledge_graph

    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    old = (datetime.now() - timedelta(days=120)).isoformat()
    recent = (datetime.now() - timedelta(days=1)).isoformat()
    # Saved by an earlier version, which kept the recall stamp in properties.
    with closing(sqlite3.connect(kg.DB_PATH)) as conn:
        for entity_id, subject, properties in (
            ("recalled", "Harbor lighthouse", {"status": "active", "recalled_at": recent}),
            ("forgotten", "Harbor ferry", {"status": "active"}),
        ):
            conn.execute(
                "INSERT INTO entities(id,entity_type,subject,description,properties,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (entity_id, "fact", subject, f"{subject} notes about the harbor.", json.dumps(properties), old, old),
            )
        conn.commit()

    kg = fresh_knowledge_graph(tmp_path, monkeypatch)  # the next start migrates

    assert json.loads(kg.get_entity("recalled")["properties"]) == {"status": "active"}
    assert kg.get_entity("recalled")["updated_at"] == old
    assert kg.recall_stamps(["recalled", "forgotten"]) == {"recalled": recent}
    ranked = kg.retrieve_memory_candidates("harbor", threshold=0.99)
    assert [candidate["id"] for candidate in ranked] == ["recalled", "forgotten"]
    assert [candidate["decay_multiplier"] for candidate in ranked] == [1.0, 0.7]
    kg.delete_entity("recalled")
    assert kg.recall_stamps() == {}
