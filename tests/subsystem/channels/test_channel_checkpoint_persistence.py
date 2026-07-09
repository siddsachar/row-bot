from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage


pytestmark = pytest.mark.subsystem


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    for name in (
        "row_bot.data_paths",
        "row_bot.threads",
        "row_bot.channels.runtime",
    ):
        sys.modules.pop(name, None)
    threads = importlib.import_module("row_bot.threads")
    runtime = importlib.import_module("row_bot.channels.runtime")
    return threads, runtime


def test_channel_assistant_persistence_repairs_human_only_checkpoint(tmp_path, monkeypatch):
    threads, runtime = _fresh_modules(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Channel fallback", thread_id="channel-fallback")
    threads.append_checkpoint_messages(thread_id, [HumanMessage(content="question")])
    delivery = SimpleNamespace(
        delivered=True,
        streamed=True,
        finalized=True,
        fallback_sent=False,
        transport="telegram:edit",
        error=None,
    )

    appended = runtime.persist_channel_assistant_message(
        {"configurable": {"thread_id": thread_id}},
        "partial answer\n\nWarning: Error: provider disconnected",
        channel_name="telegram",
        delivery=delivery,
    )

    assert appended is True
    messages = threads.get_latest_checkpoint_messages(thread_id)
    assert [getattr(message, "type", "") for message in messages] == ["human", "ai"]
    assert getattr(messages[-1], "content", "") == (
        "partial answer\n\nWarning: Error: provider disconnected"
    )
    metadata = messages[-1].additional_kwargs["row_bot_ui"]
    assert metadata["channel_checkpoint_fallback"] is True
    assert metadata["channel"] == "telegram"
    assert metadata["channel_delivery"]["delivered"] is True
    assert metadata["channel_delivery"]["transport"] == "telegram:edit"

    assert runtime.persist_channel_assistant_message(
        {"configurable": {"thread_id": thread_id}},
        "partial answer\n\nWarning: Error: provider disconnected",
        channel_name="telegram",
        delivery=delivery,
    ) is False
    assert len(threads.get_latest_checkpoint_messages(thread_id)) == 2


def test_channel_assistant_persistence_does_not_duplicate_graph_assistant(tmp_path, monkeypatch):
    threads, runtime = _fresh_modules(tmp_path, monkeypatch)
    thread_id = threads.create_thread("Channel success", thread_id="channel-success")
    threads.append_checkpoint_messages(
        thread_id,
        [
            HumanMessage(content="question"),
            AIMessage(content="graph answer"),
        ],
    )

    appended = runtime.persist_channel_assistant_message(
        {"configurable": {"thread_id": thread_id}},
        "graph answer",
        channel_name="slack",
        delivery=SimpleNamespace(delivered=True, transport="slack:native"),
    )

    assert appended is False
    messages = threads.get_latest_checkpoint_messages(thread_id)
    assert [getattr(message, "type", "") for message in messages] == ["human", "ai"]
    assert getattr(messages[-1], "content", "") == "graph answer"
