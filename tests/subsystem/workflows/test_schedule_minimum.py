"""Workflows run at most once a minute (D5's interval fix).

Every path that saves a schedule refuses one shorter than a minute with words the person and the
agent can act on; a fractional interval is never cut to 0; a schedule saved below a minute before
the minimum existed keeps its saved words and runs once a minute. One-off times and quick timers
fire once, so they keep any number of seconds. Every clock here is fixed.
"""

# ruff: noqa: F811 -- pytest fixtures are imported and used by name.
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging

import pytest

from row_bot.application import task_controls as control
from tests.subsystem.workflows.test_task_controls import create, fields, owner  # noqa: F401

pytestmark = pytest.mark.subsystem

TOO_OFTEN = "Workflows run at most once a minute; choose 1 minute or more."
SHORT = ["interval:0.01", "interval_minutes:0.5"]  # 36 seconds and 30 seconds.
NOW = datetime(2027, 1, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def tool(owner, monkeypatch):
    from row_bot.tools import task_tool

    monkeypatch.setattr(task_tool, "tasks_db", owner)
    return task_tool


def _created_id(reply: str) -> str:
    return reply.split("ID: ")[1].split()[0]


def _save_legacy(tasks, task_id: str, schedule: str) -> None:
    """A schedule saved before the minimum existed, written as an old Row-Bot left it."""
    conn = tasks._get_conn()
    conn.execute("UPDATE tasks SET schedule = ? WHERE id = ?", (schedule, task_id))
    conn.commit()
    conn.close()


def _gap(trigger, previous: datetime = NOW) -> timedelta:
    return trigger.get_next_fire_time(previous, previous) - previous


# ── Every path that saves a schedule refuses one shorter than a minute ─────────────────────────


@pytest.mark.parametrize("schedule", SHORT)
def test_the_editor_refuses_a_new_schedule_shorter_than_a_minute(owner, fields, schedule):
    with pytest.raises(control.TaskControlError, match="task_schedule_too_often"):
        create(replace(fields, schedule=schedule))
    assert owner.list_tasks() == []


@pytest.mark.parametrize("schedule", SHORT)
def test_the_editor_refuses_changing_a_schedule_to_shorter_than_a_minute(owner, fields, schedule):
    saved = create(replace(fields, schedule="daily:09:00")).task
    with pytest.raises(control.TaskControlError, match="task_schedule_too_often"):
        control.update_saved_task(saved.id, replace(saved.fields, schedule=schedule),
                                  expected_revision=saved.revision, validate=lambda: None)
    assert owner.get_task(saved.id)["schedule"] == "daily:09:00"


@pytest.mark.parametrize("schedule", SHORT)
def test_the_agent_is_told_what_to_choose_instead(owner, tool, schedule):
    reply = tool._task_create(name="Inbox watch", prompts=["Check the inbox"], schedule=schedule)

    assert reply == f"Error creating task: {TOO_OFTEN}"
    assert owner.list_tasks() == []


@pytest.mark.parametrize("schedule", SHORT)
def test_the_agent_cannot_change_a_schedule_to_shorter_than_a_minute(owner, tool, schedule):
    task_id = _created_id(tool._task_create(name="Inbox watch", prompts=["Check"], schedule="interval:2"))

    reply = tool._task_update(task_id, schedule=schedule)

    assert reply == f"Error updating task: {TOO_OFTEN}"
    assert owner.get_task(task_id)["schedule"] == "interval:2"


@pytest.mark.parametrize("schedule", SHORT)
def test_templates_and_drafts_are_refused_too(owner, schedule):
    # Templates, Proactive drafts and promoted runs all save through create_task.
    with pytest.raises(owner.TaskMutationError, match="once a minute") as refused:
        owner.create_from_template({"name": "Fast template", "prompts": ["Check"], "schedule": schedule})
    assert refused.value.code == "task_schedule_too_often"
    assert owner.list_tasks() == []


# ── A minute or more is fine ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("schedule", ["interval_minutes:1", "interval:0.5", "cron:* * * * *"])
def test_once_a_minute_or_less_often_saves_on_every_path(owner, fields, tool, schedule):
    assert create(replace(fields, schedule=schedule)).task.fields.schedule == schedule
    created = _created_id(tool._task_create(name="Agent made", prompts=["Check"], schedule=schedule))
    assert owner.get_task(created)["schedule"] == schedule
    assert tool._task_update(created, schedule="daily:08:00").startswith("Updated task")
    assert tool._task_update(created, schedule=schedule).startswith("Updated task")
    assert owner.get_task(created)["schedule"] == schedule


def test_a_quick_timer_may_be_seconds_away(owner, tool):
    # A timer fires once; it isn't a repeating schedule, so the minimum doesn't apply.
    reply = tool._task_create(name="Tea", delay_minutes=0.5)

    task = owner.get_task(_created_id(reply))
    assert task["at"] and not task.get("schedule") and task["notify_only"]


# ── The scheduler ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("schedule", "seconds"), [
    ("interval_minutes:1.5", 90), ("interval:0.025", 90), ("interval_minutes:1", 60), ("interval:2", 7200),
])
def test_a_fractional_interval_is_kept_exactly(owner, schedule, seconds):
    trigger = owner._build_trigger({"schedule": schedule})

    assert _gap(trigger) == timedelta(seconds=seconds)
    last = NOW - timedelta(seconds=10)
    assert owner.estimate_next_run(
        {"enabled": 1, "schedule": schedule, "last_run": last.isoformat()}, NOW
    ) == (last + timedelta(seconds=seconds)).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


@pytest.mark.parametrize("schedule", ["interval_minutes:0.5", "interval:0.01"])
def test_a_schedule_saved_below_a_minute_runs_once_a_minute_and_keeps_its_words(
        owner, fields, schedule, caplog):
    task_id = create(replace(fields, schedule="daily:09:00", enabled=True)).task.id
    _save_legacy(owner, task_id, schedule)
    task = owner.get_task(task_id)

    with caplog.at_level(logging.WARNING, logger=owner.logger.name):
        first = owner._build_trigger(task)
        again = owner._build_trigger(task)

    assert _gap(first) == _gap(again) == timedelta(minutes=1)
    assert [record.getMessage() for record in caplog.records].count(
        f"Workflow {task_id} is saved as {schedule!r}, more often than once a minute; it runs once a minute") == 1
    assert owner.get_task(task_id)["schedule"] == schedule  # Not rewritten.
    # Its next time and a missed time count a minute too, never 0 or a second.
    last = NOW - timedelta(seconds=10)
    task = {"id": task_id, "enabled": 1, "schedule": schedule,
            "last_run": last.astimezone().replace(tzinfo=None).isoformat()}
    assert owner.estimate_next_run(task, NOW) == (last + timedelta(minutes=1)).astimezone().replace(
        tzinfo=None).isoformat(timespec="seconds")
    assert owner.missed_run_time(task, NOW - timedelta(hours=1), NOW + timedelta(minutes=5)) == (
        last + timedelta(minutes=5)).astimezone()


@pytest.mark.parametrize("schedule", ["interval_minutes:0", "interval:0"])
def test_a_schedule_saved_at_0_still_never_runs(owner, fields, schedule):
    # It has never run; an update must not start it running every minute and spending by surprise.
    task_id = create(replace(fields, schedule="daily:09:00", enabled=True)).task.id
    _save_legacy(owner, task_id, schedule)
    task = owner.get_task(task_id)

    assert owner._build_trigger(task) is None
    last = (NOW - timedelta(hours=1)).astimezone().replace(tzinfo=None).isoformat()
    assert owner.estimate_next_run({**task, "last_run": last}, NOW) is None
    assert owner.missed_run_time({**task, "last_run": last}, NOW - timedelta(hours=2), NOW) is None
    assert owner.get_task(task_id)["schedule"] == schedule
    # And it can't be saved at 0 again.
    with pytest.raises(owner.TaskMutationError, match="once a minute"):
        owner.update_task(task_id, schedule=schedule)


def test_a_legacy_schedule_below_a_minute_can_still_be_opened_and_fixed(owner, fields):
    saved = create(replace(fields, schedule="daily:09:00")).task
    _save_legacy(owner, saved.id, "interval_minutes:0.5")

    opened = control.get_task_editor(saved.id)
    assert opened.fields.schedule == "interval_minutes:0.5"
    fixed = control.update_saved_task(saved.id, replace(opened.fields, schedule="interval_minutes:1"),
                                      expected_revision=opened.revision, validate=lambda: None)
    assert fixed.task.fields.schedule == "interval_minutes:1"
