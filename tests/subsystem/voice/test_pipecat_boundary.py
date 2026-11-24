"""Pipecat inside Row-Bot: no risky switches, no transcript logging, nothing networked.

Row-Bot imports only Pipecat's local audio analysis (Silero VAD and Smart
Turn). These tests hold the review's conditions: the environment switches that
run arbitrary files or record the person's audio are off, loguru reaches
Row-Bot's logs only at INFO and above, and building the analyzers imports no
runner, transport, cloud service, tracing or WebRTC stack.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest

from row_bot.voice.engine import pipecat_boundary

pytestmark = pytest.mark.subsystem

_NEVER_IMPORTED = ("pipecat.runner", "pipecat.cli", "pipecat.services", "pipecat.transports",
                   "pipecat.audio.turn.smart_turn.http_smart_turn", "aiortc", "sentry_sdk", "opentelemetry",
                   "daily")


def test_pipecat_switches_that_run_files_or_record_audio_are_scrubbed(monkeypatch):
    monkeypatch.setenv("PIPECAT_SETUP_FILES", "evil.py")
    monkeypatch.setenv("PIPECAT_SMART_TURN_LOG_DATA", "1")
    monkeypatch.setenv("PIPECAT_LOG_LLM_CONTEXT", "FULL")
    pipecat_boundary.scrub_environment()
    assert "PIPECAT_SETUP_FILES" not in os.environ
    assert "PIPECAT_LOG_LLM_CONTEXT" not in os.environ
    assert os.environ["PIPECAT_SMART_TURN_LOG_DATA"] == "0"


def test_loguru_reaches_row_bot_logs_at_info_and_never_at_debug(caplog, capfd):
    pipecat_boundary.bridge_logging()
    from loguru import logger

    caplog.set_level(logging.DEBUG, logger="row_bot.voice.pipecat")
    logger.debug("Generating TTS [a private sentence]")
    logger.info("Loaded the turn model")
    messages = [record.getMessage() for record in caplog.records if record.name == "row_bot.voice.pipecat"]
    assert messages == ["Loaded the turn model"]
    # loguru's own stderr sink is gone: nothing is printed past Row-Bot's logging.
    out, err = capfd.readouterr()
    assert "private sentence" not in out + err and "Loaded the turn model" not in err


@pytest.mark.slow
def test_the_bundled_models_run_offline_without_an_audio_log_or_network_stack(tmp_path):
    """In a fresh interpreter: import Row-Bot Voice, build the analyzers, judge a turn."""
    script = f"""
import asyncio, os, sys
os.environ["PIPECAT_SMART_TURN_LOG_DATA"] = "1"
os.environ["PIPECAT_SETUP_FILES"] = "never-run.py"
import row_bot.voice.client_duplex, row_bot.voice.engine.session
assert "pipecat" not in sys.modules and "loguru" not in sys.modules, "loaded before a session"
from row_bot.voice.engine.pipecat_boundary import SileroActivity, SmartTurn
activity, turn = SileroActivity(), SmartTurn()
assert activity.window_samples == 512
assert 0.0 <= activity.confidence(b"\\0\\0" * 512) <= 1.0
probability = asyncio.run(turn.complete_probability(b"\\0\\0" * 16000))
assert 0.0 <= probability <= 1.0
bad = [name for name in sys.modules if name.startswith({_NEVER_IMPORTED!r})]
assert not bad, bad
print("ok")
"""
    env = {**os.environ, "ROW_BOT_DATA_DIR": str(tmp_path / "data"), "ROW_BOT_INSTALL_COUNT": "0"}
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")
    assert not (tmp_path / "smart_turn_audio_log").exists()
