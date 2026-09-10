"""Live plaintext pages reuse the one bounded JSON spool and exact owner cut."""
from __future__ import annotations

import base64
import hashlib

import pytest

from row_bot.application import live_content
from row_bot.api.v1.schemas import LazyContent
from tests.subsystem.client_protocol.test_live_content import store as store
from tests.contracts.client_platform.test_headless_lifecycle import platform as platform
from tests.contracts.client_platform.test_protocol_boundaries import client as client, protocol_clock as protocol_clock

pytestmark = pytest.mark.subsystem
REFERENCE = "live:synthetic-pass:synthetic-segment"


class MeasuredFile:
    def __init__(self, file, on_read=None):
        self.file = file
        self.reads = []
        self.on_read = on_read
    def read(self, size):
        assert 0 <= size <= 65536
        self.reads.append((self.file.tell(), size))
        data = self.file.read(size)
        if self.on_read:
            self.on_read()
        return data
    def __getattr__(self, name):
        return getattr(self.file, name)


def collect(store, conversation="fixture", reference=REFERENCE):
    cursor, parts, seen = None, [], set()
    for _ in range(1000):
        page = store.read_text_page(conversation, reference, cursor)
        LazyContent.model_validate(page)
        text = base64.b64decode(page["data"]).decode("utf-8", errors="strict")
        assert len(text) <= 16384 and len(text.encode()) <= 65536
        assert page["media_type"] == "text/plain"
        parts.append(text)
        if not page["has_more"]:
            assert page["next_cursor"] is None
            return "".join(parts)
        assert page["next_cursor"] and page["next_cursor"] not in seen
        cursor = page["next_cursor"]
        seen.add(cursor)
    pytest.fail("Live text continuation failed to terminate")


@pytest.mark.parametrize("text", [
    "", "a" * 16384, "a" * 16385,
    "\x00" * 10924, "😀" * 16383 + "漢😀尾",
    ('\n\r\t\b\f\x00"\\é漢😀\\u0000' * 20000),
], ids=["empty", "exact-page", "page-plus-one", "escaped-controls", "unicode-boundary", "mixed-escapes"])
def test_exact_text_pages_keep_complete_unicode_and_escape_units(store, text):
    store.append("fixture", REFERENCE, text)
    spool = store._spools[("fixture", REFERENCE)]
    measured = MeasuredFile(spool.file)
    spool.file = measured
    assert collect(store) == text
    starts = [offset for offset, _ in measured.reads]
    assert starts[0] == len(live_content.PREFIX)
    assert starts == sorted(set(starts))
    # JSON consumers still receive their unchanged representation.
    page = store.read_page("fixture", REFERENCE, 5)
    assert page["media_type"] == "application/json"
    assert base64.b64decode(page["data"]) == live_content.PREFIX[:5]


def test_message_beyond_two_mib_is_complete_without_prefix_rescan(store):
    text = "漢😀\x00\n" * 300000
    assert len(text.encode()) > 2 * 1024 * 1024
    store.append("fixture", REFERENCE, text)
    actual = collect(store)
    assert hashlib.sha256(actual.encode()).digest() == hashlib.sha256(text.encode()).digest()


@pytest.mark.parametrize("change", ["append", "settle", "restart", "conversation", "reference", "tamper", "json"])
def test_live_text_cursor_rejects_changed_spool_owner_or_representation(store, tmp_path, change):
    store.append("fixture", REFERENCE, "A" * 30000)
    cursor = store.read_text_page("fixture", REFERENCE)["next_cursor"]
    target, conversation, reference = store, "fixture", REFERENCE
    if change == "append":
        store.append("fixture", REFERENCE, "B")
    elif change == "settle":
        store.discard("fixture", REFERENCE)
    elif change == "restart":
        target = live_content.LiveContentStore(root=tmp_path, validate=lambda _: None)
    elif change == "conversation":
        conversation = "other"
        store.append(conversation, REFERENCE, "Foreign text")
    elif change == "reference":
        reference = "live:other:segment"
        store.append("fixture", reference, "Foreign text")
    elif change == "tamper":
        cursor = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
    else:
        cursor = store.read_page("fixture", REFERENCE, 100)["next_cursor"]
    with pytest.raises(live_content.LiveContentError, match="cursor_expired"):
        target.read_text_page(conversation, reference, cursor)
    if target is not store:
        target.close()


def test_plaintext_cursor_cannot_be_used_as_json_cursor(store):
    store.append("fixture", REFERENCE, "x" * 20000)
    cursor = store.read_text_page("fixture", REFERENCE)["next_cursor"]
    with pytest.raises(live_content.LiveContentError, match="cursor_expired"):
        store.read_page("fixture", REFERENCE, cursor=cursor)


def test_api_live_text_uses_existing_spool_without_checkpoint_read(platform, client, tmp_path, monkeypatch):
    from row_bot import threads
    local = live_content.LiveContentStore(root=tmp_path / "spools")
    monkeypatch.setattr(live_content, "live_content_store", local)
    local.append("conversation-a", REFERENCE, "Live 😀\n" * 5000)
    monkeypatch.setattr(threads.checkpointer, "get_tuple", lambda *_: (_ for _ in ()).throw(AssertionError("No checkpoint expansion")))
    from row_bot.runtime import checkpoint_reader
    monkeypatch.setattr(checkpoint_reader, "open_checkpoint", lambda *_: (_ for _ in ()).throw(AssertionError("Live content is not in a checkpoint")))
    try:
        response = client.get(f"/api/v1/conversations/conversation-a/text/{REFERENCE}")
        assert response.status_code == 200, response.text
        assert response.json()["has_more"]
        assert response.json()["media_type"] == "text/plain"
        assert base64.b64decode(response.json()["data"]).decode().startswith("Live 😀\n")
        assert collect(local, "conversation-a") == "Live 😀\n" * 5000
    finally:
        local.close()


def test_delete_between_spool_read_and_response_rejects_content(platform, client, tmp_path, monkeypatch):
    from row_bot.runtime import admissions
    local = live_content.LiveContentStore(root=tmp_path / "spools")
    monkeypatch.setattr(live_content, "live_content_store", local)
    local.append("conversation-a", REFERENCE, "PRIVATE synthetic live content")
    spool = local._spools[("conversation-a", REFERENCE)]
    spool.file = MeasuredFile(spool.file, on_read=lambda: admissions.close_admission("conversation-a"))
    try:
        response = client.get(f"/api/v1/conversations/conversation-a/text/{REFERENCE}")
        assert response.status_code == 403, response.text
        assert "PRIVATE" not in response.text
    finally:
        local.close()
