"""The day's start and end: the morning brief and the end-of-day wrap come from workflows the person can edit.

Real engine ticks on a fake clock. Most tests finish the workflow's run at once with its expanded message as
the reply (``DayRuns``); the last runs it for real as a chat turn in the workflow's own conversation, with a
scripted model.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from row_bot.proactive import day as _day
from row_bot.proactive.model import Desktop
from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.subsystem.proactive.conftest import AT_DESK, ScriptedWatcher, at, candidate
from tests.subsystem.proactive.fakes import FakeCalendar, meeting
from tests.subsystem.workflows.test_workflow_runs_as_chat import Recording, reply, settle

pytestmark = pytest.mark.subsystem

CLOSING = "That's everything for today."
REAL_START_RUN = _day.start_run
NIGHT = Desktop(idle_seconds=4000, locked=True)


def _waiting(proactive, *found, night: str = "2026-11-16 06:00") -> ScriptedWatcher:
    """Things that came in overnight, in quiet hours: they wait for the morning."""
    watcher = proactive.watchers.get("fake_mail") or ScriptedWatcher()
    proactive.watchers.register(watcher)
    watcher.pending = list(found)
    proactive.engine.tick(at(night), desktop=NIGHT)
    return watcher


def _brief(proactive, day: str = "2026-11-16") -> dict:
    return next(moment for moment in proactive.store.list_moments() if moment["dedupe_key"] == f"brief:{day}")


def test_the_brief_comes_from_its_workflow_and_editing_the_workflow_changes_the_brief(proactive):
    proactive.calendars.append(FakeCalendar(items={"e1": meeting("e1", at("2026-11-16 10:00"))}))
    _waiting(proactive, candidate("mail:a"))

    proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)

    [workflow] = [task for task in proactive.tasks.list_tasks() if task["name"] == "Morning brief"]
    brief = _brief(proactive)
    assert brief["body"].startswith("Write my morning brief for")
    assert "Meetings today:\n- 10:00 Design review (with 1 other)" in brief["body"]
    assert "Waiting for you:\n- Ana hasn't replied about the deck" in brief["body"]
    assert proactive.day.which(workflow["id"]) == "brief"
    assert brief["evidence"][0]["type"] == "workflow_run" and brief["evidence"][0]["label"] == "Morning brief"

    proactive.tasks.update_task(workflow["id"], prompts=["Brief me in French, short: {{for_you}}"])
    _waiting(proactive, candidate("mail:b", title="Ben hasn't replied about the invoice"), night="2026-11-17 06:00")
    proactive.engine.tick(at("2026-11-17 08:10"), desktop=AT_DESK)

    tomorrow = _brief(proactive, "2026-11-17")
    assert tomorrow["body"].startswith("Brief me in French, short: ")
    assert "- Ben hasn't replied about the invoice" in tomorrow["body"]
    assert [task["name"] for task in proactive.tasks.list_tasks()].count("Morning brief") == 1


def test_the_brief_ends_with_the_closing_line_once(proactive):
    _waiting(proactive, candidate("mail:a"))
    proactive.day_runs.reply = f"- Ana is waiting on the deck.\n\n**{CLOSING}**"

    proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)

    assert _brief(proactive)["body"] == f"- Ana is waiting on the deck.\n\n{CLOSING}"
    _waiting(proactive, candidate("mail:b", title="Ben"), night="2026-11-17 06:00")
    proactive.day_runs.reply = "- Ben is waiting on the invoice."
    proactive.engine.tick(at("2026-11-17 08:10"), desktop=AT_DESK)
    assert _brief(proactive, "2026-11-17")["body"] == f"- Ben is waiting on the invoice.\n\n{CLOSING}"


def test_the_brief_comes_once_a_day_at_the_first_break_and_never_in_quiet_hours(proactive):
    proactive.tasks.seed_default_tasks()  # A new install: the Morning brief is a starter workflow.
    _waiting(proactive, candidate("mail:a"))
    assert proactive.day_runs.messages == [], "not in quiet hours"

    first = proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)
    proactive.engine.note_boundary("app_open", now=at("2026-11-16 13:00"))
    proactive.engine.tick(at("2026-11-16 13:00:05"), desktop=AT_DESK)

    assert first["boundary"] == "unlock" and first["sent"] == [_brief(proactive)["id"]]
    assert len(proactive.day_runs.messages) == 1, "once a day"
    starters = [task for task in proactive.tasks.list_tasks() if task["name"] == "Morning brief"]
    assert len(starters) == 1 and proactive.day.which(starters[0]["id"]) == "brief", "the starter, not a copy"


def test_a_brief_whose_run_failed_leaves_what_waits_to_come_on_its_own(proactive):
    _waiting(proactive, candidate("mail:a"))
    proactive.day_runs.status = "failed"

    proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)

    [waiting] = proactive.store.list_moments()
    assert waiting["kind"] == "follow_up" and waiting["state"] in ("pending", "shown") and waiting["surface"] != "brief"
    assert any("the workflow didn't finish" in entry["summary"] for entry in proactive.store.activity())


def test_a_deleted_brief_workflow_comes_back_only_when_the_person_asks_again(proactive):
    _waiting(proactive, candidate("mail:a"))
    proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)
    [workflow] = [task for task in proactive.tasks.list_tasks() if task["name"] == "Morning brief"]
    proactive.tasks.delete_task(workflow["id"])

    _waiting(proactive, candidate("mail:b", title="Ben"), night="2026-11-17 06:00")
    proactive.engine.tick(at("2026-11-17 08:10"), desktop=AT_DESK)
    assert "Morning brief" not in [task["name"] for task in proactive.tasks.list_tasks()]
    assert len(proactive.day_runs.messages) == 1

    proactive.settings.save({"kinds": {"brief": False}})
    proactive.settings.save({"kinds": {"brief": True}})
    _waiting(proactive, candidate("mail:c", title="Cleo"), night="2026-11-18 06:00")
    proactive.engine.tick(at("2026-11-18 08:10"), desktop=AT_DESK)
    assert "Morning brief" in [task["name"] for task in proactive.tasks.list_tasks()]
    assert len(proactive.day_runs.messages) == 2


def _evening(proactive, day: str = "2026-11-16") -> dict:
    proactive.engine.tick(at(f"{day} 08:10"), desktop=AT_DESK)
    return proactive.engine.tick(at(f"{day} 18:05"), desktop=AT_DESK)


def test_the_wrap_is_off_at_quiet_and_on_at_balanced_unless_the_person_chose(proactive):
    _waiting(proactive, candidate("mail:a"))
    _evening(proactive)
    assert not [task for task in proactive.tasks.list_tasks() if task["name"] == "End-of-day wrap"]
    assert proactive.settings.wrap_on(proactive.settings.load()) is False

    proactive.settings.save({"level": "balanced"})
    acted = _waiting(proactive, candidate("mail:b", title="Ben sent the contract"), night="2026-11-17 06:00")
    proactive.engine.tick(at("2026-11-17 08:10"), desktop=AT_DESK)
    ben = next(moment for moment in proactive.store.list_moments() if moment["dedupe_key"] == "mail:b")
    proactive.feedback.act(ben["id"], now=at("2026-11-17 11:00"))
    acted.pending = [candidate("mail:c", title="Cleo asked about Friday")]
    proactive.engine.tick(at("2026-11-17 12:00"), desktop=AT_DESK)
    result = proactive.engine.tick(at("2026-11-17 18:05"), desktop=AT_DESK)

    wrap = next(moment for moment in proactive.store.list_moments() if moment["dedupe_key"] == "wrap:2026-11-17")
    assert result["sent"] == [wrap["id"]], "at the end of the day"
    assert wrap["title"] == "Your day, wrapped" and wrap["body"].startswith("Wrap up my day")
    assert "Done today:\n- Ben sent the contract" in wrap["body"]
    assert "Moved to tomorrow:\n- Cleo asked about Friday" in wrap["body"]
    assert wrap["body"].endswith(CLOSING)

    proactive.settings.save({"wrap": False})  # The person's choice wins over the level.
    proactive.engine.tick(at("2026-11-18 08:10"), desktop=AT_DESK)
    proactive.engine.tick(at("2026-11-18 18:05"), desktop=AT_DESK)
    assert not any(moment["dedupe_key"] == "wrap:2026-11-18" for moment in proactive.store.list_moments())
    proactive.settings.save({"level": "quiet", "wrap": True})
    assert proactive.settings.wrap_on(proactive.settings.load()) is True


@pytest.fixture
def real_runs(proactive, platform, monkeypatch):  # noqa: F811
    """The brief's workflow runs for real: a chat turn in its own conversation, on a scripted model."""
    from row_bot import models, notifications, tasks
    from row_bot.application import client_platform, conversation_followups

    monkeypatch.setattr(proactive.day, "start_run", REAL_START_RUN)
    monkeypatch.setattr(client_platform, "client_platform_service", platform)
    monkeypatch.setattr(models, "get_current_model", lambda: "fixture/model")
    for key in list(conversation_followups._PENDING):
        conversation_followups._PENDING.pop(key, None)
    delivered: list[tuple[dict, str]] = []
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda task, text, **_: delivered.append((task, text)) or ("", ""))
    notices: list[str] = []
    monkeypatch.setattr(notifications, "notify", lambda title, *args, **kwargs: notices.append(title))
    return SimpleNamespace(delivered=delivered, notices=notices)


class SeesItsSurface(Recording):
    def __init__(self, *scripts) -> None:
        super().__init__(*scripts)
        self.surfaces: list[str] = []

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        self.surfaces.append(config["configurable"]["runtime_surface"])
        yield from super().stream(text, enabled_tools, config, stop_event=stop_event)


def test_a_real_brief_run_only_reads_and_its_reply_is_the_brief_and_goes_where_the_workflow_sends(
        proactive, platform, real_runs, monkeypatch):  # noqa: F811
    fake = SeesItsSurface(reply("- Ana is still waiting on the deck: send it before 10."))
    platform.stream_factory = fake.stream
    _waiting(proactive, candidate("mail:a"))
    monkeypatch.setattr(proactive.day, "time", SimpleNamespace(time=lambda: at("2026-11-16 08:11")))

    proactive.engine.tick(at("2026-11-16 08:10"), desktop=AT_DESK)
    [workflow] = [task for task in proactive.tasks.list_tasks() if task["name"] == "Morning brief"]
    settle(platform, workflow["persistent_thread_id"])

    assert fake.surfaces == ["proactive"], "the brief's run takes the read-only surface"
    assert "Waiting for you:\n- Ana hasn't replied about the deck" in fake.prompts[0]
    assert "task_update" not in fake.prompts[0], "a run that only reads keeps no notes"
    assert _brief(proactive)["body"] == f"- Ana is still waiting on the deck: send it before 10.\n\n{CLOSING}"
    [(task, text)] = real_runs.delivered
    assert task["id"] == workflow["id"] and "send it before 10" in text, "to the workflow's own delivery"
    assert not [title for title in real_runs.notices if title.endswith("finished")], "one moment, one surface"
    with sqlite3.connect(platform_threads_db()) as connection:
        kind = connection.execute("SELECT COALESCE(thread_type, '') FROM thread_meta WHERE thread_id = ?",
                                  (workflow["persistent_thread_id"],)).fetchone()[0]
    assert kind == "", "an ordinary workflow conversation"


def platform_threads_db() -> str:
    from row_bot import threads

    return threads.DB_PATH
