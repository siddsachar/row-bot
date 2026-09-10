"""Draft persistence and CAS share the retained conversation metadata owner."""
from __future__ import annotations

from contextlib import closing

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def draft_service(service, tmp_path, monkeypatch):
    from row_bot import threads
    folder = tmp_path / "thread-ui"
    folder.mkdir()
    monkeypatch.setattr(threads, "_THREAD_UI_DIR", folder)
    return service


def test_draft_roundtrip_between_retained_owner_and_two_api_clients(draft_service):
    from row_bot import threads
    from row_bot.application.attachments import register_attachment
    conversation = threads.create_thread("Draft", seed_default_skills=False)
    attachment = register_attachment(conversation, "fixture.txt", b"Synthetic attachment")
    threads.save_thread_draft(conversation, "Retained draft\nUnicode café", source="nicegui", attachments=[attachment])
    endpoint = f"/api/v1/conversations/{conversation}/draft"
    with _client(draft_service) as client:
        _, first = bootstrap(client)
        _, second = bootstrap(client)
        original = client.get(endpoint, headers=first)
        assert original.status_code == 200, original.text
        view = original.json()
        assert view["text"] == "Retained draft\nUnicode café"
        assert view["attachments"] == [attachment]
        assert client.get(endpoint, headers=second).json() == view
        body = {"expected_revision": view["revision"], "text": "Shared replacement\n<script>literal</script>",
            "attachment_refs": [attachment["attachment_ref"]]}
        saved = client.put(endpoint, headers=first, json=body)
        assert saved.status_code == 200, saved.text
        result = saved.json()
        assert result["revision"] != view["revision"]
        assert client.put(endpoint, headers=second, json=body).json() == result
        retained = threads.load_thread_draft(conversation)
        assert retained["text"] == body["text"]
        assert retained["attachments"] == [attachment]
        assert client.get(endpoint, headers=second).json() == result
        conflict = client.put(endpoint, headers=second, json={**body, "text": "Stale competing edit"})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "draft_revision_conflict"
        assert threads.load_thread_draft(conversation) == retained
        threads.save_thread_draft(conversation, "Later retained edit", source="nicegui", attachments=[])
        reloaded = client.get(endpoint, headers=first).json()
        assert reloaded["text"] == "Later retained edit"
        assert reloaded["attachments"] == []
        assert reloaded["revision"] != result["revision"]


def test_simultaneous_draft_writers_have_one_cas_winner(draft_service):
    from row_bot import threads
    from row_bot.application.conversation_drafts import read_draft, save_draft
    from row_bot.application.client_platform import ClientPlatformError
    conversation = threads.create_thread("Competing writers", seed_default_skills=False)
    original = read_draft(draft_service, conversation)
    barrier = Barrier(2)
    def save(text):
        barrier.wait(timeout=5)
        try:
            return save_draft(draft_service, conversation, {"expected_revision": original["revision"],
                "text": text, "attachment_refs": []})
        except ClientPlatformError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, ["First writer", "Second writer"]))
    assert results.count("draft_revision_conflict") == 1
    winner = next(result for result in results if isinstance(result, dict))
    assert read_draft(draft_service, conversation) == winner


def test_foreign_attachment_is_rejected_before_inspection(draft_service, monkeypatch):
    from row_bot import threads
    from row_bot.application import attachments
    conversation = threads.create_thread("Target", seed_default_skills=False)
    foreign = threads.create_thread("Foreign", seed_default_skills=False)
    attachment = attachments.register_attachment(foreign, "private.txt", b"Synthetic foreign content")
    threads.save_thread_draft(conversation, "Preserved", attachments=[])
    before = threads.load_thread_draft(conversation)
    def forbidden(*args, **kwargs):
        pytest.fail("Foreign attachment inspection preceded conversation ownership check")
    monkeypatch.setattr(attachments, "inspect_attachment", forbidden)
    endpoint = f"/api/v1/conversations/{conversation}/draft"
    with _client(draft_service) as client:
        _, headers = bootstrap(client)
        revision = client.get(endpoint, headers=headers).json()["revision"]
        response = client.put(endpoint, headers=headers, json={"expected_revision": revision,
            "text": "Forbidden", "attachment_refs": [attachment["attachment_ref"]]})
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "action_denied"
        assert "private.txt" not in response.text
        assert threads.load_thread_draft(conversation) == before


def test_deletion_after_attachment_inspection_fences_draft_write(draft_service, monkeypatch):
    from row_bot import threads
    from row_bot.application import attachments
    from row_bot.runtime import admissions
    conversation = threads.create_thread("Deletion race", seed_default_skills=False)
    attachment = attachments.register_attachment(conversation, "fixture.txt", b"Synthetic content")
    threads.save_thread_draft(conversation, "Before deletion", attachments=[])
    before = threads.load_thread_draft(conversation)
    inspect = attachments.inspect_attachment
    def close_after_inspection(ref):
        result = inspect(ref)
        admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(attachments, "inspect_attachment", close_after_inspection)
    endpoint = f"/api/v1/conversations/{conversation}/draft"
    with _client(draft_service) as client:
        _, headers = bootstrap(client)
        revision = client.get(endpoint, headers=headers).json()["revision"]
        response = client.put(endpoint, headers=headers, json={"expected_revision": revision,
            "text": "Too late", "attachment_refs": [attachment["attachment_ref"]]})
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "conversation_deleting"
        assert threads.load_thread_draft(conversation) == before


def test_session_revocation_validation_precedes_draft_or_attachment_mutation(draft_service):
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.conversation_drafts import read_draft, save_draft
    conversation = threads.create_thread("Revoked client", seed_default_skills=False)
    original = read_draft(draft_service, conversation)
    def revoked():
        raise ClientPlatformError("capability_revoked")
    with pytest.raises(ClientPlatformError, match="capability_revoked"):
        save_draft(draft_service, conversation, {"expected_revision": original["revision"],
            "text": "Do not persist", "attachment_refs": []}, revoked)
    assert read_draft(draft_service, conversation) == original
    assert threads.load_thread_draft(conversation) is None


@pytest.mark.parametrize("phase", ["before", "during", "deleted"])
def test_draft_read_fences_closed_admission_and_physical_deletion(draft_service, monkeypatch, phase):
    import sqlite3
    from row_bot import threads
    from row_bot.runtime import admissions
    from row_bot.application.conversation_drafts import read_draft
    from row_bot.application.client_platform import ClientPlatformError
    conversation = threads.create_thread("Read deletion", seed_default_skills=False)
    threads.save_thread_draft(conversation, "Private retained draft")
    actual = threads.load_thread_draft
    calls = []
    def read(identity):
        calls.append(identity)
        result = actual(identity)
        if phase == "deleted":
            with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
                conn.execute("DELETE FROM thread_meta WHERE thread_id=?", (conversation,))
        else:
            admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(threads, "load_thread_draft", read)
    if phase == "before":
        admissions.close_admission(conversation)
    with pytest.raises(ClientPlatformError, match="not_found" if phase == "deleted" else "conversation_deleting"):
        read_draft(draft_service, conversation)
    assert len(calls) == (0 if phase == "before" else 1)
