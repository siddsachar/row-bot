"""task_create and task_update called as a model calls them, through their schemas (B1).

task_create keeps the decision rules and leaves icon, description and notification text to
task_update; an empty field is unset. These go through the tools' own argument schemas.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.fixtures.tasks import fresh_tasks_module

pytestmark = pytest.mark.subsystem


@pytest.fixture
def tools(tmp_path, monkeypatch, reload_for_data_dir):
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    from row_bot.tools import task_tool

    monkeypatch.setattr(task_tool, "tasks_db", tasks)
    return tasks, {tool.name: tool for tool in task_tool.TaskTool().as_langchain_tools()}


def _created(tasks, reply: str) -> dict:
    return tasks.get_task(reply.split("ID: ")[1].split()[0])


def test_a_timer_a_reminder_at_a_time_and_a_scheduled_workflow(tools):
    tasks, by_name = tools
    create = by_name["task_create"]
    when = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    timer = _created(tasks, create.invoke({"name": "Tea", "delay_minutes": 20}))
    reminder = _created(tasks, create.invoke({"name": "Call Mom", "run_at": when.strftime("%Y-%m-%dT%H:%M")}))
    workflow = _created(tasks, create.invoke({
        "name": "Morning briefing", "schedule": "daily:08:00", "prompts": ["Brief me on today's news."],
        "approval_mode": "block"}))

    in_twenty = datetime.fromisoformat(timer["at"]) - datetime.now()
    assert timer["notify_only"] and timer.get("schedule") is None
    assert timedelta(minutes=19) < in_twenty <= timedelta(minutes=20)
    assert (reminder["at"], reminder["notify_only"]) == (when.isoformat(timespec="minutes"), True)
    assert (workflow["schedule"], workflow["prompts"], workflow["notify_only"]) == (
        "daily:08:00", ["Brief me on today's news."], False)
    assert not workflow.get("delivery_channel") and not workflow.get("model_override")
    assert not {"icon", "description", "notify_label"} & set(create.args_schema.model_fields)


def test_task_update_sets_the_icon_description_and_notification_text(tools):
    tasks, by_name = tools
    task = _created(tasks, by_name["task_create"].invoke({"name": "Stretch", "delay_minutes": 30}))

    by_name["task_update"].invoke({"task_id": task["id"], "icon": "🧘", "description": "Every desk day.",
                                   "notify_label": "Time to stretch"})
    changed = tasks.get_task(task["id"])
    by_name["task_update"].invoke({"task_id": task["id"], "description": ""})  # '' clears the description
    cleared = tasks.get_task(task["id"])

    assert (changed["icon"], changed["description"], changed["notify_label"]) == (
        "🧘", "Every desk day.", "Time to stretch")
    assert cleared["description"] == "" and cleared["icon"] == "🧘" and cleared["name"] == "Stretch"


def test_a_null_for_an_optional_field_is_the_same_as_leaving_it_out(tools):
    """Models often send null for fields they don't use; the schemas carry no null branch (B1)."""
    tasks, by_name = tools
    nulls = {"schedule": None, "run_at": None, "delivery_channel": None,
             "delivery_target": None, "model": None, "agent_profile": None, "approval_mode": None,
             "timezone": None}

    task = _created(tasks, by_name["task_create"].invoke({"name": "Water the plants", "delay_minutes": 5, **nulls}))
    by_name["task_update"].invoke({"task_id": task["id"], "name": None, "icon": None, "run_at": None,
                                   "notify_label": None, "schedule": None, "model": None, "notes": None,
                                   "timezone": None, "description": "Every other day"})
    after = tasks.get_task(task["id"])

    assert task["notify_only"] and task.get("schedule") is None and not task.get("delivery_channel")
    assert (after["name"], after["icon"], after["at"], after["description"]) == (
        "Water the plants", task["icon"], task["at"], "Every other day")
    reminder = by_name["task_create"].invoke({"name": "Plain", "delay_minutes": None, "prompts": ["Say hi."]})
    assert not _created(tasks, reminder).get("at")  # a null delay is no delay
