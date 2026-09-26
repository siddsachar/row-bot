from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json

import pytest

from row_bot import task_views
from tests.fixtures.tasks import fresh_tasks_module

pytestmark = pytest.mark.subsystem


@pytest.fixture
def saved(tmp_path, monkeypatch):
    tasks = fresh_tasks_module(tmp_path, monkeypatch)

    def seed(count=1):
        conn = tasks._get_conn()
        try:
            conn.executemany(
                "INSERT INTO tasks (id,name,description,icon,prompts,created_at,enabled,delivery_target,persistent_thread_id) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"task-{i:03}",
                        f"Saved task {i:03}",
                        "Synthetic description",
                        "",
                        '["PRIVATE_PROMPT"]',
                        "2026-01-01",
                        i % 2,
                        "PRIVATE_DESTINATION",
                        "conversation-a",
                    )
                    for i in range(count)
                ],
            )
            conn.commit()
        finally:
            conn.close()

    return tasks, seed


def test_saved_task_pages_retain_ids_order_and_never_expose_prompts_or_delivery(
    saved, monkeypatch
):
    tasks, seed = saved
    seed(205)
    for name in ("run_task_background", "get_task_channels", "get_next_fire_times"):
        monkeypatch.setattr(
            tasks, name, lambda *a, **k: pytest.fail("read triggered runtime owner")
        )
    page = task_views.list_saved_tasks(limit=100)
    assert page.total == 205 and len(page.items) == 100
    seen = list(page.items)
    while page.next_cursor:
        page = task_views.list_saved_tasks(limit=100, cursor=page.next_cursor)
        seen.extend(page.items)
    assert len(page.items) == 5
    assert [item.id for item in seen] == [f"task-{i:03}" for i in range(205)]
    assert all(item.conversation_id == "conversation-a" for item in seen)
    assert all(item.step_count == 1 for item in seen)
    assert "PRIVATE_" not in json.dumps([asdict(item) for item in seen])


def test_filter_search_applies_before_pagination(saved):
    _, seed = saved
    seed(205)
    page = task_views.list_saved_tasks(query="TASK 20", enabled=True, limit=1)
    assert page.total == 2 and page.items[0].id == "task-201"
    assert (
        task_views.list_saved_tasks(
            query="task 20", enabled=True, limit=1, cursor=page.next_cursor
        )
        .items[0]
        .id
        == "task-203"
    )


@pytest.mark.parametrize("change", ["filter", "limit", "rename", "run", "delete"])
def test_changed_cursor_is_rejected_instead_of_combining_snapshots(saved, change):
    tasks, seed = saved
    seed(3)
    page = task_views.list_saved_tasks(limit=1)
    kwargs = {"limit": 1, "cursor": page.next_cursor}
    conn = tasks._get_conn()
    try:
        if change == "filter":
            kwargs["query"] = "task"
        elif change == "limit":
            kwargs["limit"] = 2
        elif change == "rename":
            conn.execute("UPDATE tasks SET name='Renamed' WHERE id='task-002'")
        elif change == "delete":
            conn.execute("DELETE FROM tasks WHERE id='task-002'")
        else:
            conn.execute(
                "INSERT INTO task_runs(id,task_id,thread_id,started_at,status) VALUES('run','task-002','thread','2026-01-01','approval_pending')"
            )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(task_views.TaskViewError, match="cursor_expired"):
        task_views.list_saved_tasks(**kwargs)


def test_metadata_snapshot_is_consistent_across_batches_and_closes_early(saved):
    tasks, seed = saved
    seed(200)
    rows = tasks.iter_task_summary_snapshot()
    assert next(rows)["name"] == "Saved task 000"
    conn = tasks._get_conn()
    try:
        conn.execute("UPDATE tasks SET name='New name' WHERE id='task-199'")
        conn.commit()
    finally:
        conn.close()
    assert list(rows)[-1]["name"] == "Saved task 199"
    assert task_views.list_saved_tasks(query="New name").total == 1


def test_empty_and_bounded_summary_preserve_saved_schedule_and_history(saved):
    tasks, seed = saved
    assert task_views.list_saved_tasks().items == ()
    seed()
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET name=?,description=?,schedule=?,at=?,notify_only=1",
            ("N" * 400, "D" * 5000, "0 9 * * 1", "2026-09-01T09:00:00"),
        )
        conn.execute(
            "INSERT INTO task_runs(id,task_id,thread_id,started_at,status) VALUES('run','task-000','thread','2026-01-01','failed')"
        )
        conn.commit()
    finally:
        conn.close()
    row = task_views.list_saved_tasks().items[0]
    assert len(row.name) == 256 and len(row.description) == 2048
    assert row.notify_only and row.schedule == "0 9 * * 1"
    assert row.at == "2026-09-01T09:00:00" and row.last_status == "failed"


def test_summary_reports_saved_run_history_active_progress_and_next_run(
    saved, monkeypatch
):
    tasks, seed = saved
    seed(2)
    monkeypatch.setattr(
        tasks,
        "_get_scheduler",
        lambda: pytest.fail("summary read asked the scheduler"),
    )
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET enabled=1, schedule='daily:09:00' WHERE id='task-000'"
        )
        conn.executemany(
            "INSERT INTO task_runs(id,task_id,thread_id,started_at,status,steps_total,steps_done) VALUES(?,?,?,?,?,?,?)",
            [
                (
                    f"run-{day:02}",
                    "task-000",
                    "thread",
                    f"2026-09-{day:02}T09:00:00",
                    "failed" if day % 3 == 0 else "completed",
                    4,
                    4,
                )
                for day in range(1, 13)
            ]
            + [("run-live", "task-000", "thread", "2026-09-13T09:00:00", "running", 4, 2)],
        )
        conn.commit()
    finally:
        conn.close()
    first, second = task_views.list_saved_tasks().items
    assert [run.started_at[8:10] for run in first.recent_runs] == [
        "13", "12", "11", "10", "09", "08", "07", "06", "05", "04"
    ]
    assert first.recent_runs[0].status == "running"
    assert first.recent_runs[1].status == "failed"
    assert first.active_run == task_views.TaskActiveRun(
        "run-live", "running", "2026-09-13T09:00:00", 2, 4
    )
    assert first.next_run is not None and first.next_run.endswith("T09:00:00")
    assert second.recent_runs == () and second.active_run is None
    assert second.next_run is None


def test_page_revision_ignores_the_clock_derived_next_run(saved, monkeypatch):
    tasks, seed = saved
    seed(3)
    monkeypatch.setattr(tasks, "estimate_next_run", lambda row: "2026-01-01T00:00:00")
    page = task_views.list_saved_tasks(limit=1)
    monkeypatch.setattr(tasks, "estimate_next_run", lambda row: "2027-01-01T00:00:00")
    later = task_views.list_saved_tasks(limit=1, cursor=page.next_cursor)
    assert later.revision == page.revision
    assert later.items[0].next_run == "2027-01-01T00:00:00"


NOW = datetime(2026, 9, 26, 18, 30)  # a Saturday


@pytest.mark.parametrize(
    "task,expected",
    [
        ({"enabled": 0, "schedule": "daily:09:00"}, None),
        ({"enabled": 1}, None),
        ({"enabled": 1, "schedule": "daily:09:00"}, "2026-09-27T09:00:00"),
        ({"enabled": 1, "schedule": "daily:19:15"}, "2026-09-26T19:15:00"),
        ({"enabled": 1, "schedule": "weekly:monday:08:00"}, "2026-09-28T08:00:00"),
        ({"enabled": 1, "schedule": "cron:0 7 * * sat"}, "2026-10-03T07:00:00"),
        ({"enabled": 1, "schedule": "cron:not a cron"}, None),
        ({"enabled": 1, "schedule": "interval:2"}, None),
        (
            {"enabled": 1, "schedule": "interval:2", "last_run": "2026-09-26T17:45:00"},
            "2026-09-26T19:45:00",
        ),
        (
            {"enabled": 1, "schedule": "interval_minutes:30", "last_run": "2026-09-26T16:40:00"},
            "2026-09-26T18:40:00",
        ),
        ({"enabled": 1, "at": "2026-10-01T10:00:00"}, "2026-10-01T10:00:00"),
        ({"enabled": 1, "at": "2026-09-20T10:00:00"}, "2026-09-26T18:30:00"),
        (
            {"enabled": 1, "at": "2026-09-20T10:00:00", "last_run": "2026-09-20T10:00:05"},
            None,
        ),
        ({"enabled": 1, "at": "tomorrow"}, None),
    ],
)
def test_next_run_is_estimated_from_the_saved_schedule(saved, task, expected):
    tasks, _ = saved
    assert tasks.estimate_next_run(task, NOW) == expected


def test_summary_counts_advanced_steps_without_returning_step_contents(saved):
    tasks, seed = saved
    seed()
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET steps=? WHERE id='task-000'",
            ('[{"name":"PRIVATE_STEP"},{"name":"PRIVATE_SECOND_STEP"}]',),
        )
        conn.commit()
    finally:
        conn.close()
    row = task_views.list_saved_tasks().items[0]
    assert row.step_count == 2
    assert "PRIVATE_STEP" not in json.dumps(asdict(row))


def test_summary_uses_zero_steps_for_malformed_saved_json(saved):
    tasks, seed = saved
    seed()
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET steps='broken', prompts='broken' WHERE id='task-000'"
        )
        conn.commit()
    finally:
        conn.close()
    assert task_views.list_saved_tasks().items[0].step_count == 0


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"limit": True}, "invalid_task_query"),
        ({"limit": 101}, "invalid_task_query"),
        ({"query": "q" * 257}, "invalid_task_query"),
        ({"enabled": 1}, "invalid_task_query"),
        ({"cursor": "garbage"}, "cursor_expired"),
        ({"cursor": "x" * 1025}, "cursor_expired"),
    ],
)
def test_invalid_queries_fail_before_accessing_task_owner(
    saved, monkeypatch, kwargs, code
):
    tasks, _ = saved
    monkeypatch.setattr(
        tasks,
        "iter_task_summary_snapshot",
        lambda: pytest.fail("invalid query reached storage"),
    )
    with pytest.raises(task_views.TaskViewError, match=code):
        task_views.list_saved_tasks(**kwargs)
