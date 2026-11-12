from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.fixtures.tasks import fresh_tasks_module


pytestmark = pytest.mark.subsystem


def _helper_approval(tasks, monkeypatch, resumed: list, **options) -> tuple[str, str]:
    """A pending approval of a delegated helper, whose answer resumes that helper."""
    import row_bot.agent_runner as agent_runner

    monkeypatch.setattr(
        agent_runner,
        "resume_agent_run",
        lambda run_id, *, resume_token, approved: resumed.append((run_id, resume_token, approved)),
    )
    return tasks.create_approval_request(
        "helper-run", "", "approval_1", "Approve?", agent_run_id="agent-run-1", resume_kind="agent_run", **options,
    )


def test_approval_resume_tokens_are_single_use(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    resumed: list = []
    token, request_id = _helper_approval(tasks, monkeypatch, resumed)

    assert tasks.get_pending_approvals()[0]["id"] == request_id
    assert tasks.respond_to_approval(token, True, note="ok", source="web") is True
    assert tasks.respond_to_approval(token, True, note="again", source="web") is False
    assert resumed == [("agent-run-1", token, True)]
    assert tasks.get_pending_approvals() == []


def test_approval_buddy_events_include_resume_token(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    from row_bot.buddy import events
    from row_bot.buddy.events import BuddyEventBus, BuddyEventType

    bus = BuddyEventBus()
    monkeypatch.setattr(events, "_bus", bus)
    token, request_id = _helper_approval(tasks, monkeypatch, [])
    needed = bus.latest()

    assert needed is not None
    assert needed.type == BuddyEventType.APPROVAL_NEEDED
    assert needed.payload["approval_id"] == request_id
    assert needed.payload["resume_token"] == token

    assert tasks.respond_to_approval(token, True, note="ok", source="web") is True
    approved = bus.latest()

    assert approved is not None
    assert approved.type == BuddyEventType.APPROVAL_APPROVED
    assert approved.payload["approval_id"] == request_id
    assert approved.payload["resume_token"] == token


def test_an_expired_approval_resumes_nothing(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    resumed: list = []
    token, _request_id = _helper_approval(tasks, monkeypatch, resumed, timeout_minutes=1)
    conn = tasks._get_conn()
    conn.execute(
        "UPDATE approval_requests SET timeout_at = ? WHERE resume_token = ?",
        ((datetime.now() - timedelta(minutes=1)).isoformat(), token),
    )
    conn.commit()
    conn.close()

    assert tasks.respond_to_approval(token, True) is False
    assert resumed == []


def test_an_old_step_pipelines_approval_can_no_longer_resume_anything(tmp_path, reload_for_data_dir) -> None:
    """F31: the step runner is gone; an approval it left can't be answered into a run."""
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    token, _request_id = tasks.create_approval_request("old-run", "task-old", "approval_1", "Approve?")

    assert tasks.respond_to_approval(token, True) is False


def test_agent_run_approval_resumes_the_helper(tmp_path, monkeypatch, reload_for_data_dir) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    resumed: list = []
    token, _request_id = _helper_approval(tasks, monkeypatch, resumed)

    assert tasks.respond_to_approval(token, False) is True
    assert resumed == [("agent-run-1", token, False)]


def test_a_new_approval_wakes_devices_that_turned_notifications_on(
    tmp_path, monkeypatch, reload_for_data_dir
) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    from row_bot.access import push

    woken: list[int] = []
    monkeypatch.setattr(push, "wake_soon", lambda: woken.append(1))

    _helper_approval(tasks, monkeypatch, [])

    assert woken == [1]
