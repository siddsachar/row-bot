from __future__ import annotations

import asyncio
from typing import Any

import pytest

from row_bot.channels.slack import SLACK_MAX_MSG_LEN, SlackStreamTransport, _slack_goal_callbacks
from row_bot.channels.streaming import ChannelDeliveryResult, ChannelStreamConfig, ChannelStreamConsumer


pytestmark = pytest.mark.subsystem


class FakeSlackError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        if retry_after is not None:
            self.retry_after = retry_after


class FakeSlackClient:
    def __init__(self, *, native: bool = False) -> None:
        self.native = native
        self.operations: list[tuple[str, dict[str, Any]]] = []
        self.fail_next_update: Exception | None = None
        if native:
            self.chat_startStream = self._chat_startStream
            self.chat_appendStream = self._chat_appendStream
            self.chat_stopStream = self._chat_stopStream

    async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("post", kwargs))
        return {"channel": kwargs.get("channel"), "ts": f"ts-{len(self.operations)}"}

    async def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_next_update is not None:
            exc = self.fail_next_update
            self.fail_next_update = None
            raise exc
        self.operations.append(("update", kwargs))
        return {"ok": True, "ts": kwargs.get("ts")}

    async def chat_delete(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("delete", kwargs))
        return {"ok": True}

    async def _chat_startStream(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("start_stream", kwargs))
        return {"ts": "stream-ts"}

    async def _chat_appendStream(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("append_stream", kwargs))
        return {"ok": True}

    async def _chat_stopStream(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("stop_stream", kwargs))
        return {"ok": True}

    async def files_upload_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("file", kwargs))
        return {"ok": True}


async def _events(*items: tuple[str, Any]):
    for item in items:
        yield item


def _consumer(client: FakeSlackClient, *, mode: str = "auto", thread_ts: str | None = "root-ts"):
    transport = SlackStreamTransport(
        client,
        channel_id="C1",
        transport_mode=mode,
        thread_ts=thread_ts,
    )
    return ChannelStreamConsumer(
        transport,
        ChannelStreamConfig(
            channel="slack",
            transport_mode=mode,
            update_interval_s=999.0,
            min_update_chars=999,
            typing_interval_s=None,
            cursor="...",
            max_message_units=SLACK_MAX_MSG_LEN,
            sparse_progress=True,
        ),
    )


def test_slack_auto_selects_native_stream_when_supported() -> None:
    async def run_case():
        client = FakeSlackClient(native=True)
        result = await _consumer(client, mode="auto").consume_events(
            _events(("token", "partial")),
            final_text="partial final",
        )
        return result, client.operations

    result, operations = asyncio.run(run_case())

    assert result.delivered is True
    assert result.transport == "slack:native"
    assert [op for op, _kwargs in operations] == [
        "start_stream",
        "append_stream",
        "stop_stream",
    ]


def test_slack_native_capability_failure_falls_back_to_edit() -> None:
    async def run_case():
        client = FakeSlackClient(native=False)
        result = await _consumer(client, mode="native").consume_events(
            _events(("token", "partial")),
            final_text="final",
        )
        return result, client.operations

    result, operations = asyncio.run(run_case())

    assert result.transport == "slack:edit"
    assert [op for op, _kwargs in operations] == ["post", "update", "update"]


def test_slack_edit_rate_limit_retry_is_honored() -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def run_case():
        client = FakeSlackClient(native=False)
        client.fail_next_update = FakeSlackError("ratelimited", retry_after=3.0)
        consumer = _consumer(client, mode="edit")
        consumer._sleep = fake_sleep
        result = await consumer.consume_events(_events(("token", "partial")), final_text="final")
        return result, client.operations

    result, operations = asyncio.run(run_case())

    assert result.delivered is True
    assert sleeps == [3.0]
    assert [op for op, _kwargs in operations].count("update") == 2


def test_slack_long_output_uses_final_send_split_and_preview_cleanup() -> None:
    async def run_case():
        client = FakeSlackClient(native=False)
        result = await _consumer(client, mode="edit").consume_events(
            _events(("token", "short")),
            final_text="x" * (SLACK_MAX_MSG_LEN + 10),
        )
        return result, client.operations

    result, operations = asyncio.run(run_case())

    assert result.delivered is True
    assert result.fallback_sent is True
    assert [op for op, _kwargs in operations].count("post") == 3
    assert [op for op, _kwargs in operations][-1] == "delete"


def test_slack_goal_callback_preserves_no_duplicate_final(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import slack

    client = FakeSlackClient(native=False)
    said: list[str] = []

    async def say(text: str, **_kwargs: Any) -> None:
        said.append(text)

    async def streamed_turn(*_args: Any, **_kwargs: Any):
        return (
            "goal answer",
            None,
            [],
            [],
            ChannelDeliveryResult(
                delivered=True,
                streamed=True,
                finalized=True,
                fallback_sent=False,
                transport="slack:edit",
                final_text="goal answer",
            ),
        )

    monkeypatch.setattr(slack, "_stream_agent_turn_to_slack", streamed_turn)
    run_turn, send_text = _slack_goal_callbacks(client, say, channel_id="C1")

    async def run_case():
        answer, _interrupt = await run_turn("prompt", {"configurable": {"thread_id": "t"}})
        await send_text(answer)

    asyncio.run(run_case())

    assert said == []


def test_slack_interrupt_card_stores_pending_and_sends_buttons() -> None:
    from row_bot.channels import slack

    client = FakeSlackClient(native=False)
    said: list[tuple[str, dict[str, Any]]] = []
    interrupt = [
        {"__interrupt_id": "i1", "tool": "shell", "description": "Run command"},
        {"__interrupt_id": "i2", "tool": "shell", "description": "Run command 2"},
    ]
    config = {"configurable": {"thread_id": "thread-1"}}

    async def say(text: str, **kwargs: Any) -> None:
        said.append((text, kwargs))

    async def run_case():
        with slack._pending_lock:
            slack._pending_interrupts.clear()
            slack._interrupt_by_message.clear()
            slack._interrupt_by_channel.clear()
        msg_ts = await slack._send_slack_interrupt_approval_card(
            client,
            say,
            channel_id="C1",
            interrupt_data=interrupt,
            config=config,
            thread_ts="root-ts",
        )
        with slack._pending_lock:
            pending = dict(slack._pending_interrupts)
            by_message = dict(slack._interrupt_by_message)
            by_channel = dict(slack._interrupt_by_channel)
            slack._pending_interrupts.clear()
            slack._interrupt_by_message.clear()
            slack._interrupt_by_channel.clear()
        return msg_ts, pending, by_message, by_channel

    msg_ts, pending, by_message, by_channel = asyncio.run(run_case())

    assert said == []
    assert msg_ts == "ts-1"
    assert len(pending) == 1
    context_key, stored = next(iter(pending.items()))
    assert stored["data"] == interrupt
    assert stored["config"] == config
    assert stored["thread_ts"] == "root-ts"
    assert by_message == {"ts-1": context_key}
    assert by_channel == {"C1": context_key}
    op, kwargs = client.operations[0]
    assert op == "post"
    assert kwargs["channel"] == "C1"
    assert kwargs["thread_ts"] == "root-ts"
    assert [button["action_id"] for button in kwargs["blocks"][1]["elements"]] == [
        "interrupt_approve",
        "interrupt_deny",
    ]


def test_slack_interrupt_button_resumes_shared_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import slack

    client = FakeSlackClient(native=False)
    interrupt = [
        {"__interrupt_id": "i1", "tool": "shell", "description": "Run command"},
        {"__interrupt_id": "i2", "tool": "shell", "description": "Run command 2"},
    ]
    config = {"configurable": {"thread_id": "thread-1"}}
    calls: list[tuple[bool, list[str] | None, str | None]] = []

    slack._store_slack_interrupt(
        channel_id="C1",
        interrupt_data=interrupt,
        config=config,
        thread_ts="root-ts",
        message_ts="approval-ts",
    )

    async def streamed_resume(
        _client,
        _channel_id: str,
        _config: dict,
        approved: bool,
        *,
        interrupt_ids: list[str] | None = None,
        thread_ts: str | None = None,
    ):
        calls.append((approved, interrupt_ids, thread_ts))
        return (
            "resumed answer",
            None,
            [],
            [],
            ChannelDeliveryResult(
                delivered=True,
                streamed=True,
                finalized=True,
                fallback_sent=False,
                transport="slack:edit",
                final_text="resumed answer",
            ),
        )

    monkeypatch.setattr(slack, "_stream_agent_resume_to_slack", streamed_resume)
    monkeypatch.setattr(slack.ch_runtime, "resolve_goal_approval_for_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(slack.ch_runtime, "thread_id_from_config", lambda _config: None)

    body = {
        "channel": {"id": "C1"},
        "message": {"ts": "approval-ts", "thread_ts": "root-ts"},
    }
    asyncio.run(slack._handle_interrupt_button(body, client, approved=True))

    assert calls == [(True, ["i1", "i2"], "root-ts")]
    assert client.operations[0][0] == "update"
    assert client.operations[0][1]["text"] == "Approved - processing..."
    with slack._pending_lock:
        assert slack._pending_interrupts == {}
        assert slack._interrupt_by_message == {}


def test_slack_streamed_turn_invokes_channel_checkpoint_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from row_bot.channels import slack

    calls: list[tuple[str, str, bool]] = []

    def fake_persist(config, text: str, *, channel_name: str, delivery) -> bool:
        calls.append((str(text), channel_name, bool(delivery.delivered)))
        return True

    def fake_run(_user_text: str, _config: dict, event_queue) -> tuple[str, None, list, list]:
        answer = "partial answer\n\nWarning: Error: provider disconnected"
        event_queue.put(("token", "partial answer"))
        event_queue.put(("error", "provider disconnected"))
        event_queue.put(None)
        return answer, None, [], []

    monkeypatch.setattr(slack.ch_runtime, "persist_channel_assistant_message", fake_persist)
    monkeypatch.setattr(slack, "_run_agent_sync", fake_run)

    async def run_case():
        client = FakeSlackClient(native=False)
        return await slack._stream_agent_turn_to_slack(
            client,
            "C1",
            "question",
            {"configurable": {"thread_id": "thread-1"}},
        )

    answer, _interrupt, _images, _videos, delivery = asyncio.run(run_case())

    assert answer == "partial answer\n\nWarning: Error: provider disconnected"
    assert delivery.delivered is True
    assert calls == [
        ("partial answer\n\nWarning: Error: provider disconnected", "slack", True)
    ]
