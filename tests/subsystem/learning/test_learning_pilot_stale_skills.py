"""The stale-skills pilot's timeline and scorer (Lane B, MEMORY-SKILLS §4): deterministic, no network."""

from __future__ import annotations

import pytest

from tests.e2e.learning.seeded import pilot_scorer as scorer
from tests.e2e.learning.seeded import pilot_timeline as timeline_module
from tests.e2e.learning.seeded.generator import day_date


@pytest.fixture(scope="module")
def timeline() -> timeline_module.Timeline:
    return timeline_module.build()


def test_the_two_changes_land_on_days_10_and_19_and_touch_one_recurring_task_each(timeline):
    changes = {change.fact: change for change in timeline.changes}
    assert [change.day for change in timeline.changes] == [10, 19]
    assert {change.task for change in timeline.changes} == {"bread_order", "weekend_shift"}
    for change in timeline.changes:
        assert timeline.value(change.fact, change.day - 1) == change.old
        assert timeline.value(change.fact, change.day) == change.new
        assert timeline.value(change.fact, timeline.days) == change.new
        # The person tells Row-Bot on the change's morning, before that day's runs.
        tell = next(item for item in timeline.tells if item.fact == change.fact)
        assert tell.day == change.day and tell.order == 0
        assert timeline_module.mentions(change.fact, change.new, tell.prompt)
    # The task's runs ask for the value in effect on their day, and the task runs after its change too.
    for request in timeline.requests:
        task = scorer.task_of(request.id)
        fact = timeline_module.TASK_FACT[task]
        assert timeline_module.mentions(fact, timeline.value(fact, request.day), request.intent)
    after = {task: [r for r in timeline.requests if scorer.task_of(r.id) == task
                    and timeline.after_change(timeline_module.TASK_FACT[task], r.day)] for task in ("bread_order",
                                                                                                 "weekend_shift")}
    assert len(after["bread_order"]) == 15 and len(after["weekend_shift"]) == 5
    assert changes["weekend_shift_hours"].day <= 20


def test_the_world_changes_with_the_fact(timeline):
    change = timeline.change("weekend_shift_hours")
    rewritten = timeline.files_changed_on(change.day)[timeline_module.ROTA_POLICY]
    assert "(version 4)" in rewritten and "(09:00-14:00)" in rewritten and "10:00-15:00" not in rewritten
    assert timeline.files_changed_on(change.day - 1) == {}
    sandgate = timeline.change("sandgate_orders_email").day

    def letters(day: int) -> list:
        messages = timeline_module.apps_on(timeline.history.start_apps, day)["mail"]["messages"]
        return [message for message in messages if message["id"] == "msg_sandgate_address"]

    assert letters(sandgate - 1) == [] and letters(sandgate)


def test_the_same_seed_gives_the_same_timeline():
    assert timeline_module.build().fingerprint() == timeline_module.build().fingerprint()
    assert timeline_module.build(seed=7).fingerprint() != timeline_module.build().fingerprint()


def _mail(to: str) -> dict:
    return {"kind": "mail", "to": to, "body": "Please deliver 20 large loaves tomorrow."}


def _event(start: str, end: str) -> dict:
    return {"kind": "event", "title": "Ayo - weekend shift", "date": "2026-11-28", "start": start, "end": end}


def test_a_run_is_stale_only_after_the_change_and_only_on_the_old_value(timeline):
    old, new = timeline_module.SANDGATE_OLD, timeline_module.SANDGATE_NEW
    before = scorer.score(timeline, "mara-pilot-d09-bread", 9, [_mail(old)])
    assert (before.verdict, before.after_change) == (scorer.CURRENT, False)
    assert scorer.score(timeline, "mara-pilot-d10-bread", 10, [_mail(old)]).verdict == scorer.STALE
    assert scorer.score(timeline, "mara-pilot-d11-bread", 11, [_mail(new)]).verdict == scorer.CURRENT
    assert scorer.score(timeline, "mara-pilot-d11-bread", 11, [_mail("someone@else.example")]).verdict == scorer.OTHER
    assert scorer.score(timeline, "mara-pilot-d11-bread", 11, []).verdict == scorer.NO_ACTION
    # Both addresses in one attempt: it acted on the old fact.
    assert scorer.score(timeline, "mara-pilot-d12-bread", 12, [_mail(new), _mail(old)]).verdict == scorer.STALE
    # An event in a bread-order run isn't the task's action.
    assert scorer.score(timeline, "mara-pilot-d12-bread", 12, [_event("10:00", "15:00")]).verdict == scorer.NO_ACTION

    assert scorer.score(timeline, "mara-pilot-d17-shift", 17, [_event("10:00", "15:00")]).verdict == scorer.CURRENT
    assert scorer.score(timeline, "mara-pilot-d17-shift", 17, [_event("09:00", "14:00")]).verdict == scorer.OTHER
    assert scorer.score(timeline, "mara-pilot-d22-shift", 22, [_event("10:00", "15:00")]).verdict == scorer.STALE
    assert scorer.score(timeline, "mara-pilot-d22-shift", 22, [_event("09:00", "14:00")]).verdict == scorer.CURRENT


def test_the_scorer_reads_the_timeline_not_a_fixed_answer(timeline):
    from dataclasses import replace

    # The same action on the same day flips when the timeline's change moves.
    later = replace(timeline, changes=tuple(replace(change, day=25) if change.fact == "sandgate_orders_email"
                                            else change for change in timeline.changes))
    old = timeline_module.SANDGATE_OLD
    assert scorer.score(timeline, "mara-pilot-d15-bread", 15, [_mail(old)]).verdict == scorer.STALE
    assert scorer.score(later, "mara-pilot-d15-bread", 15, [_mail(old)]).verdict == scorer.CURRENT


def test_actions_come_from_the_apps_state_the_attempt_left():
    before = {"mail": {"messages": [{"id": "msg_1", "folder": "sent", "to": "a@x.example"}]},
              "calendar": {"events": [{"id": "evt_1", "title": "Old", "start": "10:00", "end": "15:00"}]}}
    after = {"mail": {"messages": [*before["mail"]["messages"],
                                   {"id": "sent_2", "folder": "sent", "to": "b@x.example", "body": "20"},
                                   {"id": "msg_9", "folder": "inbox", "to": "me@example.test"}]},
             "calendar": {"events": [*before["calendar"]["events"],
                                     {"id": "evt_2", "title": "Ayo - weekend shift", "date": "2026-11-28",
                                      "start": "09:00", "end": "14:00"}]}}
    found = scorer.actions_between(before, after)
    assert [item["kind"] for item in found] == ["mail", "event"]
    assert found[0]["to"] == "b@x.example" and found[1]["start"] == "09:00"


def test_recall_counts_as_surfacing_the_new_fact_only_when_it_states_it(timeline):
    assert scorer.surfaced(timeline, "bread_order", 12, ["Sandgate orders go to orders@sandgate-bakery.example"])
    assert not scorer.surfaced(timeline, "bread_order", 12, ["Sandgate Bakery: hello@sandgate-bakery.example"])
    assert scorer.surfaced(timeline, "bread_order", 12, ["Sandgate: hello@sandgate-bakery.example"], which="old")
    assert not scorer.surfaced(timeline, "bread_order", 9, ["orders@sandgate-bakery.example"])
    assert scorer.surfaced(timeline, "weekend_shift", 22, ["The weekend shift now runs 9am to 2pm"])
    assert scorer.surfaced(timeline, "weekend_shift", 22, ["Saturday shifts: 09:00-14:00"])
    assert not scorer.surfaced(timeline, "weekend_shift", 22, ["Hartwell delivers before 09:00"])
    assert not scorer.surfaced(timeline, "weekend_shift", 22, ["Weekend shift 19:00 briefing"])


def test_rates_and_the_fixed_decision_rule():
    runs = [
        {"task": "bread_order", "after_change": True, "verdict": "stale", "recalled_new": True, "skill_loaded": True},
        {"task": "bread_order", "after_change": True, "verdict": "current", "recalled_new": True},
        {"task": "weekend_shift", "after_change": True, "verdict": "stale", "recalled_new": False},
        {"task": "weekend_shift", "after_change": True, "verdict": "no_action", "recalled_new": False},
        {"task": "weekend_shift", "after_change": False, "verdict": "current"},
    ]
    found = scorer.rates(runs)
    assert found["overall"] == {"stale": 2, "runs": 4, "rate": 0.5}
    assert found["recall_surfaced_new"] == {"stale": 1, "runs": 2, "rate": 0.5}
    assert found["recall_did_not_surface_new"] == {"stale": 1, "runs": 2, "rate": 0.5}
    assert found["skill_loaded"]["runs"] == 1 and found["verdicts"]["no_action"] == 1
    assert scorer.decision({"main": 0.0, "local": 0.2}) == "go"
    assert scorer.decision({"main": 0.04, "local": 0.0}) == "no-go"
    assert scorer.decision({"main": 0.05, "local": 0.0}) == "in between"
    assert scorer.decision({"main": 0.19, "local": None}) == "undecided"


def test_the_seeded_skill_names_the_value_of_its_day(timeline):
    early = timeline_module.seeded_skill("bread_order", 3, timeline)["instructions"]
    assert timeline_module.SANDGATE_OLD in early and timeline_module.SANDGATE_NEW not in early
    shift = timeline_module.seeded_skill("weekend_shift", 5, timeline)["instructions"]
    assert "start 10:00, end 15:00" in shift
    assert day_date(1).weekday() == 0  # live day 1 is a Monday: the schedule's weekdays line up


def test_seeds_pool_into_one_rate_with_its_interval():
    from tests.e2e.learning.seeded import pilot_report

    run = {"task": "bread_order", "after_change": True, "recalled_new": True}
    table = pilot_report.pooled({1: [{**run, "verdict": "stale"}, {**run, "verdict": "current"}],
                                 2: [{**run, "verdict": "current"}, {**run, "verdict": "current"}]})
    assert table["1"]["overall"]["rate"] == 0.5 and table["2"]["overall"]["stale"] == 0
    assert table["pooled"]["overall"] == {"stale": 1, "runs": 4, "rate": 0.25,
                                          "wilson95": pilot_report.wilson(1, 4)}
    assert pilot_report.wilson(4, 20) == [8.1, 41.6] and pilot_report.wilson(0, 0) is None
