"""Near-duplicate memories are merged, distinct ones never are, and recall prefers the newer, more specific.

P2: extraction saved 1.2-4x as many memories, and a near neighbour outranked the right one (LoCoMo
qa37, qa137). A new fact that a saved memory of the same kind and thing already says is skipped, one that
says all a saved one does and more updates it, and the same holds inside one batch, all in reconcile
without a model call. The rule is strict (every word of the smaller fact, numbers included, after common
words and simple endings), so two facts that each say something the other doesn't stay apart.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def stack(tmp_path, monkeypatch, reload_for_data_dir):
    kg, reconcile = reload_for_data_dir(tmp_path / "data", "row_bot.knowledge_graph", "row_bot.memory_reconcile")
    kg._skip_reindex = True
    close: dict[str, float] = {}

    def semantic_search(query, top_k=5, threshold=0.5, *, for_auto_recall=False, stats=None):
        if stats is not None:
            stats["mean"] = 0.40
        hits = [{**kg.get_entity(entity_id), "score": score}
                for entity_id, score in sorted(close.items(), key=lambda item: -item[1])]
        return [hit for hit in hits if hit["score"] >= threshold][:top_k]

    monkeypatch.setattr(kg, "semantic_search", semantic_search)
    return kg, reconcile, close


def _never(prompt: str) -> str:
    raise AssertionError("no model call expected")


def _add_everything(prompt: str) -> str:
    import json
    import re

    facts = [int(number) for number in re.findall(r'"fact": (\d+)', prompt)]
    return json.dumps({"decisions": [{"fact": fact, "action": "add", "memory_id": "", "subject": "", "text": ""}
                                     for fact in facts]})


# ── Merged ───────────────────────────────────────────────────────────────────


def test_a_fact_a_saved_memory_already_says_in_other_words_is_skipped(stack) -> None:
    kg, reconcile, close = stack
    saved = kg.save_entity("event", "Melanie's sunset painting",
                           "Melanie painted a sunset over the lake on 23 August 2023, her favourite piece.")
    close[saved["id"]] = 0.9

    [outcome] = reconcile.reconcile(
        [reconcile.NewMemory("event", "Sunset painting", "Melanie painted the lake sunset on 23 August 2023.")],
        invoke=_never)

    assert outcome.action == "skip" and outcome.entity_id == saved["id"]
    assert len(kg.list_entities(limit=10)) == 1


def test_a_fact_that_says_all_a_saved_one_does_and_more_updates_it(stack) -> None:
    kg, reconcile, close = stack
    saved = kg.save_entity("person", "Caroline", "Caroline attends a support group.")
    close[saved["id"]] = 0.9
    fuller = "Caroline has been attending an LGBTQ support group every Tuesday since May 2023."

    [outcome] = reconcile.reconcile([reconcile.NewMemory("person", "Caroline's support group", fuller)],
                                    invoke=_never)

    assert outcome.action == "update" and outcome.entity_id == saved["id"]
    assert kg.get_entity(saved["id"])["description"] == fuller
    assert len(kg.list_entities(limit=10)) == 1


def test_two_new_facts_where_one_says_more_are_saved_once(stack) -> None:
    kg, reconcile, _close = stack

    outcomes = reconcile.reconcile([
        reconcile.NewMemory("event", "Pottery workshop", "Melanie went to a pottery workshop."),
        reconcile.NewMemory("event", "Melanie's pottery workshop",
                            "Melanie went to a pottery workshop with her kids on 14 July 2023."),
    ], invoke=_never)

    assert [outcome.action for outcome in outcomes] == ["add"]
    [entity] = kg.list_entities(limit=10)
    assert entity["description"] == "Melanie went to a pottery workshop with her kids on 14 July 2023."


# ── Never merged ─────────────────────────────────────────────────────────────

DISTINCT = {
    "another painting the same day": (
        ("event", "Horse painting", "Melanie painted a horse on 23 August 2023."),
        ("event", "Sunset painting", "Melanie painted a sunset on 23 August 2023."),
    ),
    "another number": (
        ("event", "Moving day", "The move to the new flat took 5 hours."),
        ("event", "Moving day", "The move to the new flat took 6 hours."),
    ),
    "another person": (
        ("person", "Sister", "The user's sister lives in Leeds."),
        ("person", "Friend", "The user's friend lives in Leeds."),
    ),
    "another kind of memory": (
        ("preference", "Sunset painting", "Melanie loves painting sunsets."),
        ("event", "Painting sunsets", "Melanie loves painting sunsets at the lake."),
    ),
    "another thing, though the words overlap": (
        ("place", "Lisbon", "City where the user studied art in 2019."),
        ("project", "Art portfolio", "The user studied art in Lisbon in 2019 and is building a portfolio."),
    ),
}


@pytest.mark.parametrize("name", list(DISTINCT))
def test_distinct_facts_are_never_merged(stack, name: str) -> None:
    kg, reconcile, close = stack
    (old_category, old_subject, old_text), (category, subject, text) = DISTINCT[name]
    saved = kg.save_entity(old_category, old_subject, old_text)
    close[saved["id"]] = 0.9

    [outcome] = reconcile.reconcile([reconcile.NewMemory(category, subject, text)], invoke=_add_everything)

    assert outcome.action == "add", name
    assert kg.get_entity(saved["id"])["description"] == old_text
    assert len(kg.list_entities(limit=10)) == 2


def test_a_short_fact_is_too_short_to_call_a_duplicate(stack) -> None:
    _kg, reconcile, _close = stack
    assert not reconcile.covers("Melanie paints sunsets over the lake.", "Paints.")
    assert reconcile.covers("Melanie paints sunsets over the lake.", "Melanie painted a sunset.")


# ── Learning's lessons (self_knowledge) are never merged, either way ─────────

LESSON = "Before a long report, hand the reading to a helper and summarise its notes."
LESSON_FULLER = "Before a long report, hand the reading to a helper, then summarise its notes in five bullets."


def test_two_near_duplicate_lessons_both_survive_unchanged(stack) -> None:
    kg, reconcile, close = stack
    saved = kg.save_entity("self_knowledge", "Long reports", LESSON)
    close[saved["id"]] = 0.95

    outcomes = reconcile.reconcile([
        reconcile.NewMemory("self_knowledge", "Long reports", LESSON_FULLER),
        reconcile.NewMemory("self_knowledge", "Long report reading", LESSON),
    ], invoke=_never)

    assert [outcome.action for outcome in outcomes] == ["add", "add"]
    assert kg.get_entity(saved["id"])["description"] == LESSON
    lessons = sorted(entity["description"] for entity in kg.list_entities("self_knowledge", limit=10))
    assert lessons == sorted([LESSON, LESSON, LESSON_FULLER])


def test_an_ordinary_near_duplicate_never_absorbs_a_lesson_nor_is_absorbed_by_one(stack) -> None:
    kg, reconcile, close = stack
    lesson = kg.save_entity("self_knowledge", "Long reports", LESSON)
    note = kg.save_entity("concept", "Long reports", LESSON_FULLER)
    close.update({lesson["id"]: 0.95, note["id"]: 0.95})

    # An ordinary fact fuller than the lesson: the lesson is not rewritten (it is no candidate at all).
    [ordinary] = reconcile.reconcile(
        [reconcile.NewMemory("concept", "Long reports", LESSON_FULLER + " Keep each note short.")],
        invoke=_add_everything)
    # A lesson that an ordinary memory already says: it is still saved as its own lesson.
    [new_lesson] = reconcile.reconcile([reconcile.NewMemory("self_knowledge", "Long reports", LESSON)],
                                       invoke=_never)

    assert ordinary.entity_id != lesson["id"]
    assert kg.get_entity(lesson["id"])["description"] == LESSON
    assert new_lesson.action == "add" and new_lesson.entity_id not in {lesson["id"], note["id"]}
    assert kg.get_entity(note["id"])["description"] != LESSON


# ── Recall prefers the newer, more specific ──────────────────────────────────


def _recall(monkeypatch, candidates: list[dict], query: str = "What did Melanie paint recently?"):
    import row_bot.knowledge_graph as kg
    from row_bot import memory_policy

    monkeypatch.setattr(kg, "count_entities", lambda: len(candidates))

    def retrieve(_query, **kwargs):
        kwargs["diagnostics"].update({"semantic_status": "used", "semantic_mean": 0.3})
        return [dict(candidate) for candidate in candidates]

    monkeypatch.setattr(kg, "retrieve_memory_candidates", retrieve)
    return memory_policy.build_auto_recall(query, [])


def _candidate(memory_id: str, subject: str, text: str, updated: str, semantic: float, kind: str = "event") -> dict:
    return {"id": memory_id, "subject": subject, "description": text, "entity_type": kind, "updated_at": updated,
            "semantic_score": semantic, "lexical_score": 0.5, "score": semantic, "decay_multiplier": 0.9}


def test_recall_keeps_the_more_specific_of_two_near_duplicates_even_when_it_ranks_lower(monkeypatch) -> None:
    decision = _recall(monkeypatch, [
        _candidate("vague", "Painting", "Melanie painted a sunset.", "2023-08-24", 0.82),
        _candidate("specific", "Sunset painting", "Melanie painted a sunset over the lake on 23 August 2023.",
                   "2023-08-20", 0.78),
    ])

    assert [memory["id"] for memory in decision.selected] == ["specific"]
    assert any(item["reason"] == "near_duplicate" and item["id"] == "vague" for item in decision.trace["rejected"])


def test_recall_keeps_the_newer_of_two_memories_that_say_the_same(monkeypatch) -> None:
    decision = _recall(monkeypatch, [
        _candidate("older", "Sunset painting", "Melanie painted a sunset over the lake.", "2023-07-01", 0.80),
        _candidate("newer", "Lake painting", "Over the lake, Melanie painted a sunset.", "2023-08-23", 0.79),
    ])

    assert [memory["id"] for memory in decision.selected] == ["newer"]


def test_recall_never_folds_two_near_duplicate_lessons(monkeypatch) -> None:
    decision = _recall(monkeypatch, [
        _candidate("lesson-a", "Long reports", LESSON, "2026-11-01", 0.80, kind="self_knowledge"),
        _candidate("lesson-b", "Long reports, fuller", LESSON_FULLER, "2026-11-10", 0.79, kind="self_knowledge"),
    ], query="How should I handle a long report?")

    assert {memory["id"] for memory in decision.selected} == {"lesson-a", "lesson-b"}


def test_recall_keeps_both_of_two_distinct_paintings(monkeypatch) -> None:
    decision = _recall(monkeypatch, [
        _candidate("horse", "Horse painting", "Melanie painted a horse on 23 August 2023.", "2023-08-23", 0.80),
        _candidate("sunset", "Sunset painting", "Melanie painted a sunset on 13 October 2023.", "2023-10-13", 0.79),
    ])

    assert {memory["id"] for memory in decision.selected} == {"horse", "sunset"}
