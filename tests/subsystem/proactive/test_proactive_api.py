"""The client API: For you, "Why this?", feedback, the Activity log, settings and rules, over HTTP."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app
from tests.subsystem.proactive.conftest import AT_DESK, ScriptedWatcher, at, candidate, start_day

pytestmark = pytest.mark.subsystem


@pytest.fixture
def api(proactive):
    client, _service, _active = client_app()
    with client:
        _data, headers = bootstrap(client)
        yield client, headers


def _moments(proactive, *candidates) -> list[dict]:
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    watcher.pending = list(candidates)
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 09:00"))
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    return proactive.store.list_moments()


def test_for_you_lists_open_moments_urgent_first_and_why_this_shows_the_evidence(proactive, api):
    client, headers = api
    _moments(proactive, candidate("mail:a"),
             candidate("event:b", kind="meeting_prep", urgency="urgent", title="Design review in 10 minutes"))

    page = client.get("/api/v1/proactive/moments", headers=headers)
    assert page.status_code == 200, page.text
    titles = [(item["title"], item["urgency"], item["state"]) for item in page.json()["items"]]
    assert titles[0] == ("Design review in 10 minutes", "urgent", "shown")
    assert ("Ana hasn't replied about the deck", "normal", "pending") in titles

    urgent = page.json()["items"][0]
    detail = client.get(f"/api/v1/proactive/moments/{urgent['moment_id']}", headers=headers).json()
    assert detail["evidence"] == [{"type": "message", "id": "b", "label": "Design review in 10 minutes",
                                   "source": "gmail"}]
    assert detail["why"] == ["It's urgent, so it didn't wait for a break."]
    assert [entry["summary"].split(":")[0] for entry in detail["history"]] == ["Shown", "New"]


def test_feedback_settles_a_moment_once_and_less_like_this_writes_a_visible_rule(proactive, api):
    client, headers = api
    [moment] = [item for item in _moments(proactive, candidate("mail:a")) if item["state"] == "shown"]
    url = f"/api/v1/proactive/moments/{moment['id']}/feedback"

    less = client.post(url, json={"action": "less_like_this", "scope": "topic"}, headers=headers)
    assert less.status_code == 200, less.text
    assert less.json()["moment"]["state"] == "dismissed"
    again = client.post(url, json={"action": "act"}, headers=headers)
    assert again.status_code == 409 and again.json()["code"] == "revision_conflict"

    settings = client.get("/api/v1/proactive/settings", headers=headers).json()
    [rule] = settings["rules"]
    assert rule["words"] == "Never about “ana@example.com”"
    edited = client.put(f"/api/v1/proactive/rules/{rule['rule_id']}",
                        json={"type": "never_about", "topic": "Ana"}, headers=headers)
    assert [item["words"] for item in edited.json()["rules"]] == ["Never about “Ana”"]
    deleted = client.delete(f"/api/v1/proactive/rules/{rule['rule_id']}", headers=headers)
    assert deleted.json()["rules"] == []
    assert client.delete(f"/api/v1/proactive/rules/{rule['rule_id']}", headers=headers).status_code == 404


def test_the_activity_log_pages_newest_first_and_includes_silent_passes(proactive, api):
    client, headers = api
    watcher = ScriptedWatcher(every_seconds=60)
    proactive.watchers.register(watcher)
    for minute in range(5):
        proactive.engine.tick(at(f"2026-11-16 23:0{minute}"), desktop=AT_DESK)

    first = client.get("/api/v1/proactive/activity?limit=3", headers=headers).json()
    rest = client.get(f"/api/v1/proactive/activity?limit=3&before={first['next_before']}", headers=headers).json()

    summaries = [item["summary"] for item in first["items"] + rest["items"]]
    assert summaries == ["Checked fake_mail: nothing new."] * 5
    assert rest["next_before"] is None


def test_settings_read_and_change_with_their_privacy_lines(proactive, api):
    client, headers = api

    read = client.get("/api/v1/proactive/settings", headers=headers).json()
    assert read["level"] == "quiet", "Quiet is the default"
    assert (read["quiet_start"], read["quiet_end"]) == ("22:00", "07:30")
    assert "main chat model" in read["privacy"]
    assert all(source["privacy"] for source in read["sources"])
    assert "Never window titles" in read["desktop_context_privacy"]

    changed = client.put("/api/v1/proactive/settings", headers=headers, json={
        "level": "balanced", "quiet_start": "21:30", "kinds": {"date": False}, "urgent_kinds": ["meeting_prep"],
        "desktop_context": False})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["level"], body["quiet_start"], body["desktop_context"]) == ("balanced", "21:30", False)
    kinds = {kind["id"]: kind for kind in body["kinds"]}
    assert not kinds["date"]["on"] and kinds["follow_up"]["on"] and kinds["meeting_prep"]["urgent"]

    same = client.put("/api/v1/proactive/settings", headers=headers, json={"quiet_start": "07:30"})
    assert same.status_code == 422, "quiet hours that start and end together say nothing"
    assert client.put("/api/v1/proactive/settings", headers=headers, json={"level": "loud"}).status_code == 422


def test_opening_the_app_is_a_break(proactive, api):
    client, headers = api
    watcher = ScriptedWatcher()
    proactive.watchers.register(watcher)
    start_day(proactive)
    watcher.pending = [candidate("mail:a")]
    proactive.engine.tick(at("2026-11-16 09:00"), desktop=AT_DESK)
    assert proactive.store.list_moments()[0]["state"] == "pending"

    noted = client.post("/api/v1/proactive/boundaries", json={"kind": "app_open"}, headers=headers)

    assert noted.status_code == 200 and noted.json() == {"schema_version": 1, "noted": "app_open"}
    assert proactive.store.get_state("boundary")["kind"] == "app_open"
