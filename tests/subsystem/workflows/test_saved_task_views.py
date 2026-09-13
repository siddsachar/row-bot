from __future__ import annotations

from dataclasses import asdict
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
