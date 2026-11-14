"""The restraint suite, timing half: one attention budget, quiet hours, breaks, and never mid-typing or on a call.

Real engine ticks on a fake clock with a fake desktop; scripted watchers stand in for mail and the calendar.
"""
from __future__ import annotations

import pytest

from row_bot.proactive.model import Desktop
from tests.subsystem.proactive.conftest import AT_DESK, ScriptedWatcher, at, candidate, start_day

pytestmark = pytest.mark.subsystem


def _states(proactive) -> dict[str, str]:
    return {moment["dedupe_key"]: moment["state"] for moment in proactive.store.list_moments()}


def _five_kinds(watcher: ScriptedWatcher) -> None:
    watcher.pending = [candidate("mail:a", kind="follow_up"), candidate("date:b", kind="date", topic="Mum"),
                       candidate("change:c", kind="change", topic="Weekly report"),
                       candidate("ask:d", kind="workflow_offer", topic="news digest"),
                       candidate("event:e", kind="meeting_prep", topic="Design review")]


def test_quiet_shows_at_most_three_a_day_one_per_break_tick_and_the_rest_wait(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    _five_kinds(watcher)
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 09:00"))

    sent = [proactive.engine.tick(at(f"2026-11-16 09:00:{second:02d}"), desktop=AT_DESK)["sent"]
            for second in (0, 30)]
    sent += [proactive.engine.tick(at(f"2026-11-16 09:01:{second:02d}"), desktop=AT_DESK)["sent"]
             for second in (0, 30)]

    assert [len(batch) for batch in sent] == [1, 1, 1, 0], "one at a time, and Quiet stops at three"
    states = _states(proactive)
    assert sorted(states.values()).count("shown") == 3 and sorted(states.values()).count("pending") == 2
    # Tomorrow's first break brings what waited, in the morning brief.
    waited = {key for key, state in states.items() if state == "pending"}
    assert proactive.engine.tick(at("2026-11-17 08:00"), desktop=AT_DESK)["sent"]
    carried = {moment["dedupe_key"] for moment in proactive.store.list_moments() if moment["surface"] == "brief"}
    assert carried == waited and _states(proactive)["brief:2026-11-17"] == "shown"


def test_nothing_reaches_the_person_in_quiet_hours_even_when_urgent(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    watcher.pending = [candidate("event:late", kind="meeting_prep", urgency="urgent", topic="Late call")]

    result = proactive.engine.tick(at("2026-11-16 23:10"), desktop=AT_DESK)

    assert result["moments"] and result["sent"] == []
    assert _states(proactive) == {"event:late": "pending"}
    # Quiet hours are the person's: move them and it may go.
    proactive.settings.save({"quiet_start": "23:30", "quiet_end": "07:00"})
    assert proactive.engine.tick(at("2026-11-16 23:11"), desktop=AT_DESK)["sent"]


@pytest.mark.parametrize("desktop,why", [
    (Desktop(idle_seconds=1, locked=False, focus=False, mic_in_use=False, camera_in_use=False), "typing"),
    (Desktop(idle_seconds=40, locked=False, focus=False, mic_in_use=True, camera_in_use=False), "mic"),
    (Desktop(idle_seconds=40, locked=False, focus=False, mic_in_use=False, camera_in_use=True), "camera"),
    (Desktop(idle_seconds=40, locked=False, app_kind="meeting"), "meeting app"),
    (Desktop(idle_seconds=40, locked=False, focus=True), "Focus"),
])
def test_never_while_typing_on_a_call_or_in_focus_not_even_urgent(proactive, desktop, why):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    watcher.pending = [candidate("event:soon", kind="meeting_prep", urgency="urgent")]

    assert proactive.engine.tick(at("2026-11-16 10:00"), desktop=desktop)["sent"] == [], why
    assert proactive.engine.tick(at("2026-11-16 10:00:30"), desktop=AT_DESK)["sent"], "it goes once that ends"


def test_ending_a_call_is_a_break_and_the_batched_moment_goes_then(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    on_call = Desktop(idle_seconds=40, locked=False, mic_in_use=True, camera_in_use=False, app_kind="meeting")
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)  # The day's first break passes, empty.
    watcher.pending = [candidate("mail:a")]

    assert proactive.engine.tick(at("2026-11-16 10:00"), desktop=on_call)["sent"] == []
    assert proactive.engine.tick(at("2026-11-16 10:05"), desktop=on_call)["sent"] == []
    after = proactive.engine.tick(at("2026-11-16 10:30"), desktop=AT_DESK)

    assert after["boundaries"] == ["call_end"] and after["sent"]
    moment = proactive.store.list_moments()[0]
    assert any("your call ended" in line for line in moment["why"])


def test_a_normal_moment_waits_for_a_break_and_an_urgent_one_does_not(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)  # The day's first break passes, empty.
    watcher.pending = [candidate("mail:a"), candidate("event:b", kind="meeting_prep", urgency="urgent")]

    result = proactive.engine.tick(at("2026-11-16 11:00"), desktop=AT_DESK)

    assert _states(proactive) == {"mail:a": "pending", "event:b": "shown"}
    assert result["boundary"] is None
    unlocked = proactive.engine.tick(at("2026-11-16 11:20"), desktop=Desktop(idle_seconds=600, locked=True))
    assert unlocked["sent"] == []
    back = proactive.engine.tick(at("2026-11-16 11:21"), desktop=AT_DESK)
    assert back["boundaries"] == ["unlock", "idle_return"] and _states(proactive)["mail:a"] == "shown"


def test_a_second_moment_of_the_same_kind_waits_out_its_cooldown(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    watcher.pending = [candidate("mail:a"), candidate("mail:b", topic="ben@example.com")]
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 09:00"))

    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    proactive.engine.tick(at("2026-11-16 09:00:30"), desktop=AT_DESK)
    assert sorted(_states(proactive).values()) == ["pending", "shown"], "Quiet: one follow-up per four hours"

    proactive.engine.note_boundary("app_open", now=at("2026-11-16 13:30"))
    assert proactive.engine.tick(at("2026-11-16 13:30:10"), desktop=AT_DESK)["sent"]


def test_off_runs_no_watcher_and_shows_nothing(proactive):
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    proactive.settings.save({"level": "off"})
    watcher.pending = [candidate("mail:a")]

    assert proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK) == {"off": True}
    assert watcher.scans == 0 and proactive.store.list_moments() == []
