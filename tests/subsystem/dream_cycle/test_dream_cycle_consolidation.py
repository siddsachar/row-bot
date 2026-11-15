from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.subsystem


def fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir):
    import row_bot.knowledge_graph as kg
    import row_bot.dream_cycle as dream_cycle

    (kg,) = reload_for_data_dir(tmp_path / "row-bot-data", "row_bot.knowledge_graph")
    kg._skip_reindex = True
    (dream_cycle,) = reload_for_data_dir(tmp_path / "row-bot-data", "row_bot.dream_cycle")
    return dream_cycle, kg


def test_duplicate_merge_repoints_relations_and_deletes_duplicate(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    survivor = kg.save_entity("person", "Alice", "Alice writes tests.", aliases="A.", source="test")
    duplicate = kg.save_entity("person", "Alice", "Alice writes deterministic subsystem tests.", aliases="Al", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    kg.add_relation(duplicate["id"], project["id"], "works_on", source="test")
    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt, **_kwargs: "Alice writes deterministic subsystem tests for Row-Bot.",
    )

    result = dream_cycle._merge_entities(survivor, duplicate)

    assert result is not None
    assert result["survivor_id"] == survivor["id"]
    assert kg.get_entity(duplicate["id"]) is None
    updated = kg.get_entity(survivor["id"])
    assert updated["description"] == "Alice writes deterministic subsystem tests for Row-Bot."
    assert set(part.strip() for part in updated["aliases"].split(",")) == {"A.", "Al"}
    assert kg.get_relations(survivor["id"], direction="outgoing")[0]["relation_type"] == "works_on"


def test_enrichment_accepts_grounded_update_and_rejects_cross_entity_contamination(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    alice = kg.save_entity("person", "Alice", "Alice likes tea.", source="test")
    excerpts = ["Alice likes tea and writes tests for Row-Bot during late work."]

    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt, **_kwargs: "Alice likes tea and writes tests for Row-Bot during late work.",
    )

    # Row-Bot is named in the excerpts, so naming it is not contamination.
    result = dream_cycle._enrich_entity(alice, excerpts, other_names={"bob", "row-bot"})

    assert result is not None
    assert result["new_length"] > result["old_length"]
    assert kg.get_entity(alice["id"])["description"] == "Alice likes tea and writes tests for Row-Bot during late work."

    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt, **_kwargs: "Alice likes tea. Bob owns unrelated deployment decisions.",
    )
    assert dream_cycle._enrich_entity(alice, excerpts, other_names={"bob"}) is None


def test_relation_inference_accepts_high_confidence_json_and_tracks_evidence(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    alice = kg.save_entity("person", "Alice", "Alice writes tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt, **_kwargs: json.dumps(
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
def test_relation_inference_rejects_vague_low_confidence_or_negative_results(tmp_path, monkeypatch, payload, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    alice = kg.save_entity("person", "Alice", "Alice writes tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt, **_kwargs: json.dumps(payload))

    assert dream_cycle._infer_relation(alice, project, "Alice and Row-Bot appear together.") is None
    assert kg.get_relations(alice["id"]) == []


class _DreamModel:
    """The default chat model as Dream Cycle sees it: records each request and answers in order."""

    def __init__(self, replies: list[str], requests: list[dict] | None = None, bound: dict | None = None) -> None:
        self.replies = replies
        self.requests = [] if requests is None else requests
        self.bound = dict(bound or {})

    def bind(self, **kwargs):
        return _DreamModel(self.replies, self.requests, {**self.bound, **kwargs})

    def invoke(self, _messages):
        from langchain_core.messages import AIMessage

        self.requests.append(dict(self.bound))
        return AIMessage(content=self.replies.pop(0))


@pytest.fixture
def dream_model(monkeypatch):
    from row_bot import models
    from row_bot.providers import background

    model = _DreamModel([])
    asked: list[str] = []

    def get_llm_for(ref, num_ctx=None, *, reasoning_plan=None):
        asked.append(ref)
        return model

    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(models, "get_llm_for", get_llm_for)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    return model, asked


def test_dream_merge_reads_a_schema_description_and_never_stores_a_cut_short_one(
        tmp_path, monkeypatch, dream_model, reload_for_data_dir) -> None:
    """WP C1: Dream asks the default model for ``{"description": ...}`` with a bounded reply."""
    from row_bot.providers import background

    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    model, asked = dream_model
    survivor = kg.save_entity("person", "Alice", "Alice writes tests.", source="test")
    duplicate = kg.save_entity("person", "Alice", "Alice writes deterministic subsystem tests.", source="test")
    model.replies.extend(['{"description": "Alice writes deterministic subsys',
                          '{"description": "Alice writes deterministic subsystem tests."}'])

    assert dream_cycle._merge_entities(survivor, duplicate) is None
    assert kg.get_entity(duplicate["id"]) is not None

    assert dream_cycle._merge_entities(survivor, duplicate) is not None
    assert kg.get_entity(survivor["id"])["description"] == "Alice writes deterministic subsystem tests."
    assert asked == ["model:openai:gpt-5", "model:openai:gpt-5"]
    request = model.requests[-1]
    assert request["max_tokens"] == background.ANSWER_TOKENS["dream"] + background.REASONING_HEADROOM_TOKENS
    assert request["response_format"]["json_schema"]["schema"]["required"] == ["description"]


def test_dream_inference_asks_for_its_relation_schema_with_a_small_cap(tmp_path, monkeypatch, dream_model, reload_for_data_dir) -> None:
    from row_bot.providers import background

    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    model, _asked = dream_model
    alice = kg.save_entity("person", "Alice", "Alice writes tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project under test.", source="test")
    model.replies.append(json.dumps({"has_relation": True, "relation_type": "works_on", "source": "Alice",
                                     "target": "Row-Bot", "confidence": 0.92,
                                     "evidence": "Alice writes tests for Row-Bot."}))

    result = dream_cycle._infer_relation(alice, project, "Alice writes tests for Row-Bot.")

    assert result is not None and result["relation_type"] == "works_on"
    [request] = model.requests
    assert request["max_tokens"] == dream_cycle._INFER_MAX_TOKENS + background.REASONING_HEADROOM_TOKENS
    assert set(request["response_format"]["json_schema"]["schema"]["required"]) == {
        "has_relation", "relation_type", "source", "target", "confidence", "evidence"}
