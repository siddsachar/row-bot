from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from row_bot.channels.streaming import ChannelDeliveryResult


pytestmark = pytest.mark.subsystem


class FakeTelegramChat:
    id = 123
    type = "group"

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.sent_kwargs: list[dict[str, Any]] = []

    async def send_message(self, text: str, **kwargs: Any):
        self.sent.append(text)
        self.sent_kwargs.append(dict(kwargs))
        return type("Message", (), {"message_id": len(self.sent)})()


def _fresh_goal_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    for name in (
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.agent_runs",
        "row_bot.goals",
        "row_bot.channels.registry",
        "row_bot.channels.thread_notifications",
        "row_bot.channels.runtime",
    ):
        sys.modules.pop(name, None)
    tasks = importlib.import_module("row_bot.tasks")
    threads = importlib.import_module("row_bot.threads")
    agent_runs = importlib.import_module("row_bot.agent_runs")
    goals = importlib.import_module("row_bot.goals")
    registry = importlib.import_module("row_bot.channels.registry")
    runtime = importlib.import_module("row_bot.channels.runtime")
    registry._reset()
    return tasks, threads, agent_runs, goals, registry, runtime


def test_telegram_goal_callback_skips_duplicate_final_send(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import telegram

    chat = FakeTelegramChat()

    async def streamed_turn(*_args: Any, **_kwargs: Any):
        return (
            "final answer",
            None,
            [],
            [],
            ChannelDeliveryResult(
                delivered=True,
                streamed=True,
                finalized=True,
                fallback_sent=False,
                transport="telegram:edit",
                final_text="final answer",
            ),
        )

    monkeypatch.setattr(telegram, "_stream_agent_turn_to_telegram", streamed_turn)
    run_turn, send_text = telegram._telegram_goal_callbacks(chat)

    async def run_case():
        answer, interrupt = await run_turn("prompt", {"configurable": {"thread_id": "t"}})
        await send_text(answer)
        return answer, interrupt

    answer, interrupt = pytest.importorskip("asyncio").run(run_case())

    assert answer == "final answer"
    assert interrupt is None
    assert chat.sent == []


def test_telegram_goal_callback_fallback_sends_failed_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import telegram

    chat = FakeTelegramChat()
    long_answer = "x" * (telegram.MAX_TG_MESSAGE_LEN + 25)

    async def failed_streamed_turn(*_args: Any, **_kwargs: Any):
        return (
            long_answer,
            None,
            [],
            [],
            ChannelDeliveryResult(
                delivered=False,
                streamed=True,
                finalized=False,
                fallback_sent=True,
                transport="telegram:edit",
                final_text=long_answer,
                error="send failed",
            ),
        )

    monkeypatch.setattr(telegram, "_stream_agent_turn_to_telegram", failed_streamed_turn)
    run_turn, send_text = telegram._telegram_goal_callbacks(chat)

    async def run_case():
        answer, _interrupt = await run_turn("prompt", {"configurable": {"thread_id": "t"}})
        await send_text(answer)

    pytest.importorskip("asyncio").run(run_case())

    assert len(chat.sent) >= 2
    assert "".join(chat.sent) == long_answer


def test_telegram_interrupt_card_stores_pending_and_sends_buttons() -> None:
    from row_bot.channels import telegram

    chat = FakeTelegramChat()
    config = {"configurable": {"thread_id": "goal-thread"}}
    interrupt = {
        "__interrupt_id": "interrupt-1",
        "tool": "run_command",
        "description": "Run shell command: echo ok",
    }

    async def run_case():
        with telegram._pending_lock:
            telegram._pending_interrupts.clear()
        await telegram._send_interrupt_approval_card(
            chat,
            chat.id,
            interrupt,
            config,
            message_thread_id=77,
        )
        with telegram._pending_lock:
            pending = dict(telegram._pending_interrupts[chat.id])
            telegram._pending_interrupts.clear()
        return pending

    pending = pytest.importorskip("asyncio").run(run_case())

    assert len(chat.sent) == 1
    assert "Reply YES or NO" not in chat.sent[0]
    assert "run_command" in chat.sent[0]
    assert pending["data"] == interrupt
    assert pending["config"] == config
    assert "_ts" in pending
    assert chat.sent_kwargs[0]["message_thread_id"] == 77
    markup = chat.sent_kwargs[0]["reply_markup"]
    buttons = markup.inline_keyboard[0]
    assert [button.callback_data for button in buttons] == [
        "interrupt_approve",
        "interrupt_deny",
    ]


def test_telegram_goal_first_interrupt_uses_inline_card(monkeypatch: pytest.MonkeyPatch) -> None:
    from row_bot.channels import telegram

    chat = FakeTelegramChat()
    chat.id = 456
    message = SimpleNamespace(
        text="/goal run command that needs approval",
        message_id=55,
        message_thread_id=77,
    )
    update = SimpleNamespace(
        message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=1),
    )
    context = SimpleNamespace(chat_data={})
    sent_safe_texts: list[tuple[str, int | None]] = []
    approval_cards: list[dict[str, Any]] = []

    monkeypatch.setattr(telegram, "_is_authorised", lambda _update: True)
    monkeypatch.setattr(telegram, "_thread_id_for_command", lambda *_args: "goal-thread")
    monkeypatch.setattr(
        telegram.ch_runtime,
        "prepare_channel_goal_start",
        lambda *_args: SimpleNamespace(
            goal={"id": "goal-1"},
            prompt="goal prompt",
            objective="run command that needs approval",
        ),
    )

    async def fake_send_safe_text(_chat, text: str, *, message_thread_id=None):
        sent_safe_texts.append((text, message_thread_id))

    async def fake_run_goal(**_kwargs: Any):
        return SimpleNamespace(
            interrupt_data={
                "__interrupt_id": "interrupt-1",
                "tool": "run_command",
                "description": "Run shell command",
            }
        )

    async def fake_send_card(_chat, chat_id, interrupt_data, config, *, message_thread_id=None):
        approval_cards.append(
            {
                "chat_id": chat_id,
                "interrupt_data": interrupt_data,
                "config": config,
                "message_thread_id": message_thread_id,
            }
        )

    monkeypatch.setattr(telegram, "_send_telegram_safe_text", fake_send_safe_text)
    monkeypatch.setattr(telegram.ch_runtime, "run_channel_goal_async", fake_run_goal)
    monkeypatch.setattr(telegram, "_send_interrupt_approval_card", fake_send_card)

    pytest.importorskip("asyncio").run(telegram._cmd_goal(update, context))

    assert len(sent_safe_texts) == 1
    assert "Goal started" in sent_safe_texts[0][0]
    assert sent_safe_texts[0][1] == 77
    assert len(approval_cards) == 1
    assert approval_cards[0]["chat_id"] == 456
    assert approval_cards[0]["config"] == {"configurable": {"thread_id": "goal-thread"}}
    assert approval_cards[0]["interrupt_data"]["tool"] == "run_command"
    assert approval_cards[0]["message_thread_id"] == 77


def test_channel_goal_terminal_notification_waits_until_answer_send(tmp_path, monkeypatch):
    tasks, threads, _agent_runs, goals, registry, runtime = _fresh_goal_runtime(tmp_path, monkeypatch)
    from tests.fixtures.channels import FakeChannel

    channel = FakeChannel(name="fake")
    channel._running = True
    registry.register(channel)
    thread_id = threads.create_thread("Channel goal terminal")
    tasks.record_thread_channel_ref(
        thread_id,
        channel="fake",
        target="conversation-1",
        external_conversation_id="conversation-1",
    )
    start = runtime.prepare_channel_goal_start("/goal ship streaming", thread_id)
    assert start is not None
    monkeypatch.setattr(
        goals,
        "_verify_goal",
        lambda *_args, **_kwargs: {"verdict": "complete", "reason": "done"},
    )
    send_order: list[str] = []

    def send_text(text: str) -> None:
        assert channel.messages == []
        send_order.append(text)

    result = runtime.run_channel_goal_sync(
        channel_name="fake",
        thread_id=thread_id,
        config={"configurable": {"thread_id": thread_id}},
        first_prompt=start.prompt,
        run_turn=lambda _prompt, _config: ("final answer", None),
        send_text=send_text,
    )

    assert result.status == "completed"
    assert send_order == ["final answer"]
    assert [(msg.target, msg.text) for msg in channel.messages] == [
        ("conversation-1", "Goal completed: ship streaming")
    ]


def test_goal_completed_during_channel_turn_counts_the_turn(tmp_path, monkeypatch):
    _tasks, threads, _agent_runs, goals, _registry, runtime = _fresh_goal_runtime(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Goal accounting")
    start = runtime.prepare_channel_goal_start("/goal finish accounting", thread_id)
    assert start is not None

    def run_turn(_prompt: str, _config: dict):
        goals.update_goal_progress(
            thread_id=thread_id,
            status="completed",
            progress="Finished from inside the turn.",
        )
        return "finished", None

    result = runtime.run_channel_goal_sync(
        channel_name="telegram",
        thread_id=thread_id,
        config={"configurable": {"thread_id": thread_id}},
        first_prompt=start.prompt,
        run_turn=run_turn,
        send_text=lambda _text: None,
    )

    goal = goals.get_current_goal(thread_id, include_terminal=True)
    assert result.status == "completed"
    assert goal["turns_used"] == 1
