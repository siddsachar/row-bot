"""The one place Row-Bot imports Pipecat (``pipecat-ai``, BSD-2).

Row-Bot Voice uses only Pipecat's local audio analysis: Silero VAD and Smart
Turn v3, whose ONNX weights ship in the wheel and run on onnxruntime. Nothing
here imports Pipecat's runner, CLI, transports, cloud services or its Kokoro
and Whisper services (they download models), and tracing and Sentry stay off.

Before the first import this module:

- turns off loguru's default stderr sink (``LOGURU_AUTOINIT=False``) and
  bridges loguru to stdlib ``logging`` at INFO and above only: Pipecat logs
  transcripts and synthesised text at DEBUG, which must never reach a log file;
- removes ``PIPECAT_SETUP_FILES`` (it makes Pipecat execute arbitrary Python
  files) and forces ``PIPECAT_SMART_TURN_LOG_DATA=0`` (it writes the person's
  raw audio as WAV files to the working directory).

It is imported only when a voice session starts, so app start-up never loads
Pipecat, loguru or onnxruntime for it.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

from row_bot.voice.streaming_base import INPUT_SAMPLE_RATE

_LOG = logging.getLogger("row_bot.voice.pipecat")
_LOCK = threading.Lock()
_BRIDGED = False
_REMOVED_ENV = ("PIPECAT_SETUP_FILES", "PIPECAT_LOG_LLM_CONTEXT")
_FORCED_ENV = {"PIPECAT_SMART_TURN_LOG_DATA": "0"}


def scrub_environment() -> None:
    """Pipecat's risky switches off. Called before any analyzer is built."""
    for name in _REMOVED_ENV:
        os.environ.pop(name, None)
    os.environ.update(_FORCED_ENV)


def _forward(message: Any) -> None:
    record = message.record
    level = record["level"].no
    if level < logging.INFO:
        return
    _LOG.log(min(level, logging.CRITICAL), "%s", record["message"])


def bridge_logging() -> None:
    """Loguru into stdlib logging, INFO and above, exactly once."""
    global _BRIDGED
    with _LOCK:
        if _BRIDGED:
            return
        os.environ["LOGURU_AUTOINIT"] = "False"
        from loguru import logger

        # Also removes the default sink if loguru was imported before this ran.
        logger.remove()
        logger.add(_forward, level="INFO", format="{message}", backtrace=False, diagnose=False)
        _BRIDGED = True


def prepare() -> None:
    from row_bot import onnxruntime_telemetry

    scrub_environment()
    bridge_logging()
    onnxruntime_telemetry.switch_off()  # before Silero or Smart Turn opens an ONNX Runtime session


class SileroActivity:
    """Silero VAD's speech confidence for one 32 ms window (512 samples)."""

    def __init__(self) -> None:
        prepare()
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        self._analyzer = SileroVADAnalyzer(sample_rate=INPUT_SAMPLE_RATE)
        self._analyzer.set_sample_rate(INPUT_SAMPLE_RATE)
        self.window_samples = int(self._analyzer.num_frames_required())

    def confidence(self, pcm16: bytes) -> float:
        return float(self._analyzer.voice_confidence(pcm16))


class SmartTurn:
    """Smart Turn v3's probability that the person has finished their turn."""

    def __init__(self) -> None:
        prepare()
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

        self._analyzer = LocalSmartTurnAnalyzerV3(sample_rate=INPUT_SAMPLE_RATE)
        self._analyzer.set_sample_rate(INPUT_SAMPLE_RATE)

    async def complete_probability(self, pcm16: bytes) -> float:
        # The analyzer keeps its own buffer: give it exactly this utterance.
        self._analyzer.clear()
        self._analyzer.append_audio(pcm16, True)
        _state, metrics = await self._analyzer.analyze_end_of_turn()
        self._analyzer.clear()
        return float(getattr(metrics, "probability", 0.0) or 0.0)

    async def close(self) -> None:
        await self._analyzer.cleanup()
