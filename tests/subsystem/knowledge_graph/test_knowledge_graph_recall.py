from __future__ import annotations

import pytest

from tests.fixtures.knowledge_graph import fresh_knowledge_graph


pytestmark = pytest.mark.subsystem


def test_graph_recall_ranks_semantic_seed_before_neighbors(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice coordinates Row-Bot subsystem tests.", tags="testing", source="test")
    project = kg.save_entity("project", "Row-Bot", "Row-Bot is the local-first assistant project.", source="test")
    kg.add_relation(alice["id"], project["id"], "works_on", confidence=0.8, source="test")
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [{**alice, "score": 0.95}])

    results = kg.retrieve_memory_candidates(
        "Who coordinates Row-Bot subsystem tests?",
        top_k=1,
        threshold=0.3,
        hops=1,
        max_results=5,
        include_keyword=False,
    )

    assert [result["subject"] for result in results[:2]] == ["Alice", "Row-Bot"]
    assert results[0]["via"] == "semantic"
    assert results[1]["via"] == "graph"
    assert results[1]["relations"][0]["type"] == "works_on"
    assert results[0]["score"] > results[1]["score"]


def test_keyword_recall_falls_back_when_semantic_search_is_empty(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    kg.save_entity("preference", "Tea", "The user prefers oolong tea during late work.", tags="drink", source="test")
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])

    results = kg.retrieve_memory_candidates("oolong tea", include_keyword=True, max_results=3)

    assert results
    assert results[0]["subject"] == "Tea"
    assert results[0]["via"] in {"fts", "keyword", "hybrid"}
