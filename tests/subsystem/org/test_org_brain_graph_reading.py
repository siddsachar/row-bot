"""How the company knowledge graph reads a chat model's answer (``row_bot_org.brain.graph.read_answer``): strictly,
so junk gives nothing, with the app's relation normalisation, dedupe by normalised name and type, and the per-part
caps. No database or model."""

from __future__ import annotations

import json

import pytest

from row_bot_org.brain import graph


def entity(category: str, subject: str, **extra: object) -> dict:
    return {"category": category, "subject": subject, "content": extra.pop("content", ""), **extra}


def relation(label: str, source: str, target: str, **extra: object) -> dict:
    return {"relation_type": label, "source_subject": source, "target_subject": target, **extra}


@pytest.mark.parametrize("answer", [
    "Sure! Here are the entities: Atlas and Priya.",
    '{"entities": []}',
    "[1, 2]",
    json.dumps([{"category": "person"}]),
    json.dumps([entity("person", 7)]),
    json.dumps([entity("person", "Priya", content=None)]),
    json.dumps([entity("person", "Priya", aliases=[1])]),
    json.dumps([relation("uses", "A", "B", confidence="high")]),
    json.dumps([relation("uses", "A", "B", confidence=True)]),
    "```json\n[]",
    "Here you go:\n```json\n[]\n```",
    "[" + " " * graph.MAX_ANSWER_CHARACTERS + "]",
    "[" * 60_000,
    json.dumps([relation("uses", "A", "B", confidence=10 ** 400)]),
    json.dumps([relation("uses", "A", "B", confidence=1.5)]),
    '[{"relation_type": "uses", "source_subject": "A", "target_subject": "B", "confidence": NaN}]',
], ids=["prose", "object", "numbers", "half-entity", "number-name", "null-content", "number-alias",
        "word-confidence", "bool-confidence", "open-fence", "prose-and-fence", "too-long", "too-deep",
        "huge-confidence", "over-one", "nan"])
def test_an_answer_that_isnt_the_array_asked_for_is_refused_whole(answer: str) -> None:
    with pytest.raises(graph.JunkAnswer):
        graph.read_answer(answer)


def test_a_sound_answer_keeps_company_entities_and_labelled_relations_between_them() -> None:
    found = graph.read_answer("```json\n" + json.dumps([
        entity("person", "Priya", content="Leads\n the  Atlas project."),
        entity("Project", "  Atlas ", aliases="ATL, atlas, ATL"),
        entity("organization", "Globex"),
        entity("preference", "Coffee"),
        entity("person", "x" * (graph.MAX_NAME + 1)),
        entity("person", "\u00df" * graph.MAX_NAME),  # Its compared form, "ss" for each, is too long.
        entity("project", "ATLAS", aliases=["Payments"]),
        relation("led_by", "Atlas", "Priya"),
        relation("Customer Of", "Globex", "atlas"),
        relation("associated_with", "Priya", "Globex"),
        relation("invents", "Priya", "Globex"),
        relation("uses", "Atlas", "Atlas"),
        relation("uses", "Atlas", "Coffee"),
        relation("works_on", "Priya", "Atlas", confidence=0.5),
        relation("leads", "Priya", "Atlas"),
    ]) + "\n```")
    assert [(e.name, e.entity_type, e.description, e.aliases) for e in found.entities] == [
        ("Priya", "person", "Leads the Atlas project.", ()),
        ("Atlas", "project", "", ("ATL", "Payments")),
        ("Globex", "organisation", "", ()),
    ]
    assert [(r.source, r.relation_type, r.target) for r in found.relations] == [
        ("priya", "leads", "atlas"), ("globex", "customer_of", "atlas"), ("priya", "related_to", "globex")]


def test_a_part_gives_at_most_so_many_entities_and_relations() -> None:
    names = [f"Team {n}" for n in range(graph.MAX_ENTITIES + 5)]
    pairs = [(a, b) for a in names[:graph.MAX_ENTITIES] for b in names[:graph.MAX_ENTITIES] if a < b]
    found = graph.read_answer(json.dumps([entity("team", name) for name in names]
                                         + [relation("part_of", a, b) for a, b in pairs]))
    assert [e.name for e in found.entities] == names[:graph.MAX_ENTITIES]
    assert len(found.relations) == graph.MAX_RELATIONS
    assert graph.read_answer("[]") == graph.NOTHING


@pytest.mark.parametrize(("label", "expected"), [
    ("leads", ("leads", False)), ("Led By", ("leads", True)), ("works-for", ("member_of", False)),
    ("is_part_of", ("part_of", False)), ("has_owns", ("owns", False)), ("owned_by", ("owns", True)),
    ("connected_to", ("related_to", False)), ("mentors", ("related_to", False)),
])
def test_relation_labels_are_normalised_to_the_vocabulary(label: str, expected: tuple[str, bool]) -> None:
    assert graph.canonical_relation(label) == expected
