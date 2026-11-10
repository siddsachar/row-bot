"""N9: a schedule keeps its wall-clock time in its own IANA zone, across daylight saving.

A time that doesn't exist that day runs at the next valid minute; a time that happens twice runs
once. Schedules saved before zones were kept get the computer's zone, so nothing moves. Every
clock here is fixed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.fixtures.tasks import fresh_tasks_module

pytestmark = pytest.mark.subsystem

UTC = timezone.utc


@pytest.fixture
def tasks(tmp_path, reload_for_data_dir):
    return fresh_tasks_module(tmp_path, reload_for_data_dir)


def _next(tasks, task: dict, now: datetime) -> datetime:
    trigger = tasks._build_trigger(task, now=now)
    return trigger.get_next_fire_time(None, now)


def test_the_wall_clock_time_is_kept_across_a_dst_change(tasks):
    # London's clocks go back an hour early on 25 October 2026.
    task = {"schedule": "daily:09:00", "timezone": "Europe/London"}
    summer = _next(tasks, task, datetime(2026, 10, 23, 12, 0, tzinfo=UTC))
    winter = tasks._build_trigger(task).get_next_fire_time(summer, summer + timedelta(seconds=1))

    assert summer.isoformat() == "2026-10-24T09:00:00+01:00"
    assert winter.isoformat() == "2026-10-25T09:00:00+00:00"


def test_a_time_that_doesnt_exist_that_day_runs_at_the_next_valid_minute(tasks):
    # 01:30 on 29 March 2026 doesn't exist in London: the clocks go from 01:00 to 02:00.
    task = {"schedule": "daily:01:30", "timezone": "Europe/London"}
    gap = _next(tasks, task, datetime(2026, 3, 28, 12, 0, tzinfo=UTC))
    after = tasks._build_trigger(task).get_next_fire_time(gap, gap + timedelta(seconds=1))

    assert gap.isoformat() == "2026-03-29T02:00:00+01:00"
    assert after.isoformat() == "2026-03-30T01:30:00+01:00"
    assert tasks.estimate_next_run({**task, "enabled": 1}, datetime(2026, 3, 28, 12, 0, tzinfo=UTC)) == (
        "2026-03-29T02:00:00+01:00")


def test_a_time_that_happens_twice_runs_once(tasks):
    # 01:30 on 25 October 2026 happens twice in London: the clocks go back from 02:00 to 01:00.
    task = {"schedule": "daily:01:30", "timezone": "Europe/London"}
    trigger = tasks._build_trigger(task)
    first = trigger.get_next_fire_time(None, datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    then = trigger.get_next_fire_time(first, first + timedelta(seconds=1))

    assert first.isoformat() == "2026-10-25T01:30:00+01:00"
    assert then.isoformat() == "2026-10-26T01:30:00+00:00"
    # Started inside the repeated hour, after the first 01:30: the second one is not run.
    assert trigger.get_next_fire_time(None, datetime(2026, 10, 25, 1, 20, tzinfo=UTC)) == then


def test_the_next_run_is_named_in_the_schedules_zone(tasks):
    now = datetime(2026, 10, 22, 18, 0, tzinfo=UTC)  # Thursday afternoon in New York.
    task = {"enabled": 1, "schedule": "cron:0 9 * * mon-fri", "timezone": "America/New_York"}

    assert tasks.estimate_next_run(task, now) == "2026-10-23T09:00:00-04:00"
    assert tasks.estimate_next_run({**task, "timezone": "Asia/Tokyo"}, now) == "2026-10-23T09:00:00+09:00"


@pytest.mark.parametrize(("days", "expected"), [
    ("1-5", ["Mon 02", "Tue 03", "Wed 04", "Thu 05", "Fri 06", "Mon 09"]),
    ("mon-fri", ["Mon 02", "Tue 03", "Wed 04", "Thu 05", "Fri 06", "Mon 09"]),
    ("0,6", ["Sun 01", "Sat 07", "Sun 08", "Sat 14", "Sun 15", "Sat 21"]),
    ("7", ["Sun 01", "Sun 08", "Sun 15", "Sun 22", "Sun 29", "Sun 06"]),
    ("1-5/2", ["Mon 02", "Wed 04", "Fri 06", "Mon 09", "Wed 11", "Fri 13"]),
])
def test_cron_days_are_read_as_standard_cron(tasks, days, expected):
    # "Every weekday" written as cron's 1-5 runs Monday to Friday, not Tuesday to Saturday.
    trigger = tasks._build_trigger({"schedule": f"cron:0 8 * * {days}", "timezone": "Europe/London"})
    fire = trigger.get_next_fire_time(None, datetime(2026, 11, 1, 0, 0, tzinfo=UTC))  # A Sunday.
    seen = []
    for _ in range(6):
        seen.append(fire.strftime("%a %d"))
        fire = trigger.get_next_fire_time(fire, fire + timedelta(seconds=1))

    assert seen == expected


def test_a_one_time_run_is_at_its_wall_clock_time_in_its_zone(tasks):
    now = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    trigger = tasks._build_trigger({"at": "2026-12-01T09:00:00", "timezone": "Asia/Tokyo"}, now=now)

    assert trigger.run_date == datetime(2026, 12, 1, 0, 0, tzinfo=UTC)


def test_a_new_schedule_starts_with_the_computers_zone(tasks, monkeypatch):
    from row_bot import schedule_zones

    monkeypatch.setattr(schedule_zones, "local_zone_name", lambda: "Europe/Paris")
    scheduled = tasks.create_task("Digest", prompts=["Summarize"], schedule="daily:08:00")
    chosen = tasks.create_task("Standup", prompts=["Prepare"], schedule="daily:08:00", timezone="Asia/Kolkata")
    manual = tasks.create_task("Ad hoc", prompts=["Help"])

    assert tasks.get_task(scheduled)["timezone"] == "Europe/Paris"
    assert tasks.get_task(chosen)["timezone"] == "Asia/Kolkata"
    assert tasks.get_task(manual)["timezone"] == ""


def test_existing_schedules_get_the_computers_zone_on_first_start_and_keep_their_times(tasks, monkeypatch):
    conn = tasks._get_conn()
    try:
        conn.executemany(
            "INSERT INTO tasks (id,name,prompts,schedule,at,created_at,enabled,timezone) VALUES (?,?,?,?,?,?,1,'')",
            [("daily", "Daily", '["Go"]', "daily:09:00", None, "2026-01-01"),
             ("once", "Once", '["Go"]', None, "2026-12-01T09:00:00", "2026-01-01"),
             ("manual", "Manual", '["Go"]', None, None, "2026-01-01")],
        )
        conn.commit()
    finally:
        conn.close()
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    before = {task_id: tasks.estimate_next_run(tasks.get_task(task_id), now) for task_id in ("daily", "once")}
    from row_bot import schedule_zones

    monkeypatch.setattr(schedule_zones, "local_zone_name", lambda: "Europe/London")
    monkeypatch.setattr(schedule_zones, "zone", lambda name: schedule_zones.ZoneInfo(name or "Europe/London"))

    tasks.ensure_task_schema(force=True)

    assert {row["id"]: row["timezone"] for row in tasks.list_tasks()} == {
        "daily": "Europe/London", "once": "Europe/London", "manual": ""}
    after = {task_id: tasks.estimate_next_run(tasks.get_task(task_id), now) for task_id in ("daily", "once")}
    # The same wall-clock times, now with the zone they were kept in.
    assert {key: value[:19] for key, value in after.items()} == {
        "daily": "2026-10-23T09:00:00", "once": "2026-12-01T09:00:00"}
    assert before["daily"] is not None and before["once"] is not None


def test_a_zone_that_isnt_one_is_refused(tasks):
    from row_bot.application.task_controls import TaskControlError, TaskEditableFields, create_saved_task

    fields = TaskEditableFields("Digest", "", "", ("Summarize",), True, "daily:09:00", None, False, "", None,
                                timezone="Mars/Olympus")
    with pytest.raises(TaskControlError) as refused:
        create_saved_task(fields, stable_task_id="digest", validate=lambda: None)

    assert refused.value.code == "invalid_task_schedule"
    assert tasks.get_task("digest") is None
