"""The restraint suite, engine half: silent when nothing changed, one moment per thing, the person's rules first.

Real engine ticks on a fake clock; scripted watchers stand in for the sources.
"""
from __future__ import annotations

import pytest

from row_bot.proactive.model import Desktop
from tests.subsystem.proactive.conftest import AT_DESK, ScriptedWatcher, at, balanced, candidate, start_day

pytestmark = pytest.mark.subsystem


def _no_triage(*_args, **_kwargs):
    raise AssertionError("nothing changed, so nothing may reach the model")


def test_silent_when_nothing_changed_but_the_pass_is_logged(proactive, monkeypatch):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    monkeypatch.setattr(proactive.engine, "_triage", _no_triage)

    first = proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    proactive.engine.tick(at("2026-11-16 09:00:30"), desktop=AT_DESK)  # Not due yet: no scan at all.
    proactive.engine.tick(at("2026-11-16 09:01"), desktop=AT_DESK)

    assert first["ran"] == ["fake_mail"] and first["candidates"] == 0
    assert watcher.scans == 2 and proactive.store.list_moments() == []
    passes = [entry["summary"] for entry in proactive.store.activity() if entry["summary"].startswith("Checked")]
    assert passes == ["Checked fake_mail: nothing new."] * 2


def test_a_source_that_isnt_connected_or_is_off_is_skipped_and_says_so(proactive):
    watcher = ScriptedWatcher(connected=False)
    calendar = ScriptedWatcher(name="fake_calendar", source="calendar")
    proactive.watchers.register(watcher)
    proactive.watchers.register(calendar)
    proactive.settings.save({"sources": {"calendar": False}})

    result = proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)

    assert result["ran"] == [] and watcher.scans == 0 and calendar.scans == 0
    assert "Checked nothing: nothing new. Skipped (not connected): fake_mail." in [
        entry["summary"] for entry in proactive.store.activity()]


def test_one_thing_gets_one_moment_ever(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    watcher.pending = [candidate("mail:m1"), candidate("mail:m1")]
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    [moment] = proactive.store.list_moments()
    proactive.feedback.dismiss(moment["id"], now=at("2026-11-16 09:05"))

    watcher.pending = [candidate("mail:m1")]  # Still no reply a day later: still no second follow-up.
    proactive.engine.tick(at("2026-11-17 09:00"), desktop=AT_DESK)

    assert [item["dedupe_key"] for item in proactive.store.list_moments()] == ["mail:m1"]
    assert proactive.store.list_moments()[0]["state"] == "dismissed"


def test_never_about_rules_keep_a_candidate_out_before_triage(proactive, monkeypatch):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    proactive.store.save_rule(proactive.feedback.check_rule({"type": "never_about", "topic": "Ana"}),
                              now=at("2026-11-16 08:00"))
    seen: list[str] = []
    original = proactive.engine._triage
    monkeypatch.setattr(proactive.engine, "_triage",
                        lambda found, *args: seen.extend(c.key for c in found) or original(found, *args))
    watcher.pending = [candidate("mail:ana", topic="Ana Silva <ana@example.com>"),
                       candidate("mail:ben", topic="ben@example.com", title="Ben hasn't replied")]

    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)

    assert seen == ["mail:ben"], "the rule applies before triage sees anything"
    assert [moment["dedupe_key"] for moment in proactive.store.list_moments()] == ["mail:ben"]
    assert any("Never about “Ana”" in entry["summary"] for entry in proactive.store.activity())


def test_not_at_this_time_holds_a_kind_through_that_hour(proactive):
    balanced(proactive)
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    proactive.store.save_rule(proactive.feedback.check_rule({"type": "not_at_time", "kind": "follow_up",
                                                              "hour": 9}), now=at("2026-11-16 08:00"))
    start_day(proactive)
    watcher.pending = [candidate("mail:a")]
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 09:00"))

    assert proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)["sent"] == []
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 10:00"))
    assert proactive.engine.tick(at("2026-11-16 10:00:10"), desktop=AT_DESK)["sent"]


def test_a_kind_that_is_off_makes_no_moments(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    proactive.settings.save({"kinds": {"follow_up": False}})
    watcher.pending = [candidate("mail:a")]

    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)

    assert proactive.store.list_moments() == []


def test_the_first_break_of_the_day_gathers_what_waits_into_one_brief(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    # Overnight, two things came in; nothing reaches the person in quiet hours.
    watcher.pending = [candidate("mail:a"), candidate("date:b", kind="date", title="Mum's birthday is Thursday")]
    proactive.engine.tick(at("2026-11-16 06:00"), desktop=Desktop(idle_seconds=4000, locked=True))

    morning = proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)

    brief = next(moment for moment in proactive.store.list_moments() if moment["kind"] == "brief")
    assert morning["sent"] == [brief["id"]], "the brief goes out, not each thing on its own"
    # The brief's workflow ran on what waited (the scripted run replies with its message).
    assert "Waiting for you:\n- Ana hasn't replied about the deck\n- Mum's birthday is Thursday\n" in brief["body"]
    assert brief["body"].endswith("\n\nThat's everything for today.")
    carried = [moment for moment in proactive.store.list_moments() if moment["kind"] != "brief"]
    assert {moment["surface"] for moment in carried} == {"brief"}
    assert proactive.store.shown_since(at("2026-11-16 00:00")) == 1, "the brief counts once against the budget"
    # A second break the same day brings no second brief.
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 12:00"))
    proactive.engine.tick(at("2026-11-16 12:00:05"), desktop=AT_DESK)
    assert sum(moment["kind"] == "brief" for moment in proactive.store.list_moments()) == 1
    assert len(proactive.day_runs.messages) == 1, "its workflow ran once"


def test_no_brief_when_there_is_nothing_for_today(proactive):
    proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)

    assert proactive.store.list_moments() == [] and proactive.day_runs.messages == []
    assert proactive.store.activity()[0]["summary"] == "Morning brief: nothing for today, so none."
