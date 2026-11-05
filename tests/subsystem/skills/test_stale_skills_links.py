"""Skill–memory links while the stale-skills setting is on: made at create, remade at every patch, extended when a
memory the skill names is written later."""

from __future__ import annotations

import pytest

from row_bot import stale_skills
from tests.fixtures.stale_skills import stale_world

pytestmark = pytest.mark.subsystem

SKILL = ("Use this when Mara asks for the bread order.\n1. Send one email with `mail_send` to Sandgate Bakery, "
         "hello@sandgate-bakery.example, with the number of large loaves.\n2. Tell Mara it's done.")


@pytest.fixture
def world(tmp_path, monkeypatch, reload_for_data_dir):
    return stale_world(tmp_path, monkeypatch, reload_for_data_dir, arm="N")


def _refs(name: str) -> set[tuple[str, str]]:
    from row_bot.stale_skills import links

    return {(item["property"], item["span"]) for item in (links.skill_record(name) or {})["references"]}


def test_a_skill_is_linked_to_the_memories_it_names_when_it_is_created(world):
    world.kg.save_entity("organisation", "Sandgate Bakery", "Bakery; orders to hello@sandgate-bakery.example.",
                         aliases="the bakery")
    world.kg.save_entity("person", "Hal Brennan", "Building manager, hal@lanternlane.example.")
    world.kg.save_entity("person", "User", "Mara; sends hello@sandgate-bakery.example the bread order.")
    world.skill("bread_order_procedure", SKILL)

    assert _refs("bread_order_procedure") == {("subject", "Sandgate Bakery"),
                                              ("value", "hello@sandgate-bakery.example")}
    [created] = [item for item in stale_skills.operations() if item["event"] == "skill_create"]
    assert created["skill"] == "bread_order_procedure" and len(created["references"]) == 2


def test_a_patch_relinks_the_skill_and_moves_its_revision(world):
    from row_bot.stale_skills import links

    world.kg.save_entity("organisation", "Sandgate Bakery", "Bakery; orders to hello@sandgate-bakery.example.")
    world.kg.save_entity("organisation", "Fenwick Bakery", "Second bakery; orders@fenwick.example.")
    world.skill("bread_order_procedure", SKILL)
    first = links.skill_record("bread_order_procedure")
    world.later(days=3)
    world.skills.update_skill("bread_order_procedure", instructions=SKILL.replace(
        "Sandgate Bakery, hello@sandgate-bakery.example", "Fenwick Bakery, orders@fenwick.example"))
    world.later(days=1)
    world.skills.update_skill("bread_order_procedure", description="Only the description changed.")

    assert _refs("bread_order_procedure") == {("subject", "Fenwick Bakery"), ("value", "orders@fenwick.example")}
    record = links.skill_record("bread_order_procedure")
    assert (first["revised_at"], record["revised_at"]) == ("2026-11-02T09:00:00", "2026-11-05T09:00:00")
    assert [item["event"] for item in stale_skills.operations() if item.get("skill")] == ["skill_create", "patch"]


def test_a_memory_written_after_the_skill_is_linked_to_it(world):
    world.skill("petty_cash_procedure", "1. Email finance@lanternlane.example: the petty-cash float is GBP 150.")
    assert _refs("petty_cash_procedure") == set()
    world.later(days=20)
    world.kg.save_entity("fact", "Weekly petty-cash float", "The float went up from GBP 150 to GBP 200.")

    assert _refs("petty_cash_procedure") == {("subject_part", "petty-cash float"), ("value", "gbp 150")}


def test_values_are_matched_however_they_are_written():
    from row_bot.stale_skills import links

    assert links.value_tokens("£1,200.50 and GBP 150.00, 1pm to 16:00, a 48-hour notice, 5°C, 3 shifts") == {
        "gbp 1200.5": "money", "gbp 150": "money", "13:00": "time", "16:00": "time", "48 hours": "measure",
        "5 °c": "measure", "3 shifts": "measure"}
    assert links.value_tokens("GBP 1.50 a loaf") == {"gbp 1.5": "money"}  # not a time of day
    # A clock time with its am/pm (smoke R, 26 Oct: "from 6:00 PM to 8:00 PM" was read as 06:00 and 08:00).
    assert links.value_tokens("from 6:00 PM to 8:00 pm, or 12:30am") == {"18:00": "time", "20:00": "time",
                                                                          "00:30": "time"}
    assert links.as_written("gbp 150", "the float is £150 a week") == "£150"  # shown as the text writes it
