"""What the person does with a moment teaches it: rules they can see and edit, and kinds that go quiet."""
from __future__ import annotations

import pytest

from tests.subsystem.proactive.conftest import AT_DESK, ScriptedWatcher, at, candidate, start_day

pytestmark = pytest.mark.subsystem


def _shown(proactive, *candidates, when: str = "2026-11-16 09:00") -> list[dict]:
    watcher = proactive.watchers.get("fake_mail") or ScriptedWatcher()
    proactive.watchers.register(watcher)
    watcher.pending = list(candidates)
    proactive.engine.note_boundary("app_open", now=at(when))
    proactive.engine.tick(at(when), desktop=AT_DESK)
    return [moment for moment in proactive.store.list_moments() if moment["state"] == "shown"]


@pytest.mark.parametrize("scope,words", [
    ("topic", "Never about “ana@example.com”"),
    ("kind", "At most one follow-ups a day"),
    ("time", "No follow-ups around 09:00"),
])
def test_less_like_this_dismisses_and_writes_the_rule_the_person_chose(proactive, scope, words):
    start_day(proactive)
    [moment] = _shown(proactive, candidate("mail:a"))

    dismissed, rule = proactive.feedback.less_like_this(moment["id"], scope, now=at("2026-11-16 09:05"))

    assert dismissed["state"] == "dismissed"
    assert proactive.feedback.rule_words(rule) == words
    assert proactive.store.rules() == [rule]
    assert proactive.store.kind_stats("follow_up")["dismissed"] == 1


def test_a_kind_left_alone_three_times_goes_quiet_says_so_and_comes_back_when_turned_on(proactive):
    start_day(proactive)
    for day, key in ((16, "a"), (17, "b"), (18, "c")):
        start_day(proactive, f"2026-11-{day}")
        _shown(proactive, candidate(f"change:{key}", kind="change", title=f"Report {key} failed"),
               when=f"2026-11-{day} 09:00")
    proactive.feedback.sweep(at("2026-11-18 23:00"))

    assert proactive.store.kind_stats("change")["ignored_streak"] == 3
    notes = [entry["summary"] for entry in proactive.store.activity() if entry["kind"] == "quiet"]
    assert notes == ["Changes you care about went quiet: the last 3 were left alone. "
                     "Turn them back on in Settings › Proactive."]
    start_day(proactive, "2026-11-19")
    assert _shown(proactive, candidate("change:d", kind="change"), when="2026-11-19 09:00") == []

    proactive.settings.save({"kinds": {"change": False}})
    proactive.settings.save({"kinds": {"change": True}})
    proactive.engine.note_boundary("app_open", now=at("2026-11-19 14:00"))
    assert proactive.engine.tick(at("2026-11-19 14:00:05"), desktop=AT_DESK)["sent"], "it speaks again"


def test_acting_on_one_ends_a_streak_of_ignores(proactive):
    start_day(proactive)
    [first] = _shown(proactive, candidate("change:a", kind="change"))
    proactive.feedback.sweep(at("2026-11-16 22:00"))
    start_day(proactive, "2026-11-17")
    [second] = _shown(proactive, candidate("change:b", kind="change"), when="2026-11-17 09:00")

    proactive.feedback.act(second["id"], now=at("2026-11-17 09:01"), action_id="open_source")

    assert proactive.store.kind_stats("change")["ignored_streak"] == 0
    assert proactive.store.get_moment(first["id"])["state"] == "ignored"


def test_not_now_brings_it_back_at_a_later_break_not_the_current_one(proactive):
    start_day(proactive)
    [moment] = _shown(proactive, candidate("mail:a"))

    proactive.feedback.not_now(moment["id"], now=at("2026-11-16 09:01"))
    proactive.engine.tick(at("2026-11-16 09:01:30"), desktop=AT_DESK)
    assert proactive.store.get_moment(moment["id"])["state"] == "pending"

    proactive.engine.note_boundary("app_open", now=at("2026-11-16 13:30"))
    proactive.engine.tick(at("2026-11-16 13:30:05"), desktop=AT_DESK)
    assert proactive.store.get_moment(moment["id"])["state"] == "shown"


def test_a_settled_moment_takes_no_second_outcome(proactive):
    start_day(proactive)
    [moment] = _shown(proactive, candidate("mail:a"))
    proactive.feedback.dismiss(moment["id"], now=at("2026-11-16 09:01"))

    with pytest.raises(proactive.feedback.FeedbackError) as raised:
        proactive.feedback.act(moment["id"], now=at("2026-11-16 09:02"))
    assert raised.value.code == "revision_conflict"


@pytest.mark.parametrize("rule", [
    {"type": "never_about", "topic": "  "},
    {"type": "fewer"},
    {"type": "not_at_time", "kind": "date", "hour": 24},
    {"type": "always", "kind": "date"},
])
def test_rules_that_say_nothing_clear_are_refused(proactive, rule):
    with pytest.raises(proactive.feedback.FeedbackError):
        proactive.feedback.check_rule(rule)
