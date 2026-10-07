"""B291: a reminder for an exact date and time is stored for that minute, in one call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.tasks import fresh_tasks_module

pytestmark = pytest.mark.subsystem


@pytest.fixture
def tool(tmp_path, monkeypatch):
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.tools import task_tool

    monkeypatch.setattr(task_tool, "tasks_db", tasks)
    return tasks, task_tool


def _created_id(reply: str) -> str:
    return reply.split("ID: ")[1].split()[0]


def test_a_reminder_at_a_stated_time_fires_at_that_minute(tool):
    tasks, task_tool = tool
    when = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    reply = task_tool._task_create(name="Chase Ashgrove", run_at=when.strftime("%Y-%m-%dT%H:%M"))

    task = tasks.get_task(_created_id(reply))
    assert (task["at"], task["notify_only"], task.get("schedule")) == (when.isoformat(timespec="minutes"), True, None)
    assert tasks._build_trigger(task).run_date.replace(tzinfo=None) == when


def test_a_time_with_a_zone_is_stored_as_the_same_moment_in_local_time(tool):
    tasks, task_tool = tool
    moment = (datetime.now(timezone.utc) + timedelta(days=2)).replace(second=0, microsecond=0)

    reply = task_tool._task_create(name="Call", run_at=moment.isoformat())

    assert tasks.get_task(_created_id(reply))["at"] == moment.astimezone().replace(tzinfo=None).isoformat(
        timespec="minutes")


@pytest.mark.parametrize("value", ["2020-01-01T09:00", "tomorrow at nine"])
def test_a_past_or_unreadable_time_is_refused(tool, value):
    tasks, task_tool = tool

    reply = task_tool._task_create(name="Too late", run_at=value)

    assert reply.startswith("Error creating task: run_at")
    assert tasks.list_tasks() == []


def test_moving_a_reminder_changes_its_time_without_deleting_it(tool):
    tasks, task_tool = tool
    first = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    task_id = _created_id(task_tool._task_create(name="Chase", run_at=first.strftime("%Y-%m-%dT%H:%M")))
    later = first + timedelta(hours=2)

    reply = task_tool._task_update(task_id, run_at=later.strftime("%Y-%m-%dT%H:%M"))

    assert f"Fires at: {later.isoformat(timespec='minutes')}" in reply
    assert [task["id"] for task in tasks.list_tasks()] == [task_id]
