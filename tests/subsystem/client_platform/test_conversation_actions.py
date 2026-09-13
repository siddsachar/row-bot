from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

import pytest

from row_bot.application.conversation_actions import (
    ConversationActionError,
    execute_conversation_action,
    read_conversation_action_receipt,
    read_conversation_actions,
    review_conversation_action,
)
from row_bot.runtime import admissions


pytestmark = pytest.mark.subsystem


@pytest.fixture
def isolated_service(tmp_path, monkeypatch):
    from row_bot import agent_profiles, tasks, threads
    from row_bot.application.client_platform import ClientPlatformService
    from row_bot.projection.conversation import ConversationProjection
    from row_bot.runtime.executions import GenerationRuntimeRegistry

    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    monkeypatch.setattr(agent_profiles, "_SCHEMA_READY", False)
    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(threads, "_MEDIA_DIR", media)
    threads._ensure_thread_db()
    connection = sqlite3.connect(threads.DB_PATH, check_same_thread=False)
    monkeypatch.setattr(threads, "checkpointer", threads._DeletionAwareSqliteSaver(connection))
    result = ClientPlatformService()
    result.registry = GenerationRuntimeRegistry()
    result.projection = ConversationProjection(result.registry.server_epoch)
    yield result
    result.registry.shutdown()
    connection.close()


def _review(service, conversation: str, action: str, revision: str, fields: dict):
    return review_conversation_action(
        service, conversation, action, revision, fields, validate=lambda: None
    )


def _command(action: str, review: dict, fields: dict, command_id: str | None = None) -> dict:
    identity = command_id or str(uuid4())
    payload = {
        **fields,
        "checkpoint_revision": review["checkpoint_revision"],
        "action_digest": review["action_digest"],
        **({"export_title": review["fields"]["title"]} if action == "conversation.export" else {}),
    }
    return {
        "command_id": identity,
        "client_session_id": str(uuid4()),
        "type": action,
        "expected_revision": review["revision"],
        "payload": payload,
    }


def _execute(service, conversation: str, command: dict, *, owner: str = "local-owner"):
    reviewed = []
    result = execute_conversation_action(
        service,
        conversation,
        command,
        owner_id=owner,
        key=command["command_id"],
        validate=lambda: None,
        validate_review=lambda review: reviewed.append(review),
    )
    return result


def test_passive_view_is_bounded_and_reports_missing_archive_owner(isolated_service, tmp_path):
    from row_bot import threads

    conversation = threads.create_thread("Private local title", seed_default_skills=False)
    before_name = threads.get_thread_name(conversation)
    before_messages = threads.get_latest_checkpoint_messages(conversation)
    snapshot = read_conversation_actions(isolated_service, conversation, validate=lambda: None)

    assert snapshot == {
        "schema_version": 1,
        "conversation_id": conversation,
        "revision": "0",
        "checkpoint_revision": "",
        "title": "Private local title",
        "pinned": False,
        "capabilities": {
            "rename": {"available": True, "code": None},
            "pin": {"available": True, "code": None},
            "archive": {"available": False, "code": "conversation_archive_unavailable"},
            "export": {"available": True, "code": None},
        },
    }
    assert threads.get_thread_name(conversation) == before_name
    assert threads.get_latest_checkpoint_messages(conversation) == before_messages
    assert not (threads._MEDIA_DIR / conversation).exists()
    assert str(tmp_path) not in json.dumps(snapshot)


def test_reviewed_rename_and_pin_use_canonical_revision_owner_and_replay(isolated_service):
    from langchain_core.messages import HumanMessage
    from row_bot import threads

    conversation = threads.create_thread("Before", seed_default_skills=False)
    threads.append_checkpoint_messages(
        conversation, [HumanMessage(id="saved-user", content="Keep this history")]
    )
    rename_review = _review(isolated_service, conversation, "conversation.rename", "0", {"title": "After"})
    rename = _command("conversation.rename", rename_review, {"title": "After"})

    first = _execute(isolated_service, conversation, rename)
    repeated = _execute(isolated_service, conversation, rename)
    assert repeated == first
    assert first["conversation"] == {
        "conversation_id": conversation,
        "revision": "1",
        "title": "After",
        "pinned": False,
    }

    pin_review = _review(isolated_service, conversation, "conversation.pin", "1", {"pinned": True})
    pinned = _execute(isolated_service, conversation, _command("conversation.pin", pin_review, {"pinned": True}))
    assert pinned["conversation"]["pinned"] is True
    assert pinned["conversation"]["revision"] == "2"
    assert [message.id for message in threads.get_latest_checkpoint_messages(conversation)] == ["saved-user"]


def test_export_is_local_opaque_excludes_internal_messages_and_recovers_admission(isolated_service, tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from row_bot import threads
    from row_bot.application.attachments import read_attachment

    conversation = threads.create_thread("Export title", seed_default_skills=False)
    threads.append_checkpoint_messages(
        conversation,
        [
            SystemMessage(id="system", content="private system instruction"),
            HumanMessage(id="user", content="Public question"),
            AIMessage(id="assistant", content="Public answer"),
            ToolMessage(id="tool", tool_call_id="call", content="private tool result"),
        ],
    )
    review = _review(isolated_service, conversation, "conversation.export", "0", {})
    command = _command("conversation.export", review, {})
    # Simulate a process stopping after durable admission but before publication.
    admissions.claim_command(
        "local-owner",
        command["command_id"],
        command,
        conversation,
        exclusive_target=True,
        initial_result={"command_id": command["command_id"], "status": "accepted"},
    )

    exported = _execute(isolated_service, conversation, command)
    assert exported["status"] == "completed"
    assert set(exported["export"]) == {
        "attachment_ref",
        "file_name",
        "size_bytes",
        "checkpoint_revision",
    }
    metadata, data = read_attachment(exported["export"]["attachment_ref"])
    text = data.decode()
    assert metadata["name"] == "conversation-export.md"
    assert "# Export title" in text
    assert "## You\n\nPublic question" in text
    assert "## Row-Bot\n\nPublic answer" in text
    assert "private system instruction" not in text
    assert "private tool result" not in text
    assert str(tmp_path) not in json.dumps(exported)

    assert _execute(isolated_service, conversation, command) == exported
    assert read_conversation_action_receipt(
        isolated_service,
        conversation,
        command["command_id"],
        owner_id="local-owner",
        validate=lambda: None,
    ) == exported

    retried_review = _review(isolated_service, conversation, "conversation.export", "0", {})
    retried = _command("conversation.export", retried_review, {})
    admissions.claim_command(
        "local-owner",
        retried["command_id"],
        retried,
        conversation,
        exclusive_target=True,
    )
    admissions.complete_command(
        "local-owner",
        retried["command_id"],
        {
            "command_id": retried["command_id"],
            "status": "partial",
            "action": "conversation.export",
            "code": "conversation_export_unconfirmed",
        },
    )
    assert _execute(isolated_service, conversation, retried)["status"] == "completed"


def test_changed_review_and_archive_fail_before_mutation(isolated_service):
    from row_bot import threads

    conversation = threads.create_thread("Before", seed_default_skills=False)
    review = _review(isolated_service, conversation, "conversation.rename", "0", {"title": "Reviewed"})
    command = _command("conversation.rename", review, {"title": "Changed"})
    with pytest.raises(ConversationActionError, match="conversation_review_changed"):
        _execute(isolated_service, conversation, command)
    assert threads.get_thread_name(conversation) == "Before"

    with pytest.raises(ConversationActionError, match="conversation_archive_unavailable"):
        _review(isolated_service, conversation, "conversation.archive", "0", {})
    assert not (threads._MEDIA_DIR / conversation).exists()


def test_stale_revision_and_cross_target_receipt_fail_closed(isolated_service):
    from row_bot import threads

    first = threads.create_thread("First", seed_default_skills=False)
    second = threads.create_thread("Second", seed_default_skills=False)
    review = _review(isolated_service, first, "conversation.pin", "0", {"pinned": True})
    command = _command("conversation.pin", review, {"pinned": True})
    _execute(isolated_service, first, command)

    with pytest.raises(ConversationActionError) as stale:
        _review(isolated_service, first, "conversation.pin", "0", {"pinned": False})
    assert stale.value.code == "revision_conflict"
    assert stale.value.current_revision == "1"
    assert read_conversation_action_receipt(
        isolated_service, second, command["command_id"], owner_id="local-owner", validate=lambda: None
    ) is None
