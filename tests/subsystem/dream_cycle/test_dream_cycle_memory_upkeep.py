"""Dream Cycle improves long-term memory across the whole graph each night.

On a real profile (736 memories) a 50-memory rotating batch and raw-score
thresholds of 0.93/0.98 merged five pairs in a hundred nights, found no
contradictions, enriched nothing and pruned nothing. These tests drive whole
nights with a fake model and a fake memory search.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.subsystem

_COMPARE = "read alike"  # in the comparison prompt only


class _Model:
    """Dream's model calls (``_llm_call``): records each prompt, answers by kind."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.decide = lambda _block: "different"
        self.merged = ""

    def __call__(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        if "self-analysis engine" in prompt:
            return '{"insights": []}'
        if "co-occur in" in prompt:
            return '{"has_relation": false}'
        if _COMPARE in prompt:
            decisions = []
            for block in prompt.split("\nPair ")[1:]:
                number = int(block.split("\n", 1)[0])
                decisions.append({"pair": number, "decision": self.decide(block), "reason": "fixture"})
            return json.dumps({"decisions": decisions})
        if "near-duplicates" in prompt:
            return json.dumps({"description": self.merged})
        return '{"description": ""}'

    def asked(self, marker: str, since: int = 0) -> list[str]:
        return [prompt for prompt in self.prompts[since:] if marker in prompt]


@pytest.fixture
def night(reload_for_data_dir, tmp_path, monkeypatch):
    threads, kg, memory_evolution, dream_cycle = reload_for_data_dir(
        tmp_path / "data", "row_bot.threads", "row_bot.knowledge_graph", "row_bot.memory_evolution",
        "row_bot.dream_cycle")
    import row_bot.insights as insights
    from row_bot import models

    monkeypatch.setattr(kg, "_skip_reindex", True)
    monkeypatch.setattr(kg, "repair_projections", lambda **_kwargs: {"complete": True})
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(insights, "auto_prune", lambda: 0)
    monkeypatch.setattr(insights, "set_last_analysis", lambda: None)
    monkeypatch.setattr(dream_cycle, "_collect_system_snapshot", lambda: "KNOWLEDGE GRAPH: fixture")
    dream_cycle._save_config({**dream_cycle._load_config(), "min_entities": 2})
    model = _Model()
    monkeypatch.setattr(dream_cycle, "_llm_call", model)
    return threads, kg, memory_evolution, dream_cycle, model


def _memory(kg, kind: str, subject: str, description: str, day: int) -> dict:
    """A memory saved on day *day* of 2026 (local time, as Row-Bot stores it)."""
    entity = kg.save_entity(kind, subject, description, source="test")
    saved = (datetime(2026, 1, 1) + timedelta(days=day)).isoformat()
    conn = kg._get_conn()
    conn.execute("UPDATE entities SET created_at = ?, updated_at = ? WHERE id = ?", (saved, saved, entity["id"]))
    conn.commit()
    conn.close()
    return kg.get_entity(entity["id"])


def _search(kg, monkeypatch, scores: dict[frozenset, float], mean: float = 0.45) -> list[str]:
    """A memory search like the local embedding model's: unrelated memories score about *mean*."""
    searched: list[str] = []
    graph: dict[str, list[dict]] = {}

    def semantic_search(text, top_k=5, threshold=0.5, *, for_auto_recall=False, stats=None):
        entities = graph.get("entities") or []
        if not any(kg._entity_text(entity) == text for entity in entities):
            entities = graph["entities"] = kg.list_entities(limit=10_000)
        [query] = [entity for entity in entities if kg._entity_text(entity) == text]
        searched.append(query["id"])
        if stats is not None:
            stats["mean"] = mean
        hits = [{**entity, "score": scores.get(frozenset((query["id"], entity["id"])), mean)}
                for entity in entities]
        hits.sort(key=lambda hit: -hit["score"])
        return [hit for hit in hits if hit["score"] >= threshold][:top_k]

    monkeypatch.setattr(kg, "semantic_search", semantic_search)
    return searched


def _status(memory_evolution, entity: dict) -> dict:
    return memory_evolution.get_properties(entity)


def test_a_paraphrased_duplicate_anywhere_in_a_large_graph_is_merged_on_the_first_night(night, monkeypatch) -> None:
    _threads, kg, _evolution, dream_cycle, model = night
    conn = kg._get_conn()  # more memories than the 50 one night used to look at, saved at once
    conn.executemany(
        "INSERT INTO entities (id, entity_type, subject, description, aliases, tags, properties, source, "
        "created_at, updated_at) VALUES (?, 'fact', ?, ?, '', '', '{}', 'test', ?, ?)",
        [(f"filler{number:03d}", f"Filler {number}", f"Filler fact number {number} about topic {number}.",
          f"2026-01-{number % 28 + 1:02d}T00:00:00", f"2026-01-{number % 28 + 1:02d}T00:00:00")
         for number in range(60)])
    conn.commit()
    conn.close()
    older = _memory(kg, "fact", "Demo recordings", "Records the demo videos with OBS Studio.", 200)
    newer = _memory(kg, "fact", "Recording tool", "Uses OBS to record demo videos.", 300)
    # 0.80 raw: well under the old 0.93 bar, but 0.64 calibrated against this model's 0.45 mean.
    searched = _search(kg, monkeypatch, {frozenset((older["id"], newer["id"])): 0.80})
    model.decide = lambda block: "same" if "OBS" in block else "different"
    model.merged = "Records the demo videos with OBS Studio."

    summary = dream_cycle.run_dream_cycle()

    [merge] = summary["merges"]
    assert (merge["survivor_id"], merge["duplicate_id"]) == (older["id"], newer["id"])
    assert kg.get_entity(newer["id"]) is None
    assert "Recording tool" in kg.get_entity(older["id"])["aliases"]
    # One search per memory, and only the close pair is asked about.
    assert len(searched) == len(set(searched)) == 62
    [compare] = model.asked(_COMPARE)
    assert compare.count("\nPair ") == 1
    assert dream_cycle.get_journal(1)[0]["merges"][0]["duplicate_id"] == newer["id"]


def test_a_newer_contrary_preference_supersedes_the_older_one_which_is_kept(night, monkeypatch) -> None:
    _threads, kg, memory_evolution, dream_cycle, model = night
    older = _memory(kg, "preference", "Recording theme", "Prefers a dark theme for screen recordings.", 10)
    newer = _memory(kg, "preference", "Recording theme", "Prefers a light theme for screen recordings.", 40)
    _search(kg, monkeypatch, {frozenset((older["id"], newer["id"])): 0.92})
    model.decide = lambda block: "replaces"

    summary = dream_cycle.run_dream_cycle()

    [replaced] = summary["superseded"]
    assert (replaced["old_id"], replaced["new_id"]) == (older["id"], newer["id"])
    kept = kg.get_entity(older["id"])
    assert kept["description"] == "Prefers a dark theme for screen recordings."
    assert _status(memory_evolution, kept)["status"] == "superseded"
    assert _status(memory_evolution, kept)["superseded_by"] == newer["id"]
    current = kg.get_entity(newer["id"])
    assert _status(memory_evolution, current)["status"] == "active"
    assert _status(memory_evolution, current)["supersedes"] == [older["id"]]
    assert summary["merges"] == [] and "1 replaced by newer memories" in summary["summary"]
    assert dream_cycle.get_journal(1)[0]["superseded"][0]["old_id"] == older["id"]

    asked = len(model.prompts)
    dream_cycle.run_dream_cycle()  # a superseded memory is not compared again
    assert model.asked(_COMPARE, asked) == []


def test_different_memories_are_left_alone_and_not_asked_about_again_until_one_changes(night, monkeypatch) -> None:
    _threads, kg, memory_evolution, dream_cycle, model = night
    birthday = _memory(kg, "fact", "Alice birthday", "Alice's birthday is in May.", 1)
    anniversary = _memory(kg, "fact", "Alice anniversary", "Alice's wedding anniversary is in May.", 2)
    _search(kg, monkeypatch, {frozenset((birthday["id"], anniversary["id"])): 0.90})
    model.decide = lambda block: "different"

    summary = dream_cycle.run_dream_cycle()

    assert summary["merges"] == [] and summary["superseded"] == []
    for entity in (birthday, anniversary):
        assert kg.get_entity(entity["id"]) == entity
    assert len(model.asked(_COMPARE)) == 1

    asked = len(model.prompts)
    dream_cycle.run_dream_cycle()
    assert model.asked(_COMPARE, asked) == []

    kg.update_entity(anniversary["id"], "Alice's wedding anniversary is on 12 May.")
    asked = len(model.prompts)
    dream_cycle.run_dream_cycle()
    assert len(model.asked(_COMPARE, asked)) == 1


def test_a_lesson_is_never_rewritten_or_superseded_and_its_merge_hands_the_lesson_over(night, monkeypatch) -> None:
    _threads, kg, memory_evolution, dream_cycle, model = night
    from row_bot import learning

    handed_over: list[tuple[str, str]] = []
    monkeypatch.setattr(learning, "lesson_memory_deleted_soon", lambda old, new: handed_over.append((old, new)))
    zone = _memory(kg, "self_knowledge", "Time zones", "Confirm the time zone before scheduling a meeting.", 1)
    zone_again = _memory(kg, "self_knowledge", "Time zones", "Confirm the time zone before scheduling a meeting!", 5)
    files = _memory(kg, "self_knowledge", "Deleting files", "Always ask before deleting files in the workspace.", 2)
    files_again = _memory(kg, "self_knowledge", "Deleting files", "Always ask before deleting files in the workspace", 6)
    short = _memory(kg, "self_knowledge", "Answer length", "Keep answers short.", 3)
    longer = _memory(kg, "self_knowledge", "Answer length",
                     "Keep answers short unless the person asks for detail, then explain fully.", 7)
    _search(kg, monkeypatch, {frozenset((a["id"], b["id"])): 0.97
                              for a, b in ((zone, zone_again), (files, files_again), (short, longer))})
    # The model would merge the first and replace the others, if it were asked.
    model.decide = lambda block: "same" if "time zone" in block else "replaces"
    model.merged = "A rewritten lesson."

    summary = dream_cycle.run_dream_cycle()

    [merge] = summary["merges"]
    assert (merge["survivor_id"], merge["duplicate_id"]) == (zone["id"], zone_again["id"])
    assert handed_over == [(zone_again["id"], zone["id"])]
    assert kg.get_entity(zone["id"]) == zone  # not a word (nor its stamp) changed
    assert kg.get_entity(zone_again["id"]) is None
    assert summary["superseded"] == []
    for lesson in (files, files_again, short, longer):
        assert kg.get_entity(lesson["id"]) == lesson
    # Only near-identical lessons are compared at all; no merge text was asked for.
    [compare] = model.asked(_COMPARE)
    assert "time zone" in compare and "deleting files" in compare and "unless the person asks" not in compare
    assert model.asked("near-duplicates") == []
    # Enrichment never rewrites a lesson either.
    assert dream_cycle._enrich_entity(short, ["Keep answers short, always."], set()) is None
    assert kg.get_entity(short["id"]) == short


def _relation(kg, source: dict, target: dict, kind: str, confidence: float, *, updated_days_ago: float,
              decayed_days_ago: float | None = None, origin: str = "dream_infer") -> str:
    relation = kg.add_relation(source["id"], target["id"], kind, source=origin, confidence=confidence,
                               properties={"evidence": "fixture"})
    props = {"evidence": "fixture"}
    if decayed_days_ago is not None:
        props["decayed_at"] = (datetime.now(timezone.utc) - timedelta(days=decayed_days_ago)).isoformat()
    updated = (datetime.now() - timedelta(days=updated_days_ago)).isoformat()  # naive local, as stored
    conn = kg._get_conn()
    conn.execute("UPDATE relations SET updated_at = ?, properties = ? WHERE id = ?",
                 (updated, json.dumps(props), relation["id"]))
    conn.commit()
    conn.close()
    return relation["id"]


def _relation_row(kg, relation_id: str) -> dict | None:
    conn = kg._get_conn()
    row = conn.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def test_inferences_decay_ten_percent_per_thirty_days_unused_and_are_pruned_below_point_three(night) -> None:
    _threads, kg, _evolution, dream_cycle, _model = night
    people = [_memory(kg, "person", name, f"{name}, a friend.", 1) for name in ("Ann", "Ben", "Cat", "Dan")]
    unused = _relation(kg, people[0], people[1], "knows", 0.9, updated_days_ago=61)
    stamped = _relation(kg, people[0], people[2], "knows", 0.8, updated_days_ago=100, decayed_days_ago=31)
    restated = _relation(kg, people[0], people[3], "knows", 0.5, updated_days_ago=5, decayed_days_ago=40)
    fading = _relation(kg, people[1], people[2], "knows", 0.32, updated_days_ago=31)
    stated = _relation(kg, people[1], people[3], "knows", 0.9, updated_days_ago=400, origin="extraction")
    before = {relation_id: _relation_row(kg, relation_id) for relation_id in (unused, stamped, restated, stated)}

    decayed, pruned = dream_cycle._decay_inferences()

    assert {entry["relation_id"]: entry["new_confidence"] for entry in decayed} == {unused: 0.729, stamped: 0.72}
    assert [entry["relation_id"] for entry in pruned] == [fading]
    assert _relation_row(kg, fading) is None
    for relation_id in (unused, stamped):
        row = _relation_row(kg, relation_id)
        assert row["updated_at"] == before[relation_id]["updated_at"]
        assert json.loads(row["properties"])["evidence"] == "fixture"
    # Two whole periods since it was stated: its stamp moves on by exactly 60 days.
    stamp = datetime.fromisoformat(json.loads(_relation_row(kg, unused)["properties"])["decayed_at"])
    stated_at = datetime.fromisoformat(before[unused]["updated_at"]).astimezone(timezone.utc)
    assert stamp - stated_at == timedelta(days=60)
    # Re-stated after its last decay: it starts over, and five days is not a period.
    assert _relation_row(kg, restated) == before[restated]
    assert _relation_row(kg, stated) == before[stated]  # only Dream's inferences decay

    assert dream_cycle._decay_inferences() == ([], [])  # the next night: nothing more is due


def test_a_night_reads_each_conversation_once_for_enrichment_and_inference(night, monkeypatch) -> None:
    threads, kg, _evolution, dream_cycle, model = night
    import row_bot.memory_extraction as memory_extraction

    for subject, kind, description in [("Alice", "person", "Alice, a friend."),
                                       ("Bob", "person", "Bob, a neighbour."),
                                       ("Acme", "organisation", "Acme, a company.")]:
        kg.save_entity(kind, subject, description, source="test")
    for thread_id, text in [("t1", "Alice works at Acme with Bob."), ("t2", "Alice and Bob visited Acme."),
                            ("t3", "Bob fixed the fence.")]:
        threads.create_thread(thread_id, thread_id=thread_id, seed_default_skills=False)
        assert threads.append_checkpoint_messages(thread_id, [
            HumanMessage(id=f"{thread_id}-h", content=text), AIMessage(id=f"{thread_id}-a", content="Noted.")])
    _search(kg, monkeypatch, {})
    read: list[str] = []
    real = memory_extraction._get_thread_messages
    monkeypatch.setattr(memory_extraction, "_get_thread_messages", lambda tid: read.append(tid) or real(tid))

    dream_cycle.run_dream_cycle()

    assert sorted(read) == ["t1", "t2", "t3"]
    assert len(model.asked("thin description")) == 3
    infer = model.asked("co-occur in")
    assert len(infer) == 3
    excerpts = [prompt.split("Conversation excerpt where both appear:\n", 1)[1] for prompt in infer]
    assert all("Alice works at Acme with Bob." in excerpt for excerpt in excerpts)
