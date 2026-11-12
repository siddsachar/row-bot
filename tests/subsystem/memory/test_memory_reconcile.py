"""New memories are reconciled with saved ones before anything is written.

Measured on a long-used profile (Nov 2026): memories that matched by name were merged by appending
"old. new", one person record passed 30,000 characters, and a later "light theme" sat beside an earlier
"dark theme" because the two had different subjects. Each new fact is now compared with the few saved
memories closest to it, and one decision adds, updates, supersedes (keeping the old as history) or skips it.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def stack(tmp_path, monkeypatch, reload_for_data_dir):
    kg, memory, extraction, reconcile, tool = reload_for_data_dir(
        tmp_path / "data", "row_bot.knowledge_graph", "row_bot.memory", "row_bot.memory_extraction",
        "row_bot.memory_reconcile", "row_bot.tools.memory_tool")
    kg._skip_reindex = True
    close: dict[str, float] = {}

    def semantic_search(query, top_k=5, threshold=0.5, *, for_auto_recall=False, stats=None):
        if stats is not None:
            stats["mean"] = 0.40
        hits = []
        for entity_id, score in sorted(close.items(), key=lambda item: -item[1]):
            entity = kg.get_entity(entity_id)
            if entity is not None and score >= threshold:
                hits.append({**entity, "score": score})
        return hits[:top_k]

    monkeypatch.setattr(kg, "semantic_search", semantic_search)
    return kg, memory, extraction, reconcile, tool, close


def _props(entity: dict) -> dict:
    return json.loads(entity.get("properties") or "{}")


def _answer(*decisions: dict):
    calls: list[str] = []

    def invoke(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"decisions": list(decisions)})

    invoke.calls = calls
    return invoke


def _never(prompt: str) -> str:
    raise AssertionError("no model call expected")


def test_a_fact_with_nothing_close_is_added_without_a_model_call(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, _close = stack

    [outcome] = reconcile.reconcile([reconcile.NewMemory("preference", "Coffee order", "Takes a flat white.")],
                                    invoke=_never)

    assert outcome.action == "add"
    assert kg.get_entity(outcome.entity_id)["description"] == "Takes a flat white."


def test_a_newer_contrary_preference_supersedes_and_the_old_one_is_kept(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, close = stack
    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    close[dark["id"]] = 0.82
    invoke = _answer({"fact": 1, "action": "supersede", "memory_id": dark["id"], "subject": "Recording theme"})

    [outcome] = reconcile.reconcile(
        [reconcile.NewMemory("preference", "Recording theme", "Recordings of the app are made in light theme.")],
        invoke=invoke)

    assert outcome.action == "supersede"
    old, new = kg.get_entity(dark["id"]), kg.get_entity(outcome.entity_id)
    assert old["description"] == dark["description"]  # kept, as history
    assert _props(old)["status"] == "superseded" and _props(old)["superseded_by"] == new["id"]
    assert new["description"] == "Recordings of the app are made in light theme."
    assert dark["id"] in invoke.calls[0]  # the model saw the saved memory it replaces


def test_an_update_rewrites_the_memory_instead_of_appending(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, close = stack
    dog = kg.save_entity("person", "Biscuit", "The user's dog, a beagle.")
    close[dog["id"]] = 0.85
    rewritten = "The user's dog, a nine-year-old beagle who is scared of thunder."

    [outcome] = reconcile.reconcile(
        [reconcile.NewMemory("person", "Biscuit", "Biscuit is nine and scared of thunder.")],
        invoke=_answer({"fact": 1, "action": "update", "memory_id": dog["id"], "text": rewritten}))

    assert (outcome.action, outcome.entity_id) == ("update", dog["id"])
    assert kg.get_entity(dog["id"])["description"] == rewritten
    assert kg.count_entities() == 1


def test_a_fact_already_saved_word_for_word_is_skipped_without_a_model_call(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, _close = stack
    city = kg.save_entity("place", "Leeds", "The city where the user lives, since 2019.")

    [outcome] = reconcile.reconcile([reconcile.NewMemory("place", "Leeds", "the city where the user lives")],
                                    invoke=_never)

    assert (outcome.action, outcome.entity_id) == ("skip", city["id"])
    assert kg.get_entity(city["id"])["description"] == city["description"]


def test_an_answer_naming_a_memory_that_was_not_offered_adds_the_fact(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, close = stack
    near = kg.save_entity("project", "Garden beds", "Raised beds planned for spring.")
    other = kg.save_entity("project", "Kitchen", "Kitchen renovation.")
    close[near["id"]] = 0.80

    [outcome] = reconcile.reconcile(
        [reconcile.NewMemory("project", "Greenhouse", "Building a small greenhouse in May 2027.")],
        invoke=_answer({"fact": 1, "action": "update", "memory_id": other["id"], "text": "rewritten"}))

    assert outcome.action == "add"
    assert kg.get_entity(other["id"])["description"] == "Kitchen renovation."
    assert kg.get_entity(outcome.entity_id)["subject"] == "Greenhouse"


def test_without_an_answer_nothing_is_written(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, close = stack
    near = kg.save_entity("project", "Garden beds", "Raised beds planned for spring.")
    close[near["id"]] = 0.80

    def outage(prompt: str) -> str:
        raise TimeoutError("the model did not answer")

    with pytest.raises(reconcile.ReconcileUnavailable):
        reconcile.reconcile([reconcile.NewMemory("project", "Greenhouse", "Building a greenhouse.")], invoke=outage)
    assert kg.count_entities() == 1


def test_a_document_never_rewrites_or_flags_a_personal_memory(stack, monkeypatch) -> None:
    kg, _memory, extraction, _reconcile, tool, close = stack
    mine = kg.save_entity("fact", "Blood type", "The user's blood type is O negative.", source="live")
    close[mine["id"]] = 0.90
    monkeypatch.setattr(tool, "_check_contradiction", lambda *args, **kwargs: "It says A positive.")

    extraction._dedup_and_save(
        [{"category": "fact", "subject": "Blood type", "content": "Blood type A positive is the most common."}],
        source="document:handbook.pdf")

    kept = kg.get_entity(mine["id"])
    assert kept["description"] == mine["description"] and kept["source"] == "live"
    assert _props(kept).get("status", "active") == "active"
    documents = [e for e in kg.list_entities(limit=10) if e["source"] == "document:handbook.pdf"]
    assert [e["subject"] for e in documents] == ["Blood type"]


def test_a_self_knowledge_memory_is_never_rewritten_or_replaced(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, close = stack
    lesson = kg.save_entity("self_knowledge", "Video: check the transcript", "Read the transcript before sending.",
                            source="learning", properties={"learning_change": "change-1"})
    close[lesson["id"]] = 0.95

    [outcome] = reconcile.reconcile(
        [reconcile.NewMemory("self_knowledge", "Video: check the transcript", "Also check the captions are off.")],
        invoke=_never)

    assert outcome.action == "add" and outcome.entity_id != lesson["id"]
    assert kg.get_entity(lesson["id"])["description"] == "Read the transcript before sending."
    assert _props(kg.get_entity(lesson["id"])) == {"learning_change": "change-1"}


def test_a_fact_about_the_person_gets_its_own_memory_once_their_record_is_full(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, _close = stack
    user = kg.save_entity("person", "User", "About the person. " * 100)
    invoke = _answer({"fact": 1, "action": "add", "subject": "Running"})

    [outcome] = reconcile.reconcile([reconcile.NewMemory("preference", "User", "Runs 5 km every Sunday morning.")],
                                    invoke=invoke)

    assert outcome.action == "add" and outcome.entity_id != user["id"]
    assert kg.get_entity(outcome.entity_id)["subject"] == "Running"
    assert kg.get_entity(user["id"])["description"] == user["description"]
    assert user["id"] not in invoke.calls[0]


def test_extraction_reconciles_and_links_by_the_saved_memory(stack) -> None:
    kg, _memory, extraction, _reconcile, _tool, close = stack
    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    close[dark["id"]] = 0.82
    invoke = _answer({"fact": 1, "action": "supersede", "memory_id": dark["id"], "subject": "Recording theme"})

    saved = extraction._dedup_and_save([
        {"category": "preference", "subject": "Recording theme", "content": "App recordings in light theme."},
        {"relation_type": "prefers", "source_subject": "User", "target_subject": "Recording theme", "confidence": 0.9},
    ], invoke=invoke)

    assert saved >= 1
    light = kg.find_by_subject(None, "Recording theme")
    assert _props(kg.get_entity(dark["id"]))["superseded_by"] == light["id"]
    user = kg.find_by_subject(None, "User")
    assert user is None or any(r["peer_subject"] == "Recording theme" for r in kg.get_relations(user["id"]))


def test_save_memory_says_what_it_replaced(stack, monkeypatch) -> None:
    kg, _memory, _extraction, _reconcile, tool, close = stack
    from row_bot.providers import background

    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    close[dark["id"]] = 0.82
    monkeypatch.setattr(background, "invoke_background", lambda *args, **kwargs: json.dumps({"decisions": [
        {"fact": 1, "action": "supersede", "memory_id": dark["id"], "subject": ""}]}))

    reply = tool._save_memory("preference", "Recording theme", "Recordings of the app are made in light theme.")

    assert reply.startswith("Memory saved. It replaces an older memory, kept as history: App recordings theme")
    assert _props(kg.get_entity(dark["id"]))["status"] == "superseded"


def test_save_memory_keeps_the_fact_when_the_model_cannot_answer(stack, monkeypatch) -> None:
    kg, _memory, _extraction, _reconcile, tool, close = stack
    from row_bot.providers import background

    near = kg.save_entity("project", "Garden beds", "Raised beds planned for spring.")
    close[near["id"]] = 0.80

    def outage(*args, **kwargs):
        raise TimeoutError("no answer")

    monkeypatch.setattr(background, "invoke_background", outage)

    reply = tool._save_memory("project", "Greenhouse", "Building a greenhouse in May 2027.")

    assert reply.startswith("Memory saved successfully.")
    assert kg.find_by_subject(None, "Greenhouse") is not None
    assert kg.get_entity(near["id"])["description"] == near["description"]


def test_a_long_record_is_never_cut_down_or_hidden(stack) -> None:
    kg, _memory, _extraction, reconcile, _tool, close = stack
    register = kg.save_entity("project", "Outreach register", "Sent to creator. " * 200)
    close[register["id"]] = 0.88

    outcomes = reconcile.reconcile(
        [reconcile.NewMemory("project", "Outreach register", "Sent to one more creator on 12 Nov 2026."),
         reconcile.NewMemory("project", "Outreach plan", "Outreach now goes out on Tuesdays.")],
        invoke=_answer({"fact": 1, "action": "update", "memory_id": register["id"], "text": "Shortened."},
                       {"fact": 2, "action": "supersede", "memory_id": register["id"], "subject": "Outreach plan"}))

    # The model saw only the start of the record: rewriting it would cut the rest, replacing it would hide it.
    assert [outcome.action for outcome in outcomes] == ["add", "add"]
    kept = kg.get_entity(register["id"])
    assert kept["description"] == register["description"]
    assert _props(kept).get("status", "active") == "active"
