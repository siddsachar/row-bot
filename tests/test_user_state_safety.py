from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest


def test_pytest_blocks_live_row_bot_user_state_writes():
    live_file = Path.home() / ".row-bot" / "pytest-live-write-guard.json"

    with pytest.raises(AssertionError, match="live app user state"):
        live_file.write_text("{}", encoding="utf-8")

    with pytest.raises(AssertionError, match="live app user state"):
        sqlite3.connect(Path.home() / ".row-bot" / "threads.db")


def test_git_in_a_test_folder_never_reaches_the_checkout(tmp_path):
    # Test folders live under the checkout's .tmp. A git command run there by the
    # code under test (a workspace shell command, a clone) must not find the
    # checkout's own repository and commit or reset it (B216).
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=tmp_path,
                            capture_output=True, text=True, check=False)

    assert result.returncode != 0, f"git found a repository above {tmp_path}: {result.stdout.strip()}"


def test_voice_runtime_follows_changed_test_data_dir(tmp_path, monkeypatch):
    import row_bot.voice.runtime
    from row_bot import voice

    first = tmp_path / "first"
    second = tmp_path / "second"

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(first))
    saved = voice.runtime._settings_path()
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"talk_model": "local-whisper-base"}), encoding="utf-8")
    assert voice.runtime.load_voice_runtime_settings().talk_model == "local-whisper-base"

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(second))
    loaded = voice.runtime.load_voice_runtime_settings()

    assert loaded.talk_model == "local-whisper"
    assert (first / "voice_runtime_settings.json").exists()
    assert not (second / "voice_runtime_settings.json").exists()
