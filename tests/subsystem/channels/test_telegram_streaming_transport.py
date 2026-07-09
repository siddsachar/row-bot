from __future__ import annotations

import asyncio
from typing import Any

import pytest

from row_bot.channels.streaming import ChannelStreamConfig, ChannelStreamConsumer
from row_bot.channels.telegram import (
    MAX_TG_MESSAGE_LEN,
    TelegramStreamTransport,
    _split_message,
    _telegram_text_units,
)


pytestmark = pytest.mark.subsystem


class FakeTelegramError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        if retry_after is not None:
            self.retry_after = retry_after


class FakeMessage:
    def __init__(self, chat: "FakeChat", text: str) -> None:
        self.chat = chat
        self.text = text
        self.message_id = len(chat.sent) + 100
        self.deleted = False
        self.fail_final_edit = False
        self.fail_not_modified = False

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        if self.fail_not_modified:
            raise FakeTelegramError("Message is not modified")
        if self.fail_final_edit and "final" in text:
            raise FakeTelegramError("final edit failed")
        self.text = text
        self.chat.operations.append(("edit", text, kwargs))

    async def delete(self) -> None:
        self.deleted = True
        self.chat.operations.append(("delete", self.message_id, {}))


class FakeChat:
    def __init__(
        self,
        *,
        chat_type: str = "private",
        draft_supported: bool = False,
    ) -> None:
        self.id = 123
        self.type = chat_type
        self.draft_supported = draft_supported
        self.sent: list[FakeMessage] = []
        self.drafts: list[str] = []
        self.draft_ids: list[int] = []
        self.operations: list[tuple[str, Any, dict[str, Any]]] = []
        self.fail_next_message_final_edit = False
        self.fail_next_message_not_modified = False

    async def send_action(self, action: str) -> None:
        self.operations.append(("typing", action, {}))

    async def send_message(self, text: str, **kwargs: Any) -> FakeMessage:
        message = FakeMessage(self, text)
        message.fail_final_edit = self.fail_next_message_final_edit
        message.fail_not_modified = self.fail_next_message_not_modified
        self.fail_next_message_final_edit = False
        self.fail_next_message_not_modified = False
        self.sent.append(message)
        self.operations.append(("send", text, kwargs))
        return message

    async def send_message_draft(
        self,
        draft_id: int,
        text: str | None = None,
        **kwargs: Any,
    ) -> None:
        if not self.draft_supported:
            raise AttributeError("draft unavailable")
        self.draft_ids.append(draft_id)
        self.drafts.append(str(text or ""))
        self.operations.append(
            ("draft", str(text or ""), {"draft_id": draft_id, **kwargs})
        )


def _consumer(
    chat: FakeChat,
    *,
    mode: str = "auto",
    draft_id: int = 4242,
    min_update_chars: int = 999,
    update_interval_s: float = 999.0,
    max_stale_interval_s: float = 3.0,
) -> ChannelStreamConsumer:
    transport = TelegramStreamTransport(chat, transport_mode=mode, draft_id=draft_id)
    return ChannelStreamConsumer(
        transport,
        ChannelStreamConfig(
            channel="telegram",
            transport_mode=mode,
            update_interval_s=update_interval_s,
            max_stale_interval_s=max_stale_interval_s,
            min_update_chars=min_update_chars,
            typing_interval_s=None,
            cursor="...",
            max_message_units=MAX_TG_MESSAGE_LEN,
            fresh_final_after_s=None,
            sparse_progress=True,
        ),
    )


async def _events(*items: tuple[str, Any]):
    for item in items:
        yield item


def test_telegram_edit_mode_updates_and_finalizes() -> None:
    async def run_case():
        chat = FakeChat(chat_type="group", draft_supported=True)
        result = await _consumer(chat, mode="edit").consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, chat

    result, chat = asyncio.run(run_case())

    assert result.delivered is True
    assert result.transport == "telegram:edit"
    assert [op for op, _text, _kwargs in chat.operations] == [
        "send",
        "edit",
        "typing",
        "edit",
    ]
    assert chat.sent[0].deleted is False


def test_telegram_auto_uses_draft_only_for_supported_private_chats() -> None:
    async def private_case():
        chat = FakeChat(chat_type="private", draft_supported=True)
        result = await _consumer(chat, mode="auto").consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, chat

    async def group_case():
        chat = FakeChat(chat_type="group", draft_supported=True)
        result = await _consumer(chat, mode="auto").consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, chat

    private_result, private_chat = asyncio.run(private_case())
    group_result, group_chat = asyncio.run(group_case())

    assert private_result.transport == "telegram:draft"
    assert private_result.fallback_sent is True
    assert [op for op, _text, _kwargs in private_chat.operations].count("draft") >= 2
    assert "" not in private_chat.drafts
    assert private_chat.draft_ids
    assert set(private_chat.draft_ids) == {4242}
    assert [op for op, _text, _kwargs in private_chat.operations].count("send") == 1
    assert group_result.transport == "telegram:edit"
    assert [op for op, _text, _kwargs in group_chat.operations][0] == "send"


def test_telegram_unsupported_draft_falls_back_to_edit() -> None:
    async def run_case():
        chat = FakeChat(chat_type="private", draft_supported=False)
        result = await _consumer(chat, mode="draft").consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, chat

    result, chat = asyncio.run(run_case())

    assert result.transport == "telegram:edit"
    assert [op for op, _text, _kwargs in chat.operations][0] == "send"


def test_telegram_long_messages_split_by_utf16_units() -> None:
    chunks = _split_message("😀" * 3000)

    assert len(chunks) == 2
    assert all(_telegram_text_units(chunk) <= MAX_TG_MESSAGE_LEN for chunk in chunks)
    assert "".join(chunks) == "😀" * 3000


def test_telegram_overflow_preview_freezes_until_chunked_final_send() -> None:
    async def run_case():
        chat = FakeChat(chat_type="group")
        consumer = _consumer(
            chat,
            mode="edit",
            min_update_chars=1,
            update_interval_s=0.0,
            max_stale_interval_s=30.0,
        )
        long_prefix = "a" * (MAX_TG_MESSAGE_LEN + 20)
        long_tail = "b" * 120
        result = await consumer.consume_events(
            _events(("token", long_prefix), ("token", long_tail)),
            final_text=long_prefix + long_tail,
        )
        return result, chat

    result, chat = asyncio.run(run_case())

    edits = [text for op, text, _kwargs in chat.operations if op == "edit"]
    final_sends = [text for op, text, _kwargs in chat.operations if op == "send"][1:]

    assert result.delivered is True
    assert result.fallback_sent is True
    assert len(edits) == 1
    assert any(
        "Streaming preview; final answer will be sent in parts." in text
        for text in edits
    )
    assert "b" * 40 not in edits[-1]
    assert all(_telegram_text_units(text) <= MAX_TG_MESSAGE_LEN for text in edits)
    assert len(final_sends) == 2


def test_telegram_draft_overflow_preview_keeps_refreshing_until_final_send() -> None:
    async def run_case():
        chat = FakeChat(chat_type="private", draft_supported=True)
        consumer = _consumer(
            chat,
            mode="auto",
            min_update_chars=1,
            update_interval_s=0.0,
            max_stale_interval_s=30.0,
        )
        long_prefix = "a" * (MAX_TG_MESSAGE_LEN + 20)
        long_tail = "b" * 120
        result = await consumer.consume_events(
            _events(("token", long_prefix), ("token", long_tail)),
            final_text=long_prefix + long_tail,
        )
        return result, chat

    result, chat = asyncio.run(run_case())

    final_sends = [text for op, text, _kwargs in chat.operations if op == "send"]

    assert result.delivered is True
    assert result.transport == "telegram:draft"
    assert result.fallback_sent is True
    assert len(chat.drafts) >= 3
    assert "" not in chat.drafts
    assert len(set(chat.drafts)) > 1
    assert "b" * 40 in chat.drafts[-1]
    assert all(_telegram_text_units(text) <= MAX_TG_MESSAGE_LEN for text in chat.drafts)
    assert len(final_sends) == 2


def test_telegram_final_edit_failure_falls_back_to_final_send() -> None:
    async def run_case():
        chat = FakeChat(chat_type="group")
        chat.fail_next_message_final_edit = True
        consumer = _consumer(chat, mode="edit")
        result = await consumer.consume_events(
            _events(("token", "partial")),
            final_text="final answer",
        )
        return result, chat

    result, chat = asyncio.run(run_case())

    assert result.delivered is True
    assert result.fallback_sent is True
    assert [text for op, text, _kwargs in chat.operations if op == "send"][
        -1
    ].endswith("final answer")


def test_telegram_message_not_modified_is_success() -> None:
    async def run_case():
        chat = FakeChat(chat_type="group")
        chat.fail_next_message_not_modified = True
        consumer = _consumer(chat, mode="edit")
        result = await consumer.consume_events(
            _events(("token", "same")),
            final_text="same",
        )
        return result, chat

    result, chat = asyncio.run(run_case())

    assert result.delivered is True
    assert [op for op, _text, _kwargs in chat.operations].count("send") == 1


def test_telegram_streamed_turn_invokes_channel_checkpoint_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from row_bot.channels import telegram

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

    monkeypatch.setattr(telegram.ch_runtime, "persist_channel_assistant_message", fake_persist)
    monkeypatch.setattr(telegram, "_run_agent_sync", fake_run)

    async def run_case():
        chat = FakeChat(chat_type="group")
        result = await telegram._stream_agent_turn_to_telegram(
            chat,
            "question",
            {"configurable": {"thread_id": "thread-1"}},
        )
        return result

    answer, _interrupt, _images, _videos, delivery = asyncio.run(run_case())

    assert answer == "partial answer\n\nWarning: Error: provider disconnected"
    assert delivery.delivered is True
    assert calls == [
        ("partial answer\n\nWarning: Error: provider disconnected", "telegram", True)
    ]
