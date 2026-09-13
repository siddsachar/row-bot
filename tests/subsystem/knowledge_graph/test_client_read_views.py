from __future__ import annotations

import builtins
from dataclasses import asdict
import json
import sqlite3

import pytest

from row_bot import knowledge_views as views
from tests.fixtures.knowledge_graph import fresh_knowledge_graph


@pytest.fixture
def saved(tmp_path, monkeypatch):
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"entity-{i:04}",
                    "fact" if i % 2 else "project",
                    f"Saved {i}",
                    "x" * 1200 + (" late needle" if i == 204 else ""),
                    "special alias" if i == 201 else "",
                    "100%_literal",
                    '{"secret":"private"}',
                    "document:/private/secret.txt",
                    "created",
                    "updated",
                )
                for i in range(205)
            ],
        )
    return kg


def test_cold_read_does_not_create_or_import_owners(tmp_path, monkeypatch):
    target = tmp_path / "not-created"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(target))
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name not in {
            "row_bot.knowledge_graph",
            "row_bot.documents",
            "row_bot.document_jobs",
        }
        assert "embedding" not in name
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    for read in (views.list_saved_entities, views.list_saved_documents):
        page = read()
        assert page.availability == "missing"
        assert page.total is None and page.items == ()
    assert not target.exists()


def test_full_library_paging_and_private_projection(saved):
    found, cursor = [], None
    while True:
        page = views.list_saved_entities(limit=80, cursor=cursor)
        assert page.availability == "available" and page.total == 205
        assert len(page.items) <= 80
        found.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert found == [f"entity-{i:04}" for i in range(205)]
    item = page.items[0]
    assert item.truncated and len(item.description) == 1000
    assert item.saved_state == "saved" and item.semantic_state == "unknown"
    encoded = json.dumps(asdict(page))
    assert (
        "private" not in encoded
        and "source" not in encoded
        and "properties" not in encoded
    )


@pytest.mark.parametrize(
    "query,expected",
    [
        ("late needle", ["entity-0204"]),
        ("SPECIAL ALIAS", ["entity-0201"]),
        ("100%_literal", None),
        ("' OR 1=1 --", []),
    ],
)
def test_search_full_fields_before_bounded_page(saved, query, expected):
    page = views.list_saved_entities(query=query, limit=10)
    if expected is None:
        assert page.total == 205
    else:
        assert [item.id for item in page.items] == expected
    assert views.list_saved_entities(entity_type="fact").total == 102


@pytest.mark.parametrize("change", ["data", "filter", "limit", "tail_match"])
def test_revision_and_filter_bound_cursors(saved, change):
    options = {"limit": 1}
    if change == "tail_match":
        with sqlite3.connect(saved.DB_PATH) as conn:
            conn.execute("UPDATE entities SET description=description || ' marker'")
        options["query"] = "marker"
    first = views.list_saved_entities(**options)
    if change == "filter":
        options["entity_type"] = "fact"
    elif change == "limit":
        options["limit"] = 2
    elif change == "tail_match":
        with sqlite3.connect(saved.DB_PATH) as conn:
            conn.execute(
                "UPDATE entities SET description=substr(description,1,length(description)-7) WHERE id='entity-0204'"
            )
    else:
        with sqlite3.connect(saved.DB_PATH) as conn:
            conn.execute("UPDATE entities SET subject='Changed' WHERE id='entity-0204'")
    with pytest.raises(views.KnowledgeViewError, match="cursor_expired"):
        views.list_saved_entities(cursor=first.next_cursor, **options)


@pytest.mark.parametrize(
    "options",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"query": "x" * 257},
        {"query": None},
        {"entity_type": "x" * 65},
    ],
)
def test_invalid_queries_rejected_without_open(monkeypatch, options):
    monkeypatch.setattr(views.sqlite3, "connect", lambda *a, **k: pytest.fail("opened"))
    with pytest.raises(views.KnowledgeViewError, match="invalid_knowledge_query"):
        views.list_saved_entities(**options)


@pytest.mark.parametrize("cursor", ["!", "e30=", "a" * 1025])
def test_invalid_cursors(cursor):
    with pytest.raises(views.KnowledgeViewError, match="cursor_expired"):
        views.list_saved_entities(cursor=cursor)


def test_snapshot_bounded_reads_and_readonly_connection(saved, monkeypatch):
    original = sqlite3.connect
    batches, statements = [], []
    changed = False

    class Cursor(sqlite3.Cursor):
        def fetchmany(self, size=1):
            nonlocal changed
            batches.append(size)
            result = super().fetchmany(size)
            if result and not changed:
                changed = True
                with original(saved.DB_PATH) as writer:
                    writer.execute(
                        "UPDATE entities SET subject='Next revision' WHERE id='entity-0204'"
                    )
            return result

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            statements.append(sql)
            return self.cursor(factory=Cursor).execute(sql, parameters)

    def connect(path, **kwargs):
        assert path.endswith("?mode=ro") and kwargs["uri"]
        conn = original(path, factory=Connection, **kwargs)
        return conn

    monkeypatch.setattr(views.sqlite3, "connect", connect)
    page = views.list_saved_entities(query="Saved 204")
    assert page.total == 1 and page.items[0].subject == "Saved 204"
    assert max(batches) == 128
    assert all(
        sql.strip().split()[0] in {"SELECT", "PRAGMA", "BEGIN"} for sql in statements
    )
    assert "PRAGMA query_only=ON" in statements
    assert views.list_saved_entities(query="Saved 204").total == 0


@pytest.mark.parametrize("kind", ["corrupt", "legacy", "bad_identity"])
def test_unavailable_never_reconciles(saved, kind):
    if kind == "corrupt":
        # Separate corrupt file avoids touching a live WAL connection.
        path = (
            views.get_row_bot_data_dir(create=False) / "document_ingestion" / "jobs.db"
        )
        path.parent.mkdir()
        path.write_bytes(b"not a database")
        before = path.read_bytes()
        page = views.list_saved_documents()
        assert path.read_bytes() == before
    else:
        with sqlite3.connect(saved.DB_PATH) as conn:
            if kind == "legacy":
                conn.execute("ALTER TABLE entities RENAME TO old_entities")
            else:
                conn.execute(
                    "UPDATE entities SET id='/private/path' WHERE id='entity-0000'"
                )
        page = views.list_saved_entities()
    assert page.availability == "unavailable" and page.total is None and not page.items


@pytest.fixture
def document_store(tmp_path, monkeypatch):
    from row_bot.document_jobs import DocumentJobService

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "documents"))
    service = DocumentJobService(tmp_path / "documents")
    batch = service.create_batch()
    jobs = [
        service.create_staging_job(batch, i, f"/private/folder/report-{i}.txt")
        for i in range(3)
    ]
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "UPDATE document_jobs SET status='completed',stage='finalize',completed_at='done' WHERE id=?",
            (jobs[0].id,),
        )
        conn.execute(
            "UPDATE document_jobs SET status='future-status',stage='future-stage',index_progress_current=-1 WHERE id=?",
            (jobs[1].id,),
        )
        conn.execute(
            "INSERT INTO document_records VALUES(?,?,?,?,?,?,?,?)",
            (
                "orphan",
                "/private/other/orphan.txt",
                "stored",
                "/private/path",
                "hash",
                1,
                "saved",
                "done",
            ),
        )
    return service, jobs


def test_documents_saved_states_paths_and_orphans(document_store):
    service, jobs = document_store
    page = views.list_saved_documents(limit=100)
    by_id = {item.id: item for item in page.items}
    assert page.total == 4 and by_id[jobs[0].id].record_state == "partial"
    assert by_id[jobs[0].id].status == "completed"
    assert (
        by_id[jobs[1].id].status == "unknown" and by_id[jobs[1].id].stage == "unknown"
    )
    assert by_id[jobs[1].id].index_current is None
    assert by_id[jobs[2].id].record_state == "job_only"
    assert by_id["orphan"].record_state == "record_only"
    assert all(item.searchability == "unknown" for item in page.items)
    assert "private" not in json.dumps(asdict(page))
    assert by_id["orphan"].name == "orphan.txt"
    assert views.list_saved_documents(status="unknown").total == 2
    assert views.list_saved_documents(query="REPORT-2").items[0].id == jobs[2].id


def test_document_record_reconciliation_changes_revision(document_store):
    service, jobs = document_store
    page = views.list_saved_documents(limit=1)
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "INSERT INTO document_records VALUES(?,?,?,?,?,?,?,?)",
            (
                jobs[0].id,
                jobs[0].original_name,
                jobs[0].stored_name,
                jobs[0].staged_path,
                jobs[0].content_sha256,
                jobs[0].size_bytes,
                "saved",
                "done",
            ),
        )
    with pytest.raises(views.KnowledgeViewError, match="cursor_expired"):
        views.list_saved_documents(limit=1, cursor=page.next_cursor)
    item = views.list_saved_documents(status="completed").items[0]
    assert item.record_state == "saved" and item.searchability == "unknown"


def test_document_full_library_search_and_paging(document_store):
    service, jobs = document_store
    with sqlite3.connect(service.db_path) as conn:
        conn.executemany(
            "INSERT INTO document_records VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    f"record-{i:04}",
                    "report " + "x" * 400 + (" Needle" if i == 204 else ""),
                    "stored",
                    "/private/file",
                    "hash",
                    1,
                    "saved",
                    "done",
                )
                for i in range(205)
            ],
        )
    page = views.list_saved_documents(query="needle")
    assert page.total == 1 and page.items[0].id == "record-0204"
    assert len(page.items[0].name) <= 256 and page.items[0].truncated
    first = views.list_saved_documents(query="report x", limit=100)
    second = views.list_saved_documents(
        query="report x", limit=100, cursor=first.next_cursor
    )
    third = views.list_saved_documents(
        query="report x", limit=100, cursor=second.next_cursor
    )
    assert len(first.items + second.items + third.items) == 205
    assert len({item.id for item in first.items + second.items + third.items}) == 205


def test_readonly_query_cannot_write(saved, monkeypatch):
    original = sqlite3.connect
    attempts = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql == "PRAGMA query_only=ON":
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    super().execute("DELETE FROM entities")
                self.rollback()
                attempts.append(True)
            return result

    monkeypatch.setattr(
        views.sqlite3, "connect", lambda *a, **k: original(*a, factory=Connection, **k)
    )
    assert views.list_saved_entities().total == 205
    assert attempts == [True]


def test_failed_later_batch_discards_partial_results(saved, monkeypatch):
    original = views._entity
    seen = 0

    def broken(row):
        nonlocal seen
        seen += 1
        if seen == 130:
            raise ValueError("private failure")
        return original(row)

    monkeypatch.setattr(views, "_entity", broken)
    result = views.list_saved_entities()
    assert (
        result.availability == "unavailable"
        and not result.items
        and result.total is None
    )
    assert "private" not in json.dumps(asdict(result))


def test_empty_initialized_store_distinguished_from_missing(saved):
    with sqlite3.connect(saved.DB_PATH) as conn:
        conn.execute("DELETE FROM entities")
    page = views.list_saved_entities()
    assert page.availability == "available" and page.total == 0 and not page.items


def test_cursor_expires_when_store_becomes_unsupported(saved):
    first = views.list_saved_entities(limit=1)
    with sqlite3.connect(saved.DB_PATH) as conn:
        conn.execute("ALTER TABLE entities RENAME TO retired_entities")
    with pytest.raises(views.KnowledgeViewError, match="cursor_expired"):
        views.list_saved_entities(limit=1, cursor=first.next_cursor)


def test_generated_entity_columns_are_not_evaluated(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    with sqlite3.connect(tmp_path / "memory.db") as conn:
        conn.execute("""CREATE TABLE entities (
            id TEXT PRIMARY KEY, entity_type TEXT, subject TEXT,
            description TEXT GENERATED ALWAYS AS (lower(subject)),
            aliases TEXT, tags TEXT, updated_at TEXT)""")
        conn.execute(
            "INSERT INTO entities(id,entity_type,subject,aliases,tags,updated_at) VALUES('entity','fact','Title','','','saved')"
        )
    result = views.list_saved_entities()
    assert result.availability == "unavailable" and result.total is None


def test_query_deadline_returns_unavailable_without_partial_rows(saved, monkeypatch):
    readings = iter((0.0, 3.0))
    monkeypatch.setattr(views.time, "monotonic", lambda: next(readings, 3.0))
    result = views.list_saved_entities()
    assert (
        result.availability == "unavailable"
        and result.total is None
        and not result.items
    )


def test_oversized_sqlite_value_returns_unavailable(saved):
    with sqlite3.connect(saved.DB_PATH) as conn:
        conn.execute(
            "UPDATE entities SET description=? WHERE id='entity-0204'",
            ("x" * (16 * 1024 * 1024 + 1),),
        )
    result = views.list_saved_entities(query="tail needle")
    assert (
        result.availability == "unavailable"
        and result.total is None
        and not result.items
    )
