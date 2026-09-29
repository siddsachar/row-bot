"""An Agent's approval that times out settles and stops the Agent (B166).

The monitor held its timeout write open while resuming the Agent, which
writes the same database, so the resume waited until "database is locked":
the approval never timed out and the monitor failed every minute.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401


pytestmark = pytest.mark.subsystem


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


def test_a_timed_out_agent_approval_settles_without_locking_the_database(service):  # noqa: F811
    from row_bot import agent_runs, tasks, threads

    parent = threads.create_thread("Parent", seed_default_skills=False)
    run = agent_runs.create_agent_run(parent_thread_id=parent, display_name="Research",
                                      status="waiting_approval", prompt="Synthetic task")
    waiting = agent_runs.create_agent_run(parent_thread_id=parent, display_name="Other",
                                          status="waiting_approval", prompt="Another task")
    _token, approval_id = tasks.create_approval_request(
        run_id=run["id"], task_id="", step_id="agent_interrupt", message="Run a synthetic command?",
        agent_run_id=run["id"], resume_kind="agent_run", parent_thread_id=parent)
    _other_token, still_open = tasks.create_approval_request(
        run_id=waiting["id"], task_id="", step_id="agent_interrupt", message="Another question?",
        agent_run_id=waiting["id"], resume_kind="agent_run", parent_thread_id=parent)
    _expire(tasks, approval_id)

    tasks._check_approval_timeouts()

    assert tasks.get_approval_request_statuses([approval_id, still_open]) == {
        approval_id: "timed_out", still_open: "pending"}
    assert agent_runs.get_agent_run(run["id"])["status"] == "stopped"
    assert agent_runs.get_agent_run(waiting["id"])["status"] == "waiting_approval"
    # Nothing is left holding the database: a second pass and a write go through.
    tasks._check_approval_timeouts()
    agent_runs.finish_agent_run(waiting["id"], "stopped", status_message="Synthetic stop")
