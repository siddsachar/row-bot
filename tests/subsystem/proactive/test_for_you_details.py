"""What the client reads besides the moments themselves: For you's group per moment, the level preview,
connected sources, each kind's last 30 days, the once-per-person introduction, and Buddy staying out of the
morning brief's own workflow run."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app
from tests.subsystem.proactive.conftest import AT_DESK, ScriptedWatcher, at, balanced, candidate, start_day

pytestmark = pytest.mark.subsystem


def _moment(proactive, key: str, *, made: str = "2026-11-16 09:00", **fields):
    from row_bot.proactive.model import Evidence

    evidence = fields.pop("evidence", None)
    starts = fields.pop("starts", None)
    if starts:
        evidence = (Evidence("event", key, "Design review", "google_calendar", seen=str(int(at(starts)))),)
    return proactive.store.add_moment(candidate(key, evidence=evidence, **fields), title=fields.get("title", key),
                                      body="", now=at(made))


def test_each_moment_has_a_group_from_what_it_is_about_not_from_when_it_was_shown(proactive):
    from row_bot.application.client_proactive import moment_group

    now = at("2026-11-16 10:00")
    soon = _moment(proactive, "meeting:a", kind="meeting_prep", urgency="urgent", starts="2026-11-16 10:10")
    later = _moment(proactive, "moved:b", kind="change", starts="2026-11-16 15:30")
    tomorrow = _moment(proactive, "moved:c", kind="change", starts="2026-11-17 09:00")
    follow_up = _moment(proactive, "mail:d")
    birthday = _moment(proactive, "date:e", kind="date", due_at=at("2026-11-19 09:00"))

    groups = {moment["dedupe_key"]: moment_group(moment, now) for moment in (soon, later, tomorrow, follow_up, birthday)}
    assert groups == {"meeting:a": "now", "moved:b": "later_today", "moved:c": "ready", "mail:d": "ready",
                      "date:e": "ready"}
    # The morning brief marks what it carries as shown: their groups stay.
    proactive.store.move(later["id"], "shown", from_states=("pending",), now=now, surface="brief", shown_at=now)
    assert moment_group(proactive.store.get_moment(later["id"]), now) == "later_today"


def test_not_now_moves_a_moment_to_ready_when_you_are_over_the_api(proactive):
    import time

    from row_bot.proactive.model import Evidence

    now = time.time()  # The API groups by the real clock.
    starts = Evidence("event", "e1", "Design review", "google_calendar", seen=str(int(now + 1800)))
    proactive.store.add_moment(candidate("moved:b", kind="change", evidence=(starts,)), title="Design review moved",
                               body="", now=now)
    client, _service, _active = client_app()
    with client:
        _data, headers = bootstrap(client)
        [item] = client.get("/api/v1/proactive/moments", headers=headers).json()["items"]
        assert item["group"] == "now" and item["starts_at"]
        client.post(f"/api/v1/proactive/moments/{item['moment_id']}/feedback", json={"action": "not_now"},
                    headers=headers)
        [after] = client.get("/api/v1/proactive/moments", headers=headers).json()["items"]
    assert after["group"] == "ready"


def test_the_preview_says_what_each_level_would_have_brought_up_today(proactive):
    from row_bot.proactive import preview

    assert preview.preview(at("2026-11-16 12:00"))["example"] is True, "nothing today: an example day"
    _moment(proactive, "brief:2026-11-16", kind="brief", made="2026-11-16 08:00")
    _moment(proactive, "meeting:a", kind="meeting_prep", urgency="urgent", made="2026-11-16 09:50")
    _moment(proactive, "mail:b", made="2026-11-16 10:30")
    _moment(proactive, "date:c", kind="date", made="2026-11-16 11:00")
    _moment(proactive, "mail:d", made="2026-11-16 11:15", topic="ben@example.com")

    result = preview.preview(at("2026-11-16 12:00"))
    where = {level["level"]: [(item["moment_id"] and item["title"], item["where"]) for item in level["items"]]
             for level in result["levels"]}
    assert result["example"] is False and where["off"] == []
    assert where["quiet"] == [("brief:2026-11-16", "buddy"), ("meeting:a", "buddy"), ("mail:b", "home"),
                              ("date:c", "home"), ("mail:d", "home")], "Quiet: the brief and what's imminent"
    assert where["balanced"] == [("brief:2026-11-16", "buddy"), ("meeting:a", "buddy"), ("mail:b", "buddy"),
                                 ("date:c", "buddy"), ("mail:d", "home")], "a second follow-up waits out its cooldown"
    assert [item["where"] for item in result["levels"][3]["items"]] == ["buddy"] * 5


def test_settings_say_which_sources_are_connected_and_each_kinds_last_30_days(proactive):
    from types import SimpleNamespace

    proactive.mail.append(SimpleNamespace(name="gmail", available=lambda: True))
    balanced(proactive)
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    watcher.pending = [candidate("mail:a")]
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 09:00"))
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    [moment] = [item for item in proactive.store.list_moments() if item["kind"] == "follow_up"]
    proactive.feedback.act(moment["id"], now=at("2026-11-16 09:05"))

    client, _service, _active = client_app()
    with client:
        _data, headers = bootstrap(client)
        read = client.get("/api/v1/proactive/settings", headers=headers).json()
        assert not read["introduced"]
        changed = client.put("/api/v1/proactive/settings", json={"introduced": True}, headers=headers).json()
    connected = {source["id"]: source["connected"] for source in read["sources"]}
    assert connected["mail"] is True and connected["calendar"] is False and connected["chats"] is True
    counts = {kind["id"]: kind["last_30_days"] for kind in read["kinds"]}
    assert counts["follow_up"] == {"shown": 1, "acted": 1, "dismissed": 0, "ignored": 0}
    assert counts["date"] == {"shown": 0, "acted": 0, "dismissed": 0, "ignored": 0}
    assert changed["introduced"] is True and proactive.settings.load()["introduced"] is True


def test_buddy_does_not_wear_workflow_running_for_the_morning_briefs_own_run(proactive, monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    monkeypatch.setattr(brain_mod, "get_buddy_config", lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"})
    proactive.store.set_state("workflows", {"brief": "brief-task"})
    brain = BuddyBrain()

    quiet = brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STARTED, source="tasks",
                                     payload={"task_id": "brief-task", "label": "Morning brief"}, id=3))
    assert quiet.message != "Morning brief" and quiet.animation != "pack_bag"
    other = brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STARTED, source="tasks",
                                     payload={"task_id": "weekly-report", "label": "Weekly report"}, id=4))
    assert other.message == "Weekly report" and other.animation == "pack_bag"
