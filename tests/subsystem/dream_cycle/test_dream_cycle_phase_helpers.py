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


def test_find_merge_candidates_uses_similarity_type_and_subject_guards(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    alice = {"id": "a", "entity_type": "person", "subject": "Alice", "description": "Writes tests"}
    bob = {"id": "b", "entity_type": "person", "subject": "Bob", "description": "Also writes tests"}
    project = {"id": "p", "entity_type": "project", "subject": "Alice", "description": "Wrong type"}
    user = {"id": "u", "entity_type": "person", "subject": "User", "description": "Never merge"}

    def fake_semantic_search(_text, top_k, threshold):
        assert top_k == 5
        assert threshold == 0.93
        return [
            {**bob, "score": 0.97},
            {**project, "score": 0.99},
            {**alice, "score": 1.0},
        ]

    monkeypatch.setattr(kg, "_entity_text", lambda entity: entity["description"])
    monkeypatch.setattr(kg, "semantic_search", fake_semantic_search)

    assert dream_cycle._find_merge_candidates([alice, user], threshold=0.93) == []

    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [{**bob, "score": 0.99}])
    candidates = dream_cycle._find_merge_candidates([alice, bob], threshold=0.93)
    assert candidates == [(alice, {**bob, "score": 0.99}, 0.99)]


def test_enrichment_input_helpers_filter_merged_and_noisy_entities(tmp_path, monkeypatch) -> None:
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    entities = [
        {"id": "old", "subject": "Old", "description": "short", "aliases": "O", "updated_at": "1"},
        {"id": "merged", "subject": "Merged", "description": "tiny", "aliases": "", "updated_at": "0"},
        {"id": "long", "subject": "Long", "description": "x" * 100, "aliases": "L", "updated_at": "2"},
        {"id": "user", "subject": "User", "description": "tiny", "aliases": "", "updated_at": "3"},
    ]

    thin, other_subjects = dream_cycle._prepare_enrichment_inputs(
        entities,
        merges=[{"duplicate_id": "merged"}],
        min_chars=20,
    )

    assert [entity["id"] for entity in thin] == ["old", "user"]
    assert other_subjects == {"old", "long"}


def test_extract_relevant_sentences_and_conversation_mentions(tmp_path, monkeypatch) -> None:
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch)

    text = "User: Alice likes tea. Bob deploys. Assistant: Alice writes tests! Other sentence."
    assert dream_cycle._extract_relevant_sentences(text, {"alice"}) == (
        "User: Alice likes tea. Assistant: Alice writes tests!"
    )

    import row_bot.memory_extraction as memory_extraction
    import row_bot.threads as threads

    monkeypatch.setattr(threads, "_list_threads", lambda: [("t1", "one", "", ""), ("t2", "two", "", "")])
    monkeypatch.setattr(
        memory_extraction,
        "_get_thread_messages",
        lambda tid: [{"role": "user", "content": f"{tid}: Alice and Al appear together."}],
    )
    monkeypatch.setattr(
        memory_extraction,
        "_format_conversation",
        lambda messages: "\n".join(f"{m['role']}: {m['content']}" for m in messages),
    )

    assert dream_cycle._find_conversation_mentions("Alice", aliases="Al") == [
        "user: t1: Alice and Al appear together.",
        "user: t2: Alice and Al appear together.",
    ]


def test_validate_enrichment_allows_related_subjects_and_rejects_unrelated(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    entity = {"id": "alice", "subject": "Alice"}

    monkeypatch.setattr(kg, "get_relations", lambda *_args, **_kwargs: [{"peer_subject": "Bob"}])

    assert dream_cycle._validate_enrichment("Alice works with Bob on tests.", entity, {"bob"}) is True
    assert dream_cycle._validate_enrichment("Alice owns Carol deployment facts.", entity, {"carol"}) is False


def test_cooccurring_pairs_ignore_rejections_existing_edges_and_duplicate_subjects(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    alice = {"id": "alice", "entity_type": "person", "subject": "Alice", "aliases": "A.", "description": ""}
    bob = {"id": "bob", "entity_type": "person", "subject": "Bob", "aliases": "", "description": ""}
    carol = {"id": "carol", "entity_type": "person", "subject": "Carol", "aliases": "", "description": ""}

    import row_bot.memory_extraction as memory_extraction
    import row_bot.threads as threads

    monkeypatch.setattr(threads, "_list_threads", lambda: [("t1", "one", "", ""), ("t2", "two", "", "")])
    monkeypatch.setattr(memory_extraction, "_get_thread_messages", lambda _tid: [{"role": "user", "content": "Alice Bob Carol"}])
    monkeypatch.setattr(memory_extraction, "_format_conversation", lambda messages: messages[0]["content"])
    monkeypatch.setattr(dream_cycle, "_is_pair_recently_rejected", lambda a, b: "carol" in {a, b})
    monkeypatch.setattr(
        kg,
        "get_relations",
        lambda entity_id, direction="both": [{"peer_id": "bob", "relation_type": "works_on"}]
        if entity_id == "alice"
        else [],
    )

    pairs = dream_cycle._find_cooccurring_pairs([alice, bob, carol])

    assert pairs == []


def test_relation_inference_handles_invalid_json_model_failure_and_reverse_direction(tmp_path, monkeypatch) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    alice = kg.save_entity("person", "Alice", "Alice tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project.", source="test")

    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt: "not json")
    assert dream_cycle._infer_relation(alice, project, "Alice works on Row-Bot.") is None

    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt: (_ for _ in ()).throw(RuntimeError("model down")))
    assert dream_cycle._infer_relation(alice, project, "Alice works on Row-Bot.") is None

    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt: json.dumps(
            {
                "has_relation": True,
                "relation_type": "maintains",
                "source": "Row-Bot",
                "target": "Alice",
                "confidence": 0.91,
                "evidence": "Row-Bot is maintained by Alice.",
            }
        ),
    )

    result = dream_cycle._infer_relation(alice, project, "Row-Bot is maintained by Alice.")

    assert result is not None
    assert result["source_id"] == project["id"]
    assert result["target_id"] == alice["id"]
    assert kg.get_relations(project["id"], direction="outgoing")[0]["relation_type"] == "maintains"


def test_insights_phase_stores_added_and_merged_insights(tmp_path, monkeypatch) -> None:
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch)
    statuses: list[str] = []

    import row_bot.insights as insights

    added_items = [
        {
            "category": "system_health",
            "severity": "warning",
            "title": "Check logs",
            "body": "Warnings increased.",
            "evidence": ["warning count"],
            "suggestion": "Review warnings",
            "confidence": 0.9,
        },
        {
            "category": "workflow",
            "title": "Duplicate task",
            "body": "Task already exists.",
            "confidence": 0.8,
        },
    ]

    def fake_add_insight(**kwargs):
        if kwargs["title"] == "Duplicate task":
            return {**kwargs, "_merged": True}
        return kwargs

    monkeypatch.setattr(dream_cycle, "_collect_system_snapshot", lambda: "snapshot")
    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt: json.dumps(added_items))
    monkeypatch.setattr(insights, "add_insight", fake_add_insight)
    monkeypatch.setattr(insights, "auto_prune", lambda: 1)
    monkeypatch.setattr(insights, "set_last_analysis", lambda: None)

    result = dream_cycle._run_insights_phase("cycle-1", on_status=statuses.append)

    assert result["insights_added"] == 1
    assert result["insights_merged"] == 1
    assert "Auto-pruned 1 stale insight(s)" in statuses
    assert statuses[-1] == "Insights phase: 1 new, 1 merged"
