from __future__ import annotations

import importlib
import json

import pytest


pytestmark = pytest.mark.subsystem


def fresh_dream_and_kg(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    import row_bot.knowledge_graph as kg
    import row_bot.dream_cycle as dream_cycle

    kg = importlib.reload(kg)
    kg._skip_reindex = True
    dream_cycle = importlib.reload(dream_cycle)
    return dream_cycle, kg


def test_duplicate_merge_repoints_relations_and_deletes_duplicate(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    survivor = kg.save_entity("person", "Alice", "Alice writes tests.", aliases="A.", source="test")
    duplicate = kg.save_entity("person", "Alice", "Alice writes deterministic subsystem tests.", aliases="Al", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    kg.add_relation(duplicate["id"], project["id"], "works_on", source="test")
    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt: "Alice writes deterministic subsystem tests for Row-Bot.",
    )

    result = dream_cycle._merge_entities(survivor, duplicate)

    assert result is not None
    assert result["survivor_id"] == survivor["id"]
    assert kg.get_entity(duplicate["id"]) is None
    updated = kg.get_entity(survivor["id"])
    assert updated["description"] == "Alice writes deterministic subsystem tests for Row-Bot."
    assert set(part.strip() for part in updated["aliases"].split(",")) == {"A.", "Al"}
    assert kg.get_relations(survivor["id"], direction="outgoing")[0]["relation_type"] == "works_on"


def test_enrichment_accepts_grounded_update_and_rejects_cross_entity_contamination(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice likes tea.", source="test")
    excerpts = ["Alice likes tea and writes tests for Row-Bot during late work."]

    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt: "Alice likes tea and writes tests for Row-Bot during late work.",
    )

    result = dream_cycle._enrich_entity(alice, excerpts, other_subjects={"Bob", "Row-Bot"})

    assert result is not None
    assert result["new_length"] > result["old_length"]
    assert kg.get_entity(alice["id"])["description"] == "Alice likes tea and writes tests for Row-Bot during late work."

    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt: "Alice likes tea. Bob owns unrelated deployment decisions.",
    )
    assert dream_cycle._enrich_entity(alice, excerpts, other_subjects={"Bob"}) is None


def test_relation_inference_accepts_high_confidence_json_and_tracks_evidence(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice writes tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt: json.dumps(
            {
                "has_relation": True,
                "relation_type": "works_on",
                "source": "Alice",
                "target": "Row-Bot",
                "confidence": 0.91,
                "evidence": "Alice writes tests for Row-Bot.",
            }
        ),
    )

    result = dream_cycle._infer_relation(alice, project, "Alice writes tests for Row-Bot.", co_occurrence_count=2)
    relation = kg.get_relations(alice["id"], direction="outgoing")[0]

    assert result is not None
    assert result["relation_type"] == "works_on"
    assert result["confidence"] == 0.91
    assert relation["source"] == "dream_infer"
    assert json.loads(relation["properties"]) == {"evidence": "Alice writes tests for Row-Bot.", "co_occurrences": 2}


@pytest.mark.parametrize(
    "payload",
    [
        {"has_relation": True, "relation_type": "related_to", "confidence": 0.95},
        {"has_relation": True, "relation_type": "works_on", "confidence": 0.5},
        {"has_relation": False, "relation_type": "works_on", "confidence": 0.95},
    ],
)
def test_relation_inference_rejects_vague_low_confidence_or_negative_results(tmp_path, monkeypatch, payload) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice writes tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt: json.dumps(payload))

    assert dream_cycle._infer_relation(alice, project, "Alice and Row-Bot appear together.") is None
    assert kg.get_relations(alice["id"]) == []
