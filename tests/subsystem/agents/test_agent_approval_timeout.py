"""An Agent's approval that times out settles and stops the Agent (B166).

The monitor held its timeout write open while resuming the Agent, which
writes the same database, so the resume waited until "database is locked":
the approval never timed out and the monitor failed every minute.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta

import pytest


pytestmark = pytest.mark.subsystem


def _fresh_modules(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    for name in ("row_bot.tasks", "row_bot.agent_runs", "row_bot.agent_orchestrator"):
        sys.modules.pop(name, None)
    import row_bot.agent_orchestrator as orchestrator
    import row_bot.agent_runs as agent_runs
    import row_bot.tasks as tasks

    importlib.reload(orchestrator)
    return importlib.reload(tasks), importlib.reload(agent_runs)


def _expire(tasks, approval_id: str) -> None:
    conn = tasks._get_conn()
    try:
        conn.execute(
            "UPDATE approval_requests SET timeout_at = ? WHERE id = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), approval_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_a_timed_out_agent_approval_settles_without_locking_the_database(tmp_path, monkeypatch):
    tasks, agent_runs = _fresh_modules(tmp_path, monkeypatch)
    run = agent_runs.create_agent_run(parent_thread_id="parent", display_name="Research",
                                      status="waiting_approval", prompt="Synthetic task")
    waiting = agent_runs.create_agent_run(parent_thread_id="parent", display_name="Other",
                                          status="waiting_approval", prompt="Another task")
    _token, approval_id = tasks.create_approval_request(
        run_id=run["id"], task_id="", step_id="agent_interrupt", message="Run a synthetic command?",
        agent_run_id=run["id"], resume_kind="agent_run", parent_thread_id="parent")
    _other_token, still_open = tasks.create_approval_request(
        run_id=waiting["id"], task_id="", step_id="agent_interrupt", message="Another question?",
        agent_run_id=waiting["id"], resume_kind="agent_run", parent_thread_id="parent")
    _expire(tasks, approval_id)

    tasks._check_approval_timeouts()

    assert tasks.get_approval_request_statuses([approval_id, still_open]) == {
        approval_id: "timed_out", still_open: "pending"}
    assert agent_runs.get_agent_run(run["id"])["status"] == "stopped"
    assert agent_runs.get_agent_run(waiting["id"])["status"] == "waiting_approval"
    # Nothing is left holding the database: a second pass and a write go through.
    tasks._check_approval_timeouts()
    agent_runs.finish_agent_run(waiting["id"], "stopped", status_message="Synthetic stop")
