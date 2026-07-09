from __future__ import annotations

import asyncio
from typing import Any

import pytest

from row_bot.channels.streaming import ChannelStreamConfig, ChannelStreamConsumer
from row_bot.channels.whatsapp import WA_MAX_MESSAGE_LEN, WhatsAppStreamTransport


pytestmark = pytest.mark.subsystem


async def _events(*items: tuple[str, Any]):
    for item in items:
        yield item


def _patch_bridge(monkeypatch: pytest.MonkeyPatch):
    from row_bot.channels import whatsapp

    operations: list[tuple[str, Any]] = []

    def send_message(chat_id: str, text: str, *, wait_key: bool = False):
        operations.append(("send", {"chat_id": chat_id, "text": text, "wait_key": wait_key}))
        return {"id": f"msg-{len(operations)}"} if wait_key else None

    def edit_message(chat_id: str, msg_key: dict, text: str) -> None:
        operations.append(("edit", {"chat_id": chat_id, "msg_key": msg_key, "text": text}))

    def send_and_wait(method: str, params: dict | None = None, timeout: float = 15.0):
        operations.append(("wait", {"method": method, "params": params, "timeout": timeout}))
        return {"ok": True, "msgKey": {"id": f"wait-{len(operations)}"}}

    def typing(chat_id: str, presence: str = "composing") -> None:
        operations.append(("typing", {"chat_id": chat_id, "presence": presence}))

    monkeypatch.setattr(whatsapp, "_send_message_sync", send_message)
    monkeypatch.setattr(whatsapp, "_edit_message_sync", edit_message)
    monkeypatch.setattr(whatsapp, "_send_and_wait", send_and_wait)
    monkeypatch.setattr(whatsapp, "_typing_sync", typing)
    return operations


def _consumer():
    return ChannelStreamConsumer(
        WhatsAppStreamTransport("chat-1"),
        ChannelStreamConfig(
            channel="whatsapp",
            transport_mode="edit",
            update_interval_s=999.0,
            min_update_chars=999,
            typing_interval_s=1.0,
            cursor="...",
            max_message_units=WA_MAX_MESSAGE_LEN,
            sparse_progress=True,
        ),
    )


def test_whatsapp_edit_stream_updates_and_finalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    operations = _patch_bridge(monkeypatch)

    async def run_case():
        return await _consumer().consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )

    result = asyncio.run(run_case())

    assert result.delivered is True
    assert result.transport == "whatsapp:edit"
    assert [op for op, _payload in operations] == ["typing", "send", "edit", "wait"]


def test_whatsapp_long_output_uses_final_send_split(monkeypatch: pytest.MonkeyPatch) -> None:
    operations = _patch_bridge(monkeypatch)

    async def run_case():
        return await _consumer().consume_events(
            _events(("token", "short")),
            final_text="x" * (WA_MAX_MESSAGE_LEN + 25),
        )

    result = asyncio.run(run_case())

    assert result.delivered is True
    assert result.fallback_sent is True
    assert [op for op, _payload in operations].count("send") == 3


def test_whatsapp_streamed_turn_invokes_channel_checkpoint_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from row_bot.channels import whatsapp

    _patch_bridge(monkeypatch)
    calls: list[tuple[str, str, bool]] = []

    def fake_persist(config, text: str, *, channel_name: str, delivery) -> bool:
        calls.append((str(text), channel_name, bool(delivery.delivered)))
        return True

    def fake_run(_user_text: str, _config: dict, event_queue) -> tuple[str, None, list, list]:
        answer = "partial answer"
        event_queue.put(("token", "partial answer"))
        event_queue.put(None)
        return answer, None, [], []

    monkeypatch.setattr(whatsapp.ch_runtime, "persist_channel_assistant_message", fake_persist)
    monkeypatch.setattr(whatsapp, "_run_agent_sync", fake_run)

    async def run_case():
        return await whatsapp._stream_agent_turn_to_whatsapp(
            "chat-1",
            "question",
            {"configurable": {"thread_id": "thread-1"}},
        )

    answer, _interrupt, _images, _videos, delivery = asyncio.run(run_case())

    assert answer == "partial answer"
    assert delivery.delivered is True
    assert calls == [("partial answer", "whatsapp", True)]
