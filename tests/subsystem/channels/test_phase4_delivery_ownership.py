from __future__ import annotations

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.fixtures.channels import FakeChannel
from tests.subsystem.channels.test_channel_thread_notifications import _fresh_modules

pytestmark = pytest.mark.subsystem


@pytest.fixture
def saved(tmp_path, monkeypatch):
    tasks, threads, _runs, registry, notifications = _fresh_modules(tmp_path, monkeypatch)
    channel = FakeChannel()
    channel._running = True
    registry.register(channel)
    yield tasks, threads, registry, notifications, channel
    registry._reset()


def intent(tasks, key="delivery-1", *, target="destination-1", text="Saved final"):
    return tasks.upsert_channel_thread_notification(
        key=key, thread_id="parent", channel="fake", target=target,
        kind="final", text=text,
    )


def test_atomic_claim_excludes_concurrent_duplicate_send(saved, monkeypatch):
    tasks, _threads, _registry, notifications, channel = saved
    record = intent(tasks)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def send(target, text):
        calls.append((target, text))
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(channel, "send_message", send)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(notifications._deliver_record, record)
        try:
            assert entered.wait(5)
            assert tasks.get_channel_thread_notification(record["key"])["status"] == "uncertain"
            assert workers.submit(notifications._deliver_record, record).result(5) is False
        finally:
            release.set()
        assert first.result(5) is True
    assert calls == [("destination-1", "Saved final")]
    assert tasks.get_channel_thread_notification(record["key"])["attempts"] == 1


@pytest.mark.parametrize("failure", ["transport_after_effect", "publication_exception", "publication_rejected"])
def test_ambiguous_send_never_replays_after_reload(saved, monkeypatch, failure):
    tasks, _threads, _registry, notifications, channel = saved
    record = intent(tasks)
    original_send = channel.send_message

    def send(target, text):
        original_send(target, text)
        if failure == "transport_after_effect":
            raise RuntimeError("synthetic connection lost after send")

    monkeypatch.setattr(channel, "send_message", send)
    if failure != "transport_after_effect":
        def publish(*_args, **_kwargs):
            if failure == "publication_exception":
                raise RuntimeError("synthetic commit failure")
            return False
        monkeypatch.setattr(tasks, "mark_channel_thread_notification_delivered", publish)
    assert notifications._deliver_record(record) is False
    assert tasks.get_channel_thread_notification(record["key"])["status"] == "uncertain"
    assert tasks.list_pending_channel_thread_notifications() == []
    notifications = importlib.reload(notifications)
    assert notifications.reconcile_pending_channel_notifications() == 0
    assert notifications._deliver_record(record) is False
    assert len(channel.messages) == 1


def test_pre_send_failure_is_retryable_but_old_ambiguous_failure_is_not(saved):
    tasks, _threads, _registry, notifications, channel = saved
    record = intent(tasks)
    channel._running = False
    assert notifications._deliver_record(record) is False
    row = tasks.get_channel_thread_notification(record["key"])
    assert row["status"] == "failed" and row["last_error"].startswith("not_sent: ")
    channel._running = True
    assert notifications.reconcile_pending_channel_notifications() == 1
    assert len(channel.messages) == 1
    legacy = intent(tasks, "legacy-failure")
    conn = tasks._get_conn()
    try:
        conn.execute("UPDATE channel_thread_notifications SET status='failed', last_error='timeout' WHERE key=?", (legacy["key"],))
        conn.commit()
    finally:
        conn.close()
    assert notifications.reconcile_pending_channel_notifications() == 0
    assert notifications._deliver_record(legacy) is False
    assert len(channel.messages) == 1


def test_stale_attempt_cannot_publish_or_reopen_new_attempt(saved):
    tasks, *_ = saved
    row = intent(tasks)
    first = tasks.claim_channel_thread_notification(row["key"])
    assert tasks.mark_channel_thread_notification_failed(row["key"], "offline", attempt=first["attempts"])
    second = tasks.claim_channel_thread_notification(row["key"])
    assert second["attempts"] == first["attempts"] + 1
    assert not tasks.mark_channel_thread_notification_delivered(row["key"], attempt=first["attempts"])
    assert not tasks.mark_channel_thread_notification_failed(row["key"], "offline", attempt=first["attempts"])
    assert tasks.mark_channel_thread_notification_delivered(row["key"], attempt=second["attempts"])
    assert tasks.claim_channel_thread_notification(row["key"]) is None


@pytest.mark.parametrize("field", ["text", "target"])
def test_identity_cannot_be_rebound_during_or_before_delivery(saved, field):
    tasks, *_ = saved
    original = intent(tasks)
    with pytest.raises(ValueError, match="already bound"):
        intent(tasks, **{field: "another"})
    claimed = tasks.claim_channel_thread_notification(original["key"])
    with pytest.raises(ValueError, match="already bound"):
        intent(tasks, **{field: "another"})
    assert tasks.get_channel_thread_notification(original["key"])[field] == original[field]
    assert tasks.get_channel_thread_notification(original["key"])["attempts"] == claimed["attempts"]


def test_stale_input_record_never_overrides_persisted_destination(saved):
    tasks, _threads, _registry, notifications, channel = saved
    record = intent(tasks)
    record.update(target="stale-target", text="stale-text")
    assert notifications._deliver_record(record)
    assert [(msg.target, msg.text) for msg in channel.messages] == [("destination-1", "Saved final")]


def test_reconciliation_preserves_destination_order_and_other_destinations_progress(saved, monkeypatch):
    tasks, _threads, _registry, notifications, channel = saved
    intent(tasks, "slow-1", target="slow", text="first")
    intent(tasks, "slow-2", target="slow", text="second")
    intent(tasks, "fast", target="fast")
    blocked, release, fast = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def send(target, text):
        calls.append((target, text))
        if target == "slow" and text == "first":
            blocked.set()
            assert release.wait(5)
        if target == "fast":
            fast.set()

    monkeypatch.setattr(channel, "send_message", send)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(notifications.reconcile_pending_channel_notifications)
        try:
            assert blocked.wait(5)
            assert fast.wait(5)
            assert not pending.done()
            assert notifications.reconcile_pending_channel_notifications() == 0
            assert ("slow", "second") not in calls
        finally:
            release.set()
        assert pending.result(5) == 3
    assert [text for target, text in calls if target == "slow"] == ["first", "second"]


def orchestrator_for(saved, monkeypatch):
    from row_bot import agent_orchestrator
    from tests.subsystem.agents.test_agent_orchestration import _orchestration

    module = importlib.reload(agent_orchestrator)
    monkeypatch.setattr(module, "_DELIVERY_EXECUTOR", None)
    return module, _orchestration(module)


def test_orchestration_dispatch_does_not_hold_global_lock(saved, monkeypatch):
    orchestrator, row = orchestrator_for(saved, monkeypatch)
    blocked, release, fast = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def deliver(_row, kind, _text, key):
        calls.append(key)
        if kind == "slow":
            blocked.set()
            assert release.wait(5)
        else:
            fast.set()
        return True

    monkeypatch.setattr(orchestrator, "_DELIVERY_EXECUTOR", deliver)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(orchestrator._deliver_once, row, kind="slow", text="same")
        try:
            assert blocked.wait(5)
            assert not orchestrator._deliver_once(row, kind="slow", text="same")
            other = workers.submit(orchestrator._deliver_once, row, kind="fast", text="other")
            assert fast.wait(5)
            assert other.result(5)
        finally:
            release.set()
        assert first.result(5)
    assert len(calls) == 2


def test_orchestration_exception_is_uncertain_without_blind_retry(saved, monkeypatch):
    orchestrator, row = orchestrator_for(saved, monkeypatch)
    calls = []

    def deliver(*args):
        calls.append(args)
        raise RuntimeError("effect may have happened")

    monkeypatch.setattr(orchestrator, "_DELIVERY_EXECUTOR", deliver)
    assert not orchestrator._deliver_once(row, kind="final", text="answer")
    message = orchestrator.list_messages(row["id"])[0]
    assert message["delivery_status"] == "uncertain"
    assert orchestrator.retry_pending_deliveries() == 0
    assert not orchestrator._deliver_once(row, kind="final", text="answer")
    assert len(calls) == 1


def test_downstream_receipt_repairs_orchestration_publication_without_resend(saved, monkeypatch):
    tasks, _threads, _registry, _notifications, channel = saved
    orchestrator, row = orchestrator_for(saved, monkeypatch)
    tasks.record_thread_channel_ref(row["parent_thread_id"], channel="fake", target="destination-1")
    original = orchestrator._complete_delivery_attempt

    def fail(*_args):
        raise RuntimeError("synthetic receipt publication failure")

    monkeypatch.setattr(orchestrator, "_complete_delivery_attempt", fail)
    assert not orchestrator._deliver_once(row, kind="final", text="answer")
    assert len(channel.messages) == 1
    assert orchestrator.list_messages(row["id"])[0]["delivery_status"] == "uncertain"
    monkeypatch.setattr(orchestrator, "_complete_delivery_attempt", original)
    assert orchestrator.retry_pending_deliveries() == 1
    assert len(channel.messages) == 1
    assert orchestrator.list_messages(row["id"])[0]["delivery_status"] == "delivered"


def test_orchestration_reconciliation_is_bounded_and_destinations_independent(saved, monkeypatch):
    tasks, *_ = saved
    orchestrator, first = orchestrator_for(saved, monkeypatch)
    second = orchestrator.create_or_get_orchestration(
        parent_thread_id="other-parent", parent_generation_id="other-generation",
        root_objective="Synthetic", model_ref="provider:model", approval_mode="block",
        runtime_surface="channel",
    )
    tasks.record_thread_channel_ref(first["parent_thread_id"], channel="fake", target="slow")
    tasks.record_thread_channel_ref(second["parent_thread_id"], channel="fake", target="fast")
    for row in (first, second):
        orchestrator.record_message(row["id"], kind="final", content="final",
                                    message_id=f"orchestration:{row['id']}:final")
    blocked, release, fast = threading.Event(), threading.Event(), threading.Event()

    def deliver(row, *_args):
        if row["id"] == first["id"]:
            blocked.set()
            assert release.wait(5)
        else:
            fast.set()
        return True

    monkeypatch.setattr(orchestrator, "_DELIVERY_EXECUTOR", deliver)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(orchestrator.retry_pending_deliveries)
        try:
            assert blocked.wait(5)
            assert fast.wait(5)
            assert not pending.done()
            assert orchestrator.retry_pending_deliveries() == 0
        finally:
            release.set()
        assert pending.result(5) == 2


def test_failed_claim_publication_never_dispatches(saved, monkeypatch):
    tasks, _threads, _registry, notifications, channel = saved
    row = intent(tasks)
    original = tasks.claim_channel_thread_notification

    def fail(key):
        original(key)
        raise RuntimeError("claim commit response lost")

    monkeypatch.setattr(tasks, "claim_channel_thread_notification", fail)
    assert not notifications._deliver_record(row)
    assert channel.messages == []
    monkeypatch.setattr(tasks, "claim_channel_thread_notification", original)
    assert notifications.reconcile_pending_channel_notifications() == 0
    assert tasks.get_channel_thread_notification(row["key"])["status"] == "uncertain"


def test_rebinding_key_to_other_thread_cannot_append_checkpoint(saved):
    tasks, threads, _registry, notifications, _channel = saved
    intent(tasks)
    assert not notifications.deliver_parent_thread_notification(
        key="delivery-1", thread_id="another-parent", kind="final", text="Saved final",
    )
    assert threads.get_latest_checkpoint_messages("another-parent") == []


@pytest.mark.parametrize("outcome", ["ack", "lost_ack", "rejected", "wrong_id", "write_failure"])
def test_whatsapp_outbound_requires_exact_bridge_ack_and_cleans_receipt_owner(monkeypatch, outcome):
    import json
    from types import SimpleNamespace
    from row_bot.channels import whatsapp
    from row_bot.channels.streaming import ChannelDeliveryUncertain

    writes = []

    class Event:
        signalled = False

        def set(self):
            self.signalled = True

        def wait(self, _timeout):
            command = json.loads(writes[-1])
            if outcome != "lost_ack":
                whatsapp._handle_bridge_message({
                    "type": "response", "id": command["id"] + (1 if outcome == "wrong_id" else 0),
                    "ok": outcome == "ack", "msgKey": {"id": "confirmed-message"},
                })
            return self.signalled

    def write(value):
        writes.append(value)
        if outcome == "write_failure":
            raise OSError("synthetic pipe failure")

    monkeypatch.setattr(whatsapp, "threading", SimpleNamespace(Event=Event))
    monkeypatch.setattr(whatsapp, "_pending_responses", {})
    monkeypatch.setattr(whatsapp, "_response_data", {})
    monkeypatch.setattr(whatsapp, "_running", True)
    monkeypatch.setattr(whatsapp, "_bridge_proc", SimpleNamespace(stdin=SimpleNamespace(write=write, flush=lambda: None)))
    if outcome == "ack":
        assert whatsapp.send_outbound("synthetic-chat", "answer") is None
    else:
        with pytest.raises(ChannelDeliveryUncertain):
            whatsapp.send_outbound("synthetic-chat", "answer")
    assert len(writes) == 1
    assert whatsapp._pending_responses == {} and whatsapp._response_data == {}
    # An ACK or error arriving after ownership retired cannot revive or replay it.
    command_id = json.loads(writes[0])["id"]
    whatsapp._handle_bridge_message({"type": "response", "id": command_id, "ok": True})
    whatsapp._handle_bridge_message({"type": "response", "id": command_id, "ok": False})
    assert whatsapp._pending_responses == {} and whatsapp._response_data == {}
    assert len(writes) == 1


@pytest.mark.parametrize("failure", ["lost_ack", "producer_failure"])
def test_whatsapp_inbound_chain_never_reruns_agent_or_repeats_uncertain_final(monkeypatch, failure):
    from row_bot.channels import whatsapp

    bridge_calls, agent_calls, saved = [], [], []
    monkeypatch.setattr(whatsapp, "_get_or_create_thread", lambda *_: "synthetic-thread")
    monkeypatch.setattr(whatsapp, "_typing_sync", lambda *_: None)
    monkeypatch.setattr(whatsapp, "_pending_interrupts", {})
    monkeypatch.setattr(whatsapp, "_pending_task_approvals", {})
    monkeypatch.setattr(whatsapp.ch_commands, "is_thread_scoped_command", lambda *_: False)
    monkeypatch.setattr(whatsapp.ch_commands, "dispatch", lambda *_, **__: None)
    monkeypatch.setattr(whatsapp.ch_runtime, "prepare_channel_goal_start", lambda *_: None)
    monkeypatch.setattr(whatsapp.ch_runtime, "persist_channel_assistant_message", lambda *args, **kwargs: saved.append((args, kwargs)))

    def bridge(method, params=None, timeout=15):
        bridge_calls.append((method, params))
        return None if failure == "lost_ack" else {"ok": True, "msgKey": {"id": "status-message"}}

    def producer(_text, _config, sink):
        agent_calls.append("started")
        if failure == "producer_failure":
            raise RuntimeError("synthetic failure after possible tool effect")
        sink.put(("token", "final answer"))
        return "final answer", None, [], []

    monkeypatch.setattr(whatsapp, "_send_and_wait", bridge)
    monkeypatch.setattr(whatsapp, "_run_agent_sync", producer)
    whatsapp._process_inbound({"from": "synthetic-chat", "body": "hello", "isSelfChat": True})
    assert agent_calls == ["started"]
    assert len(bridge_calls) == 1
    if failure == "lost_ack":
        assert saved[0][1]["delivery"].uncertain
        assert not saved[0][1]["delivery"].delivered
    else:
        assert "could not be confirmed" in bridge_calls[0][1]["text"]


@pytest.mark.parametrize("failure,expected_calls", [("lost_ack", 1), ("parse", 2), ("other_rejection", 1)])
def test_telegram_outbound_fallback_only_for_proven_parse_rejection(monkeypatch, failure, expected_calls):
    import asyncio
    from concurrent.futures import Future
    from types import SimpleNamespace
    from telegram.error import BadRequest
    from row_bot.channels import telegram

    calls = []
    error = TimeoutError("synthetic lost ACK") if failure == "lost_ack" else BadRequest(
        "Can't parse entities: unsupported start tag" if failure == "parse" else "Chat not found"
    )

    async def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise error
        return SimpleNamespace(message_id=7)

    def schedule(coro, _loop):
        result = Future()
        try:
            result.set_result(asyncio.run(coro))
        except Exception as exc:
            result.set_exception(exc)
        return result

    monkeypatch.setattr(telegram, "_running", True)
    monkeypatch.setattr(telegram, "_app", SimpleNamespace(bot=SimpleNamespace(send_message=send)))
    monkeypatch.setattr(telegram, "_bot_loop", SimpleNamespace(is_running=lambda: True))
    monkeypatch.setattr(telegram.asyncio, "run_coroutine_threadsafe", schedule)
    if failure == "parse":
        telegram.send_outbound(7, "**answer**")
        assert "parse_mode" not in calls[-1]
    else:
        with pytest.raises(type(error)):
            telegram.send_outbound(7, "**answer**")
    assert len(calls) == expected_calls
