from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from tests.subsystem.knowledge_graph.test_knowledge_projection_recovery import add, projection_stack as _projection_stack

projection_stack = _projection_stack
pytestmark = pytest.mark.subsystem


def _saved_projection_queue(kg):
    """The durable revision and projection work a knowledge change leaves behind."""
    with closing(sqlite3.connect(kg.DB_PATH)) as conn:
        revision = conn.execute("SELECT revision FROM knowledge_projection_state").fetchone()[0]
        work = conn.execute("""SELECT entity_id,revision,semantic_pending,wiki_pending
            FROM knowledge_projection_work ORDER BY entity_id""").fetchall()
    return revision, work


def _during_coverage_check(kg, monkeypatch, action):
    """Run ``action`` once while a recall is part-way through its coverage check."""
    batches = kg._projection_source_batches
    fired = []

    def interleaved(conn, size):
        for batch in batches(conn, size):
            if not fired:
                fired.append(True)
                action()
            yield batch

    monkeypatch.setattr(kg, "_projection_source_batches", interleaved)
    return fired


def test_recall_touch_is_not_a_knowledge_change(projection_stack):
    kg, embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.rebuild_index()
    before = _saved_projection_queue(kg)
    count = len(embedding.batches)
    kg.touch_recalled([entity["id"]])
    assert _saved_projection_queue(kg) == before
    assert kg.get_entity(entity["id"]) == entity
    assert kg.recall_stamps([entity["id"]])[entity["id"]]
    assert kg.memory_vector_status()["ready"]
    kg.repair_projections()
    assert len(embedding.batches) == count


def test_concurrent_recalls_with_touches_both_use_the_vector_index(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg, "Oolong tea")
    add(kg, "Green tea")
    kg.rebuild_index()
    inner: dict = {}

    def other_agent_recalls():
        found = kg.retrieve_memory_candidates("tea", diagnostics=inner)
        kg.touch_recalled([memory["id"] for memory in found])

    fired = _during_coverage_check(kg, monkeypatch, other_agent_recalls)
    outer: dict = {}
    found = kg.retrieve_memory_candidates("tea", diagnostics=outer)
    kg.touch_recalled([memory["id"] for memory in found])
    assert fired
    assert inner["semantic_status"] == "used"
    assert outer["semantic_status"] == "used", outer


def test_real_edit_during_coverage_check_still_falls_back(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg, "Oolong tea")
    kg.rebuild_index()
    _during_coverage_check(kg, monkeypatch, lambda: kg.update_entity(
        entity["id"], "Oolong tea is brewed at a different temperature now."))
    diagnostics: dict = {}
    kg.retrieve_memory_candidates("tea", diagnostics=diagnostics)
    assert diagnostics["semantic_status"] == "fallback"
    assert diagnostics["semantic_fallback_code"] == "memory_index_pending"


def test_relation_change_during_coverage_check_retries_and_uses_the_index(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    first, second = add(kg, "Oolong tea"), add(kg, "Green tea")
    kg.rebuild_index()
    added = []
    _during_coverage_check(kg, monkeypatch, lambda: added.append(kg.add_relation(
        first["id"], second["id"], "part_of", source="test")))
    diagnostics: dict = {}
    kg.retrieve_memory_candidates("tea", diagnostics=diagnostics)
    assert added and added[0] is not None
    assert diagnostics["semantic_status"] == "used", diagnostics


def test_model_switch_and_single_update_never_claim_complete_library(projection_stack):
    kg, _embedding, fingerprint, _config = projection_stack
    first, second = add(kg, "First"), add(kg, "Second")
    kg.rebuild_index()
    fingerprint["model"] = "review-v2"
    kg._upsert_index(first["id"])
    assert not kg.memory_vector_status()["ready"]
    assert kg.get_entity(second["id"])
    kg.rebuild_index()
    assert kg.memory_vector_status()["ready"]


def test_same_count_foreign_coverage_does_not_pass_readiness(projection_stack):
    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg)
    kg.rebuild_index()
    selected = kg._VECTOR_DIR / "generations" / kg._projection_state()["generation"] / "manifest.json"
    value = json.loads(selected.read_text())
    value["ids"] = ["foreign-identity"]
    selected.write_text(json.dumps(value))
    assert not kg.memory_vector_status()["ready"]


def test_recall_keeps_coverage_and_a_property_change_needs_new_vectors(projection_stack):
    kg, embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.rebuild_index()
    count = len(embedding.batches)
    kg.touch_recalled([entity["id"]])
    assert kg.memory_vector_status()["ready"]
    kg._upsert_index(entity["id"])
    assert len(embedding.batches) == count
    kg.update_entity(entity["id"], entity["description"], properties={"meaningful": "changed"})
    assert not kg.memory_vector_status()["ready"]
    kg.rebuild_index()
    assert len(embedding.batches) == count + 1


def test_pending_semantics_keep_lexical_recall_without_model_loading(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg, "Oolong tea")
    monkeypatch.setattr(kg, "_get_embedding_model", lambda **_kw: pytest.fail("passive recall must not rebuild"))
    with pytest.raises(kg.MemorySemanticUnavailable):
        kg.semantic_search("Oolong tea", for_auto_recall=True)
    assert any(row["id"] == entity["id"] for row in kg.fts_search_entities("oolong"))


@pytest.mark.parametrize("operation", ["rebuild", "manual_query", "auto_query"])
def test_captured_config_is_bound_through_factory_aba(projection_stack, monkeypatch, operation):
    kg, embedding, _fingerprint, config = projection_stack
    config["selected"] = "A"
    add(kg)
    if operation != "rebuild":
        kg.rebuild_index()
    captures = []

    def factory(*, config=None, for_auto_recall=False):
        # Settings change during factory admission; the provider must still be
        # built for the exact configuration captured with generation metadata.
        projection_stack[3]["selected"] = "B"
        captures.append((config["selected"], for_auto_recall))
        projection_stack[3]["selected"] = "A"
        return embedding

    monkeypatch.setattr(kg, "_get_embedding_model", factory)
    if operation == "rebuild":
        kg.rebuild_index()
    else:
        assert kg.semantic_search("Synthetic", for_auto_recall=operation == "auto_query")
    assert captures == [("A", operation == "auto_query")]


def test_internal_batch_queries_only_unchanged_prior_candidates_without_rebuild(projection_stack):
    kg, embedding, _fingerprint, _config = projection_stack
    changed = add(kg, "Changed prior candidate")
    unchanged = add(kg, "Existing synonym candidate")
    kg.rebuild_index()
    embedding.batches.clear()
    with kg.projection_batch(drain_on_exit=False):
        kg.update_entity(changed["id"], "New semantic meaning must not use prior vector")
        added = add(kg, "New source still pending")
        matches = kg.semantic_search("A synonym for existing saved information", top_k=10, threshold=0)
        assert [entity["id"] for entity in matches] == [unchanged["id"]]
        assert added["id"] not in [entity["id"] for entity in matches]
        assert not kg.memory_vector_status()["ready"]
        assert embedding.batches == []
    with pytest.raises(kg.MemorySemanticUnavailable):
        kg.semantic_search("Normal automatic recall remains strict", for_auto_recall=True)
    assert embedding.batches == []


def test_internal_batch_with_no_prior_generation_never_starts_provider(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    monkeypatch.setattr(kg, "_get_embedding_model", lambda **_kw: pytest.fail("batch query must not repair"))
    with kg.projection_batch(drain_on_exit=False):
        add(kg)
        with pytest.raises(kg.MemorySemanticUnavailable):
            kg.semantic_search("New source requires explicit projection")
