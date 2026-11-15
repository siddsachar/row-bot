"""The stale-skills world timeline for six people, its scorer, the arms' harness and the run's resume (Design A,
Lane B): deterministic, no network."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tests.e2e.learning.stale_skills import phrasing, run, scorer, world

SEEDS = (20261022, 20261023, 20261024, 7, 99)


@pytest.fixture(scope="module")
def timeline() -> world.Timeline:
    return world.build()


@pytest.fixture(scope="module")
def worlds() -> dict[str, world.Timeline]:
    return {person: world.build(person=person) for person in world.PEOPLE}


def _runs_of(timeline: world.Timeline, procedure: str) -> list:
    return [item for item in timeline.runs if timeline.procedure_of(item.id) == procedure]


@pytest.mark.parametrize("person, seed", [("mara", seed) for seed in SEEDS] + [
    (person, world.SEED) for person in world.PEOPLE if person != "mara"])
def test_the_changes_follow_the_pre_registered_rule(person, seed):
    timeline = world.build(person=person, seed=seed)
    days = [change.day for change in timeline.changes]
    assert len(days) == 8 and len({change.procedure for change in timeline.changes}) == 8
    assert all(20 <= day <= 110 for day in days)
    assert all(later - earlier >= 7 for earlier, later in zip(days, days[1:]))
    assert {change.kind for change in timeline.changes} == set(world.KINDS)
    unchanged = [item.id for item in timeline.procedures if timeline.change_of(item.id) is None]
    assert len(unchanged) == 8
    for procedure in timeline.procedures:
        # The procedures that change run twice a week, the others once (the user's decision, 26 Oct).
        assert len(timeline.schedule[procedure.id]) == (1 if procedure.id in unchanged else 2)
        runs = _runs_of(timeline, procedure.id)
        gaps = [later.day - earlier.day for earlier, later in zip(runs, runs[1:])]
        assert runs[0].day <= 7 and max(gaps) <= 7
    for change in timeline.changes:
        assert change.old != change.new
        assert timeline.value(change.procedure, change.day - 1) == change.old
        assert timeline.value(change.procedure, change.day) == change.new == timeline.value(change.procedure, 120)
        # Every changed procedure runs inside the 14-day window after its change.
        assert any(change.day <= item.day < change.day + world.WINDOW_DAYS for item in _runs_of(timeline,
                                                                                               change.procedure))
    # Two newer-skill events (the person edits a skill; memory is never told) on procedures the world never changes.
    edits = list(timeline.edits)
    assert len(edits) == world.EDITS_PER_PERSON and {edit.procedure for edit in edits} < set(unchanged)
    assert edits[1].day - edits[0].day >= 7 and all(edit.channel == world.EDIT for edit in edits)
    for edit in edits:
        procedure = timeline.procedure(edit.procedure)
        assert procedure.kind != "policy" and 40 <= edit.day <= 110 and edit.old != edit.new
        assert edit.old == timeline.start[edit.procedure] == timeline.value(edit.procedure, edit.day - 1)
        assert timeline.value(edit.procedure, edit.day) == edit.new == timeline.value(edit.procedure, 120)
        # No world source names its fact (the contacts page leaves it out), so nothing but the skill says it moved.
        assert all(f"({procedure.title})" not in timeline.runbook(day) for day in (1, edit.day, 120))
        assert any(edit.day <= item.day < edit.day + world.WINDOW_DAYS for item in _runs_of(timeline, edit.procedure))
    for procedure in set(unchanged) - {edit.procedure for edit in edits}:  # the six controls
        assert {json.dumps(timeline.value(procedure, day), sort_keys=True) for day in range(1, 121)} == {
            json.dumps(timeline.start[procedure], sort_keys=True)}


def test_the_same_seed_gives_the_same_world_and_another_seed_another():
    first, again = world.build(), world.build()
    assert first.fingerprint() == again.fingerprint()
    assert world.build(seed=20261023).fingerprint() != first.fingerprint()
    # The phrasing model changes the words only: the facts, days and values stay the same.
    phrased = world.build(phrasing=phrasing.load(world.PERSON, "main"))
    assert phrased.fingerprint() != first.fingerprint()
    assert [change.to_dict() for change in phrased.changes] == [change.to_dict() for change in first.changes]
    assert [(item.id, item.intent) for item in phrased.runs] == [(item.id, item.intent) for item in first.runs]


def test_a_shorter_run_lives_the_same_days(timeline):
    short = world.build(days=60)
    assert [change.to_dict() for change in short.changes] == [change.to_dict() for change in timeline.changes]
    for name in ("runs", "tells", "probes"):
        assert [repr(item) for item in getattr(short, name)] == [repr(item) for item in getattr(timeline, name)
                                                                  if item.day <= 60]


def test_six_people_each_have_sixteen_procedures_over_the_six_kinds(worlds):
    for person, timeline in worlds.items():
        kinds = [item.kind for item in timeline.procedures]
        assert {kind: kinds.count(kind) for kind in world.KINDS} == {"address": 3, "supplier": 3, "hours": 3,
                                                                      "amount": 2, "role": 2, "policy": 3}, person
        assert len({item.id for item in timeline.procedures}) == 16
        assert timeline.history.past, person  # S1's 30-day past, from S1's generator (the new three too)
    channels = [change.channel for timeline in worlds.values() for change in timeline.changes]
    assert 0.35 <= channels.count("in_passing") / len(channels) <= 0.65  # seeded 50/50


@pytest.mark.parametrize("person", world.PEOPLE)
def test_the_world_renders_each_fact_where_the_rule_says(worlds, person):
    timeline = worlds[person]
    for procedure in timeline.procedures:
        runs = _runs_of(timeline, procedure.id)
        first_value = timeline.value(procedure.id, runs[0].day)
        assert world.states(procedure.kind, first_value, runs[0].prompt)  # the person explains it once
        for item in runs[1:]:
            if "FYI" in item.prompt:
                continue
            assert not any(world.states(procedure.kind, value, item.prompt) for value in procedure.pool)
            # The run's right outcome (the reference) carries the value in effect on its day.
            reference = json.dumps(item.task.reference.calls[0]["args"], ensure_ascii=False)
            assert world.states(procedure.kind, timeline.value(procedure.id, item.day), reference)
    for change in timeline.changes:
        arrives = timeline.arrivals[change.procedure]
        told = [message for tell in timeline.tells if tell.day == change.day and tell.about == change.procedure
                for message in tell.messages]
        passing = [item.prompt for item in timeline.runs if item.day == arrives and "FYI" in item.prompt
                   and world.states(change.kind, change.new, item.prompt)]
        assert (told if change.channel == "told" else passing)
        # In passing on a day with no other request (a weekend): with the next request, before any run of its own.
        assert arrives == change.day or (change.channel == "in_passing" and not any(
            change.day <= item.day < arrives for item in timeline.runs))
        assert world.states(change.kind, change.new, (told or passing)[0])
        # The world itself changes that morning: the runbook (and a policy's own document), and an inbox letter.
        files = timeline.files_on(change.day)
        assert world.states(change.kind, change.new, files[timeline.catalogue.runbook])
        assert not world.states(change.kind, change.new, timeline.runbook(change.day - 1)) or change.kind == "hours"
        procedure = timeline.procedure(change.procedure)
        if procedure.doc:
            assert world.states(change.kind, change.new, files[procedure.doc])
            assert procedure.doc_rule.format(n=change.new["number"]) in files[procedure.doc]
            assert procedure.doc_rule.format(n=change.old["number"]) in timeline.document(procedure.doc, change.day - 1)
        letters = [item["id"] for item in timeline.apps_on(change.day)["mail"]["messages"]]
        assert f"msg_change_{change.procedure}" in letters
        assert f"msg_change_{change.procedure}" not in [item["id"] for item in
                                                        timeline.apps_on(change.day - 1)["mail"]["messages"]]


@pytest.mark.parametrize("person", world.PEOPLE)
def test_each_run_passes_its_check_with_the_right_outcome_and_fails_with_the_decoy(worlds, person):
    from row_bot.learning.packs.fake_apps import FakeApps
    from row_bot.learning.packs.verify import Outcome, verify

    for item in worlds[person].runs:
        for solution, wanted in ((item.task.reference, True), (item.task.decoy, False)):
            apps = FakeApps(item.task.apps)
            for call in solution.calls:
                apps.call(call["tool"], call["args"])
            assert verify(item.task, Outcome("Done.", {}, apps.state()), backend="auto").passed is wanted, item.id


@pytest.mark.parametrize("person", world.PEOPLE)
def test_probes_take_their_answers_from_the_timeline(worlds, person):
    timeline = worlds[person]
    assert sorted({probe.day for probe in timeline.probes}) == list(world.PROBE_DAYS)
    for day in world.PROBE_DAYS:
        mine = [probe for probe in timeline.probes if probe.day == day]
        # As-of and update probes need a change before the probe day (the first change may come after day 30).
        changed = any(change.day <= day for change in timeline.changes)
        assert {probe.category for probe in mine} == set(world.PROBE_CATEGORIES) - (
            set() if changed else {"as_of", "update"})
    for probe in timeline.probes:
        if probe.category == "as_of":
            change = timeline.change_of(probe.fact)
            assert change.day <= probe.day and probe.expected == (json.dumps(change.old, sort_keys=True),)
        if probe.category == "current" and probe.fact in {item.id for item in timeline.procedures}:
            assert probe.expected == (json.dumps(timeline.value(probe.fact, probe.day), sort_keys=True),)


def _mail(to: str, body: str = "Hello") -> dict:
    return {"kind": "mail", "id": "sent_1", "to": to, "subject": "Order", "body": body}


def _event(start: str, end: str) -> dict:
    return {"kind": "event", "id": "evt_9", "title": "x", "date": "2026-12-01", "start": start, "end": end}


def _action(change: world.Change, value: world.Value) -> dict:
    procedure = world.PROCEDURE[change.procedure]
    if procedure.kind == "hours":
        return _event(value["start"], value["end"])
    if procedure.kind in ("address", "supplier", "role"):
        return _mail(value["email"])
    return _mail(procedure.to, f"Reminder: {world.say(procedure.kind, value)}.")


def _run_id(timeline: world.Timeline, procedure: str, after: bool) -> tuple[str, int]:
    change = timeline.shift_of(procedure)
    item = next(item for item in _runs_of(timeline, procedure) if (item.day >= change.day) == after)
    return item.id, item.day


def test_the_scorer_marks_old_new_and_neither_only_from_the_timeline(timeline):
    for change in timeline.changes:
        before_id, before_day = _run_id(timeline, change.procedure, after=False)
        after_id, after_day = _run_id(timeline, change.procedure, after=True)
        old, new = _action(change, change.old), _action(change, change.new)
        assert scorer.score_run(timeline, before_id, before_day, [old]).verdict == scorer.CURRENT
        assert scorer.score_run(timeline, before_id, before_day, [new]).verdict == scorer.NEITHER
        scored = scorer.score_run(timeline, after_id, after_day, [old])
        assert scored.verdict == scorer.OLD and scored.after_change
        assert scored.in_window == (after_day - change.day < world.WINDOW_DAYS)
        assert scorer.score_run(timeline, after_id, after_day, [new]).verdict == scorer.CURRENT
        assert scorer.score_run(timeline, after_id, after_day, []).verdict == scorer.NO_ACTION
        # Only the first acting step counts; an action of another kind isn't one.
        other = _event("23:00", "23:30") if world.PROCEDURE[change.procedure].action == "mail" else _mail("x@y.example")
        assert scorer.score_run(timeline, after_id, after_day, [other, old, new]).verdict == scorer.OLD
        # Move the change past the run: the same action is now right. The scorer reads only the timeline.
        moved = replace(timeline, changes=tuple(replace(item, day=after_day + 1) if item is change else item
                                                for item in timeline.changes))
        assert scorer.score_run(moved, after_id, after_day, [old]).verdict == scorer.CURRENT
    address = next(change for change in timeline.changes if change.kind in ("address", "supplier", "role"))
    after_id, after_day = _run_id(timeline, address.procedure, after=True)
    both = _mail(f"{address.old['email']}, {address.new['email']}")
    assert scorer.score_run(timeline, after_id, after_day, [both]).verdict == scorer.BOTH
    assert scorer.score_run(timeline, after_id, after_day, [_mail("someone@else.example")]).verdict == scorer.NEITHER


def test_value_checkers_read_each_kind_of_fact():
    assert world.states("hours", {"start": "14:00", "end": "17:00"}, "from 2pm to 5pm")
    assert not world.states("hours", {"start": "14:00", "end": "17:00"}, "from 1pm")
    assert world.states("amount", {"amount": 200}, "a float of £200.00")
    assert not world.states("amount", {"amount": 200}, "GBP 2000")
    assert world.states("policy", {"number": 72, "unit": "hours"}, "swaps need 72 hours' notice")
    assert world.states("policy", {"number": 4, "unit": "°C"}, "fridges at 4°C or below")
    assert world.states("policy", {"number": 4, "unit": "shifts"}, "at most four shifts a week")
    assert not world.states("policy", {"number": 4, "unit": "shifts"}, "at most 3 shifts a week, 4 days")
    assert world.states("policy", {"number": 5, "unit": "days"}, "freezes 5 working days before release")
    assert world.states("policy", {"number": 3, "unit": "%"}, "a late fee of 3% a month")
    assert world.states("policy", {"number": 90, "unit": "minutes"}, "at least ninety minutes each")
    assert world.states("policy", {"number": 2, "unit": "reviewers"}, "every merge needs two reviewers")
    assert world.states("policy", {"number": 134, "unit": "°C"}, "cycles run at 134 °C")
    assert not world.states("policy", {"number": 6, "unit": "children"}, "6 adults per trip")


def test_probe_scoring(timeline):
    probe = {"category": "current", "kind": "address", "expected": [json.dumps({"email": "new@x.example"})],
             "wrong": [json.dumps({"email": "old@x.example"})]}
    assert scorer.score_probe(probe, "It's new@x.example now.") == "right"
    assert scorer.score_probe(probe, "It's old@x.example.") == "old"
    assert scorer.score_probe(probe, "It moved from old@x.example to new@x.example.") == "right"
    assert scorer.score_probe(probe, "I don’t have that saved anywhere.") == "abstained"
    as_of = {**probe, "category": "as_of", "expected": probe["wrong"], "wrong": probe["expected"]}
    assert scorer.score_probe(as_of, "Back then it was old@x.example.") == "right"
    assert scorer.score_probe(as_of, "It was new@x.example, after the move from old@x.example.") == "both"
    assert scorer.score_probe(as_of, "It was new@x.example.") == "current_instead"
    assert scorer.score_probe({**probe, "category": "update"}, "It changed from old@x.example to new@x.example.") \
        == "right"
    assert scorer.score_probe({**probe, "category": "update", "expected": probe["expected"] + probe["wrong"]},
                              "It's new@x.example.") == "partial"
    abstain = {"category": "abstention", "kind": "abstain", "expected": [], "wrong": []}
    assert scorer.score_probe(abstain, "I don't have a phone number for Sam Okafor.") == "right"
    assert scorer.score_probe(abstain, "It's 07700 900123.") == "wrong"
    date = {"category": "current", "kind": "date", "expected": ["15 January"], "wrong": ["14 January"]}
    assert scorer.score_probe(date, "The MOT is on January 15th.") == "right"
    assert scorer.score_probe(date, "The MOT is on 14 Jan.") == "old"


def test_the_primary_rate_counts_every_run_in_the_window(timeline):
    change = timeline.changes[0]
    window = [item for item in _runs_of(timeline, change.procedure)
              if change.day <= item.day < change.day + world.WINDOW_DAYS]
    scored = [{**scorer.score_run(timeline, item.id, item.day,
                                  [_action(change, change.old if number == 0 else change.new)] if number < 2 else []
                                  ).to_dict(), "skill_loaded": number == 0, "recalled_new": True, "passed_first": False}
              for number, item in enumerate(window)]
    found = scorer.metrics(timeline, scored)
    assert found["stale_execution"]["k"] == 1 and found["stale_execution"]["n"] == len(window)
    assert found["stale_skill_loaded"] == {"k": 1, "n": 1, "rate": 1.0, "wilson": scorer.wilson(1, 1)}
    assert found["time_to_fix"][change.procedure]["days_to_first_right"] == (window[1].day - change.day
                                                                             if len(window) > 1 else None)


def test_a_run_resumes_at_its_first_unfinished_step(timeline):
    every = run.steps(timeline, 3)
    assert [step[0] for step in every[:1]] == ["morning"] and every[-1] == ("night", 3, "night-3")
    state = {"mornings": [1, 2], "runs": [{"id": item[2]} for item in every if item[0] == "run" and item[1] == 1],
             "tells": [{"id": item[2]} for item in every if item[0] == "tell" and item[1] == 1],
             "probes": [], "evenings": [{"day": 1}], "nights": {"1": {"done": True}, "2": {"steps": {"dream": {}}}}}
    left = run.remaining(timeline, state, 3)
    assert left[0] == next(step for step in every if step[1] == 2 and step[0] != "morning")
    assert ("night", 2, "night-2") in left and all(step[1] >= 2 for step in left)
    # A turn cut off by a crash (never recorded) is asked again; a finished one never is.
    first_run_day2 = next(step for step in every if step[1] == 2 and step[0] == "run")
    state["runs"].append({"id": first_run_day2[2]})
    assert first_run_day2 not in run.remaining(timeline, state, 3)
    assert run.remaining(timeline, {"mornings": [1, 2, 3], "nights": {str(day): {"done": True} for day in (1, 2, 3)},
                                    "evenings": [{"day": day} for day in (1, 2, 3)],
                                    "runs": [{"id": step[2]} for step in every if step[0] == "run"],
                                    "tells": [{"id": step[2]} for step in every if step[0] == "tell"]}, 3) == []


def test_a_lost_sign_in_stops_the_run_before_it_saves_another_step(tmp_path, monkeypatch):
    from row_bot.providers import codex
    from tests.e2e.learning.seeded import live

    monkeypatch.setattr(run, "SIGN_IN_LOST", [])
    monkeypatch.setattr(codex, "refresh_codex_token", codex.refresh_codex_token)  # restored after the test
    monkeypatch.setattr(live.SignIn, "_write_back", live.SignIn._write_back)
    state = live.State(tmp_path / "state.json")
    run.stop_saving_once_sign_in_lost(state)
    state.data["notes"] = ["day 1"]
    state.save()

    run.guard_sign_in()
    with pytest.raises(run.RefreshRefused):  # ChatGPT answered 401: the run may not refresh
        codex.refresh_codex_token("a refresh token")
    state.data["notes"] = ["day 1", "day 2, run without the simulated person"]
    with pytest.raises(SystemExit) as stop:
        state.save()
    assert stop.value.code == run.EXIT_LIMIT  # the queue waits, then resumes from day 1 with the sign-in read again
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["notes"] == ["day 1"]


def test_phrasing_keeps_only_variants_that_add_no_fact():
    template = "Send {alias} tomorrow's bread order: {n} large loaves."
    assert phrasing.keeps(template, "Can you send {alias} tomorrow's order? {n} large loaves.")
    assert not phrasing.keeps(template, "Send {alias} the order: {n} loaves to orders@x.example.")
    assert not phrasing.keeps(template, "Send {alias} 20 loaves.")
    assert not phrasing.keeps(template, "Send the bakery {n} large loaves.")
    # A variant that asks for something else (a question, a draft) is dropped, unless the template asks it too.
    assert not phrasing.keeps("Ask finance for the van's fuel allowance for the week of {date}.",
                              "Find out from finance how much the van's fuel allowance is for the week of {date}.")
    assert not phrasing.keeps("Maintenance summary to the building manager - {n} open tickets.",
                              "Draft a maintenance summary for the building manager - {n} open tickets.")
    assert phrasing.keeps("Rota reminder please - remind them how much notice a swap needs.",
                          "Remind the volunteers how much notice a swap needs, please.")
    # The world uses only the variants that keep the request (a file written before a rule tightened included).
    for model in ("main", "qwen3.8:27b"):
        options = world.Phrasings(phrasing.load(world.PERSON, model))
        for slot, template in world.slots().items():
            for variant in options.options(slot, template)[1:]:
                assert phrasing.keeps(template, variant)


def test_the_quiet_hours_stop_new_days_before_00_40():
    from datetime import datetime

    assert run.quiet_soon(datetime(2026, 10, 26, 0, 26))
    assert run.quiet_soon(datetime(2026, 10, 26, 4, 59))
    assert run.quiet_soon(datetime(2026, 10, 26, 5, 0)) is None
    assert run.quiet_soon(datetime(2026, 10, 25, 23, 50)) is None
    assert run.limit_error("⚠️ Rate limit reached — please wait") and not run.limit_error("recursion limit")
    # A slower model stops taking new days earlier, through midnight.
    assert run.quiet_soon(datetime(2026, 10, 25, 23, 55), start=(23, 50))
    assert run.quiet_soon(datetime(2026, 10, 26, 0, 10), start=(23, 50))
    assert run.quiet_soon(datetime(2026, 10, 25, 23, 40), start=(23, 50)) is None
    assert run.quiet_soon(datetime(2026, 10, 26, 12, 0), start=(23, 50)) is None
    # A cut-off of 05:00 leaves no night pause at all (a night the person's Row-Bot stays closed).
    assert all(run.quiet_soon(datetime(2026, 10, 26, hour, 30), start=(5, 0)) is None for hour in range(24))


def test_phase_3s_grid_is_the_pre_registered_one(tmp_path):
    from tests.e2e.learning.stale_skills import grid

    def arms(queue: str, model: str) -> dict[str, set[str]]:
        found: dict[str, set[str]] = {}
        for cell in grid.cells(queue):
            if cell.model == model:
                found.setdefault(cell.arm, set()).add(cell.person)
        return found

    everyone = set(world.PEOPLE)
    assert arms("main", "main") == {arm: everyone for arm in ("N", "R", "D")}
    assert arms("local", "q9b") == {arm: everyone for arm in ("N", "R", "D", "F", "A")}
    assert arms("local", "q4b") == arms("local", "q27b") == {arm: everyone for arm in ("N", "R")}
    names = [cell.name for queue in ("main", "local") for cell in grid.cells(queue)]
    assert len(names) == len(set(names)) == 72 and all(name.startswith("sa3_") for name in names)
    # Every run is blind; the main model's runs never touch the booking file (they would close lane B's booking).
    main = grid.command(grid.cells("main")[0], tmp_path, "profile", "booking.txt")
    local = grid.command(grid.cells("local")[0], tmp_path, "profile", "booking.txt")
    assert "--blind" in main and "--blind" in local
    assert "--gpu-booking" not in main and local[local.index("--gpu-booking") + 1] == "booking.txt"
    # A local cell keeps its model's night cut-off unless the queue is told otherwise for a night.
    night = grid.command(grid.cells("local")[0], tmp_path, "profile", "booking.txt", "05:00")
    assert local[local.index("--no-new-day-from") + 1] == grid.MODELS["q9b"]["cutoff"]
    assert night[night.index("--no-new-day-from") + 1] == "05:00"
    assert grid.progress(tmp_path / "nothing") == {"days": 0, "steps": 0, "tokens": 0, "gpu_seconds": 0.0,
                                                   "finished": False}


def test_the_turn_context_can_say_the_simulated_date():
    from datetime import datetime, timezone

    moment = datetime(2026, 12, 1, 9, 30)
    simulated = run.simulated_datetime(lambda: moment)
    assert simulated.now() == moment
    assert simulated.now().astimezone().date() == moment.date()
    assert simulated.now(timezone.utc) == moment.astimezone(timezone.utc)


def test_an_amount_is_never_read_from_a_date_or_a_time():
    assert world.states("amount", {"amount": 16}, "Adult tickets are GBP 16 this week.")
    assert not world.states("amount", {"amount": 16}, "Price note for the week of 2026-11-16.")
    assert not world.states("amount", {"amount": 16}, "For the week of 16 November, see you at 16:30.")
    assert not world.states("amount", {"amount": 16}, "From November 16 the room is free.")


@pytest.mark.parametrize("person", world.PEOPLE)
def test_the_smoke_world_has_two_changes_told_first_thing_and_skills_naming_the_old_values(person):
    smoke = world.build(person=person, smoke=True)
    full = world.build(person=person)
    assert smoke.days == world.SMOKE_DAYS and smoke.probes == ()
    assert [(change.day, change.channel) for change in smoke.changes] == [(1, "told"), (2, "told")]
    assert [(change.procedure, change.old, change.new) for change in smoke.changes] == [
        (change.procedure, change.old, change.new) for change in full.changes[:2]]
    for change in smoke.changes:
        tell = next(item for item in smoke.tells if item.about == change.procedure)
        assert tell.day == change.day and tell.order == 0  # told before that day's runs
        assert {item.day for item in smoke.runs if smoke.procedure_of(item.id) == change.procedure} == {1, 2}
    running = sorted({smoke.procedure_of(item.id) for item in smoke.runs})
    assert len(running) == 4 and set(smoke.seeded_skills) < set(running)
    consent = [item for item in running if item not in smoke.seeded_skills]
    assert [smoke.consent_run[item] for item in consent] == [1]  # one skill comes through the approval card
    for item in smoke.runs:  # no request states a fact: the skill and memory are all the agent has
        procedure = smoke.procedure(smoke.procedure_of(item.id))
        assert not any(world.states(procedure.kind, value, item.prompt) for value in procedure.pool)
    for procedure_id in smoke.seeded_skills:
        procedure = smoke.procedure(procedure_id)
        skill = run.seeded_skill(procedure, smoke.start[procedure_id], smoke.catalogue.first_name)
        assert world.states(procedure.kind, smoke.start[procedure_id], skill["instructions"])
        assert smoke.catalogue.first_name in skill["instructions"]
    # The person edits one seeded unchanged procedure's skill on the second morning (a newer-skill event).
    [edit] = smoke.edits
    assert (edit.day, edit.procedure in smoke.seeded_skills, smoke.change_of(edit.procedure)) == (2, True, None)
    assert smoke.value(edit.procedure, 1) == edit.old and smoke.value(edit.procedure, 2) == edit.new


def test_each_arm_runs_its_own_product_setting(tmp_path):
    for arm, setting in (("N", "N"), ("R", "R"), ("D", "D"), ("F", "F"), ("A", "R")):
        written = run.write_setting(tmp_path / arm, arm)
        assert written == {"enabled": True, "arm": setting}
        assert json.loads((tmp_path / arm / "stale_skills.json").read_text(encoding="utf-8")) == written


def test_arm_f_repairs_from_the_persons_words_never_from_the_timeline(timeline):
    procedure = world.PROCEDURE["bread_order"]
    said = {"reaction": [{"action": "correct", "text": "No - that address isn't read any more."}]}
    assert run.repair_correction(said, procedure) == "No - that address isn't read any more."
    silent = run.repair_correction({"reaction": [{"action": "give_up", "text": ""}]}, procedure)
    assert not any(world.states(procedure.kind, value, silent) for value in procedure.pool)


def test_patches_are_scored_true_or_false_from_the_timeline(timeline):
    from tests.e2e.learning.seeded import generator

    change = timeline.changes[0]
    unchanged = next(item.id for item in timeline.procedures if timeline.change_of(item.id) is None)
    skills = {change.procedure: {"name": f"{change.procedure}_procedure"}, unchanged: {"name": f"{unchanged}_procedure"}}

    def at(day: int) -> str:
        return f"{generator.day_date(day).isoformat()}T08:00:00"

    operations = [
        {"event": "patch_candidate", "arm": "R", "outcome": "proposed", "proposal_id": "p1"},
        {"event": "patch_candidate", "arm": "R", "outcome": "proposed", "proposal_id": "p2"},
        {"event": "patch_candidate", "arm": "R", "outcome": "no_change"},
        {"event": "patch", "skill": f"{change.procedure}_procedure", "cause": "proposal:p1", "at": at(change.day + 1)},
        {"event": "patch", "skill": f"{unchanged}_procedure", "cause": "proposal:p2", "at": at(change.day + 1)},
        {"event": "patch", "skill": f"{change.procedure}_procedure", "cause": "", "at": at(change.day + 2)},
    ]
    found = scorer.patch_metrics(timeline, skills, operations)
    assert (found["applied"], found["false"], found["false_rate"]) == (2, 1, 0.5)
    assert [(item["procedure"], item["true"], item["days_after_change"]) for item in found["patches"]] == [
        (change.procedure, True, 1), (unchanged, False, None)]
    assert found["candidates"] == {"R": {"proposed": 2, "no_change": 1}}
    early = scorer.patch_metrics(timeline, skills, [{**operations[3], "at": at(change.day - 1)}])
    assert early["false"] == 1  # a patch before the change is a false patch too


@pytest.mark.parametrize("person", world.PEOPLE)
def test_the_persons_edit_moves_every_value_the_skill_states_or_adds_a_dated_line(worlds, person):
    timeline = worlds[person]
    for edit in timeline.edits:
        procedure = timeline.procedure(edit.procedure)
        skill = run.seeded_skill(procedure, edit.old, timeline.catalogue.first_name)["instructions"]
        edited, replaced = world.edit_skill_text(procedure, skill, edit)
        assert replaced and world.states(procedure.kind, edit.new, edited)
        if procedure.kind == "hours":  # both times move at once (a new end may be the old start)
            assert f"start {edit.new['start']}, end {edit.new['end']}" in edited
        else:
            assert not world.states(procedure.kind, edit.old, edited)
        assert edited.count("\n") == skill.count("\n")  # the same skill, its value moved
        # A skill that doesn't state the value gets one dated line saying the new one.
        plain = f"Use this when {timeline.catalogue.first_name} asks for {procedure.title}."
        added, replaced = world.edit_skill_text(procedure, plain, edit)
        assert not replaced and added.startswith(plain) and world.states(procedure.kind, edit.new, added)
        assert f"Updated on {world.generator.day_date(edit.day).day} " in added


def test_a_newer_skill_run_on_the_older_memory_is_a_wrong_switch_and_not_an_unchanged_control(timeline):
    edit = timeline.edits[0]
    procedure = timeline.procedure(edit.procedure)
    after_id, after_day = _run_id(timeline, edit.procedure, after=True)
    before_id, before_day = _run_id(timeline, edit.procedure, after=False)

    def action(value: world.Value) -> dict:
        if procedure.kind == "hours":
            return _event(value["start"], value["end"])
        if procedure.kind in ("address", "supplier", "role"):
            return _mail(value["email"])
        return _mail(procedure.to, f"Reminder: {world.say(procedure.kind, value)}.")

    wrong = scorer.score_run(timeline, after_id, after_day, [action(edit.old)])
    assert (wrong.verdict, wrong.edited, wrong.after_edit, wrong.changed) == (scorer.OLD, True, True, False)
    assert wrong.edit_window == (after_day - edit.day < world.WINDOW_DAYS)
    assert scorer.score_run(timeline, after_id, after_day, [action(edit.new)]).verdict == scorer.CURRENT
    assert scorer.score_run(timeline, before_id, before_day, [action(edit.old)]).verdict == scorer.CURRENT
    runs = [{**wrong.to_dict(), "passed_first": False},
            {**scorer.score_run(timeline, before_id, before_day, [action(edit.old)]).to_dict(), "passed_first": True}]
    found = scorer.metrics(timeline, runs)
    assert found["wrong_switch"]["k"] == int(wrong.edit_window)
    assert found["wrong_switch_all_after_edit"] == {"k": 1, "n": 1, "rate": 1.0, "wilson": scorer.wilson(1, 1)}
    assert found["unchanged_success"]["n"] == 0  # the edited procedure is not one of the unchanged controls


def test_the_second_result_counts_a_changes_runs_only_once_it_reached_memory(timeline):
    from tests.e2e.learning.seeded import generator

    change, other = timeline.changes[0], timeline.changes[1]
    said = world.say(change.kind, change.new)

    def at(day: int, hour: int) -> str:
        return f"{generator.day_date(day).isoformat()}T{hour:02d}:00:00"

    operations = [
        {"event": "write", "at": at(change.day - 1, 9), "text": f"It is {said}.", "cause": ""},  # before the change
        {"event": "write", "at": at(change.day, 21), "text": f"Merged: {said}.", "cause": "merge"},  # Dream's tidying
        {"event": "supersede", "at": at(change.day, 22), "new": f"Now {said}.", "cause": "", "entity_id": "e1",
         "subject": "It", "origin": "thread:t1"},
        {"event": "write", "at": at(other.day, 10), "text": "Something else entirely.", "cause": ""},
    ]
    stored = scorer.stored_changes(timeline, operations)
    assert stored[change.procedure] == {"at": at(change.day, 22), "day": change.day, "event": "supersede",
                                        "subject": "It", "entity_id": "e1", "origin": "thread:t1"}
    assert stored[other.procedure] is None

    window = [item for item in _runs_of(timeline, change.procedure)
              if change.day <= item.day < change.day + world.WINDOW_DAYS]
    runs = [{**scorer.score_run(timeline, item.id, item.day, [_action(change, change.old)]).to_dict(),
             "at": f"{generator.day_date(item.day).isoformat()}T10:00:00", "passed_first": False} for item in window]
    found = scorer.metrics(timeline, runs, stored)
    assert found["stale_execution"]["n"] == len(window)
    reached = found["reached_memory"]
    assert reached["stored"] == 1 and reached["changes"][other.procedure] is None
    # Only the runs after the change reached memory (22:00 on its day) count in the second result.
    assert reached["stale_execution"]["n"] == len([item for item in window if item.day > change.day])


def test_an_edit_moves_both_times_once_when_the_new_end_is_the_old_start():
    procedure = next(item for item in world.PROCEDURES if item.kind == "hours")
    edit = world.Change(procedure.id, "hours", 50, {"start": "09:00", "end": "10:00"},
                        {"start": "10:00", "end": "11:00"}, world.EDIT)
    edited, replaced = world.edit_skill_text(procedure, "Book it from 09:00 to 10:00 (or 9am-10am).", edit)
    assert (edited, replaced) == ("Book it from 10:00 to 11:00 (or 10:00-11:00).", True)


def test_phase_3s_analysis_tests_ratios_with_holm_and_a_paired_bootstrap():
    import math

    from tests.e2e.learning.stale_skills import analysis

    people = [(f"p{i}", f"c{j}") for i in range(6) for j in range(8)]
    n_arm = {key: (2, 4) for key in people}  # 50%
    r_arm = {key: (0, 4) if index % 4 else (1, 4) for index, key in enumerate(people)}  # 1/16 of runs
    f_arm = dict(n_arm)
    tests = analysis.holm({"R/N": analysis.bootstrap({"R": r_arm, "N": n_arm}, analysis.ratio("R", "N"), resamples=2000),
                           "R/F": analysis.bootstrap({"R": r_arm, "F": f_arm}, analysis.ratio("R", "F"),
                                                     resamples=2000)})
    assert all(item["upper_below_1"] for item in tests.values())
    assert sorted(item["level"] for item in tests.values()) == [0.95, 0.975]
    # The same arm twice: the ratio sits at 1 and its interval holds 1.
    same = analysis.holm({"R/N": analysis.bootstrap({"R": n_arm, "N": dict(n_arm)}, analysis.ratio("R", "N"),
                                                      resamples=2000)})
    assert same["R/N"]["interval"][0] <= 1 <= same["R/N"]["interval"][1] and not same["R/N"]["upper_below_1"]
    # A denominator arm with no stale run makes the ratio infinite (it counts against R), never a drop.
    assert analysis.ratio("R", "N")({"R": 0.1, "N": 0.0}) == math.inf
    assert analysis.ratio("R", "N")({"R": 0.0, "N": 0.0}) is None
    # Paired: one draw of clusters serves both arms, so identical arms give a difference of exactly 0 every time.
    assert set(analysis.bootstrap({"A": n_arm, "B": dict(n_arm)}, analysis.difference("A", "B"), resamples=200)) == {0.0}


def test_phase_3s_decision_follows_section_8():
    from tests.e2e.learning.stale_skills import analysis

    def blocks(n_rate_runs: tuple[int, int], r_runs: tuple[int, int], false: int) -> dict:
        def block(k: int, n: int) -> dict:
            clusters = {(f"p{i}", f"c{j}"): (k, n) for i in range(6) for j in range(8)}
            controls = {(f"p{i}", f"u{j}"): (15, 17) for i in range(6) for j in range(6)}
            return {"_clusters": clusters, "_reached": clusters, "_controls": controls, "_edits": {},
                    "stale_execution": {"k": 48 * k, "n": 48 * n, "rate": k / n},
                    "patches": {"false_rate": false / 40, "own_applied": 40, "false": false},
                    "wrong_switch_all_after_edit": analysis.rate(0, 80), "wrong_switch_14_days": analysis.rate(0, 20)}

        return {("q9b", "N"): block(*n_rate_runs), ("q9b", "F"): block(*n_rate_runs), ("q9b", "R"): block(*r_runs),
                ("q9b", "A"): block(*r_runs), ("q9b", "D"): block(*n_rate_runs)}

    held = analysis.analyse(blocks((1, 4), (0, 4), 1), resamples=1000)
    assert held["h_a1_holds"] and held["decision"].startswith("H-A1 holds")
    too_many_false = analysis.analyse(blocks((1, 4), (0, 4), 3), resamples=1000)
    assert not too_many_false["h_a1_holds"] and "measurement paper" in too_many_false["decision"]
    assert "stop" in analysis.analyse(blocks((1, 20), (1, 20), 0), resamples=1000)["decision"]


def test_a_grid_cell_runs_under_one_supervisor_at_a_time(tmp_path):
    import os

    from tests.e2e.learning.stale_skills import grid

    folder = tmp_path / "sa3_main_N_mara"
    assert grid.lock(folder)
    # Another live process holds it: not taken. A holder that ended: taken over.
    (folder / "grid.lock").write_text(json.dumps({"pid": os.getppid()}), encoding="utf-8")
    assert not grid.lock(folder)
    (folder / "grid.lock").write_text(json.dumps({"pid": 999_999_999}), encoding="utf-8")
    assert grid.lock(folder)
    grid.unlock(folder)
    assert not (folder / "grid.lock").exists()


def test_a_local_run_keeps_the_card_for_a_block_and_yields_to_a_priority_lane(tmp_path):
    import time
    from types import SimpleNamespace

    from tests.e2e.learning.seeded.scratch import GpuGate

    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("20:00 lane-B START SA3 stale skills: day 1\n", encoding="utf-8")
    gate = GpuGate(booking, "lane-B", training=lambda: False, quiet=lambda: None, sleep=lambda _s: None)
    gate.held = "day 1"
    me = SimpleNamespace(lane="local", gpu=gate, args=SimpleNamespace(block_minutes=40, priority_lanes="lane-C"))
    now = time.monotonic()
    assert run.StaleRun._keep_card(me, now)
    assert not run.StaleRun._keep_card(me, now - 41 * 60)  # the block is over
    with booking.open("a", encoding="utf-8") as handle:
        handle.write("20:10 lane-A note: S1 waits for the card (next: generations)\n")
    assert run.StaleRun._keep_card(me, now)  # an ordinary lane waits for the block's end
    with booking.open("a", encoding="utf-8") as handle:
        handle.write("20:11 lane-C note: C2 waits for the card (next: training)\n")
    assert not run.StaleRun._keep_card(me, now)  # a priority lane gets it at once
    gate.training = lambda: True
    me.args.priority_lanes = ""
    assert not run.StaleRun._keep_card(me, now)  # the person's own training always wins
    gate.training, gate.quiet = (lambda: False), (lambda: "quiet hours")
    assert not run.StaleRun._keep_card(me, now)
    assert not run.StaleRun._keep_card(SimpleNamespace(lane="main", gpu=gate, args=me.args), now)


def test_a_cell_whose_first_day_never_reached_the_model_stops_for_a_look(tmp_path):
    """DA1: the 4B's first grid lost all 120 days to a refused model; now the first day's night stops such a cell."""
    from types import SimpleNamespace

    from tests.e2e.learning.seeded import live

    refused = {"attempts": [{"error": "error", "error_text": ["not Agent-ready"], "tool_calls": 0, "calls": [],
                             "answer": ""}], "day": 1}
    acted = {"attempts": [{"error": "", "tool_calls": 2, "calls": ["send_email"], "answer": "Sent."}], "day": 1}
    answered_after_an_error = {"attempts": [{"error": "error", "tool_calls": 0, "calls": [], "answer": "Done."}],
                               "day": 1}
    assert run.unreached(refused) and not run.unreached(acted) and not run.unreached(answered_after_an_error)

    state = live.State(tmp_path / "state.json")
    state.data.update(runs=[refused, refused], notes=[])
    me = SimpleNamespace(state=state, save=state.save)
    with pytest.raises(SystemExit) as stop:
        run.StaleRun._check_reachable(me, 1)
    assert stop.value.code == run.EXIT_UNREACHABLE
    assert "DA1" in json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["notes"][-1]["note"]

    state.data["runs"] = [refused, acted]  # one request reached the model: the cell runs on
    run.StaleRun._check_reachable(me, 1)
    run.StaleRun._check_reachable(SimpleNamespace(state=SimpleNamespace(data={"runs": [refused]})), 2)  # day 1 only


def test_the_4b_is_registered_under_a_name_the_build_reads_as_tool_capable():
    """DA1: the same GGUF, named in the qwen3.5 family, so the build treats its tools as it treats the 9B's."""
    from row_bot.providers.ollama import is_ollama_tool_capable, normalize_ollama_family
    from tests.e2e.learning.stale_skills import grid

    four_b, nine_b = grid.MODELS["q4b"]["local"], grid.MODELS["q9b"]["local"]
    assert is_ollama_tool_capable(four_b) and normalize_ollama_family(four_b) == normalize_ollama_family(nine_b)


def test_a_night_left_open_waits_only_while_the_person_trains():
    quiet = "the person's quiet hours (00:40-05:00: their Row-Bot may train and compare)"

    assert run.person_wait(False, False, quiet) == quiet
    assert run.person_wait(False, False, None) is None
    assert run.person_wait(True, False, quiet) is None
    assert run.person_wait(True, True, None) == "the person's own Row-Bot is training"
