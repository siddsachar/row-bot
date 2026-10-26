"""The precedence study's items and scorer (Lane B, PRECEDENCE.md): deterministic, no network."""

from __future__ import annotations

from collections import Counter

import pytest

from tests.e2e.learning.precedence import items as items_module
from tests.e2e.learning.precedence import scorer


@pytest.fixture(scope="module")
def generated() -> tuple:
    return items_module.generate()


def test_the_same_seed_gives_the_same_items_and_another_seed_others(generated):
    assert items_module.fingerprint(generated) == items_module.fingerprint(items_module.generate())
    assert items_module.fingerprint(items_module.generate(seed=1)) != items_module.fingerprint(generated)
    assert len(generated) == 200
    assert set(Counter(item.kind for item in generated).values()) <= {33, 34}
    assert len({item.id for item in generated}) == 200
    assert all(item.x != item.y for item in generated)


@pytest.mark.parametrize("condition", items_module.CONDITIONS)
def test_each_condition_puts_the_values_where_the_table_says(generated, condition):
    layout = items_module.LAYOUT[condition]
    for item in generated:
        built = items_module.prompt(item, condition)
        values = {"x": item.x, "y": item.y}
        if layout["skill"] is None:
            assert built.skill is None
        else:
            assert scorer.states(item, values[layout["skill"]], built.skill)
            other = "y" if layout["skill"] == "x" else "x"
            assert not scorer.states(item, values[other], built.skill)
            assert built.skill_updated in built.skill
        if layout["memory"] is None:
            assert built.memory is None
        else:
            assert scorer.states(item, values[layout["memory"]], built.memory)
            assert built.memory_since in built.memory
        assert (built.note is not None) == layout["note"]
        if built.memory and built.skill:
            # The newer side carries the later date: the memory in 3 and 4, the skill in 6.
            newer = built.skill_updated if condition == 6 else built.memory_since
            older = built.memory_since if condition == 6 else built.skill_updated
            assert older < newer
        if built.note:
            assert scorer.states(item, built.skill_value, built.note) and built.memory_since in built.note


def test_the_first_action_is_read_from_the_items_tool_or_the_text(generated):
    by_kind = {item.kind: item for item in generated}
    mail = by_kind["address"]
    assert scorer.first_action(mail, [{"name": "mail_send", "args": {"to": mail.x}}], "")["choice"] == scorer.X
    assert scorer.first_action(mail, [{"name": "tool_invoke", "args": {"name": "mail_send",
                                                                      "arguments": {"to": mail.y}}}], "")["choice"] == "y"
    # Another tool first: no side taken yet.
    assert scorer.first_action(mail, [{"name": "search_memory", "args": {"query": mail.x}}], "")["choice"] == "neither"
    assert scorer.first_action(mail, [], f"Should I use {mail.x} or {mail.y}?")["choice"] == "both"
    assert scorer.first_action(mail, [], "Which address?")["choice"] == "neither"
    hours = by_kind["hours"]
    call = {"name": "calendar_create_event", "args": {"start": hours.x, "end": "23:59"}}
    assert scorer.first_action(hours, [call], "")["choice"] == "x"
    amount = by_kind["amount"]
    body = {"name": "mail_send", "args": {"to": "a@b.example", "subject": "Payment",
                                          "body": f"We'll pay {amount.y.split()[-1]}.00 this month."}}
    assert scorer.first_action(amount, [body], "")["choice"] == "y"
    role = by_kind["role"]
    assert scorer.first_action(role, [{"name": "issues_update", "args": {"assignee": role.y.split()[0]}}],
                               "")["choice"] == "y"
    policy = by_kind["policy"]
    assert scorer.first_action(policy, [{"name": "issues_update", "args": {"add_labels": [policy.x]}}],
                               "")["choice"] == "x"


def test_hours_match_their_written_forms_and_not_other_times():
    item = next(item for item in items_module.generate() if item.kind == "hours")
    assert scorer.states(item, "09:00", "from 9am") and scorer.states(item, "09:00", "at 9:00")
    assert not scorer.states(item, "09:00", "at 19:00") and not scorer.states(item, "09:00", "09:30")
    assert scorer.states(item, "16:30", "4:30 pm")


def test_statistics_and_the_fixed_decision_rule():
    assert scorer.wilson(0, 0) is None and scorer.wilson(4, 20) == [8.1, 41.6]
    assert scorer.mcnemar_exact(0, 0) == 1.0
    assert scorer.mcnemar_exact(10, 0) == pytest.approx(2 / 2 ** 10)
    adjusted = scorer.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted == {"a": 0.03, "c": 0.06, "b": 0.06}
    assert scorer.decision(0.09, True, 0.001, 0.0) == "precedence does not replicate"
    assert scorer.decision(0.12, True, 0.001, 0.0) == "in between"
    assert scorer.decision(0.30, True, 0.01, 0.05) == "both hold"
    assert scorer.decision(0.30, True, 0.01, 0.06) == "precedence replicates, the note does not work"
    assert scorer.decision(0.30, False, 0.01, 0.0) == "precedence replicates, the note does not work"
    interval = scorer.item_clustered_interval({"a": [1, 1, 1], "b": [0, 0, 0]})
    assert interval[0] <= 50.0 <= interval[1]


def test_reads_take_no_side_and_a_question_is_scored_like_a_reply(generated):
    mail = next(item for item in generated if item.kind == "address")
    assert scorer.only_reads([{"name": "skill_load", "args": {}}, {"name": "mail_list", "args": {}}])
    assert not scorer.only_reads([{"name": "skill_load", "args": {}}, {"name": "mail_send", "args": {}}])
    assert not scorer.only_reads([]) and not scorer.only_reads([{"name": "ask_person", "args": {}}])
    asked = {"name": "ask_person", "args": {"questions": [{"question": f"Send to {mail.x}?"}]}}
    assert scorer.first_action(mail, [asked], "")["choice"] == "x"
    assert scorer.first_action(mail, [asked], "")["via"] == "ask"
    # The item's own tool wins over a question in the same reply.
    assert scorer.first_action(mail, [asked, {"name": "mail_send", "args": {"to": mail.y}}], "")["choice"] == "y"
