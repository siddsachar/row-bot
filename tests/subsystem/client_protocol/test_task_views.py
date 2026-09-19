"""Authenticated saved-task views over the real isolated SQLite owner."""

from __future__ import annotations

import base64
import json

import pytest

from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app
from tests.subsystem.workflows.test_saved_task_views import saved  # noqa: F401

pytestmark = pytest.mark.subsystem
ENDPOINT = "/api/v1/tasks"


@pytest.fixture
def api_saved(saved, monkeypatch):  # noqa: F811
    from row_bot.providers import runtime

    monkeypatch.setattr(
        runtime, "provider_status", lambda *a, **k: {"configured": False}
    )
    return saved


def _read(client, headers, **params):
    response = client.get(ENDPOINT, headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    return response.json()


def test_saved_tasks_require_current_session_origin_and_authentication(
    api_saved, monkeypatch
):
    tasks, seed = api_saved
    seed()
    client, service, active = client_app(remote=True)
    with client:
        assert client.get(ENDPOINT).status_code == 401
        _, headers = bootstrap(client)
        assert (
            client.get(
                ENDPOINT, headers={"X-Client-Session": headers["X-Client-Session"]}
            ).status_code
            == 403
        )
        assert (
            client.get(
                ENDPOINT, headers={**headers, "Origin": "http://foreign.invalid"}
            ).status_code
            == 403
        )
        assert _read(client, headers)["items"][0]["id"] == "task-000"
        active["value"] = False
        monkeypatch.setattr(
            tasks,
            "iter_task_summary_snapshot",
            lambda: pytest.fail("Revoked query reached storage"),
        )
        response = client.get(ENDPOINT, headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"
        assert "items" not in response.json()
        assert service.commands == []


def test_revocation_during_snapshot_never_delivers_task_metadata(
    api_saved, monkeypatch
):
    tasks, seed = api_saved
    seed()
    client, _, active = client_app(remote=True)
    original = tasks.iter_task_summary_snapshot
    closed = []

    def revoke_during_read():
        rows = original()
        try:
            yield from rows
            active["value"] = False
        finally:
            rows.close()
            closed.append(True)

    monkeypatch.setattr(tasks, "iter_task_summary_snapshot", revoke_during_read)
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINT, headers=headers)
        assert response.status_code == 401
        assert "Saved task" not in response.text
        assert "items" not in response.json()
        assert closed == [True]


def test_all_saved_pages_and_filters_preserve_ids_and_private_field_boundary(api_saved):
    _, seed = api_saved
    seed(205)
    client, service, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        page = _read(client, headers, limit=100)
        assert page["schema_version"] == 1 and page["total"] == 205
        revision = page["revision"]
        items = page["items"]
        while page["next_cursor"]:
            page = _read(client, headers, limit=100, cursor=page["next_cursor"])
            assert page["revision"] == revision
            items.extend(page["items"])
        assert len(page["items"]) == 5
        assert [item["id"] for item in items] == [f"task-{i:03}" for i in range(205)]
        assert all(item["conversation_id"] == "conversation-a" for item in items)
        assert "PRIVATE_" not in json.dumps(items)
        assert set(items[0]) == {
            "id",
            "name",
            "description",
            "icon",
            "enabled",
            "notify_only",
            "schedule",
            "at",
            "last_run",
            "last_status",
            "conversation_id",
        }
        enabled = _read(client, headers, query="TASK 20", enabled="true", limit=1)
        assert enabled["total"] == 2 and enabled["items"][0]["id"] == "task-201"
        following = _read(
            client,
            headers,
            query=" task 20 ",
            enabled="true",
            limit=1,
            cursor=enabled["next_cursor"],
        )
        assert (
            following["items"][0]["id"] == "task-203"
            and following["next_cursor"] is None
        )
        disabled = _read(client, headers, query="task 20", enabled="false")
        assert [item["id"] for item in disabled["items"]] == [
            "task-200",
            "task-202",
            "task-204",
        ]
        assert _read(client, headers, query="No matching synthetic task")["total"] == 0
        assert service.commands == []


@pytest.mark.parametrize(
    "change", ["enabled-filter", "query-filter", "limit", "task", "history", "delete"]
)
def test_cursors_reject_changed_filters_or_saved_snapshot(api_saved, change):
    tasks, seed = api_saved
    seed(3)
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        cursor = _read(client, headers, limit=1)["next_cursor"]
        params = {"limit": 1, "cursor": cursor}
        conn = tasks._get_conn()
        try:
            if change == "enabled-filter":
                params["enabled"] = "true"
            elif change == "query-filter":
                params["query"] = "task"
            elif change == "limit":
                params["limit"] = 2
            elif change == "task":
                conn.execute(
                    "UPDATE tasks SET name='Changed after first page' WHERE id='task-002'"
                )
            elif change == "delete":
                conn.execute("DELETE FROM tasks WHERE id='task-002'")
            else:
                conn.execute(
                    "INSERT INTO task_runs(id,task_id,thread_id,started_at,status) VALUES('history','task-002','synthetic-thread','2026-01-01','approval_pending')"
                )
            conn.commit()
        finally:
            conn.close()
        response = client.get(ENDPOINT, headers=headers, params=params)
        assert response.status_code == 410
        assert response.json()["code"] == "cursor_expired"
        assert "items" not in response.json()
        assert _read(client, headers)["schema_version"] == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"query": "x" * 257}])
def test_invalid_task_queries_fail_before_storage(api_saved, monkeypatch, params):
    tasks, _ = api_saved
    monkeypatch.setattr(
        tasks,
        "iter_task_summary_snapshot",
        lambda: pytest.fail("Invalid query reached storage"),
    )
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINT, headers=headers, params=params)
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_task_query"


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "PRIVATE_INVALID_CURSOR",
        "x" * 1025,
        base64.urlsafe_b64encode(b"[]").decode(),
    ],
)
def test_bad_cursor_is_typed_and_does_not_expose_input(api_saved, monkeypatch, cursor):
    tasks, _ = api_saved
    monkeypatch.setattr(
        tasks,
        "iter_task_summary_snapshot",
        lambda: pytest.fail("Invalid cursor reached storage"),
    )
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINT, headers=headers, params={"cursor": cursor})
        assert response.status_code == 410
        assert response.json()["code"] == "cursor_expired"
        assert "PRIVATE_INVALID_CURSOR" not in response.text


def test_snapshot_stays_consistent_through_mid_read_write_and_cursor_expires(
    api_saved, monkeypatch
):
    tasks, seed = api_saved
    seed(205)
    original = tasks.iter_task_summary_snapshot
    changed, closed = [], []

    def concurrent_snapshot():
        rows = original()
        try:
            first = next(rows)
            if not changed:
                conn = tasks._get_conn()
                try:
                    conn.execute(
                        "UPDATE tasks SET name='New saved name' WHERE id='task-204'"
                    )
                    conn.commit()
                finally:
                    conn.close()
                changed.append(True)
            yield first
            yield from rows
        finally:
            rows.close()
            closed.append(True)

    monkeypatch.setattr(tasks, "iter_task_summary_snapshot", concurrent_snapshot)
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        captured = _read(client, headers, query="task", limit=100)
        assert captured["total"] == 205
        response = client.get(
            ENDPOINT,
            headers=headers,
            params={"query": "task", "limit": 100, "cursor": captured["next_cursor"]},
        )
        assert response.status_code == 410
        assert (
            _read(client, headers, query="New saved name")["items"][0]["id"]
            == "task-204"
        )
        assert closed == [True, True, True]


def test_task_reads_preserve_stored_history_and_do_not_execute_or_modify(
    api_saved, monkeypatch
):
    tasks, seed = api_saved
    seed(3)
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET schedule='daily:09:00',at='2026-09-01T09:00:00',notify_only=1,last_run='stored-last-run'"
        )
        conn.executemany(
            "INSERT INTO task_runs(id,task_id,thread_id,started_at,status) VALUES(?,?,?,?,?)",
            [
                ("a", "task-000", "synthetic-thread", "2026-01-01", "completed"),
                ("b", "task-000", "synthetic-thread", "2026-01-01", "approval_pending"),
                ("c", "task-001", "synthetic-thread", "2025-01-01", "running"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    for name in (
        "run_task_background",
        "get_task_channels",
        "get_next_fire_times",
        "list_tasks",
    ):
        monkeypatch.setattr(
            tasks,
            name,
            lambda *a, **k: pytest.fail("Saved GET invoked a runtime/unbounded owner"),
        )
    traced = []
    original_conn = tasks._get_conn

    def read_connection():
        connection = original_conn()
        connection.set_trace_callback(traced.append)
        return connection

    client, service, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        monkeypatch.setattr(tasks, "_get_conn", read_connection)
        page = _read(client, headers)
        assert [item["last_status"] for item in page["items"]] == [
            "approval_pending",
            "running",
            None,
        ]
        assert all(item["last_run"] == "stored-last-run" for item in page["items"])
        assert all(
            item["schedule"] == "daily:09:00" and item["at"] == "2026-09-01T09:00:00"
            for item in page["items"]
        )
        assert all(item["notify_only"] for item in page["items"])
        assert all(
            sql.lstrip().split()[0].upper() in {"BEGIN", "SELECT"} for sql in traced
        )
        assert service.commands == []


@pytest.mark.parametrize("identity", ["invalid path/private", "x" * 129])
def test_invalid_stored_identity_fails_closed_without_leaking(api_saved, identity):
    tasks, seed = api_saved
    seed()
    conn = tasks._get_conn()
    try:
        conn.execute("UPDATE tasks SET id=?", (identity,))
        conn.commit()
    finally:
        conn.close()
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINT, headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "task_metadata_unavailable"
        assert "invalid path/private" not in response.text
        assert "items" not in response.json()


def test_response_and_sqlite_batches_remain_bounded_with_large_saved_metadata(
    api_saved, monkeypatch
):
    tasks, seed = api_saved
    seed(300)
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE tasks SET description=?,name=?,icon=?",
            ("D" * 20000, "N" * 400, "I" * 100),
        )
        conn.commit()
    finally:
        conn.close()
    original = tasks._get_conn
    batches, closed = [], []
    allowed = {
        "id",
        "name",
        "description",
        "icon",
        "enabled",
        "notify_only",
        "schedule",
        "at",
        "last_run",
        "last_status",
        "persistent_thread_id",
    }

    class ObservedCursor:
        def __init__(self, cursor):
            self.cursor = cursor
            if cursor.description is not None:
                assert {column[0] for column in cursor.description} <= allowed

        def fetchmany(self, size):
            assert 1 <= size <= 128
            rows = self.cursor.fetchmany(size)
            batches.append(len(rows))
            assert all(len(row["description"]) <= 2048 for row in rows)
            return rows

    class ObservedConnection:
        def __init__(self):
            self.connection = original()

        def execute(self, *args):
            return ObservedCursor(self.connection.execute(*args))

        def close(self):
            closed.append(True)
            self.connection.close()

    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        monkeypatch.setattr(tasks, "_get_conn", ObservedConnection)
        page = _read(client, headers, limit=100)
        assert page["total"] == 300 and len(page["items"]) == 100
        assert all(
            len(item["name"]) == 256
            and len(item["description"]) == 2048
            and len(item["icon"]) == 32
            for item in page["items"]
        )
        assert max(batches) <= 128 and sum(batches) == 300
        assert closed == [True]


def test_latest_status_lookup_does_not_rescan_full_history_per_task(
    api_saved, monkeypatch
):
    tasks, seed = api_saved
    seed(500)
    conn = tasks._get_conn()
    try:
        conn.executemany(
            "INSERT INTO task_runs(id,task_id,thread_id,started_at,status) VALUES(?,?,?,?,?)",
            [
                (
                    f"run-{i:05}",
                    f"task-{i % 500:03}",
                    "synthetic-thread",
                    f"2026-{i:05}",
                    "completed",
                )
                for i in range(5000)
            ],
        )
        conn.commit()
    finally:
        conn.close()
    original = tasks._get_conn
    steps = [0]
    statements = []

    def counted_connection():
        connection = original()

        def progress():
            steps[0] += 1000
            # This counts SQLite work, not wall time. A per-task scan of all
            # history exceeded 10 million instructions on this same fixture.
            return int(steps[0] > 1_000_000)

        connection.set_progress_handler(progress, 1000)
        connection.set_trace_callback(statements.append)
        return connection

    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        monkeypatch.setattr(tasks, "_get_conn", counted_connection)
        page = _read(client, headers, limit=1)
        assert page["total"] == 500 and len(page["items"]) == 1
        assert page["items"][0]["last_status"] == "completed"
        assert steps[0] <= 1_000_000
    conn = original()
    try:
        query = next(sql for sql in statements if sql.lstrip().startswith("SELECT"))
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + query)]
        assert any("USING INDEX idx_task_runs_latest_by_task" in step for step in plan)
        assert not any(step == "SCAN r" for step in plan)
    finally:
        conn.close()


def test_schema_adds_latest_run_index_to_existing_database_without_losing_rows(
    api_saved,
):
    tasks, seed = api_saved
    seed(3)
    conn = tasks._get_conn()
    try:
        conn.execute("DROP INDEX idx_task_runs_latest_by_task")
        conn.execute("UPDATE tasks SET safety_mode='allow_all'")
        conn.execute(
            "INSERT INTO task_runs(id,task_id,thread_id,started_at,status) VALUES('retained','task-001','synthetic-thread','2026-01-01','approval_pending')"
        )
        conn.commit()
        before_tasks = [
            dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")
        ]
        before_runs = [
            dict(row) for row in conn.execute("SELECT * FROM task_runs ORDER BY id")
        ]
    finally:
        conn.close()
    tasks.ensure_task_schema(force=True)
    tasks.ensure_task_schema(force=True)
    conn = tasks._get_conn()
    try:
        assert [
            dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")
        ] == before_tasks
        assert [
            dict(row) for row in conn.execute("SELECT * FROM task_runs ORDER BY id")
        ] == before_runs
        columns = [
            (row[2], row[3])
            for row in conn.execute("PRAGMA index_xinfo(idx_task_runs_latest_by_task)")
            if row[5]
        ]
        assert columns == [("task_id", 0), ("started_at", 1), ("id", 1)]
    finally:
        conn.close()
