from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def test_pytest_blocks_live_row_bot_user_state_writes():
    live_file = Path.home() / ".row-bot" / "pytest-live-write-guard.json"

    with pytest.raises(AssertionError, match="live app user state"):
        live_file.write_text("{}", encoding="utf-8")

    with pytest.raises(AssertionError, match="live app user state"):
        sqlite3.connect(Path.home() / ".row-bot" / "threads.db")


def test_voice_runtime_follows_changed_test_data_dir(tmp_path, monkeypatch):
    import row_bot.voice.runtime
    from row_bot import voice

    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(first))
    voice.runtime.update_voice_runtime_settings(talk_provider="local", talk_model="local-whisper-base")

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(second))
    loaded = voice.runtime.load_voice_runtime_settings()

    assert loaded.talk_model == "local-whisper"
    assert (first / "voice_runtime_settings.json").exists()
    assert not (second / "voice_runtime_settings.json").exists()
