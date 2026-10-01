"""Background notices reach clients over the event stream (parity rows 10, 11)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.schemas import NoticeFrame, NoticePage
from row_bot.api.v1.security import ClientSecurity
from row_bot.application import app_notices as notices_module
from row_bot.application.app_notices import MAX_NOTICES, NoticeJournal
from tests.subsystem.client_protocol.test_protocol_security import Service, bootstrap

pytestmark = pytest.mark.subsystem


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def journal(monkeypatch):
    fresh = NoticeJournal(clock=Clock())
    monkeypatch.setattr(notices_module, "app_notices", fresh)
    return fresh


def test_journal_is_bounded_and_folds_repeats_into_one_notice():
    clock = Clock()
    journal = NoticeJournal(clock=clock)
    first = journal.post(title="⚡ Task Complete", message="Digest finished.", level="info", requested=True)
    again = journal.post(title="⚡ Task Complete", message="Digest finished.", level="info")
    assert again.id == first.id and again.count == 2 and again.requested
    assert [notice.id for notice in journal.since(0)] == [first.id]
    # Out of the coalescing window a repeat is news again.
    clock.now += timedelta(minutes=30)
    later = journal.post(title="⚡ Task Complete", message="Digest finished.", level="info")
    assert later.id != first.id
    for index in range(MAX_NOTICES * 2):
        journal.post(title="Workflow", message=f"Run {index}", level="warning")
    assert len(journal.since(0)) == MAX_NOTICES
    assert journal.since(journal.latest()) == []


def test_journal_cleans_text_and_keeps_start_up_warnings_for_monitor():
    journal = NoticeJournal(clock=Clock())
    notice = journal.startup_warning("⚠️ The plugin 'rss' didn't load: environment not prepared", source="plugins")
    assert notice.level == "warning" and notice.startup
    assert notice.message == "The plugin 'rss' didn't load: environment not prepared"
    long = journal.post(title="x" * 400, message="y" * 900)
    assert len(long.title) <= 120 and len(long.message) <= 500
    page = NoticePage.model_validate(journal.page())
    assert page.server_epoch == journal.epoch
    assert page.startup_warnings == ["The plugin 'rss' didn't load: environment not prepared"]


def test_notify_posts_a_notice_for_every_open_client(journal, monkeypatch):
    from row_bot import notifications
    monkeypatch.setattr(notifications, "_desktop_notify", lambda *_: None)
    monkeypatch.setattr(notifications, "_play_sound", lambda *_: None)
    notifications.notify("Row-Bot – API Error", "Rate limit reached.", toast_type="negative", source="model")
    notifications.notify("Document Ingestion", "3 complete", source="documents", requested=True)
    notifications.notify("Row-Bot – API Error", "Shown in the chat instead.", toast_type="negative", in_app=False)
    posted = journal.since(0)
    assert [(notice.level, notice.source, notice.requested) for notice in posted] == [
        ("error", "model", False), ("info", "documents", True)]


def notice_client(journal):
    security = ClientSecurity("fixture", clock=lambda: 10.0)
    service = Service()
    service.snapshot = lambda conversation: {"conversation_id": conversation, "server_epoch": service.server_epoch,
        "projection_revision": "0", "cursor": "0", "checkpoint_revision": "", "rows": [], "generation": None}
    service.events_since = lambda *_: {"server_epoch": service.server_epoch, "snapshot_required": False, "events": []}
    app = create_client_platform_app(service, security=security, choices=lambda: {"models": [], "capabilities": []})
    return app, security


def test_poll_and_notice_read_carry_notices_after_the_clients_position(journal):
    app, _ = notice_client(journal)
    first = journal.post(title="Approval Required", message="Digest: approve the send", level="warning")
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        _, headers = bootstrap(client)
        sub = client.post("/api/v1/conversations/idle/subscriptions", headers=headers).json()
        page = client.get("/api/v1/events/poll", headers=headers, params={
            "subscription_id": sub["subscription_id"], "cursor": sub["cursor"],
            "notices_after": 0, "notices_epoch": journal.epoch}).json()
        assert page["notices_epoch"] == journal.epoch
        assert [notice["id"] for notice in page["notices"]] == [first.id]
        page = client.get("/api/v1/events/poll", headers=headers, params={
            "subscription_id": sub["subscription_id"], "cursor": sub["cursor"],
            "notices_after": first.id, "notices_epoch": journal.epoch}).json()
        assert page["notices"] == []
        # A position from another server instance starts over.
        page = client.get("/api/v1/events/poll", headers=headers, params={
            "subscription_id": sub["subscription_id"], "cursor": sub["cursor"],
            "notices_after": 99, "notices_epoch": "old-server"}).json()
        assert [notice["id"] for notice in page["notices"]] == [first.id]
        journal.startup_warning("Tunnel didn't start: ngrok is at its agent limit.", source="tunnel")
        read = client.get("/api/v1/notices", headers=headers, params={"after": first.id, "epoch": journal.epoch})
        assert read.status_code == 200, read.text
        body = NoticePage.model_validate(read.json())
        assert [notice.message for notice in body.notices] == ["Tunnel didn't start: ngrok is at its agent limit."]
        assert body.startup_warnings == ["Tunnel didn't start: ngrok is at its agent limit."]


def test_event_stream_sends_notice_frames_without_moving_the_cursor(journal):
    app, security = notice_client(journal)
    posted = journal.post(title="Buddy generated", message="New look ready.", level="info", requested=True)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        _, headers = bootstrap(client)
        sub = client.post("/api/v1/conversations/idle/subscriptions", headers=headers).json()

        async def read_notice():
            disconnected = asyncio.Event()
            chunks = []

            async def receive():
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    chunks.append(message["body"].decode())
                    if "event: notice" in chunks[-1]:
                        disconnected.set()

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/api/v1/events",
                "raw_path": b"/api/v1/events", "root_path": "",
                "query_string": urlencode({"subscription_id": sub["subscription_id"], "cursor": sub["cursor"],
                                           "notices_after": 0, "notices_epoch": journal.epoch}).encode(),
                "headers": [(b"host", b"localhost"), *((key.lower().encode(), value.encode())
                                                       for key, value in headers.items())],
                "client": ("127.0.0.1", 1234), "server": ("localhost", 80)}
            await asyncio.wait_for(app(scope, receive, send), timeout=5)
            return "".join(chunks)

        body = asyncio.run(read_notice())
        frame = next(part for part in body.split("\n\n") if part.startswith("event: notice"))
        assert "id:" not in frame
        data = NoticeFrame.model_validate(json.loads(frame.split("data: ", 1)[1]))
        assert data.notices_epoch == journal.epoch
        assert data.notice.id == posted.id and data.notice.requested
        assert not security._subscriptions[sub["subscription_id"]].streaming
