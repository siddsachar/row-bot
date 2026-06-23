from __future__ import annotations

import json

import pytest

from tests.fixtures.knowledge_graph import fresh_knowledge_graph


pytestmark = pytest.mark.subsystem


def test_relation_contract_normalizes_aliases_and_rejects_unsafe_edges(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice leads the project.", source="test")
    project = kg.save_entity("project", "Row-Bot", "A local-first assistant.", source="test")

    relation = kg.add_relation(
        alice["id"],
        project["id"],
        "works_for",
        confidence=1.5,
        properties={"evidence": "fixture"},
        source="test",
    )

    assert relation is not None
    assert relation["relation_type"] == "employed_by"
    assert relation["confidence"] == 1.0
    assert json.loads(relation["properties"]) == {"evidence": "fixture"}
    assert kg.add_relation(alice["id"], project["id"], "works_for") is None
    assert kg.add_relation(alice["id"], alice["id"], "knows") is None
    assert kg.add_relation(alice["id"], project["id"], "related_to") is None


def test_graph_neighbors_paths_and_delete_cleanup_relations(tmp_path, monkeypatch) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice manages Bob.", source="test")
    bob = kg.save_entity("person", "Bob", "Bob works on Row-Bot.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project entity.", source="test")
    kg.add_relation(alice["id"], bob["id"], "manages", source="test")
    kg.add_relation(bob["id"], project["id"], "works_on", source="test")

    neighbors = kg.get_neighbors(alice["id"], hops=2)
    path = kg.get_shortest_path(alice["id"], project["id"])
    subgraph = kg.get_subgraph(bob["id"], hops=1)

    assert [entity["subject"] for entity in neighbors] == ["Bob", "Row-Bot"]
    assert [entity["subject"] for entity in path] == ["Alice", "Bob", "Row-Bot"]
    assert {edge["relation_type"] for edge in subgraph["edges"]} == {"manages", "works_on"}

    assert kg.delete_entity(bob["id"]) is True
    assert kg.count_relations() == 0
    assert kg.get_shortest_path(alice["id"], project["id"]) is None
