"""Revision-fenced transport composition reuses bounded retained data owners."""
from __future__ import annotations

from contextlib import closing

import json

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


def test_open_composes_real_owner_views_and_keeps_complete_bounded_history(service):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.application.conversation_open import read_open
    from row_bot.application.conversation_drafts import read_draft, save_draft
    from row_bot.application.conversation_search import history_window
    service.readiness_factory = lambda _: False
    conversation = threads.create_thread("Open composition", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [HumanMessage(id=f"message-{index}", content=f"Public {index}") for index in range(123)])
    previous = read_draft(service, conversation)
    save_draft(service, conversation, {"expected_revision": previous["revision"], "text": "Retained draft", "attachment_refs": []})
    opened = read_open(service, conversation, limit=25)
    assert opened["conversation"]["id"] == opened["workspace"]["conversation_id"] == opened["draft"]["conversation_id"] == conversation
    assert opened["conversation"]["revision"] == opened["workspace"]["revision"]
    assert opened["draft"]["text"] == "Retained draft"
    assert len(opened["history"]["rows"]) == 25
    assert opened["history"]["previous_cursor"]
    earlier = history_window(service, conversation, cursor=opened["history"]["previous_cursor"], limit=25)
    assert len(earlier["rows"]) == 25
    assert earlier["rows"][-1]["id"] != opened["history"]["rows"][-1]["id"]
    assert len(json.dumps(opened).encode()) < 2 * 1024 * 1024
    assert not service.registry.active(conversation)
    assert threads.get_latest_checkpoint_revision(conversation) == opened["history"]["checkpoint_revision"]


def test_open_rejects_deletion_during_composition_without_returning_draft(service, monkeypatch):
    from row_bot import threads
    from row_bot.runtime import admissions
    from row_bot.application import conversation_drafts
    from row_bot.application.conversation_open import read_open
    from row_bot.application.client_platform import ClientPlatformError
    service.readiness_factory = lambda _: False
    conversation = threads.create_thread("Deleting open", seed_default_skills=False)
    actual = conversation_drafts.read_draft
    def deleting(*args):
        result = actual(*args)
        admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(conversation_drafts, "read_draft", deleting)
    with pytest.raises(ClientPlatformError, match="conversation_deleting"):
        read_open(service, conversation)


@pytest.mark.parametrize("limit", [0, 101, True])
def test_open_rejects_unbounded_page_request(service, limit):
    from row_bot.application.conversation_open import read_open
    from row_bot.application.client_platform import ClientPlatformError
    with pytest.raises(ClientPlatformError, match="invalid_command"):
        read_open(service, "absent-conversation", limit=limit)


def test_open_fences_legacy_revision_change(service, monkeypatch):
    from row_bot import threads
    from row_bot.application import conversation_drafts
    from row_bot.application.conversation_open import read_open
    from row_bot.application.client_platform import ClientPlatformError
    import sqlite3
    service.readiness_factory = lambda _: False
    conversation = threads.create_thread("Changed open", seed_default_skills=False)
    actual = conversation_drafts.read_draft
    def changing(*args):
        result = actual(*args)
        with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
            conn.execute("UPDATE thread_meta SET client_revision=client_revision+1 WHERE thread_id=?", (conversation,))
        return result
    monkeypatch.setattr(conversation_drafts, "read_draft", changing)
    with pytest.raises(ClientPlatformError, match="revision_conflict"):
        read_open(service, conversation)


def test_open_rejects_oversized_legacy_draft_without_truncating_saved_data(service, tmp_path, monkeypatch):
    from row_bot import threads
    from row_bot.application.conversation_open import read_open
    from row_bot.application.client_platform import ClientPlatformError
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", tmp_path / "drafts")
    threads._THREAD_UI_DIR.mkdir()
    service.readiness_factory = lambda _: False
    conversation = threads.create_thread("Large retained draft", seed_default_skills=False)
    text = "漢" * 800000
    threads.save_thread_draft(conversation, text)
    with pytest.raises(ClientPlatformError, match="payload_too_large"):
        read_open(service, conversation)
    assert threads.load_thread_draft(conversation)["text"] == text


def test_open_reads_latest_shared_draft_without_mutating_history(service, tmp_path, monkeypatch):
    from row_bot import threads
    from row_bot.application.conversation_open import read_open
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", tmp_path / "drafts")
    threads._THREAD_UI_DIR.mkdir()
    service.readiness_factory = lambda _: False
    conversation = threads.create_thread("Shared draft", seed_default_skills=False)
    threads.save_thread_draft(conversation, "First window")
    first = read_open(service, conversation)
    threads.save_thread_draft(conversation, "Second window")
    second = read_open(service, conversation)
    assert first["draft"]["revision"] != second["draft"]["revision"]
    assert second["draft"]["text"] == "Second window"
    assert second["history"] == first["history"]
    assert not service.registry.active(conversation)


@pytest.mark.parametrize("revoke", [False, True])
def test_public_open_closed_dto_and_mid_read_session_revocation(service, tmp_path, monkeypatch, revoke):
    from fastapi.testclient import TestClient
    from pydantic import ValidationError
    from row_bot import threads
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.api.v1.schemas import ConversationOpenView
    from row_bot.application import conversation_drafts
    from tests.subsystem.client_protocol.test_protocol_security import bootstrap
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", tmp_path / "drafts")
    threads._THREAD_UI_DIR.mkdir()
    service.readiness_factory = lambda _: False
    conversation = threads.create_thread("Public open", seed_default_skills=False)
    threads.save_thread_draft(conversation, "Private retained draft")
    active = {"value": True}
    def authenticate(scope, provenance):
        return SessionIdentity("fixture-device", "fixture-session") if active["value"] else None
    app = create_client_platform_app(service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=authenticate, choices=lambda: {"models": [], "capabilities": []})
    actual = conversation_drafts.read_draft
    def read(*args):
        result = actual(*args)
        if revoke:
            active["value"] = False
        return result
    monkeypatch.setattr(conversation_drafts, "read_draft", read)
    client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345))
    try:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{conversation}/open", headers=headers)
        if revoke:
            assert response.status_code == 401
            assert response.json()["code"] == "authentication_required"
            assert "Private retained draft" not in response.text
        else:
            assert response.status_code == 200, response.text
            view = ConversationOpenView.model_validate(response.json())
            assert view.conversation.id == view.workspace.conversation_id == view.history.conversation_id == view.draft.conversation_id == conversation
            assert view.draft.text == "Private retained draft"
            assert set(response.json()) == {"conversation", "history", "workspace", "draft"}
            with pytest.raises(ValidationError):
                ConversationOpenView.model_validate({**response.json(), "private_owner": "must fail"})
    finally:
        client.close()
