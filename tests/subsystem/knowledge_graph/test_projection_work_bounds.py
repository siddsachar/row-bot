from __future__ import annotations

import pytest

from tests.subsystem.knowledge_graph.test_knowledge_projection_recovery import add, projection_stack as _projection_stack

projection_stack = _projection_stack
pytestmark = pytest.mark.subsystem


def test_complete_rebuild_bounds_each_embedding_batch(projection_stack):
    kg, embedding, _fingerprint, config = projection_stack
    conn = kg._get_conn()
    try:
        conn.executemany("""INSERT INTO entities(id,entity_type,subject,description,created_at,updated_at)
            VALUES(?,'fact',?,'Synthetic full coverage','2026','2026')""",
                         [(f"fact-{i:04}", f"Synthetic {i}") for i in range(1003)])
        conn.commit()
    finally:
        conn.close()
    kg.rebuild_index()
    assert sum(map(len, embedding.batches)) == 1003
    assert max(map(len, embedding.batches)) <= config["batch_size"]
    assert kg.memory_vector_status()["ready"]


def test_explicit_repair_limits_embedding_work_and_reports_remaining(projection_stack):
    kg, embedding, _fingerprint, _config = projection_stack
    for number in range(5):
        add(kg, f"Fact {number}")
    first = kg.repair_projections(max_entities=2)
    assert not first["complete"]
    assert sum(map(len, embedding.batches)) == 2
    second = kg.repair_projections(max_entities=2)
    assert not second["complete"]
    assert sum(map(len, embedding.batches)) == 4
    kg.repair_projections(max_entities=2)
    assert sum(map(len, embedding.batches)) == 5
    assert kg.memory_vector_status()["ready"]


def test_cancelled_rebuild_never_selects_partial_generation(projection_stack):
    kg, embedding, _fingerprint, config = projection_stack
    for number in range(5):
        add(kg, f"Fact {number}")
    config["batch_size"] = 2
    with pytest.raises(kg.KnowledgeProjectionIncomplete):
        kg.rebuild_index(cancelled=lambda: len(embedding.batches) >= 1)
    assert sum(map(len, embedding.batches)) == 2
    assert kg._projection_state()["generation"] is None


def test_source_removal_reuses_survivors_without_embedding(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    removed = add(kg, "Removed", "document:removed")
    survivor = add(kg, "Retained", "document:retained")
    kg.rebuild_index()
    monkeypatch.setattr(kg, "_skip_reindex", False)
    embedding.batches.clear()
    assert kg.delete_entities_by_source("document:removed") == 1
    assert not embedding.batches
    assert kg.get_entity(removed["id"]) is None
    assert kg.get_entity(survivor["id"])
    assert kg.memory_vector_status()["ready"]


def test_empty_rebuild_does_not_load_embedding_provider(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_get_embedding_model", lambda **_kw: pytest.fail("empty index needs no provider"))
    kg.rebuild_index()
    assert kg.memory_vector_status()["ready"]


def test_projection_enumeration_exceeds_historic_cap_without_embedding_dimensions(tmp_path, monkeypatch):
    from tests.fixtures.knowledge_graph import fresh_knowledge_graph

    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(kg, "_get_embedding_model", lambda **_kw: pytest.fail("enumeration must not load models"))
    conn = kg._get_conn()
    try:
        conn.execute("""WITH RECURSIVE numbers(n) AS (
            SELECT 0 UNION ALL SELECT n+1 FROM numbers WHERE n<100000
        ) INSERT INTO entities(id,entity_type,subject,description,created_at,updated_at)
        SELECT printf('entity-%06d',n),'fact','Synthetic enumeration','Complete source coverage','2026','2026'
        FROM numbers""")
        conn.commit()
        conn.execute("BEGIN")
        total, maximum, last = 0, 0, None
        for batch in kg._projection_source_batches(conn, 123):
            total += len(batch)
            maximum = max(maximum, len(batch))
            last = batch[-1]["id"]
        assert (total, maximum, last) == (100001, 123, "entity-100000")
    finally:
        conn.close()


def test_normal_dimension_generation_segments_and_reuses_one_query(projection_stack, monkeypatch):
    import numpy as np

    kg, embedding, fingerprint, _config = projection_stack
    fingerprint["dimension"] = 1024
    batches, queries = [], []

    def embed(texts):
        batches.append(len(texts))
        return np.ones((len(texts), 1024), dtype=np.float32)

    def query(text):
        queries.append(text)
        return np.ones(1024, dtype=np.float32)

    monkeypatch.setattr(embedding, "embed_documents", embed)
    monkeypatch.setattr(embedding, "embed_query", query)
    conn = kg._get_conn()
    try:
        conn.executemany("""INSERT INTO entities(id,entity_type,subject,description,created_at,updated_at)
            VALUES(?,'fact',?,'Synthetic normal dimension','2026','2026')""",
                         [(f"entity-{i:06}", f"Fact {i}") for i in range(2001)])
        conn.commit()
    finally:
        conn.close()
    kg.rebuild_index()
    metadata, directory = kg._load_vector_generation(kg._projection_state())
    assert [item["count"] for item in metadata["segments"]] == [2000, 1]
    assert all((directory / item["name"]).stat().st_size < 256 * 1024 * 1024 for item in metadata["segments"])
    assert sum(batches) == 2001 and max(batches) <= 32
    assert len(kg.semantic_search("query", top_k=3)) == 3
    assert queries == ["query"]


def test_repeated_writes_do_not_rebuild_graph_until_read(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    kg._ensure_graph()
    loads = []
    original = kg._load_graph

    def load():
        loads.append(True)
        original()

    monkeypatch.setattr(kg, "_load_graph", load)
    for number in range(20):
        add(kg, f"Fact {number}")
    assert loads == []
    assert kg._ensure_graph().number_of_nodes() == 20
    assert loads == [True]


def test_saved_text_byte_budget_preserves_source_and_previous_generation(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.rebuild_index()
    previous = kg._projection_state()["generation"]
    oversized = ("Synthetic large saved content " * 100).strip()
    kg.update_entity(entity["id"], oversized)
    monkeypatch.setattr(kg, "_PROJECTION_ENTITY_BYTES", 1024)
    embedding.batches.clear()
    with pytest.raises(kg.KnowledgeProjectionIncomplete, match="text budget"):
        kg.rebuild_index()
    assert not embedding.batches
    assert kg._projection_state()["generation"] == previous
    assert kg.get_entity(entity["id"])["description"] == oversized
    assert not kg.memory_vector_status()["ready"]


def test_source_batch_bytes_bound_provider_work(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    for number in range(5):
        entity = add(kg, f"Fact {number}")
        kg.update_entity(entity["id"], "S" * 600)
    monkeypatch.setattr(kg, "_PROJECTION_BATCH_BYTES", 1500)
    kg.rebuild_index()
    assert sum(map(len, embedding.batches)) == 5
    assert all(sum(len(text.encode("utf-8")) for text in batch) <= 1500 for batch in embedding.batches)


def test_transactional_fts_tracks_update_delete_and_reused_rowid(projection_stack):
    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg, "Initial lexical")
    kg.update_entity(entity["id"], "Distinctive replacement vocabulary")
    assert [row["id"] for row in kg.fts_search_entities("Distinctive")] == [entity["id"]]
    kg.delete_entity(entity["id"])
    assert kg.fts_search_entities("Distinctive") == []
    replacement = add(kg, "Newly reused row")
    assert [row["id"] for row in kg.fts_search_entities("reused")] == [replacement["id"]]


def test_context_batch_coalesces_fifty_writes_and_nested_work(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_skip_reindex", False)
    with kg.projection_batch(max_entities=100) as batch:
        for number in range(25):
            add(kg, f"Batch fact {number}")
        with kg.projection_batch() as nested:
            assert nested is batch
            for number in range(25, 50):
                add(kg, f"Batch fact {number}")
        assert embedding.batches == []
        assert kg._projection_state()["generation"] is None
    assert batch.result["complete"]
    assert sum(map(len, embedding.batches)) == 50
    assert max(map(len, embedding.batches)) <= 32
    assert len(list((kg._VECTOR_DIR / "generations").iterdir())) == 1


def test_context_batch_does_not_suppress_another_thread(projection_stack, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    kg, embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_skip_reindex", False)
    with kg.projection_batch() as batch:
        add(kg, "Buffered in caller")
        with ThreadPoolExecutor(max_workers=1) as pool:
            external = pool.submit(add, kg, "Independent thread").result(timeout=10)
        metadata, _ = kg._load_vector_generation(kg._projection_state())
        assert metadata["ids"] == [external["id"]]
        assert sum(map(len, embedding.batches)) == 1
    assert batch.result["complete"]
    assert sum(map(len, embedding.batches)) == 2


def test_failed_context_keeps_saved_work_for_explicit_repair(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_skip_reindex", False)
    with pytest.raises(ValueError, match="synthetic caller failure"):
        with kg.projection_batch() as batch:
            entity = add(kg)
            raise ValueError("synthetic caller failure")
    assert batch.result is None
    assert kg.get_entity(entity["id"])
    assert embedding.batches == []
    assert kg.repair_projections(max_entities=1)["complete"]


def test_ordinary_writes_keep_only_selected_and_previous_owned_generations(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_skip_reindex", False)
    for number in range(50):
        add(kg, f"Ordinary fact {number}")
    directories = list((kg._VECTOR_DIR / "generations").iterdir())
    assert len(directories) == 2
    import json
    assert sorted(len(json.loads((path / "manifest.json").read_text())["ids"]) for path in directories) == [49, 50]
    assert sum(map(len, embedding.batches)) == 50
    assert kg.memory_vector_status()["retirement_pending"] == 0


def test_removal_inside_batch_stays_synchronous(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    removed = add(kg, "Retired document", "document:removed")
    kept = add(kg, "Retained document", "document:kept")
    kg.rebuild_index()
    monkeypatch.setattr(kg, "_skip_reindex", False)
    with kg.projection_batch() as batch:
        add(kg, "New pending source")
        assert kg.delete_entities_by_source("document:removed") == 1
        metadata, _ = kg._load_vector_generation(kg._projection_state())
        assert removed["id"] not in metadata["ids"]
        assert kept["id"] in metadata["ids"]
        assert removed["id"] not in kg._ensure_graph()
        assert all(row["id"] != removed["id"] for row in kg.fts_search_entities("Retired"))
    assert batch.result["complete"]


def test_deferred_batch_leaves_durable_work_for_existing_batch_owner(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_skip_reindex", False)
    with kg.projection_batch(drain_on_exit=False) as batch:
        with kg.projection_batch():
            entity = add(kg)
    assert batch.result is None
    assert kg.get_entity(entity["id"])
    assert embedding.batches == []
    assert kg.repair_projections(max_entities=1)["complete"]


def test_retirement_sweep_bounds_numeric_bytes_and_resumes_validation(projection_stack, monkeypatch):
    from row_bot import flat_vector_storage

    kg, _embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(flat_vector_storage, "MAX_VECTOR_BYTES", 512)
    for number in range(100):
        add(kg, f"Segmented fact {number}")
    kg.rebuild_index()
    with kg._generation_access():
        first = next(kg.iter_entities_snapshot())
        kg.update_entity(first["id"], "First changed segment source")
        kg.rebuild_index()
        kg.update_entity(first["id"], "Second changed segment source")
        kg.rebuild_index()
    pending = kg._retirement_pending()
    assert pending == 1
    reader = kg._read_generation_segment
    bytes_read = []

    def read(directory, segment, dimension):
        bytes_read.append((directory / segment["name"]).stat().st_size)
        return reader(directory, segment, dimension)

    monkeypatch.setattr(kg, "_read_generation_segment", read)
    for _ in range(10):
        bytes_read.clear()
        outcome = kg._retire_vector_generations(max_generations=1)
        assert sum(bytes_read) <= 512
        if outcome["pending"] == 0:
            break
    else:
        pytest.fail("Bounded retirement did not resume its saved progress")
    assert kg.memory_vector_status()["ready"]


def test_document_batch_projection_failure_requires_explicit_resume(tmp_path, monkeypatch):
    from tests.subsystem.knowledge_graph.test_document_extraction_resume import _extracting_job

    jobs, _extraction, service, job = _extracting_job(tmp_path, monkeypatch)
    service.mark_completed(job.id)
    calls = []

    def fail(*, cancelled):
        assert not cancelled()
        calls.append("failed projection")
        raise RuntimeError("Synthetic provider failure")

    monkeypatch.setattr(jobs, "_finalize_shared_knowledge_indexes", fail)
    monkeypatch.setattr(jobs, "_notify_batch_complete", lambda *_args: None)
    monkeypatch.setattr(jobs, "_wake_supervisor", lambda: None)
    supervisor = jobs.DocumentSupervisor(service)
    supervisor._finalize_ready_batches()
    supervisor._finalize_ready_batches()
    assert calls == ["failed projection"]
    assert service.get_batch(job.batch_id).status == "paused"
    assert service.get_job(job.id).status == "completed"
    assert len(service.list_document_records()) == 1
    service.pause_batch(job.batch_id, paused=False)
    monkeypatch.setattr(jobs, "_finalize_shared_knowledge_indexes", lambda **_kw: calls.append("successful projection"))
    supervisor._finalize_ready_batches()
    assert calls == ["failed projection", "successful projection"]
    assert service.get_batch(job.batch_id).status == "completed"


def test_document_batch_with_remaining_projection_work_is_not_completed(tmp_path, monkeypatch):
    from tests.subsystem.knowledge_graph.test_document_extraction_resume import _extracting_job

    jobs, _extraction, service, job = _extracting_job(tmp_path, monkeypatch)
    service.mark_completed(job.id)
    monkeypatch.setattr(jobs, "_finalize_shared_knowledge_indexes", lambda **_kw: False)
    monkeypatch.setattr(jobs, "_notify_batch_complete", lambda *_args: pytest.fail("pending batch cannot announce completion"))
    jobs.DocumentSupervisor(service)._finalize_ready_batches()
    assert service.get_batch(job.batch_id).status in {"queued", "running"}
    assert job.batch_id in service.finalizable_batches()


def test_existing_document_finalizer_drains_bounded_saved_work_without_reextraction(projection_stack):
    from row_bot.document_jobs import _finalize_shared_knowledge_indexes

    kg, embedding, _fingerprint, _config = projection_stack
    conn = kg._get_conn()
    try:
        conn.executemany("""INSERT INTO entities(id,entity_type,subject,description,created_at,updated_at)
            VALUES(?,'fact',?,'Synthetic finalization','2026','2026')""",
                         [(f"entity-{i:06}", f"Final fact {i}") for i in range(1001)])
        conn.commit()
    finally:
        conn.close()
    assert _finalize_shared_knowledge_indexes() is False
    assert sum(map(len, embedding.batches)) == 1000
    assert _finalize_shared_knowledge_indexes() is None
    assert sum(map(len, embedding.batches)) == 1001
    assert kg.memory_vector_status()["ready"]


@pytest.mark.parametrize("recalled", [False, True])
def test_current_projection_repair_has_no_vector_or_fts_rewrites(projection_stack, monkeypatch, recalled):
    import faiss

    kg, embedding, _fingerprint, _config = projection_stack
    entities = [add(kg, f"Current source {number}") for number in range(50)]
    assert kg.repair_projections(max_entities=1000)["complete"]
    head = kg._projection_state()["generation"]
    directory = kg._VECTOR_DIR / "generations" / head
    before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}
    if recalled:
        kg._touch_recalled([entity["id"] for entity in entities])
    embedding.batches.clear()
    monkeypatch.setattr(faiss, "write_index", lambda *_args: pytest.fail("current vectors must not be rewritten"))
    monkeypatch.setattr(kg, "rebuild_fts_index", lambda: pytest.fail("current lexical rows must not be rebuilt"))
    outcome = kg.repair_projections(max_entities=1000)
    assert outcome["complete"] and outcome["pending"] == {"semantic": 0, "wiki": 50}
    assert kg._projection_state()["generation"] == head
    assert {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()} == before
    assert embedding.batches == []
    assert kg.memory_vector_status()["ready"]


def test_noop_check_cannot_acknowledge_a_later_source_edit(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.rebuild_index()
    head = kg._projection_state()["generation"]
    readiness = kg._read_vector_readiness
    changed = []

    def interleaved():
        outcome = readiness()
        if not changed:
            changed.append(True)
            kg.update_entity(entity["id"], "New semantic source after no-op validation")
        return outcome

    monkeypatch.setattr(kg, "_read_vector_readiness", interleaved)
    embedding.batches.clear()
    kg.rebuild_index()
    assert kg._projection_state()["generation"] != head
    assert sum(map(len, embedding.batches)) == 1
    assert kg.memory_vector_status()["ready"]


@pytest.mark.parametrize("damage", ["changed_text", "extra_row", "missing_row"])
def test_explicit_repair_restores_mismatched_lexical_projection(projection_stack, damage):
    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.repair_projections()
    conn = kg._get_conn()
    try:
        if damage == "changed_text":
            conn.execute("UPDATE entities_fts SET description='Incorrect lexical content'")
        elif damage == "extra_row":
            conn.execute("INSERT INTO entities_fts(entity_id,description) VALUES('foreign','Unowned lexical content')")
        else:
            conn.execute("DELETE FROM entities_fts")
        conn.commit()
    finally:
        conn.close()
    assert not kg._lexical_projection_current()
    assert kg.repair_projections()["complete"]
    assert kg._lexical_projection_current()
    assert kg.fts_search_entities("deterministic")[0]["id"] == entity["id"]
