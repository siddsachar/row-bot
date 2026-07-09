from __future__ import annotations

import asyncio
from typing import Any

import pytest

from row_bot.channels.discord_channel import (
    DISCORD_MAX_LEN,
    DiscordStreamTransport,
    _discord_goal_callbacks,
)
from row_bot.channels.streaming import ChannelDeliveryResult, ChannelStreamConfig, ChannelStreamConsumer


pytestmark = pytest.mark.subsystem


class FakeDiscordMessage:
    def __init__(self, channel: "FakeDiscordChannel", content: str) -> None:
        self.channel = channel
        self.content = content
        self.id = len(channel.messages) + 1
        self.fail_final_edit = False

    async def edit(self, *, content: str, **kwargs: Any) -> None:
        if self.fail_final_edit and "final" in content:
            raise RuntimeError("edit failed")
        self.content = content
        self.channel.operations.append(("edit", {"content": content, **kwargs}))

    async def delete(self) -> None:
        self.channel.operations.append(("delete", self.id))


class FakeDiscordChannel:
    def __init__(self) -> None:
        self.messages: list[FakeDiscordMessage] = []
        self.operations: list[tuple[str, Any]] = []
        self.fail_next_message_final_edit = False

    async def trigger_typing(self) -> None:
        self.operations.append(("typing", None))

    async def send(self, content: str = "", **kwargs: Any) -> FakeDiscordMessage:
        message = FakeDiscordMessage(self, content)
        message.fail_final_edit = self.fail_next_message_final_edit
        self.fail_next_message_final_edit = False
        self.messages.append(message)
        self.operations.append(("send", {"content": content, **kwargs}))
        return message


async def _events(*items: tuple[str, Any]):
    for item in items:
        yield item


def _consumer(channel: FakeDiscordChannel, *, typing_interval_s: float | None = None):
    return ChannelStreamConsumer(
        DiscordStreamTransport(channel),
        ChannelStreamConfig(
            channel="discord",
            transport_mode="edit",
            update_interval_s=999.0,
            min_update_chars=999,
            typing_interval_s=typing_interval_s,
            cursor="...",
            max_message_units=DISCORD_MAX_LEN,
            sparse_progress=True,
        ),
    )


def test_discord_edit_stream_updates_and_finalizes() -> None:
    async def run_case():
        channel = FakeDiscordChannel()
        result = await _consumer(channel).consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, channel.operations

    result, operations = asyncio.run(run_case())

    assert result.delivered is True
    assert result.transport == "discord:edit"
    assert [op for op, _payload in operations] == ["send", "edit", "edit"]


def test_discord_typing_keepalive_is_scheduled() -> None:
    async def run_case():
        channel = FakeDiscordChannel()
        consumer = _consumer(channel, typing_interval_s=1.0)
        await consumer.consume_events(_events(("token", "partial")), final_text="final")
        return channel.operations

    operations = asyncio.run(run_case())

    assert [op for op, _payload in operations].count("typing") >= 1


def test_discord_long_output_splits_and_cleans_preview() -> None:
    async def run_case():
        channel = FakeDiscordChannel()
        result = await _consumer(channel).consume_events(
            _events(("token", "short")),
            final_text="x" * (DISCORD_MAX_LEN + 10),
        )
        return result, channel.operations

    result, operations = asyncio.run(run_case())

    assert result.delivered is True
    assert result.fallback_sent is True
    assert [op for op, _payload in operations].count("send") == 3
    assert operations[-1][0] == "delete"


def test_discord_final_edit_failure_falls_back_to_send() -> None:
    async def run_case():
        channel = FakeDiscordChannel()
        channel.fail_next_message_final_edit = True
        result = await _consumer(channel).consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, channel.operations

    result, operations = asyncio.run(run_case())

    assert result.delivered is True
    assert result.fallback_sent is True
    assert [op for op, _payload in operations].count("send") == 2


def test_discord_goal_callback_preserves_no_duplicate_final(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import discord_channel

    channel = FakeDiscordChannel()

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
                transport="discord:edit",
                final_text="goal answer",
            ),
        )

    monkeypatch.setattr(discord_channel, "_stream_agent_turn_to_discord", streamed_turn)
    run_turn, send_text = _discord_goal_callbacks(channel)

    async def run_case():
        answer, _interrupt = await run_turn("prompt", {"configurable": {"thread_id": "t"}})
        await send_text(answer)

    asyncio.run(run_case())

    assert channel.messages == []


def test_discord_interrupt_card_stores_pending_and_sends_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import discord_channel

    channel = FakeDiscordChannel()
    channel.id = 42
    interrupt = [{"__interrupt_id": "i1", "tool": "shell", "description": "Run command"}]
    config = {"configurable": {"thread_id": "thread-1"}}
    fake_view = object()

    monkeypatch.setattr(discord_channel, "_make_discord_interrupt_view", lambda: fake_view)

    async def run_case():
        with discord_channel._pending_lock:
            discord_channel._pending_interrupts.clear()
            discord_channel._interrupt_by_message.clear()
            discord_channel._interrupt_by_channel.clear()
        message_id = await discord_channel._send_discord_interrupt_approval_card(
            channel,
            interrupt,
            config,
        )
        with discord_channel._pending_lock:
            pending = dict(discord_channel._pending_interrupts)
            by_message = dict(discord_channel._interrupt_by_message)
            by_channel = dict(discord_channel._interrupt_by_channel)
            discord_channel._pending_interrupts.clear()
            discord_channel._interrupt_by_message.clear()
            discord_channel._interrupt_by_channel.clear()
        return message_id, pending, by_message, by_channel

    message_id, pending, by_message, by_channel = asyncio.run(run_case())

    assert message_id == 1
    assert len(pending) == 1
    context_key, stored = next(iter(pending.items()))
    assert stored["data"] == interrupt
    assert stored["config"] == config
    assert by_message == {1: context_key}
    assert by_channel == {42: context_key}
    assert channel.operations[0][0] == "send"
    assert "needs your approval" in channel.operations[0][1]["content"]
    assert channel.operations[0][1]["view"] is fake_view


def test_discord_interrupt_button_resumes_shared_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import discord_channel

    channel = FakeDiscordChannel()
    channel.id = 42
    message = FakeDiscordMessage(channel, "approval")
    message.id = 77
    interrupt = [
        {"__interrupt_id": "i1", "tool": "shell", "description": "Run command"},
        {"__interrupt_id": "i2", "tool": "shell", "description": "Run command 2"},
    ]
    config = {"configurable": {"thread_id": "thread-1"}}
    calls: list[tuple[bool, list[str] | None]] = []

    discord_channel._store_discord_interrupt(
        channel_id=42,
        interrupt_data=interrupt,
        config=config,
        message_id=77,
    )

    async def streamed_resume(
        _channel,
        _config: dict,
        approved: bool,
        *,
        interrupt_ids: list[str] | None = None,
    ):
        calls.append((approved, interrupt_ids))
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
                transport="discord:edit",
                final_text="resumed answer",
            ),
        )

    monkeypatch.setattr(discord_channel, "_stream_agent_resume_to_discord", streamed_resume)
    monkeypatch.setattr(discord_channel.ch_runtime, "resolve_goal_approval_for_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(discord_channel.ch_runtime, "thread_id_from_config", lambda _config: None)

    asyncio.run(discord_channel._handle_discord_interrupt_button(channel, message, approved=True))

    assert calls == [(True, ["i1", "i2"])]
    assert channel.operations[0] == (
        "edit",
        {"content": "Approved - processing...", "view": None},
    )
    with discord_channel._pending_lock:
        assert discord_channel._pending_interrupts == {}
        assert discord_channel._interrupt_by_message == {}


def test_discord_streamed_turn_invokes_channel_checkpoint_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from row_bot.channels import discord_channel

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

    monkeypatch.setattr(discord_channel.ch_runtime, "persist_channel_assistant_message", fake_persist)
    monkeypatch.setattr(discord_channel, "_run_agent_sync", fake_run)

    async def run_case():
        channel = FakeDiscordChannel()
        return await discord_channel._stream_agent_turn_to_discord(
            channel,
            "question",
            {"configurable": {"thread_id": "thread-1"}},
        )

    answer, _interrupt, _images, _videos, delivery = asyncio.run(run_case())

    assert answer == "partial answer\n\nWarning: Error: provider disconnected"
    assert delivery.delivered is True
    assert calls == [
        ("partial answer\n\nWarning: Error: provider disconnected", "discord", True)
    ]
