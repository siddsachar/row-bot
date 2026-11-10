"""Fact validity while the stale-skills setting is on: when each fact held, what replaced it, and "as of" recall."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from row_bot import stale_skills
from tests.fixtures.stale_skills import stale_world

pytestmark = pytest.mark.subsystem


@pytest.fixture
def world(tmp_path, monkeypatch, reload_for_data_dir):
    return stale_world(tmp_path, monkeypatch, reload_for_data_dir, arm="N")


def _events(name: str) -> list[dict]:
    return [item for item in stale_skills.operations() if item["event"] == name]


def test_an_updated_fact_closes_and_names_the_value_that_replaced_it(world):
    from row_bot.stale_skills import facts

    bakery = world.kg.save_entity("organisation", "Sandgate Bakery", "Orders go to hello@sandgate-bakery.example.")
    world.at(datetime(2026, 11, 21, 10, 0))
    world.kg.update_entity(bakery["id"], "Orders go to orders@sandgate-bakery.example now.")

    first, second = facts.history(bakery["id"])
    assert (first["valid_from"], first["valid_to"], first["superseded_by"]) == (
        "2026-11-02T09:00:00", "2026-11-21T10:00:00", second["id"])
    assert (second["value"], second["valid_to"]) == ("Orders go to orders@sandgate-bakery.example now.", None)
    written_then, change = facts.changes()
    assert (written_then["kind"], written_then["at"]) == ("write", "2026-11-02T09:00:00")
    assert change["kind"] == "update" and change["old"].endswith("hello@sandgate-bakery.example.")
    [written] = _events("write")
    [superseded] = _events("supersede")
    assert written["entity_id"] == bakery["id"] and written["validity"] == [first["id"]]
    assert (superseded["old_validity"], superseded["new_validity"]) == (first["id"], second["id"])


def test_a_memory_superseded_by_another_closes_with_the_new_one(world):
    from row_bot.stale_skills import facts

    old = world.kg.save_entity("fact", "Petty-cash float", "The weekly float is GBP 150.")
    world.later(days=20)
    new = world.kg.save_entity("fact", "Weekly petty-cash float", "The weekly float is GBP 200 from 22 November.")
    world.later(minutes=5)
    world.memory_evolution.mark_superseded(old["id"], new["id"], reason="newer float")

    closed = facts.history(old["id"])[0]
    opened = facts.history(new["id"])[0]
    assert (closed["valid_to"], closed["superseded_by"], closed["ended_cause"]) == (
        "2026-11-22T09:05:00", opened["id"], "supersede")
    # The new memory counts once, as what superseded the old one (not also as a new memory).
    assert [(item["kind"], item["entity_id"], item["new_entity_id"]) for item in facts.changes()] == [
        ("write", old["id"], old["id"]), ("supersede", old["id"], new["id"])]


def test_only_facts_change_not_bookkeeping(world):
    from row_bot.stale_skills import facts

    entity = world.kg.save_entity("person", "Olu", "Treasurer.", properties={
        "email": "olu@lanternlane.example", "status": "active", "source_thread_id": "t1", "extraction_method": "llm"})
    world.later(days=1)
    world.memory_evolution.mark_needs_review(entity["id"], "conflicts", incoming={"content": "Priyanka is treasurer."})
    world.later(days=1)
    current = world.kg.get_entity(entity["id"])
    props = json.loads(current["properties"])
    world.kg.update_entity(entity["id"], current["description"],
                           properties={**props, "email": "priyanka@x.example", "source_thread_id": "t2"})

    facts_seen = sorted((row["fact"], row["valid_to"] is None) for row in facts.history(entity["id"]))
    assert facts_seen == [("description", True), ("property:email", False), ("property:email", True)]
    [conflict] = _events("conflict")
    assert conflict["incoming"] == "Priyanka is treasurer."


def test_as_of_recall_says_what_memory_said_then_and_leaves_out_what_came_later(world):
    bakery = world.kg.save_entity("organisation", "Sandgate Bakery", "Bakery orders go to hello@sandgate-bakery.example.")
    world.at(datetime(2026, 11, 21, 10, 0))
    world.kg.update_entity(bakery["id"], "Bakery orders go to orders@sandgate-bakery.example.")
    world.kg.save_entity("organisation", "Fenwick Bakery", "A second bakery, added on 21 November.")

    then = json.loads(world.memory_tool._search_memory("bakery", as_of="2026-11-10"))
    now = json.loads(world.memory_tool._search_memory("bakery"))

    assert [(item["subject"], item["content"]) for item in then] == [
        ("Sandgate Bakery", "Bakery orders go to hello@sandgate-bakery.example.")]
    assert {item["subject"] for item in now} == {"Sandgate Bakery", "Fenwick Bakery"}
    tool = next(item for item in world.memory_tool.MemoryTool().as_langchain_tools() if item.name == "search_memory")
    assert "as_of" in tool.args


def test_the_migration_is_additive_and_runs_once(world):
    from row_bot.stale_skills import facts

    world.set_arm(None)
    first = world.kg.save_entity("person", "Hal Brennan", "Building manager, hal@lanternlane.example.")
    second = world.kg.save_entity("organisation", "Lantern Lane", "The charity kitchen.")
    world.kg.add_relation(first["id"], second["id"], "works_at")
    before = {row["id"]: dict(row) for row in world.kg.list_entities(limit=100)}
    assert facts.TABLE not in world.tables()

    world.set_arm("N")
    world.later(days=3)
    world.kg.update_entity(first["id"], "Building manager until 5 November; now Rosa Lind is.")

    history = facts.history(first["id"])
    assert [(row["cause"], row["valid_to"] is None) for row in history if row["fact"] == "description"] == [
        ("migrate", False), ("", True)]
    assert [row["cause"] for row in facts.history(second["id"])] == ["migrate"]
    relation = [row for row in history if row["fact"] == "relation:works_at"]
    assert relation and relation[0]["target_id"] == second["id"]
    conn = world.kg._get_conn()
    try:
        assert facts.migrate(conn) == 0
    finally:
        conn.close()
    after = {row["id"]: dict(row) for row in world.kg.list_entities(limit=100)}
    assert after[second["id"]] == before[second["id"]]  # the migration never touched a memory


def test_dream_tidying_is_not_a_change_in_the_world(world):
    from row_bot.stale_skills import facts

    survivor = world.kg.save_entity("organisation", "Sandgate Bakery", "Bakery, hello@sandgate-bakery.example.")
    duplicate = world.kg.save_entity("organisation", "Sandgate", "The bakery.")
    world.later(days=1)
    with stale_skills.cause("merge"):
        world.kg.update_entity(survivor["id"], "Sandgate Bakery, the bakery, hello@sandgate-bakery.example.")
        world.kg.delete_entity(duplicate["id"])
    with stale_skills.cause("enrich"):
        world.kg.update_entity(survivor["id"], "Sandgate Bakery bakes our bread; hello@sandgate-bakery.example.")

    assert [item["kind"] for item in facts.changes(since="2026-11-02T09:00:00")] == []
    assert [item["entity_id"] for item in _events("merge")] == [duplicate["id"]]
