"""A relation label that reads the other way is saved the right way round."""

from __future__ import annotations

import pytest

from tests.fixtures.knowledge_graph import fresh_knowledge_graph


pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize("label, canonical", [
    ("written_by", "authored"),
    ("published_by", "authored"),
    ("founded_by", "founded"),
    ("used_by", "uses"),
    ("owned_by", "owns"),
])
def test_a_passive_label_swaps_the_two_ends(tmp_path, monkeypatch, reload_for_data_dir, label, canonical) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch, reload_for_data_dir)
    book = kg.save_entity("media", "Atlas Notes", "A book.", source="test")
    alice = kg.save_entity("person", "Alice", "A writer.", source="test")

    relation = kg.add_relation(book["id"], alice["id"], label)

    assert relation["relation_type"] == canonical
    assert (relation["source_id"], relation["target_id"]) == (alice["id"], book["id"])
    outgoing = kg.get_relations(alice["id"], direction="outgoing")
    assert [(r["relation_type"], r["peer_subject"]) for r in outgoing] == [(canonical, "Atlas Notes")]


def test_an_active_label_keeps_its_direction(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch, reload_for_data_dir)
    alice = kg.save_entity("person", "Alice", "A writer.", source="test")
    atlas = kg.save_entity("project", "Atlas", "A project.", source="test")

    made = kg.add_relation(atlas["id"], alice["id"], "made_by")
    created = kg.add_relation(alice["id"], atlas["id"], "created")

    # "Atlas made_by Alice" and "Alice created Atlas" say the same thing: Atlas created_by Alice.
    assert (made["source_id"], made["target_id"], made["relation_type"]) == (atlas["id"], alice["id"], "created_by")
    assert created is None  # the same edge, already saved
    assert kg.count_relations() == 1


def test_extraction_saves_a_passive_relation_the_right_way_round(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch, reload_for_data_dir)
    (extraction,) = reload_for_data_dir(tmp_path / "data", "row_bot.memory_extraction")
    monkeypatch.setattr(kg, "semantic_search", lambda *args, **kwargs: [])

    extraction._dedup_and_save([
        {"category": "media", "subject": "Field Guide", "content": "A guide to local birds."},
        {"category": "person", "subject": "Maya", "content": "The user's aunt, an ornithologist."},
        {"relation_type": "written_by", "source_subject": "Field Guide", "target_subject": "Maya", "confidence": 0.9},
    ])

    maya = kg.find_by_subject(None, "Maya")
    assert [(r["relation_type"], r["peer_subject"]) for r in kg.get_relations(maya["id"], direction="outgoing")] == [
        ("authored", "Field Guide"),
    ]


def test_tense_variants_of_one_verb_are_one_label(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    kg = fresh_knowledge_graph(tmp_path, monkeypatch, reload_for_data_dir)

    assert {kg.normalize_relation_type(label) for label in ("plans", "planning", "planned")} == {"plans"}
    assert kg.normalize_relation_type("preparing_for") == kg.normalize_relation_type("prepares_for")
    assert kg.normalize_relation_type("working-on") == "works_on"
