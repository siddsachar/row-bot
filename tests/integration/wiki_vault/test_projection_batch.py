"""Bounded batch publication keeps the canonical ownership/conflict gate."""
import sqlite3

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.subsystem]


def sparse_rows(stack, count=3):
    wiki, kg = stack["wiki_vault"], stack["kg"]
    wiki.set_enabled(True)
    return [kg.save_entity("fact", f"Synthetic row {number}", "Short note") for number in range(count)]


def test_fifty_sparse_entities_scan_each_type_once_per_operation(wiki_stack, monkeypatch):
    rows = sparse_rows(wiki_stack, 50)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    original = kg.iter_entities_snapshot
    scans, visited = [], []

    def counted(entity_type=None):
        scans.append(entity_type)
        for row in original(entity_type):
            visited.append(row["id"])
            yield row

    monkeypatch.setattr(kg, "iter_entities_snapshot", counted)
    result = wiki.export_entities_projection(iter(rows))
    assert len(result) == 50 and all(item.complete and item.disposition == "excluded" for item in result)
    assert scans == ["fact"] and len(visited) == 50
    assert all(row["subject"] in wiki._wiki_path("fact/_index.md").read_text() for row in rows)
    changed = kg.update_entity(rows[-1]["id"], "New short note")
    assert wiki.export_entities_projection([changed])[0].complete
    assert scans == ["fact", "fact"] and len(visited) == 100
    assert "New short note" in wiki._wiki_path("fact/_index.md").read_text()


def test_edit_after_first_rollup_invalidates_reuse_and_preserves_old_article(wiki_stack, monkeypatch):
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    wiki.set_enabled(True)
    rows = [kg.save_entity("fact", f"Owned {number}", "Long synthetic article content to retain.") for number in range(3)]
    paths = [wiki.export_entity_projection(row).path for row in rows]
    original_bytes = [path.read_bytes() for path in paths]
    sparse = [kg.update_entity(row["id"], "Short") for row in rows]
    publish = wiki._export_entity_projection
    calls = []
    edited = b"External editor content during admitted batch"

    def interleaved(entity, **kwargs):
        outcome = publish(entity, **kwargs)
        calls.append(outcome)
        if len(calls) == 1:
            wiki._wiki_path("fact/_index.md").write_bytes(edited)
        return outcome

    monkeypatch.setattr(wiki, "_export_entity_projection", interleaved)
    result = wiki.export_entities_projection(sparse)
    assert result[0].complete and not paths[0].exists()
    assert all(not item.complete and item.disposition == "conflict" for item in result[1:])
    assert [path.read_bytes() for path in paths[1:]] == original_bytes[1:]
    assert wiki._wiki_path("fact/_index.md").read_bytes() == edited


def test_initial_rollup_conflict_is_never_retried_or_promoted_within_batch(wiki_stack, monkeypatch):
    rows = sparse_rows(wiki_stack)
    wiki = wiki_stack["wiki_vault"]
    path = wiki._wiki_path("fact/_index.md")
    path.write_bytes(b"Unowned rollup")
    refresh = wiki._refresh_type_index
    scans = []

    def counted(entity_type):
        scans.append(entity_type)
        return refresh(entity_type)

    monkeypatch.setattr(wiki, "_refresh_type_index", counted)
    results = wiki.export_entities_projection(rows)
    assert all(not result.complete and result.disposition == "conflict" for result in results)
    assert scans == ["fact"] and path.read_bytes() == b"Unowned rollup"


def test_batch_preserves_order_stale_rows_and_writer_admission(wiki_stack, monkeypatch):
    rows = sparse_rows(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    kg.update_entity(rows[1]["id"], "Changed source")
    publish = wiki._export_entity_projection
    blocked = []

    def interleaved(entity, **kwargs):
        with sqlite3.connect(kg.DB_PATH, timeout=0) as conn:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("UPDATE entities SET description='Foreign edit' WHERE id=?", (entity["id"],))
        blocked.append(entity["id"])
        return publish(entity, **kwargs)

    monkeypatch.setattr(wiki, "_export_entity_projection", interleaved)
    results = wiki.export_entities_projection(rows)
    assert [item.disposition for item in results] == ["excluded", "stale", "excluded"]
    assert blocked == [rows[0]["id"], rows[2]["id"]]
    assert kg.get_entity(rows[1]["id"])["description"] == "Changed source"


def test_batch_cancellation_releases_source_admission_and_keeps_work_pending(wiki_stack):
    rows = sparse_rows(wiki_stack)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    calls = []

    def cancelled():
        calls.append(True)
        return len(calls) > 1

    with pytest.raises(InterruptedError, match="cancelled"):
        wiki.export_entities_projection(rows, cancelled=cancelled)
    assert len(calls) == 2
    with sqlite3.connect(kg.DB_PATH, timeout=0) as conn:
        assert conn.execute("SELECT SUM(wiki_pending) FROM knowledge_projection_work").fetchone()[0] == 3
        conn.execute("UPDATE entities SET description='Retry source' WHERE id=?", (rows[1]["id"],))
    assert wiki.export_entities_projection([kg.get_entity(row["id"]) for row in rows])[0].complete


def test_batch_rejects_duplicate_sources(wiki_stack):
    row = sparse_rows(wiki_stack, 1)[0]
    with pytest.raises(ValueError, match="Duplicate"):
        wiki_stack["wiki_vault"].export_entities_projection([row, row])


def test_disabled_batch_has_no_file_effects(wiki_stack):
    rows = sparse_rows(wiki_stack)
    wiki = wiki_stack["wiki_vault"]
    wiki.set_enabled(False)
    results = wiki.export_entities_projection(rows)
    assert all(not result.complete and result.disposition == "disabled" for result in results)
    assert not wiki._manifest_path().exists()


def test_batch_work_limit_does_not_publish_the_overflow_row(wiki_stack):
    wiki = wiki_stack["wiki_vault"]
    consumed = []

    def sources():
        for number in range(1002):
            consumed.append(number)
            yield {"id": str(number)}

    with pytest.raises(ValueError, match="exceeds 1000"):
        wiki.export_entities_projection(sources())
    assert len(consumed) == 1001
    assert not wiki._manifest_path().exists()


def test_drain_acknowledges_healthy_sibling_and_retains_conflict(wiki_stack):
    rows = sparse_rows(wiki_stack, 2)
    wiki, kg = wiki_stack["wiki_vault"], wiki_stack["kg"]
    healthy = kg.save_entity("person", "Healthy source", "A long healthy synthetic article.")
    wiki._wiki_path("fact/_index.md").write_bytes(b"External unowned rollup")
    completed, complete = kg._repair_wiki_work(max_entities=3)
    assert completed == 1 and not complete
    with sqlite3.connect(kg.DB_PATH) as conn:
        pending = dict(conn.execute("SELECT entity_id,wiki_pending FROM knowledge_projection_work"))
    assert pending[healthy["id"]] == 0
    assert all(pending[row["id"]] == 1 for row in rows)
    assert wiki._entity_md_path(healthy).is_file()
