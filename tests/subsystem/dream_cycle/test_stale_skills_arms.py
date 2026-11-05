"""The stale-skills arms: R reconciles overnight, D notes the dates (never favouring an older memory), F repairs only
after a failed run. Each acts only under its own arm."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from row_bot import stale_skills
from tests.fixtures.stale_skills import stale_world

pytestmark = pytest.mark.subsystem

BREAD = ("Use this when Mara asks for the bread order.\n1. Send one email with `mail_send` to "
         "hello@sandgate-bakery.example with the number of large loaves.\n2. Tell Mara it's done.")
FLOAT = "1. Send one email with `mail_send` to finance@lanternlane.example: the petty-cash float is GBP 150."


@pytest.fixture
def world(tmp_path, monkeypatch, reload_for_data_dir):
    return stale_world(tmp_path, monkeypatch, reload_for_data_dir, arm="R")


class Model:
    """A drafting model that swaps one value for another and records what it was asked."""

    def __init__(self, old: str = "hello@", new: str = "orders@", *, patch: bool = True) -> None:
        self.old, self.new, self.patch = old, new, patch
        self.prompts: list[str] = []

    def __call__(self, prompt: str, *, answer=None, max_tokens=None) -> str:
        self.prompts.append(prompt)
        skill = prompt.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
        return json.dumps({"patch": self.patch, "instructions": skill.replace(self.old, self.new) if self.patch else "",
                           "reason": "the address changed" if self.patch else "nothing to change"})


def _scene(world) -> dict:
    bakery = world.kg.save_entity("organisation", "Sandgate Bakery", "Orders go to hello@sandgate-bakery.example.")
    world.kg.save_entity("organisation", "Finance", "The finance team, finance@lanternlane.example.")
    world.skill("bread_order_procedure", BREAD, display="Bread order")
    world.skill("petty_cash_procedure", FLOAT, display="Petty cash")
    world.at(datetime(2026, 11, 21, 10, 0))
    world.kg.update_entity(bakery["id"], "Orders go to orders@sandgate-bakery.example from today; hello@ isn't read.")
    return bakery


def _candidates() -> list[dict]:
    return [item for item in stale_skills.operations() if item["event"] == "patch_candidate"]


def test_r_drafts_a_bounded_patch_for_the_skill_whose_memory_changed_and_only_once(world):
    from row_bot import evolution
    from row_bot.stale_skills import arms

    _scene(world)
    model = Model()
    world.at(datetime(2026, 11, 22, 1, 0))
    tonight = arms.reconcile(model)
    world.at(datetime(2026, 11, 23, 1, 0))
    next_night = arms.reconcile(model)

    assert (tonight["candidates"], tonight["proposed"], next_night["candidates"]) == (1, 1, 0)
    [candidate] = _candidates()
    assert (candidate["skill"], candidate["kind"], candidate["outcome"]) == ("bread_order_procedure", "update", "proposed")
    assert candidate["skill_says"] == ["hello@sandgate-bakery.example"]
    assert "Monday 2 November 2026" in model.prompts[0] and "Saturday 21 November 2026" in model.prompts[0]
    proposal = evolution.get_proposal(candidate["proposal_id"])
    assert proposal["proposal_type"] == "patch_skill" and proposal["status"] == "ready"
    assert proposal["preview"]["changed_lines"] == 2 and proposal["preview"]["bounded"] is True
    assert "petty_cash_procedure" not in json.dumps(_candidates())

    result = evolution.apply_proposal(proposal["id"], require_approval=False, approved_by_user=True)
    assert result["ok"]
    assert "orders@sandgate-bakery.example" in world.skills.get_skill("bread_order_procedure").instructions
    [patched] = [item for item in stale_skills.operations() if item["event"] == "patch"]
    assert patched["cause"] == f"proposal:{proposal['id']}"


def test_r_records_a_candidate_the_model_says_needs_no_patch(world):
    from row_bot.stale_skills import arms

    _scene(world)
    result = arms.reconcile(Model(patch=False))

    assert (result["candidates"], result["proposed"]) == (1, 0)
    assert [item["outcome"] for item in _candidates()] == ["no_change"]


def test_r_reconciles_a_new_memory_that_states_a_newer_value(world):
    from row_bot.stale_skills import arms

    world.kg.save_entity("organisation", "Finance", "The finance team, finance@lanternlane.example.")
    world.skill("petty_cash_procedure", FLOAT, display="Petty cash")
    world.later(days=20)
    world.kg.save_entity("fact", "Weekly petty-cash float", "From this week the float is GBP 200, not GBP 150.")
    world.kg.save_entity("fact", "Van MOT", "The van's MOT is on 15 January.")

    result = arms.reconcile(Model("GBP 150", "GBP 200"))

    assert (result["candidates"], result["proposed"]) == (1, 1)
    [candidate] = _candidates()
    assert (candidate["kind"], candidate["fresh"], candidate["skill_says"]) == ("write", ["gbp 200"], ["gbp 150"])


def test_r_does_nothing_under_another_arm(world):
    from row_bot.stale_skills import arms

    _scene(world)
    for arm in ("N", "D", "F"):
        world.set_arm(arm)
        assert arms.reconcile(Model()) == {}
    assert _candidates() == []


def test_d_names_both_values_and_both_dates_when_memory_is_newer(world):
    from row_bot.stale_skills import arms

    _scene(world)
    world.set_arm("D")
    line = arms.date_note("bread_order_procedure")

    assert "\n" not in line
    assert line.startswith('DATE CHECK for the skill "Bread order": the skill was last revised on Monday 2 November 2026 '
                           'and says "hello@sandgate-bakery.example".')
    assert 'The memory "Sandgate Bakery" is NEWER: it changed on Saturday 21 November 2026, after the skill' in line
    assert line.endswith("follow the newer memory (orders@sandgate-bakery.example), not the skill.")
    assert arms.date_note("petty_cash_procedure") == ""
    for arm in ("N", "R", "F"):
        world.set_arm(arm)
        assert arms.date_note("bread_order_procedure") == ""


def test_d_never_favours_an_older_memory_over_a_newer_skill(world):
    """The wrong-switch case: the skill was revised after memory changed, so no line sends the model to memory."""

    from row_bot.stale_skills import arms

    bakery = world.kg.save_entity("organisation", "Sandgate Bakery", "Orders go to hello@sandgate-bakery.example.")
    world.at(datetime(2026, 11, 10, 9, 0))
    world.kg.update_entity(bakery["id"], "Orders go to bread@sandgate-bakery.example.")
    world.at(datetime(2026, 11, 21, 9, 0))
    world.skill("bread_order_procedure", BREAD.replace("hello@", "orders@"), display="Bread order")
    world.set_arm("D")
    assert arms.date_note("bread_order_procedure") == ""  # newer skill, older memory: no line

    world.at(datetime(2026, 11, 22, 9, 0))
    world.skills.update_skill("bread_order_procedure", instructions=BREAD)  # revised again, after the memory
    assert arms.date_note("bread_order_procedure") == ""


def test_f_drafts_a_repair_from_the_failed_run_only_under_f(world):
    from row_bot import evolution
    from row_bot.stale_skills import arms

    _scene(world)
    model = Model()
    args = {"request": "Bread order please: 20 large loaves.", "answer": "Sent to hello@sandgate-bakery.example.",
            "correction": "That address isn't checked any more - use orders@sandgate-bakery.example.", "llm": model}
    for arm in ("N", "R", "D"):
        world.set_arm(arm)
        assert arms.repair("bread_order_procedure", **args) == {}
    world.set_arm("F")
    outcome = arms.repair("bread_order_procedure", **args)

    assert outcome["outcome"] == "proposed"
    assert "use orders@sandgate-bakery.example" in model.prompts[0] and "hello@sandgate" in model.prompts[0]
    proposal = evolution.get_proposal(outcome["proposal_id"])
    assert proposal["proposal_type"] == "patch_skill" and proposal["title"].startswith("Repair skill: Bread order")
    assert [(item["arm"], item["skill"]) for item in _candidates()] == [("F", "bread_order_procedure")]


def test_r_drafts_once_a_night_per_skill_from_its_latest_change(world):
    from row_bot.stale_skills import arms

    bakery = _scene(world)
    world.later(hours=2)
    world.kg.update_entity(bakery["id"], "Orders go to bread@sandgate-bakery.example now; hello@ isn't read.")
    model = Model()
    result = arms.reconcile(model)

    assert (result["candidates"], result["drafted"], result["proposed"]) == (2, 1, 1)
    assert "bread@sandgate-bakery.example" in model.prompts[0]
    assert sorted(item["outcome"] for item in _candidates()) == ["later_change_drafted", "proposed"]
