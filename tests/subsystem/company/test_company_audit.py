"""What a joined Row-Bot tells its company (C4), against the fake company server's S5 ingest (which checks every
event against the shared contract): a gated tool action approved, refused, failed, approved by the level and
blocked by the company's ceiling, a chat export, a data backup and an ended lockdown, each as metadata only; the
outbox's bounds and its drop count; batches of at most 100 events and 256 KiB; ids kept across a failed send; a 422
dropping one event; back-off after a 5xx and a 429; Leave's last send and delete; disconnect and re-join; no
content possible; and with no company joined, no file, no event and no network."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from row_bot import activity_log
from row_bot.application import client_company
from row_bot.audit import InvalidAuditEvent, format_time
from row_bot.company import audit, client, policy, store
from row_bot.tools import approval_gate
from tests.fixtures.company_server import AUDIT_INGEST

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

DAY = 24 * 60 * 60
# What the person's action was about: none of it may ever reach the company.
SECRET = "salary-review-2026.xlsx"
DELETE = {"tool": "delete_file", "label": "Delete a file", "description": f"Delete {SECRET}", "risk": "high"}
METADATA_KEYS = {"approval_level", "surface", "risk", "app", "rule_id"}


@pytest.fixture
def clock(monkeypatch):
    """The policy's clock (seconds since the epoch), moved by the test."""
    now = [1_800_000_000.0]
    monkeypatch.setattr(policy, "now", lambda: now[0])
    return now


@pytest.fixture
def written():
    """The activity log's writer settled after the test (it tells the company from its own thread)."""
    yield
    activity_log.flush()


def _gated(call_id: str, *, mode: str = "approve", answer: bool = True, failed: bool = False,
           ask=approval_gate.resolve_approval_line) -> None:
    """One tool call through the real approval gate, noted by the activity log as the tool-call middleware notes
    it (its arguments included), and written."""
    with approval_gate.collect_activity() as notes:
        ask(DELETE, approval_mode=mode, approval_callback=lambda _payload: answer)
    activity_log.record(activity_log.ToolCall(
        call_id=call_id, tool="delete_file", args={"path": SECRET}, thread_id="chat-1", surface="normal_chat",
        level=mode, decisions=tuple(notes), failed=failed))
    assert activity_log.flush()


def _outbox() -> list[dict]:
    path = audit.outbox_path()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _sent(server) -> list[dict]:
    return list(server.audit.values())


def _batch_ids(body: bytes) -> list[str]:
    return [event["id"] for event in json.loads(body)["events"]]


def _no_content(server) -> None:
    for text in [audit.outbox_path().read_text(encoding="utf-8") if audit.outbox_path().exists() else "",
                 *(body.decode("utf-8") for body in server.audit_batches)]:
        assert SECRET not in text and "salary" not in text and "Delete a file" not in text


# -- the events, at their seams -----------------------------------------------------------------------

def test_gated_actions_approved_refused_and_failed_tell_the_company_their_metadata_only(company_server, written):
    record = company_server.join_here()

    _gated("c1", answer=True)
    _gated("c2", answer=False)
    _gated("c3", answer=True, failed=True)
    activity_log.record(activity_log.ToolCall(call_id="r1", tool="read_notes", args={"name": SECRET},
                                              thread_id="chat-1", surface="normal_chat", level="approve"))
    assert activity_log.flush()  # Read-only: nothing needed approval, so nothing is told.
    _gated("c1", answer=True)  # The same call noted again: still one event.

    waiting = audit.waiting_ids()
    assert len(waiting) == 3
    audit.send_now()

    events = _sent(company_server)
    assert [event["id"] for event in events] == waiting
    assert [(event["outcome"], event["decided_by"]) for event in events] == [
        ("succeeded", "person"), ("denied", "person"), ("failed", "person")]
    for event in events:
        assert (event["action"], event["layer"], event["redaction"]) == ("tool.called", "org", "metadata")
        assert event["target"] == {"kind": "tool", "ref": "delete_file"}
        assert event["device_id"] == record.device_id
        assert set(event["details"]) <= METADATA_KEYS
        assert (event["details"]["approval_level"], event["details"]["surface"], event["details"]["risk"]) == (
            "approve", "chat", "high")
        assert event["turn_id"] is None and event["request_id"] is None
    _no_content(company_server)
    assert not audit.outbox_path().exists()  # Everything sent.


def test_hands_off_tells_the_company_the_level_approved_it(company_server, written):
    company_server.policy["approvals"]["max_level"] = "full"
    company_server.join_here()

    _gated("h1", mode="full")
    audit.send_now()

    [event] = _sent(company_server)
    assert (event["outcome"], event["decided_by"], event["details"]["approval_level"]) == (
        "succeeded", "hands_off", "full")


def test_an_action_the_companys_ceiling_of_block_refuses_is_told_as_blocked(company_server, written):
    company_server.policy["approvals"]["max_level"] = "block"
    company_server.join_here()

    _gated("b1", mode="approve")  # Asked through the shared levels.
    _gated("b2", mode="approve", ask=approval_gate.request_approval)  # Asked by a caller that knows it's gated.
    audit.send_now()

    events = _sent(company_server)
    assert [(event["outcome"], event["decided_by"], event["details"]["approval_level"]) for event in events] == [
        ("blocked", "block", "block"), ("blocked", "block", "block")]
    _no_content(company_server)


def test_a_data_backup_tells_the_company_once_it_is_made(company_server, tmp_path, monkeypatch):
    from row_bot.application import client_data_backup, profile_backup

    company_server.join_here()
    folder = tmp_path / "Backups"
    folder.mkdir()
    monkeypatch.setattr(client_data_backup, "_backups_folder", lambda: folder)
    monkeypatch.setattr(client_data_backup, "_JOB", {})
    monkeypatch.setattr(profile_backup, "create_backup", lambda data, destination, outside=(): {"name": "b.zip"})

    client_data_backup.backup()
    for thread in threading.enumerate():
        if thread.name == "row-bot-data-backup":
            thread.join(timeout=30)

    [event] = _outbox()
    assert (event["action"], event["outcome"], event["layer"]) == ("data.exported", "succeeded", "org")
    assert event["details"] == {"scope": "person"}
    assert event["target"]["kind"] == "export"


@pytest.fixture
def conversations(tmp_path, monkeypatch):
    """The chats and their attachments in the test's folder (as the conversation actions' own tests set them)."""
    from row_bot import agent_profiles, tasks, threads
    from row_bot.application.client_platform import ClientPlatformService, settle_background
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
    service = ClientPlatformService()
    service.registry = GenerationRuntimeRegistry()
    service.projection = ConversationProjection(service.registry.server_epoch)
    yield service
    service.registry.shutdown()
    assert settle_background(10), "Fixture left background work running"
    connection.close()


def test_a_chat_export_tells_the_company_one_chat_left_and_nothing_of_it(company_server, conversations):
    from langchain_core.messages import AIMessage, HumanMessage

    from row_bot import threads
    from row_bot.application.conversation_actions import execute_conversation_action, review_conversation_action

    company_server.join_here()
    conversation = threads.create_thread("Q4 salary plan", seed_default_skills=False)
    threads.append_checkpoint_messages(conversation, [HumanMessage(id="user", content=f"Summarise {SECRET}"),
                                                      AIMessage(id="assistant", content="Done.")])
    review = review_conversation_action(conversations, conversation, "conversation.export", "0", {},
                                        validate=lambda: None)
    command = {"command_id": str(uuid4()), "client_session_id": str(uuid4()), "type": "conversation.export",
               "expected_revision": review["revision"],
               "payload": {"checkpoint_revision": review["checkpoint_revision"],
                           "action_digest": review["action_digest"], "export_title": review["fields"]["title"]}}

    result = execute_conversation_action(conversations, conversation, command, owner_id="local-owner",
                                         key=command["command_id"], validate=lambda: None,
                                         validate_review=lambda _review: None)

    assert result["status"] == "completed"
    [event] = _outbox()
    assert (event["action"], event["outcome"]) == ("data.exported", "succeeded")
    assert event["details"] == {"scope": "person", "item_count": 1}
    text = audit.outbox_path().read_text(encoding="utf-8")
    assert conversation not in text and "Q4" not in text and SECRET not in text


def test_a_lockdown_is_told_once_it_ends_and_then_marked_sent(company_server, clock):
    company_server.policy["offline"]["max_days"] = 2
    record = company_server.join_here()
    confirmed = clock[0]
    company_server.offline = True
    clock[0] = confirmed + 2 * DAY + 1
    assert policy.state().state == "locked_down"
    assert company_server.timers == []  # Nothing to tell while it can't be reached.

    company_server.offline = False
    clock[0] += 60
    assert policy.refresh().state == "current"  # Back: the lockdown ended, and a send is started soon after.
    [timer] = company_server.timers
    assert timer.started and timer.interval == audit.SOON_SECONDS
    timer.fire()

    [event] = _sent(company_server)
    assert (event["action"], event["outcome"], event["layer"]) == ("device.locked_down", "succeeded", "org")
    assert event["target"] == {"kind": "device", "ref": record.device_id}
    assert event["details"] == {"offline_days": 2}
    assert event["occurred_at"] == format_time(datetime.fromtimestamp(confirmed + 2 * DAY, timezone.utc))
    assert policy.lockdowns() == []  # Marked sent.
    assert len(company_server.timers) == 1  # Nothing left to send.


# -- the outbox and sending ---------------------------------------------------------------------------

def test_a_full_outbox_drops_its_oldest_events_and_the_next_send_counts_them(company_server, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="row_bot.company.audit")
    company_server.join_here()
    monkeypatch.setattr(audit, "MAX_EVENTS", 3)
    for count in range(1, 6):
        audit.data_exported(item_count=count)

    assert [event["details"]["item_count"] for event in _outbox()] == [3, 4, 5]
    full = [entry for entry in caplog.records if "outbox is full" in entry.getMessage()]
    assert len(full) == 1 and full[0].levelno == logging.WARNING

    audit.send_now()
    assert any("2 older ones were dropped" in entry.getMessage() for entry in caplog.records)
    assert len(_sent(company_server)) == 3

    # The size bound too: room for two events, so a third drops the oldest.
    audit.forget()
    monkeypatch.setattr(audit, "MAX_EVENTS", 10_000)
    audit.data_exported(item_count=1)
    line = audit.outbox_path().stat().st_size
    monkeypatch.setattr(audit, "MAX_BYTES", 2 * line + 1)
    audit.data_exported(item_count=2)
    audit.data_exported(item_count=3)
    assert [event["details"]["item_count"] for event in _outbox()] == [2, 3]


def test_batches_hold_at_most_100_events_and_256_kib(company_server, monkeypatch):
    company_server.join_here()
    for _ in range(250):
        audit.data_exported(item_count=1)
    audit.send_now()
    assert [len(_batch_ids(body)) for body in company_server.audit_batches] == [100, 100, 50]
    assert all(len(body) <= 256 * 1024 for body in company_server.audit_batches)
    assert len(_sent(company_server)) == 250

    # A smaller byte limit: as many whole events as fit.
    company_server.audit_batches.clear()
    for _ in range(7):
        audit.data_exported(item_count=1)
    line = len(audit.outbox_path().read_bytes()) // 7 - 1  # One event's JSON, without its newline.
    limit = len(b'{"events":[]}') + 3 * line + 2
    monkeypatch.setattr(audit, "BATCH_BYTES", limit)
    audit.send_now()
    assert [len(_batch_ids(body)) for body in company_server.audit_batches] == [3, 3, 1]
    assert all(len(body) <= limit for body in company_server.audit_batches)


def test_each_event_keeps_its_id_across_a_failed_send_and_the_retry(company_server):
    company_server.join_here()
    audit.data_exported(item_count=1)
    audit.data_exported(item_count=2)
    before = audit.waiting_ids()

    company_server.audit_answers.append(httpx.Response(503, json={"error": "unavailable"}))
    audit.send_now()
    assert audit.waiting_ids() == before and company_server.audit == {}

    audit.send_now()
    assert [_batch_ids(body) for body in company_server.audit_batches] == [before, before]
    assert list(company_server.audit) == before
    assert audit.waiting_ids() == []


def test_an_event_the_company_refuses_is_dropped_and_the_rest_are_sent(company_server, caplog):
    caplog.set_level(logging.INFO, logger="row_bot.company.audit")
    company_server.join_here()
    for count in (1, 2, 3):
        audit.data_exported(item_count=count)
    first, refused, third = audit.waiting_ids()
    company_server.audit_answers.append(httpx.Response(
        422, json={"error": "invalid_event", "index": 1, "detail": "details.item_count: must be an integer"}))

    audit.send_now()

    assert _batch_ids(company_server.audit_batches[1]) == [first, third]
    assert list(company_server.audit) == [first, third]
    assert audit.waiting_ids() == []
    [line] = [entry.getMessage() for entry in caplog.records if "refused audit event" in entry.getMessage()]
    assert "event 1" in line and "invalid_event" in line


def test_a_5xx_or_a_429_backs_off_and_keeps_the_events(company_server, monkeypatch):
    moment = [1000.0]
    monkeypatch.setattr(audit, "monotonic", lambda: moment[0])
    company_server.join_here()
    audit.data_exported(item_count=1)
    [kept] = audit.waiting_ids()
    assert company_server.timers[-1].interval == audit.SOON_SECONDS  # Soon after the event.

    def fail_with(answer: httpx.Response) -> float:
        audit.stop()
        company_server.audit_answers.append(answer)
        audit.send_now()
        audit.send_soon()
        return company_server.timers[-1].interval

    assert fail_with(httpx.Response(503)) == 60
    assert fail_with(httpx.Response(502)) == 120
    assert fail_with(httpx.Response(429, headers={"Retry-After": "300"}, json={"error": "busy"})) == 300
    assert audit.waiting_ids() == [kept] and company_server.audit == {}

    monkeypatch.setattr(client, "_backoff_until", 0.0)  # The server's wait is over.
    audit.stop()
    audit.send_now()
    assert list(company_server.audit) == [kept]
    audit.data_exported(item_count=2)
    assert fail_with(httpx.Response(500)) == 60  # A success starts the back-off again from a minute.


def test_leave_sends_what_is_waiting_then_deletes_the_outbox(company_server):
    company_server.join_here()
    audit.data_exported(item_count=1)
    [kept] = audit.waiting_ids()

    client_company.leave()

    assert list(company_server.audit) == [kept]
    paths = [request.url.path for request in company_server.requests]
    assert paths[-2:] == [AUDIT_INGEST, "/api/device/leave"]
    assert not audit.outbox_path().exists()


def test_leave_while_the_company_cant_be_reached_still_deletes_the_outbox(company_server):
    company_server.join_here()
    audit.data_exported(item_count=1)
    company_server.offline = True

    view = client_company.leave()

    assert view["notice"] == "left_offline"
    assert company_server.audit == {}
    assert not audit.outbox_path().exists()


def test_a_disconnected_row_bot_keeps_its_events_for_the_same_device_and_a_fresh_join_starts_empty(company_server):
    record = company_server.join_here()
    audit.data_exported(item_count=1)
    [kept] = audit.waiting_ids()

    company_server.revoke(record.device_id)
    audit.send_now()
    assert store.read_record().disconnected
    assert audit.waiting_ids() == [kept]
    audit.stop()
    started = len(company_server.timers)
    audit.send_soon()
    assert len(company_server.timers) == started  # Disconnected: nothing is sent.

    # The same device joins again (Sign in again keeps its id): its events go.
    company_server.devices[record.device_id]["revoked"] = False
    issued = company_server._issue(record.device_id)
    store.save_credential(store.Credential(issued["access_token"], issued["refresh_token"], client.now() + 3600))
    store.write_record(record)
    audit.send_now()
    assert list(company_server.audit) == [kept]

    # A fresh join, as another device: what the first one left waiting isn't carried over.
    audit.data_exported(item_count=2)
    fresh = company_server.join_here()
    assert fresh.device_id != record.device_id
    assert audit.waiting_ids() == []
    audit.data_exported(item_count=3)
    [event] = _outbox()
    assert (event["device_id"], event["details"]["item_count"]) == (fresh.device_id, 3)


# -- never content, and nothing without a company ------------------------------------------------------

def test_the_helpers_take_no_summary_or_arguments_and_no_free_text(company_server):
    company_server.join_here()
    fields = {"tool": "delete_file", "outcome": "approved_by_you", "decided_by": "person",
              "approval_level": "approve", "surface": "chat"}

    with pytest.raises(TypeError):
        audit.tool_called(**fields, summary=f"Deleted {SECRET}")
    with pytest.raises(TypeError):
        audit.tool_called(**fields, arguments={"path": SECRET})
    with pytest.raises(TypeError):
        audit.data_exported(title="Q4 salary plan")
    for name, value in (("tool", "Delete salary-review 2026"), ("surface", "my salary chat"),
                        ("approval_level", "salary"), ("outcome", "Deleted the salary sheet")):
        with pytest.raises(InvalidAuditEvent):
            audit.tool_called(**{**fields, name: value})
    assert not audit.outbox_path().exists()

    # An app name that isn't an identifier is left out, never sent as text.
    assert audit.tool_called(**fields, app="Ada's salary server")
    [event] = _outbox()
    assert event["details"] == {"approval_level": "approve", "surface": "chat"}
    assert (event["redaction"], event["outcome"], event["decided_by"]) == ("metadata", "succeeded", "person")
    assert "salary" not in audit.outbox_path().read_text(encoding="utf-8")


def test_with_no_company_joined_nothing_is_recorded_kept_or_sent(company_server, written):
    audit.outbox_path().parent.mkdir(parents=True, exist_ok=True)

    _gated("n1", answer=True)
    rows, _next = activity_log.query(limit=10)
    assert [row["outcome"] for row in rows] == ["approved_by_you"]  # The person's own log, as always.
    assert not audit.data_exported(item_count=1)
    assert not audit.tool_called(tool="delete_file", outcome="approved_by_you", decided_by="person",
                                 approval_level="approve", surface="chat")
    audit.send_soon()
    audit.send_now()
    audit.leave()

    assert not audit.outbox_path().exists()
    assert audit.waiting_ids() == []
    assert company_server.requests == [] and company_server.timers == []
