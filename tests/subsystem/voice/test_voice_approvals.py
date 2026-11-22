"""Approvals answered by voice (VD4) go through the card's own command, and only for what can be undone.

The real conversation owner with scripted agent streams and disposable data; no audio or model.
"""
# ruff: noqa: F811 -- shared isolated fixture.
from __future__ import annotations

from uuid import uuid4

import pytest

from row_bot.voice.approvals import approval_notice, voice_may_answer
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.voice.test_client_voice_admission import setup

pytestmark = pytest.mark.subsystem

MOVE = {"tool": "workspace_move_file", "label": "Move / rename file",
        "description": "Move / rename file: source_path='a.md', destination_path='notes/a.md'",
        "args": {"source_path": "a.md", "destination_path": "notes/a.md"}}
DELETE = {"tool": "workspace_file_delete", "label": "Delete file", "description": "Delete file: 'a.md'",
          "args": {"path": "a.md"}}


def _waiting(service, item: dict):
    """A spoken turn whose run stops at one approval card; returns the adapter, owner, card id and answers."""
    adapter, owner, _lease = setup(service)
    answers: list[bool] = []

    def stream(_text, _enabled, _config, *, stop_event=None):
        yield "interrupt", [{"__interrupt_id": "card-1", **item}]

    def resume(_enabled, _config, approved, *, interrupt_ids=None, stop_event=None):
        answers.append(approved)
        yield "done", "Done."

    service.stream_factory, service.resume_factory = stream, resume
    run = adapter.submit(owner, "Tidy my files", str(uuid4()), lambda: None)
    handle = service.registry.conversation_generation(owner.conversation_id, run)
    assert handle.producer_done.wait(5) and handle.approval_id
    return adapter, owner, handle.approval_id, answers


def _status(service, approval_id: str) -> str:
    return service.get_approval(approval_id)["status"]


def _resumed(service, owner) -> None:
    for handle in service.registry.active(owner.conversation_id):
        assert handle.producer_done.wait(5)


@pytest.mark.parametrize("approve", [True, False])
def test_a_spoken_answer_to_an_undoable_action_resolves_it_like_a_tap(service, approve):
    adapter, owner, approval, answers = _waiting(service, MOVE)
    assert adapter.answer_approval(owner, approval, approve, str(uuid4()), lambda: None) == "answered"
    _resumed(service, owner)
    assert answers == [approve]
    assert _status(service, approval) != "pending"
    # Answered once: the same card cannot be answered again, by voice or by a tap.
    assert adapter.answer_approval(owner, approval, True, str(uuid4()), lambda: None) == "gone"


def test_a_destructive_action_is_never_answered_by_voice(service):
    adapter, owner, approval, answers = _waiting(service, DELETE)
    assert adapter.answer_approval(owner, approval, True, str(uuid4()), lambda: None) == "tap"
    assert answers == [] and _status(service, approval) == "pending"


def test_two_waiting_approvals_are_left_to_the_cards(service):
    from row_bot import tasks

    adapter, owner, approval, answers = _waiting(service, MOVE)
    tasks.create_approval_request("pass-x", "", "conversation", "Another card", resume_kind="conversation",
                                  source_thread_id=owner.conversation_id, parent_thread_id=owner.conversation_id)
    assert adapter.answer_approval(owner, approval, True, str(uuid4()), lambda: None) == "tap"
    assert answers == [] and _status(service, approval) == "pending"


def test_a_withdrawn_card_cannot_be_answered(service):
    adapter, owner, approval, answers = _waiting(service, MOVE)
    service.stop_conversation(owner.conversation_id)
    assert adapter.answer_approval(owner, approval, True, str(uuid4()), lambda: None) == "gone"
    assert answers == []


@pytest.mark.parametrize("item, voice", [
    (MOVE, True),
    ({"tool": "create_calendar_event", "label": "Create an event"}, True),
    (DELETE, False),
    ({"tool": "send_gmail_message", "label": "Send email"}, False),
    ({"tool": "outlook_cancel_event", "label": "Cancel an event"}, False),
    ({"tool": "workspace_write_file", "label": "Change files outside the workspace?"}, False),
    ({"tool": "create_calendar_event", "label": "Create an event", "risk_class": "high"}, False),
    ({"tool": "run_command", "label": "Run command", "args": {"command": "ls"}}, False),
    ({"tool": "mcp_notion_update_page", "label": "Update page"}, False),
    ({"tool": "generate_image", "label": "Make a picture", "price": {"amount": 0.04}}, False),
    ({"tool": "save_memory", "label": "Save", "person_only": True, "binding": "b"}, False),
    ({"tool": "x_post", "label": "Post", "x_post": {"text": "hi"}}, False),
    ({"tool": "row_bot_update_setting", "label": "Change a setting", "change": {"key": "k"}}, False),
])
def test_only_one_action_that_can_be_undone_may_be_answered_by_voice(item, voice):
    assert voice_may_answer([item]) is voice
    assert voice_may_answer([MOVE, MOVE]) is False


def test_the_spoken_summary_uses_the_cards_own_words():
    assert approval_notice([MOVE], "a-1") == {
        "approval_id": "a-1", "action": "Move / rename file?", "voice": True,
        "summary": "I need your OK. Move / rename file? Say yes, or tap Approve."}
    sent = approval_notice([{"tool": "send_file", "label": "Send a file", "question": "Send report.pdf to Telegram?"}],
                           "a-2")
    assert sent["summary"] == "I need your OK. Send report.pdf to Telegram? Tap Approve on the screen."
    assert approval_notice([MOVE, DELETE], "a-3")["summary"] == "I need your OK for 2 actions. Tap Approve on the screen."
    assert approval_notice([MOVE], "") is None
