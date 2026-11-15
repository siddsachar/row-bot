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


def test_a_new_approval_or_question_tells_devices_what_waits_and_where(
    tmp_path, monkeypatch, reload_for_data_dir
) -> None:
    tasks = fresh_tasks_module(tmp_path, reload_for_data_dir)
    from row_bot.access import push
    from row_bot.questions import card, normalize

    told: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        push, "notify_soon", lambda title, body="", *, conversation_id="": told.append((title, body, conversation_id))
    )

    # A helper's approval: its first line, opening the chat that started the helper.
    _helper_approval(tasks, monkeypatch, [], parent_thread_id="chat-1", source_thread_id="helper-thread")
    # Row-Bot's question in a chat: the question itself, opening that chat.
    asked = card(normalize([{"header": "Venue", "question": "Which venue?",
                             "options": [{"label": "The Hall"}, {"label": "The Park"}]}]))
    tasks.create_approval_request("pass-1", "", "conversation", "Which venue?", resume_kind="conversation",
                                  source_thread_id="chat-2", approval_payload_json={"interrupt": asked})
    # A workflow step's approval: no chat of its own, so tapping it opens Home.
    tasks.create_approval_request("run-1", "task-1", "step-1", "Send email to Sam?\nAction: send_gmail_message")

    assert told == [
        ("Approval needed", "Approve?", "chat-1"),
        ("Row-Bot asked you a question", "Which venue?", "chat-2"),
        ("Approval needed", "Send email to Sam?", ""),
    ]
