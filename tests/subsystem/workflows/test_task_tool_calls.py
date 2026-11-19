"""task_create and task_update called as a model calls them, through their schemas (B1).

task_create keeps the decision rules and leaves icon, description and notification text to
task_update; an empty field is unset. These go through the tools' own argument schemas.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
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


def test_a_schedule_set_from_a_chat_keeps_the_persons_device_zone(tools, monkeypatch):
    """M10: "Your local time" from a London browser made a Los Angeles workflow, the Mac's zone. A schedule the
    model sets without a zone keeps the zone of the device the person sent from; a zone they name wins, and
    with no device (a chat app, a workflow run) it is the computer's."""
    from row_bot import agent, schedule_zones

    tasks, by_name = tools
    create = by_name["task_create"]
    monkeypatch.setattr(schedule_zones, "local_zone_name", lambda: "America/Los_Angeles")
    weather = {"name": "Sydney weather", "schedule": "weekdays:08:00", "prompts": ["Sydney's weather today."]}

    token = agent._current_device_zone_var.set("Europe/London")
    try:
        from_device = _created(tasks, create.invoke(weather))
        named = _created(tasks, create.invoke({**weather, "timezone": "Australia/Sydney"}))
    finally:
        agent._current_device_zone_var.reset(token)
    no_device = _created(tasks, create.invoke(weather))

    assert (from_device["timezone"], named["timezone"], no_device["timezone"]) == (
        "Europe/London", "Australia/Sydney", "America/Los_Angeles")


def test_a_message_carries_its_devices_zone_into_its_turn(platform):  # noqa: F811
    """M10: the client sends its own zone with each message; the turn runs with it, and a zone that isn't one is
    left out (the computer's zone then)."""
    from langchain_core.messages import AIMessage

    from tests.contracts.client_platform.test_headless_lifecycle import command
    from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id

    zones: list[str | None] = []

    class Recording(ScriptedAgentStream):
        def stream(self, text, enabled_tools, config, *, stop_event=None):
            zones.append(config["configurable"].get("device_time_zone"))
            yield from super().stream(text, enabled_tools, config, stop_event=stop_event)

    def turn(label: str) -> tuple:
        reply = fixture_id(label + ":reply")
        return (("token", "Noted."), CheckpointCommit((AIMessage(content="Noted.", id=reply),), reply),
                ("done", "Noted."))

    fake = Recording(turn("london"), turn("nowhere"))
    platform.stream_factory = fake.stream
    for label, zone in (("london", "Europe/London"), ("nowhere", "Not/A_Zone")):
        receipt = platform.execute(
            owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target="conversation-a",
            command=command("conversation.submit", label, {
                "submission_id": fixture_id(label), "text": "Every weekday at 8, tell me Sydney's weather.",
                "attachment_refs": [], "time_zone": zone,
                "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}))
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)

    assert zones == ["Europe/London", None]
