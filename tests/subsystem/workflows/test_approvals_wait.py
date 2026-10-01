"""Approvals wait until they are answered; runs never pile up behind one (B255).

A pending approval used to time out after 30 minutes and count as a denial,
so work in the background was denied while nobody looked. Now a new approval
waits until someone answers it; a timeout a workflow step sets on purpose still
applies, and approvals saved earlier keep the expiry they were given. While a
workflow run waits, that workflow's next scheduled run is skipped and says so.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.subsystem.workflows.test_workflow_profile_runtime import (
    _install_fake_agent,
    _run_workflow_synchronously,
)

pytestmark = pytest.mark.subsystem

_YESTERDAY_NINE = datetime(2026, 9, 29, 9, 0)
_TODAY_NINE = datetime(2026, 9, 30, 9, 0)


@pytest.fixture
def workflows(tmp_path, monkeypatch, reload_for_data_dir):
    """Fresh workflow stores, a fake clock, a synchronous runner and recorded notices."""
    tasks, threads, _profiles, _runs = reload_for_data_dir(
        tmp_path / "data", "row_bot.tasks", "row_bot.threads",
        "row_bot.agent_profiles", "row_bot.agent_runs",
    )
    clock = {"now": _YESTERDAY_NINE}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(tasks, "datetime", Clock)
    _install_fake_agent(monkeypatch, [])
    _run_workflow_synchronously(monkeypatch, tasks)
    from row_bot import notifications
    from row_bot.tools import registry

    notices: list[dict] = []
    monkeypatch.setattr(notifications, "notify", lambda **kwargs: notices.append(kwargs))
    monkeypatch.setattr(registry, "get_enabled_tools", lambda: [])
    monkeypatch.setattr(tasks, "_deliver_to_channels", lambda *_, **__: ("", ""))
    monkeypatch.setattr(tasks, "_fire_completion_triggers", lambda *_, **__: None)
    return tasks, threads, clock, notices


def _approval_step(**extra) -> list[dict]:
    return [{"id": "review", "type": "approval", "message": "Send today's news?", **extra}]


def _statuses(tasks) -> dict[str, str]:
    conn = tasks._get_conn()
    try:
        return {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM approval_requests")}
    finally:
        conn.close()


def test_a_new_approval_waits_until_it_is_answered(workflows, monkeypatch):
    tasks, _threads, clock, _notices = workflows
    resumed: list[bool] = []
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda token, approved=True: resumed.append(approved))

    token, approval_id = tasks.create_approval_request("run-1", "task-1", "approval_1", "Approve?")
    clock["now"] = _YESTERDAY_NINE + timedelta(days=365)
    tasks._check_approval_timeouts()

    assert _statuses(tasks) == {approval_id: "pending"}
    assert tasks.respond_to_approval(token, True) is True
    assert resumed == [True]


def test_a_workflow_approval_step_waits_unless_it_sets_a_timeout(workflows):
    tasks, threads, clock, _notices = workflows
    waits = tasks.create_task("Waits", steps=_approval_step(), channels=[], enabled=False)
    times_out = tasks.create_task("Times out", steps=_approval_step(timeout_minutes=5), channels=[], enabled=False)
    for task_id in (waits, times_out):
        thread_id = threads.create_thread(task_id, seed_default_skills=False)
        tasks.run_task_background(task_id, thread_id, [], notification=False)
    by_task = {row["task_id"]: row["id"] for row in tasks.get_pending_approvals()}
    assert set(by_task) == {waits, times_out}

    clock["now"] = _YESTERDAY_NINE + timedelta(days=1)
    tasks._check_approval_timeouts()

    assert _statuses(tasks) == {by_task[waits]: "pending", by_task[times_out]: "timed_out"}
    assert tasks.get_run_history(times_out)[0]["status"] == "stopped"


def test_an_approval_saved_with_an_expiry_keeps_it(workflows, monkeypatch):
    """Approvals created before this change carry the old 30-minute expiry."""
    tasks, _threads, clock, _notices = workflows
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda token, approved=True: None)
    _token, approval_id = tasks.create_approval_request(
        "run-2", "task-2", "approval_1", "Approve?", timeout_minutes=30)

    clock["now"] = _YESTERDAY_NINE + timedelta(minutes=29)
    tasks._check_approval_timeouts()
    assert _statuses(tasks) == {approval_id: "pending"}
    clock["now"] = _YESTERDAY_NINE + timedelta(minutes=31)
    tasks._check_approval_timeouts()
    assert _statuses(tasks) == {approval_id: "timed_out"}


def test_a_scheduled_run_is_skipped_while_the_last_one_waits_for_approval(workflows):
    tasks, _threads, clock, notices = workflows
    from row_bot.application import task_execution

    task_id = tasks.create_task("Daily News", steps=_approval_step(), schedule="daily:09:00", channels=[])
    tasks._on_task_fire(task_id)
    [waiting] = tasks.get_pending_approvals()
    # The desktop still hears about it; in the app the approvals list says it.
    assert [notice.get("in_app", True) for notice in notices] == [False]
    notices.clear()

    clock["now"] = _TODAY_NINE
    tasks._on_task_fire(task_id)

    runs = task_execution.list_task_runs(task_id).items
    assert [run.status for run in runs] == ["skipped", "paused"]
    assert runs[0].started_at == _TODAY_NINE.isoformat()
    # The skipped run points at the conversation whose approval it waited on.
    assert runs[0].conversation_id == runs[1].conversation_id
    assert [row["id"] for row in tasks.get_pending_approvals()] == [waiting["id"]]
    assert [(notice["title"], notice["message"]) for notice in notices] == [(
        "Daily News",
        "Skipped 9:00 run: yesterday's run still waits for your approval",
    )]

    # Once answered, the next scheduled run goes ahead.
    assert tasks.respond_to_approval(waiting["resume_token"], True) is True
    clock["now"] = _TODAY_NINE + timedelta(days=1)
    tasks._on_task_fire(task_id)
    assert [run.status for run in task_execution.list_task_runs(task_id).items][0] == "paused"
    assert len(tasks.get_pending_approvals()) == 1
