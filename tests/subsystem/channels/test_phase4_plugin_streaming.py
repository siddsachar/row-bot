from __future__ import annotations

import asyncio
import contextvars
import queue
import threading

import pytest

from row_bot.cancellation import CancellationScope, current_cancellation_scope, use_cancellation_scope
from row_bot.channels.streaming import iter_channel_events

pytestmark = pytest.mark.subsystem


def test_slow_consumer_backpressures_producer_and_close_releases_full_buffer():
    reached_bound, closed = threading.Event(), threading.Event()
    produced = []

    def produce():
        try:
            for index in range(1000):
                produced.append(index)
                if index == 65:
                    reached_bound.set()
                yield "tool_call", str(index)
        finally:
            closed.set()

    async def scenario():
        stream = iter_channel_events(produce)
        assert await anext(stream) == ("tool_call", "0")
        assert await asyncio.to_thread(reached_bound.wait, 5)
        assert len(produced) == 66  # 64 waiting, one received, one backpressured.
        await stream.aclose()
        assert closed.is_set()
        assert len(produced) == 66

    asyncio.run(scenario())


def test_coalescing_preserves_complete_text_and_control_order():
    finished = threading.Event()
    approval = {"id": "approval-1", "args": {"path": "synthetic"}}

    def produce():
        for _ in range(1000):
            yield "token", "chunk"
        yield "interrupt", approval
        yield "compaction_succeeded", {"event_id": 7}
        yield "done", "chunk" * 1000
        finished.set()

    async def scenario():
        stream = iter_channel_events(produce)
        first = await anext(stream)
        assert await asyncio.to_thread(finished.wait, 5)
        events = [first] + [item async for item in stream]
        assert "".join(payload for kind, payload in events if kind == "token") == "chunk" * 1000
        assert len(events) < 20
        assert events[-3:] == [
            ("interrupt", approval), ("compaction_succeeded", {"event_id": 7}),
            ("done", "chunk" * 1000),
        ]

    asyncio.run(scenario())


def test_cancellation_keeps_ownership_until_producer_actually_returns():
    entered, cancelled, release, closed = [threading.Event() for _ in range(4)]
    marker = contextvars.ContextVar("channel-test-marker")
    marker.set("copied-context")

    def produce():
        assert marker.get() == "copied-context"
        scope = current_cancellation_scope()
        assert scope is not None
        scope.register(cancelled.set)
        try:
            entered.set()
            assert release.wait(5)
            yield "token", "must not escape after cancellation"
        finally:
            closed.set()

    async def scenario():
        async def consume():
            return [item async for item in iter_channel_events(produce)]

        task = asyncio.create_task(consume())
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        assert await asyncio.to_thread(cancelled.wait, 5)
        task.cancel()  # A second Stop cannot detach still-running work.
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_parent_scope_cancels_child_without_child_close_cancelling_parent():
    async def scenario():
        parent = CancellationScope()
        with use_cancellation_scope(parent):
            stream = iter_channel_events(lambda: iter([("token", "one"), ("done", "one")]))
            assert await anext(stream) == ("token", "one")
            await stream.aclose()
            assert not parent.is_cancelled()
            parent.cancel()
            cancelled_stream = iter_channel_events(lambda: pytest.fail("cancelled parent ran factory"))
            with pytest.raises(asyncio.CancelledError):
                await anext(cancelled_stream)

    asyncio.run(scenario())


def test_producer_error_closes_iterator_and_delivers_error_event():
    closed = threading.Event()

    def produce():
        try:
            yield "token", "partial"
            raise ValueError("synthetic failure")
        finally:
            closed.set()

    async def scenario():
        return [item async for item in iter_channel_events(produce)]

    assert asyncio.run(scenario()) == [("token", "partial"), ("error", "synthetic failure")]
    assert closed.is_set()


def test_ready_final_skips_preview_but_preserves_approval_and_context_notice(monkeypatch):
    from tests.subsystem.channels.test_channel_streaming_engine import FakeClock, FakeTransport, _consumer

    async def scenario():
        consumer = _consumer(FakeTransport(), FakeClock())
        observed = []

        async def notices():
            observed.extend(consumer.context_notices)

        monkeypatch.setattr(consumer, "_deliver_context_notices", notices)
        events = queue.Queue()
        events.put(("token", "preview"))
        events.put(("interrupt", {"approval": "preserved"}))
        events.put(("compaction_succeeded", {"event_id": 91}))
        events.put(None)
        final = asyncio.get_running_loop().create_future()
        final.set_result("authoritative final")
        result = await consumer.consume_queue(events, final_text_source=final)
        assert result.final_text == "authoritative final"
        assert consumer.interrupt_data == {"approval": "preserved"}
        assert observed == [{"event_id": 91, "display_copy": ""}]

    asyncio.run(scenario())


@pytest.mark.parametrize("channel,module_name", [
    ("telegram", "telegram"), ("discord", "discord_channel"),
    ("slack", "slack"), ("whatsapp", "whatsapp"),
])
@pytest.mark.parametrize("resume", [False, True])
def test_builtin_producer_preserves_authoritative_final_approval_media_and_resume(
    monkeypatch, channel, module_name, resume,
):
    import importlib
    from row_bot.channels.streaming import ChannelStreamConfig
    from tests.subsystem.channels.test_channel_streaming_engine import FakeTransport

    module = importlib.import_module(f"row_bot.channels.{module_name}")
    transport_name = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack", "whatsapp": "WhatsApp"}[channel]
    transport = FakeTransport()
    monkeypatch.setattr(module, f"{transport_name}StreamTransport", lambda *_args, **_kwargs: transport)
    monkeypatch.setattr(module, f"_{channel}_stream_config", lambda: ChannelStreamConfig(channel=channel, transport_mode="off"))
    saved = []
    monkeypatch.setattr(module.ch_runtime, "persist_channel_assistant_message", lambda *args, **kwargs: saved.append((args, kwargs)))
    expected = ("authoritative final", {"id": "approval-1"}, [b"fake-image"], ["synthetic-video.mp4"])

    def produce(sink):
        assert current_cancellation_scope() is not None
        sink.put(("token", "preview-only"))
        sink.put(("interrupt", expected[1]))
        sink.put(None)  # Existing collectors emit this before media capture.
        return expected

    def run(_text, _config, event_queue):
        return produce(event_queue)

    def resume_run(_config, approved, *, interrupt_ids, event_queue):
        assert approved is True and interrupt_ids == ["approval-1"]
        return produce(event_queue)

    monkeypatch.setattr(module, "_run_agent_sync", run)
    monkeypatch.setattr(module, "_resume_agent_sync", resume_run)
    config = {"configurable": {"thread_id": "synthetic-thread"}}
    args = [object()]
    if channel == "slack":
        args.append("synthetic-channel")
    args.extend([config, True] if resume else ["question", config])
    method = getattr(module, f"_stream_agent_{'resume' if resume else 'turn'}_to_{channel}")
    result = asyncio.run(method(*args, **({"interrupt_ids": ["approval-1"]} if resume else {})))
    assert result[:4] == expected
    assert result[4].delivered and not result[4].uncertain
    assert transport.operations == [("send_final", "authoritative final")]
    assert saved[0][1]["channel_name"] == channel


def test_push_producer_cancel_closes_full_buffer_before_adapter_returns(monkeypatch):
    from row_bot.channels import discord_channel
    from row_bot.channels.streaming import ChannelStreamConfig
    from tests.subsystem.channels.test_channel_streaming_engine import FakeTransport

    reached, closed = threading.Event(), threading.Event()
    started = None

    class SlowTransport(FakeTransport):
        async def start(self, text):
            started.set()
            await asyncio.Event().wait()

    def produce(_text, _config, sink):
        try:
            for index in range(1000):
                if index == 65:
                    reached.set()
                sink.put(("token", "x" * 16_384))
            pytest.fail("slow consumer did not backpressure")
        finally:
            closed.set()

    monkeypatch.setattr(discord_channel, "_run_agent_sync", produce)
    monkeypatch.setattr(discord_channel, "DiscordStreamTransport", lambda *_args: SlowTransport())
    monkeypatch.setattr(discord_channel, "_discord_stream_config", lambda: ChannelStreamConfig(channel="discord"))
    monkeypatch.setattr(discord_channel.ch_runtime, "persist_channel_assistant_message", lambda *_args, **_kwargs: pytest.fail("cancelled run published final"))

    async def scenario():
        nonlocal started
        started = asyncio.Event()
        task = asyncio.create_task(discord_channel._stream_agent_turn_to_discord(object(), "question", {}))
        await started.wait()
        assert await asyncio.to_thread(reached.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("explicit", [False, True])
def test_uncertain_effect_never_retries_from_message_or_retry_after_hint(explicit):
    from row_bot.channels.streaming import ChannelDeliveryUncertain
    from tests.subsystem.channels.test_channel_streaming_engine import FakeClock, FakeTransport, _consumer

    effects = []

    class UnconfirmedTransport(FakeTransport):
        async def update(self, handle, text, *, final=False):
            if final:
                effects.append(text)
                error_type = ChannelDeliveryUncertain if explicit else RuntimeError
                error = error_type("retry after lost ACK; rate flood")
                error.retry_after = 0.1
                raise error
            return await super().update(handle, text, final=final)

    async def scenario():
        clock, transport = FakeClock(), UnconfirmedTransport()
        result = await _consumer(transport, clock).consume_events([("token", "partial")], final_text="final")
        assert result.uncertain and not result.delivered and not result.fallback_sent
        assert effects == ["final"]
        assert clock.sleeps == []
        assert not any(kind == "send_final" for kind, _ in transport.operations)

    asyncio.run(scenario())
