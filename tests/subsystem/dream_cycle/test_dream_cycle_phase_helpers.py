from __future__ import annotations

import importlib
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


def _fake_threads(monkeypatch, conversations: dict[str, str]) -> list[str]:
    """Conversations as Dream reads them; returns the ids read, in order."""
    import row_bot.memory_extraction as memory_extraction
    import row_bot.threads as threads

    read: list[str] = []
    monkeypatch.setattr(threads, "_list_threads", lambda: [(tid, tid, "", "") for tid in conversations])

    def messages(tid):
        read.append(tid)
        return [{"role": "user", "content": conversations[tid]}]

    monkeypatch.setattr(memory_extraction, "_get_thread_messages", messages)
    monkeypatch.setattr(memory_extraction, "_format_conversation", lambda items: items[0]["content"])
    return read


def test_pair_candidates_use_calibrated_similarity_type_family_and_skip_user_archived_and_unlike_lessons(
        tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)

    def memory(eid, kind, subject, description="", created="2026-01-01", properties="{}"):
        return {"id": eid, "entity_type": kind, "subject": subject, "description": description or subject,
                "aliases": "", "created_at": created, "properties": properties}

    entities = [
        memory("a", "person", "Alice", "Alice writes tests", "2026-01-02"),
        memory("a2", "fact", "Alice", "Alice writes the tests", "2026-01-01"),   # person/fact: one family
        memory("p", "project", "Alice", "Wrong type"),
        memory("u", "person", "User", "Never paired"),
        memory("old", "person", "Archived", properties='{"status": "archived"}'),
        memory("l1", "self_knowledge", "Lesson", "Ask before deleting files."),
        memory("l2", "self_knowledge", "Lesson", "Ask before moving or renaming files in the workspace."),
        memory("far", "person", "Bob", "Bob plays chess"),
    ]
    scores = {"a": {"a2": 0.9, "p": 0.99, "u": 0.99, "old": 0.99, "far": 0.7},
              "l1": {"l2": 0.99}}
    searched: list[str] = []

    def fake_semantic_search(text, top_k, threshold, stats=None):
        [entity] = [e for e in entities if e["description"] == text]
        searched.append(entity["id"])
        stats["mean"] = 0.5  # calibrated: (0.9 - 0.5) / 0.5 = 0.8; (0.7 - 0.5) / 0.5 = 0.4
        return [{**other, "score": scores.get(entity["id"], {}).get(other["id"], 0.0)}
                for other in entities if other["id"] in scores.get(entity["id"], {})]

    monkeypatch.setattr(kg, "_entity_text", lambda entity: entity["description"])
    monkeypatch.setattr(kg, "semantic_search", fake_semantic_search)

    pairs = dream_cycle._find_pair_candidates(entities)

    # Older first; the far pair is under the calibrated bar; the lessons are not near-identical.
    assert [(older["id"], newer["id"], round(score, 2)) for older, newer, score in pairs] == [("a2", "a", 0.8)]
    # One search per memory at most, never for the User record or an archived memory.
    assert sorted(searched) == sorted(["a", "a2", "p", "l1", "l2", "far"])


def test_enrichment_inputs_skip_lessons_user_and_superseded_and_name_only_people_places_and_organisations(
        tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    entities = [
        {"id": "old", "entity_type": "person", "subject": "Old", "description": "short", "aliases": "Oldie", "updated_at": "1"},
        {"id": "gone", "entity_type": "fact", "subject": "Gone", "description": "tiny", "updated_at": "0",
         "properties": '{"status": "superseded"}'},
        {"id": "lesson", "entity_type": "self_knowledge", "subject": "Lesson", "description": "tiny", "updated_at": "0"},
        {"id": "long", "entity_type": "place", "subject": "Long", "description": "x" * 100, "aliases": "L", "updated_at": "2"},
        {"id": "tea", "entity_type": "preference", "subject": "Tea", "description": "tiny", "updated_at": "4"},
        {"id": "user", "entity_type": "person", "subject": "User", "description": "tiny", "aliases": "", "updated_at": "3"},
    ]

    thin, other_names = dream_cycle._prepare_enrichment_inputs(entities, min_chars=20)

    assert [entity["id"] for entity in thin] == ["old", "tea"]
    assert other_names == {"old", "oldie", "long"}


def test_one_read_finds_the_sentences_naming_a_memory_and_passages_naming_two(
        tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    read = _fake_threads(monkeypatch, {
        "t1": "User: Alice likes tea. Bob deploys. Assistant: Alice writes tests! Other sentence.",
        "t2": "user: t2: Alice and Al appear together.",
    })
    alice = {"id": "alice", "subject": "Alice", "aliases": "Al"}
    bob = {"id": "bob", "subject": "Bob", "aliases": ""}

    found = dream_cycle._read_conversations([alice, bob], {"alice"})

    assert read == ["t1", "t2"]
    assert found.mentions == {"alice": ["User: Alice likes tea. Assistant: Alice writes tests!",
                                        "user: t2: Alice and Al appear together."]}
    # Alice and Bob are named in adjacent sentences of t1 only.
    assert found.pairs == {("alice", "bob"): [1, ["User: Alice likes tea. Bob deploys."]]}
    assert dream_cycle._find_conversation_mentions("Alice", aliases="Al") == found.mentions["alice"]


def test_validate_enrichment_rejects_only_names_the_evidence_does_not_hold(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    entity = {"id": "alice", "subject": "Alice", "description": "Alice likes tea."}
    excerpts = ["Alice went hiking with Dana in Lisbon."]
    names = {"bob", "carol", "dana", "lisbon", "alice"}

    monkeypatch.setattr(kg, "get_relations", lambda *_args, **_kwargs: [{"peer_subject": "Bob"}])

    assert dream_cycle._validate_enrichment("Alice works with Bob on tests.", entity, names, excerpts) is True
    assert dream_cycle._validate_enrichment("Alice hiked with Dana in Lisbon.", entity, names, excerpts) is True
    assert dream_cycle._validate_enrichment("Alice owns Carol's deployment facts.", entity, names, excerpts) is False


def test_cooccurring_pairs_ignore_rejections_existing_edges_and_duplicate_subjects(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    alice = {"id": "alice", "entity_type": "person", "subject": "Alice", "aliases": "A.", "description": ""}
    bob = {"id": "bob", "entity_type": "person", "subject": "Bob", "aliases": "", "description": ""}
    carol = {"id": "carol", "entity_type": "person", "subject": "Carol", "aliases": "", "description": ""}

    _fake_threads(monkeypatch, {"t1": "Alice Bob Carol", "t2": "Alice Bob Carol"})
    monkeypatch.setattr(dream_cycle, "_rejected_with_same_evidence",
                        lambda kind, key, _prompt, _cache=None, **_kwargs: kind == "infer" and "carol" in key)
    monkeypatch.setattr(
        kg,
        "get_relations",
        lambda entity_id, direction="both": [{"peer_id": "bob", "relation_type": "works_on"}]
        if entity_id == "alice"
        else [],
    )

    conversations = dream_cycle._read_conversations([alice, bob, carol])
    pairs = dream_cycle._find_cooccurring_pairs([alice, bob, carol], conversations)

    assert pairs == []


def test_an_inference_excerpt_is_a_passage_naming_both_and_a_pair_never_named_together_is_skipped(
        tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    alice = {"id": "alice", "entity_type": "person", "subject": "Alice", "aliases": "", "description": "A friend."}
    bob = {"id": "bob", "entity_type": "person", "subject": "Bob", "aliases": "", "description": "A neighbour."}
    acme = {"id": "acme", "entity_type": "organisation", "subject": "Acme", "aliases": "", "description": "A company."}
    filler = " ".join(f"Line {n} is about the weather." for n in range(40))
    long_turn = "x" * 900
    _fake_threads(monkeypatch, {
        # Alice is named first, far from Bob; Acme never shares a passage with Alice.
        "t1": f"User: Alice likes tea. {filler} Acme hired Carol. {filler} Later, Alice met Bob at the market.",
        # One long sentence: the passage is cut to the part naming both.
        "t2": f"User: {long_turn} Bob called Alice yesterday about {long_turn}",
    })
    monkeypatch.setattr(kg, "get_relations", lambda *_args, **_kwargs: [])

    conversations = dream_cycle._read_conversations([alice, bob, acme])
    pairs = dream_cycle._find_cooccurring_pairs([alice, bob, acme], conversations)

    assert [(a["id"], b["id"], count) for a, b, _excerpt, count in pairs] == [("alice", "bob", 2)]
    excerpt = pairs[0][2]
    assert "Alice" in excerpt and "Bob" in excerpt
    assert "Later, Alice met Bob at the market." in conversations.pairs[("alice", "bob")][1]
    assert all(len(passage) <= 700 for passage in conversations.pairs[("alice", "bob")][1])
    assert ("acme", "alice") not in conversations.pairs


def test_relation_inference_handles_invalid_json_model_failure_and_reverse_direction(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    alice = kg.save_entity("person", "Alice", "Alice tests.", source="test")
    project = kg.save_entity("project", "Row-Bot", "Project.", source="test")

    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt, **_kwargs: "not json")
    assert dream_cycle._infer_relation(alice, project, "Alice works on Row-Bot.") is None

    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt, **_kwargs: (_ for _ in ()).throw(RuntimeError("model down")))
    assert dream_cycle._infer_relation(alice, project, "Alice works on Row-Bot.") is None

    monkeypatch.setattr(
        dream_cycle,
        "_llm_call",
        lambda _prompt, **_kwargs: json.dumps(
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


def test_insights_phase_stores_added_and_merged_insights(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
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

    stored: list[dict] = []

    def fake_add_insight(**kwargs):
        stored.append(kwargs)
        if kwargs["title"] == "Duplicate task":
            return {**kwargs, "_merged": True}
        return kwargs

    # Each insight remembers the model in use when it was found (B124).
    monkeypatch.setattr(importlib.import_module("row_bot.models"), "get_current_model", lambda: "fixture-model")
    monkeypatch.setattr(dream_cycle, "_collect_system_snapshot", lambda: "snapshot")
    monkeypatch.setattr(dream_cycle, "_llm_call", lambda _prompt, **_kwargs: json.dumps(added_items))
    monkeypatch.setattr(insights, "add_insight", fake_add_insight)
    monkeypatch.setattr(insights, "auto_prune", lambda: 1)
    monkeypatch.setattr(insights, "set_last_analysis", lambda: None)

    result = dream_cycle._run_insights_phase("cycle-1", on_status=statuses.append)

    assert result["insights_added"] == 1
    assert result["insights_merged"] == 1
    assert [item["found_with_model"] for item in stored] == ["fixture-model", "fixture-model"]
    assert "Auto-pruned 1 stale insight(s)" in statuses
    assert statuses[-1] == "Insights phase: 1 new, 1 merged"


def test_insights_phase_reads_a_schema_reply_with_its_own_cap(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    """WP C1: insights come back as ``{"insights": [...]}`` where the provider takes a
    schema; an insight without a skill draft carries ``null``, never an empty draft."""
    dream_cycle, _kg = fresh_dream_and_kg(tmp_path, monkeypatch, reload_for_data_dir)
    import row_bot.insights as insights

    reply = {"insights": [{
        "category": "error_pattern", "severity": "warning", "title": "Repeated timeouts",
        "body": "The calendar sync timed out twice.", "evidence": ["timeout x2"],
        "suggestion": "Check the calendar connection.", "confidence": 0.8, "auto_fixable": False,
        "skill_draft": None,
    }]}
    calls: list[dict] = []
    stored: list[dict] = []

    def fake_llm_call(_prompt, **kwargs):
        calls.append(kwargs)
        return json.dumps(reply)

    monkeypatch.setattr(dream_cycle, "_collect_system_snapshot", lambda: "snapshot")
    monkeypatch.setattr(dream_cycle, "_llm_call", fake_llm_call)
    monkeypatch.setattr(insights, "add_insight", lambda **kwargs: stored.append(kwargs) or kwargs)
    monkeypatch.setattr(insights, "auto_prune", lambda: 0)
    monkeypatch.setattr(insights, "set_last_analysis", lambda: None)

    result = dream_cycle._run_insights_phase("cycle-2")

    assert result["insights_added"] == 1
    assert stored[0]["title"] == "Repeated timeouts" and stored[0]["skill_draft"] is None
    [call] = calls
    assert call["max_tokens"] == dream_cycle._INSIGHTS_MAX_TOKENS
    assert call["answer"].schema["properties"]["insights"]["type"] == "array"
