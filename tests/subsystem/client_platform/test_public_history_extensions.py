"""Independent complete text and grouped-library checks over retained owners."""
from __future__ import annotations

import base64
import hashlib
from uuid import uuid4

import pytest

from tests.subsystem.client_platform.test_unified_workspace_acceptance import resource_service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def test_complete_public_text_pages_beyond_two_mib_preserve_unicode_and_exclude_metadata(service):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.application.message_text import read_text
    conversation = threads.create_thread("Large public text", seed_default_skills=False)
    content = "é🙂漢\n" * 220000
    assert len(content.encode()) > 2 * 1024 * 1024
    assert threads.append_checkpoint_messages(conversation, [HumanMessage(id="public-message", content=[
        {"type": "text", "text": content},
        {"type": "image_url", "image_url": {"url": "https://example.invalid/PRIVATE-METADATA"}, "text": "NON-TEXT-PRIVATE"},
        {"type": "text", "text": "Visible ending"},
    ], additional_kwargs={"private": "SECRET-ADDITIONAL-METADATA"})])
    expected = hashlib.sha256((content + "\nVisible ending\n").encode()).hexdigest()
    digest, total, cursor, seen = hashlib.sha256(), 0, None, set()
    for _ in range(100):
        page = read_text(service, conversation, "public-message", cursor=cursor)
        data = base64.b64decode(page["data"])
        assert len(data) <= 65536
        data.decode("utf-8", errors="strict")
        assert b"PRIVATE" not in data and b"SECRET" not in data
        assert page["media_type"] == "text/plain"
        digest.update(data)
        total += len(data)
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
        assert cursor and cursor not in seen
        seen.add(cursor)
    else:
        pytest.fail("Bounded text continuation did not reach the complete message")
    assert total > 2 * 1024 * 1024
    assert digest.hexdigest() == expected


def test_public_text_cursor_pins_message_and_checkpoint_and_api_does_not_expose_metadata(service):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.message_text import read_text
    conversation = threads.create_thread("Pinned text", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id="first", content="Public 🙂" * 5000, additional_kwargs={"private": "SECRET-MARKER"}),
        HumanMessage(id="second", content="Different public row"),
    ])
    first = read_text(service, conversation, "first")
    assert first["has_more"]
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        read_text(service, conversation, "second", cursor=first["next_cursor"])
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{conversation}/text/first", headers=headers)
        assert response.status_code == 200, response.text
        assert "SECRET-MARKER" not in base64.b64decode(response.json()["data"]).decode()
    assert threads.append_checkpoint_messages(conversation, [HumanMessage(id="third", content="New checkpoint")])
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        read_text(service, conversation, "first", cursor=first["next_cursor"])


def test_server_pin_and_resource_groups_find_old_conversations_beyond_first_thousand(resource_service, tmp_path):
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder, register_existing_folder
    from row_bot.designer.client_service import DeckSetup, create_deck
    folder = tmp_path / "source"
    folder.mkdir()
    workspace = register_existing_folder(AuthorizedWorkspaceFolder(folder, folder, "fixture-grant")).workspace
    artifact_id = str(uuid4())
    create_deck(artifact_id, DeckSetup())
    workspace_ids, artifact_ids = set(), set()
    for index in range(1003):
        identity = f"review-library-{index:04d}"
        has_workspace, has_artifact = index % 10 == 0, index % 11 == 0
        threads.create_thread(f"Library {index:04d}", thread_id=identity, seed_default_skills=False,
            developer_workspace_id=workspace.resource_id if has_workspace else "",
            project_id=artifact_id if has_artifact else "")
        if has_workspace:
            workspace_ids.add(identity)
        if has_artifact:
            artifact_ids.add(identity)
    service = resource_service
    first = service.list_conversations(limit=200)
    oldest = "review-library-0000"
    assert oldest not in {row["id"] for row in first["items"]}
    with _client(service) as client:
        _, headers = bootstrap(client)
        pinned = _command(client, headers, "conversation.pin", {"pinned": True}, target=oldest)
        assert pinned.status_code == 200, pinned.text
        result = client.get("/api/v1/conversations", headers=headers, params={"group": "pinned", "limit": 1})
        assert result.status_code == 200, result.text
        assert [row["id"] for row in result.json()["items"]] == [oldest]
        assert service.list_conversations(limit=1)["items"][0]["id"] == oldest
    for group, expected in (("all", {f"review-library-{index:04d}" for index in range(1003)}),
                            ("workspace", workspace_ids), ("artifact", artifact_ids)):
        cursor, found, seen = None, [], set()
        for _ in range(65):
            page = service.list_conversations(limit=17, cursor=cursor, group=group)
            assert len(page["items"]) <= 17
            found.extend(item["id"] for item in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
            assert cursor and cursor not in seen
            seen.add(cursor)
        else:
            pytest.fail("Grouped conversation continuation failed to terminate")
        assert len(found) == len(set(found))
        assert set(found) == expected
        assert oldest in found
    cursor = service.list_conversations(limit=1, group="workspace")["next_cursor"]
    with pytest.raises(ClientPlatformError, match="cursor_expired"):
        service.list_conversations(limit=1, cursor=cursor, group="artifact")


@pytest.mark.parametrize("phase", ["before", "during"])
def test_history_read_fences_pending_deletion(service, monkeypatch, phase):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.runtime import admissions, checkpoint_reader
    from row_bot.application.conversation_search import history_window
    from row_bot.application.client_platform import ClientPlatformError
    conversation = threads.create_thread("Closing history", seed_default_skills=False)
    threads.append_checkpoint_messages(conversation, [HumanMessage(id="closing-message", content="Private history")])
    actual = checkpoint_reader.BlobReader.public_row
    calls = []
    def row(reader, record, *args, **kwargs):
        calls.append(record["message_id"])
        result = actual(reader, record, *args, **kwargs)
        admissions.close_admission(conversation)
        return result
    monkeypatch.setattr(checkpoint_reader.BlobReader, "public_row", row)
    if phase == "before":
        admissions.close_admission(conversation)
    with pytest.raises(ClientPlatformError, match="conversation_deleting"):
        history_window(service, conversation)
    assert bool(calls) is (phase != "before")


def test_global_search_skips_closed_rows_before_checkpoint_and_keeps_zero_hit_continuation(service, monkeypatch):
    from row_bot import threads
    from row_bot.runtime import admissions, checkpoint_reader
    from row_bot.application.conversation_search import search
    for index in range(33):
        identity = threads.create_thread("needle title", thread_id=f"closed-scan-{index:03}", seed_default_skills=False)
        if index < 32:
            admissions.close_admission(identity)
    actual = checkpoint_reader.open_checkpoint
    opened = []
    def reader(identity, *args, **kwargs):
        opened.append(identity)
        assert identity == "closed-scan-032"
        return actual(identity, *args, **kwargs)
    monkeypatch.setattr(checkpoint_reader, "open_checkpoint", reader)
    first = search(service, "needle")
    assert first["items"] == [] and first["has_more"] and first["scanned_messages"] == 0
    assert opened == []
    second = search(service, "needle", cursor=first["next_cursor"])
    assert [item["conversation_id"] for item in second["items"]] == ["closed-scan-032"]
    assert not second["has_more"]


@pytest.mark.parametrize("scoped", [False, True])
def test_search_omits_or_rejects_hits_closed_during_scan(service, monkeypatch, scoped):
    from langchain_core.messages import HumanMessage
    from row_bot import threads
    from row_bot.runtime import admissions
    from row_bot.application import conversation_search
    from row_bot.application.client_platform import ClientPlatformError
    closed = threads.create_thread("needle closed", thread_id="needle-a", seed_default_skills=False)
    active = threads.create_thread("needle active", thread_id="needle-b", seed_default_skills=False)
    threads.append_checkpoint_messages(closed, [HumanMessage(id="close-during-scan", content="needle private")])
    actual = conversation_search._find_public_text
    def find(reader, record, needle):
        result = actual(reader, record, needle)
        admissions.close_admission(closed)
        return result
    monkeypatch.setattr(conversation_search, "_find_public_text", find)
    if scoped:
        with pytest.raises(ClientPlatformError, match="conversation_deleting"):
            conversation_search.search(service, "needle", conversation_id=closed)
    else:
        result = conversation_search.search(service, "needle")
        assert [item["conversation_id"] for item in result["items"]] == [active]


def test_scoped_search_rejects_closed_conversation_before_checkpoint_read(service, monkeypatch):
    from row_bot import threads
    from row_bot.runtime import admissions, checkpoint_reader
    from row_bot.application.conversation_search import search
    from row_bot.application.client_platform import ClientPlatformError
    conversation = threads.create_thread("needle private", seed_default_skills=False)
    admissions.close_admission(conversation)
    def forbidden_read(*args, **kwargs):
        pytest.fail("A closed conversation reached its checkpoint owner")
    monkeypatch.setattr(checkpoint_reader, "open_checkpoint", forbidden_read)
    with pytest.raises(ClientPlatformError, match="conversation_deleting"):
        search(service, "needle", conversation_id=conversation)
