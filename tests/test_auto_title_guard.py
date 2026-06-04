from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_streaming_auto_title_uses_thread_name_source_guard():
    source = (ROOT / "ui" / "streaming.py").read_text(encoding="utf-8")
    send_block = source.split("async def send_message(", 1)[1]

    assert "should_auto_rename_thread" in send_block
    assert "rename_thread(" in send_block
    assert "source=\"auto\"" in send_block
    assert "state.thread_name.startswith(\"Thread \")" not in send_block
    assert "state.thread_name.startswith(\"\\U0001f4bb Thread \")" not in send_block


def test_voice_auto_title_uses_thread_name_source_guard():
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "should_auto_rename_thread(state.thread_id, state.thread_name)" in source
    assert "rename_thread(state.thread_id, text[:50], source=\"auto\")" in source
    assert "state.thread_name.startswith(\"Thread \")" not in source


def test_command_palette_new_thread_uses_create_thread_helper():
    source = (ROOT / "ui" / "chat.py").read_text(encoding="utf-8")
    palette_block = source.split("async def _new_thread_from_palette", 1)[1]

    assert "create_thread(name, thread_id=tid)" in palette_block
