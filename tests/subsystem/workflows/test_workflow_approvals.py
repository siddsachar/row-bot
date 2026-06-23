from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.fixtures.tasks import fresh_tasks_module


pytestmark = pytest.mark.subsystem


def test_pipeline_state_round_trips_graph_interrupt_flag(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)

    tasks._save_pipeline_state(
        run_id="run-1",
        task_id="task-1",
        thread_id="thread-1",
        current_step_index=2,
        step_outputs={"prompt_1": "draft"},
        config={"configurable": {"thread_id": "thread-1"}},
        resume_token="resume-1",
        status="paused",
        graph_interrupted=True,
    )

    state = tasks._load_pipeline_state("resume-1")

    assert state is not None
    assert state["status"] == "paused"
    assert state["step_outputs"] == {"prompt_1": "draft"}
    assert state["config"]["configurable"]["thread_id"] == "thread-1"
    assert state["graph_interrupted"] == "true"

    tasks._clear_graph_interrupted("run-1")
    assert tasks._load_pipeline_state("resume-1")["graph_interrupted"] is None


def test_approval_resume_tokens_are_single_use(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    resumed: list[tuple[str, bool]] = []
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda token, approved=True: resumed.append((token, approved)))

    token, request_id = tasks.create_approval_request("run-2", "task-2", "approval_1", "Approve?")

    assert tasks.get_pending_approvals()[0]["id"] == request_id
    assert tasks.respond_to_approval(token, True, note="ok", source="web") is True
    assert tasks.respond_to_approval(token, True, note="again", source="web") is False
    assert resumed == [(token, True)]
    assert tasks.get_pending_approvals() == []


def test_expired_approval_does_not_resume_pipeline(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    resumed: list[str] = []
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda token, approved=True: resumed.append(token))

    token, _request_id = tasks.create_approval_request("run-3", "task-3", "approval_1", "Approve?", timeout_minutes=1)
    conn = tasks._get_conn()
    conn.execute(
        "UPDATE approval_requests SET timeout_at = ? WHERE resume_token = ?",
        ((datetime.now() - timedelta(minutes=1)).isoformat(), token),
    )
    conn.commit()
    conn.close()

    assert tasks.respond_to_approval(token, True) is False
    assert resumed == []


def test_agent_run_approval_resumes_agent_runner_not_workflow(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    workflow_resumes: list[str] = []
    agent_resumes: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda token, approved=True: workflow_resumes.append(token))

    import row_bot.agent_runner as agent_runner

    monkeypatch.setattr(
        agent_runner,
        "resume_agent_run",
        lambda run_id, *, resume_token, approved: agent_resumes.append((run_id, resume_token, approved)),
    )

    token, _request_id = tasks.create_approval_request(
        "workflow-run",
        "task-4",
        "approval_1",
        "Approve agent?",
        agent_run_id="agent-run-1",
        resume_kind="agent_run",
    )

    assert tasks.respond_to_approval(token, False) is True
    assert workflow_resumes == []
    assert agent_resumes == [("agent-run-1", token, False)]
