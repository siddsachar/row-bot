from __future__ import annotations

import json

import pytest

from tests.fixtures.knowledge_graph import fresh_knowledge_graph


pytestmark = pytest.mark.subsystem


def test_json_relation_fts_and_surrogate_helpers(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)

    assert kg.extract_json_block('prefix {"a": ["[not close]", {"b": 2}]} suffix', "{") == '{"a": ["[not close]", {"b": 2}]}'
    assert kg.extract_json_block("[1, 2") is None
    assert kg.normalize_relation_type("is_father_of") == "father_of"
    assert kg.normalize_relation_type("works for") == "employed_by"
    assert kg.normalize_relation_type("unknown relation") == "unknown_relation"
    assert kg._sanitize_text("ok\ud800bad") == "okbad"
    assert kg._fts_match_query("Alice's tests, Row-Bot!") == '"alice" OR "tests" OR "row-bot"'
    assert kg._fts_match_query("a the to") == ""

    entity = kg.save_entity("person", "FTS Alice", "Alice writes lexical subsystem tests.", tags="lexical", source="test")
    indexed = kg.rebuild_fts_index()
    if indexed:
        assert kg.fts_search_entities("lexical subsystem", limit=5)[0]["id"] == entity["id"]

    monkeypatch.setattr(kg, "_ensure_fts", lambda _conn=None: False)
    assert kg.fts_search_entities("lexical subsystem", limit=5) == []


def test_entity_crud_summaries_subjects_and_source_prefix_deletion(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(kg, "rebuild_index", lambda: None)

    alice = kg.save_entity(
        "person",
        "Alice Edge",
        "Alice writes tests.",
        aliases="Ally Edge",
        tags="testing,edge",
        properties={"confidence": 0.8},
        source="live",
    )
    doc = kg.save_entity("project", "Doc Edge", "Document-sourced project.", source="document:one.md")
    other_doc = kg.save_entity("project", "Other Doc Edge", "Other document.", source="document:two.md")
    relation = kg.add_relation(alice["id"], doc["id"], "works_on", source="document:one.md")
    kg.add_relation(alice["id"], other_doc["id"], "cites", source="document:two.md")

    updated = kg.update_entity(
        alice["id"],
        "Alice writes many deterministic tests.",
        subject="Alice Updated Edge",
        aliases="Ally Edge, A. Edge",
        tags="testing,updated",
        properties={"confidence": 0.9},
    )

    assert updated is not None
    assert kg.find_by_subject(None, "A. Edge")["id"] == alice["id"]
    assert kg.find_by_subject("project", "A. Edge") is None
    assert json.loads(kg.get_entity(alice["id"])["properties"]) == {"confidence": 0.9}
    assert [entity["subject"] for entity in kg.search_entities("deterministic")] == ["Alice Updated Edge"]
    summaries = {row["subject"]: row["description"] for row in kg.list_entity_summaries(description_chars=12)}
    assert summaries["Other Doc Edge"] == "Other docume"
    assert kg.list_entity_subjects([alice["id"], "", alice["id"], "missing"]) == {alice["id"]: "Alice Updated Edge"}
    assert kg.count_entities() == 3
    assert relation is not None
    assert kg.delete_entities_by_source_prefix("document:") == 2
    assert kg.get_entity(doc["id"]) is None
    assert kg.get_entity(other_doc["id"]) is None
    assert kg.count_relations() == 0
    assert kg.delete_entities_by_source("missing") == 0


def test_semantic_duplicate_and_recall_touch_paths_are_deterministic(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice Recall", "Alice coordinates recall tests.", source="test")
    project = kg.save_entity("project", "Recall Project", "Project linked to Alice.", source="test")
    kg.add_relation(alice["id"], project["id"], "works_on", confidence=0.9, source="test")

    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [{**alice, "score": 0.96}])
    assert kg.find_duplicate("person", "Alice Recall", "Alice coordinates recall tests.")["id"] == alice["id"]

    candidates = kg.retrieve_memory_candidates("Alice Recall", include_keyword=False, hops=1, max_results=5)
    assert [candidate["subject"] for candidate in candidates[:2]] == ["Alice Recall", "Recall Project"]
    assert candidates[0]["via"] in {"semantic", "hybrid"}
    assert candidates[1]["via"] in {"graph", "hybrid"}
    assert candidates[1]["relations"][0]["type"] == "works_on"

    before = json.loads(kg.get_entity(alice["id"])["properties"])
    assert "recalled_at" not in before
    recalled = kg.graph_enhanced_recall("Alice Recall", max_results=1)
    after = json.loads(kg.get_entity(recalled[0]["id"])["properties"])
    assert "recalled_at" in after


def test_graph_stats_mermaid_vis_json_delete_all_and_consolidation(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    monkeypatch.setattr(kg, "rebuild_index", lambda: None)
    monkeypatch.setattr(kg, "_upsert_index", lambda _entity_id: None)
    monkeypatch.setattr(kg, "_remove_from_index", lambda _entity_id: None)
    monkeypatch.setattr("row_bot.wiki_vault.clear_wiki_folder", lambda: None)

    alice_short = kg.save_entity("person", "Alice Merge", "Alice.", aliases="A1", tags="short", properties={"old": True})
    alice_long = kg.save_entity(
        "person",
        "Alice Merge",
        "Alice Merge writes deterministic knowledge graph tests.",
        aliases="A2",
        tags="long",
        properties={"new": True},
    )
    project = kg.save_entity("project", "Graph Project", "Project target.")
    kg.add_relation(alice_short["id"], project["id"], "works_on", source="test")

    def fake_semantic(query, top_k=5, threshold=0.9):
        if "Alice Merge" in query:
            return [{**alice_long, "score": 0.99}, {**alice_short, "score": 0.99}]
        return []

    monkeypatch.setattr(kg, "semantic_search", fake_semantic)

    assert kg.consolidate_duplicates(threshold=0.9) == 1
    survivor = kg.find_by_subject(None, "Alice Merge")
    assert survivor["id"] == alice_long["id"]
    assert set(survivor["aliases"].split(", ")) == {"A1", "A2"}
    assert set(survivor["tags"].split(", ")) == {"short", "long"}
    assert kg.get_relations(survivor["id"], direction="outgoing")[0]["peer_id"] == project["id"]

    stats = kg.get_graph_stats()
    mermaid = kg.to_mermaid(survivor["id"], hops=1)
    vis = kg.graph_to_vis_json(survivor["id"], hops=1)

    assert stats["total_entities"] == 2
    assert stats["total_relations"] == 1
    assert "Alice Merge (person)" in mermaid
    assert vis["center"] == survivor["id"]
    assert vis["stats"]["shown_nodes"] == 2
    assert vis["edges"][0]["label"] == "works_on"

    assert kg.delete_all_entities() == 2
    assert kg.graph_to_vis_json()["stats"]["total_entities"] == 0
    assert kg.to_mermaid() == "graph LR"
