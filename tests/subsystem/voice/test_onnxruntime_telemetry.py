"""ONNX Runtime's telemetry is switched off before any voice path opens an ONNX Runtime session.

A fake ``onnxruntime`` records the order of ``disable_telemetry_events()`` and session creation; fake
Kokoro, faster-whisper and Pipecat analyzers open a session the way the real ones do. No model loads.
"""

from __future__ import annotations

import sys
import types

import pytest

from row_bot import onnxruntime_telemetry

pytestmark = pytest.mark.subsystem


@pytest.fixture
def ort(monkeypatch):
    """A fake onnxruntime and a log of what happened, in order."""

    log: list[str] = []
    module = types.ModuleType("onnxruntime")

    def disable_telemetry_events() -> None:
        log.append("telemetry off")

    class InferenceSession:
        def __init__(self, *args, **kwargs) -> None:
            log.append("session")

    module.disable_telemetry_events = disable_telemetry_events
    module.InferenceSession = InferenceSession
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    monkeypatch.setattr(onnxruntime_telemetry, "_switched_off", False)
    return log


def _fake(monkeypatch, name: str, **attributes) -> None:
    parts = name.split(".")
    for index in range(1, len(parts) + 1):
        dotted = ".".join(parts[:index])
        module = types.ModuleType(dotted)
        module.__path__ = []  # a package, so the next level imports
        if index == len(parts):
            for key, value in attributes.items():
                setattr(module, key, value)
        monkeypatch.setitem(sys.modules, dotted, module)


def _opens_a_session(*args, **kwargs):
    sys.modules["onnxruntime"].InferenceSession("model.onnx")


def test_kokoro_switches_telemetry_off_before_its_session(ort, monkeypatch, tmp_path):
    from row_bot import tts

    model, voices = tmp_path / "kokoro.onnx", tmp_path / "voices.bin"
    model.write_bytes(b"m")
    voices.write_bytes(b"v")
    monkeypatch.setattr(tts, "_MODEL_PATH", model)
    monkeypatch.setattr(tts, "_VOICES_PATH", voices)
    monkeypatch.setattr(tts, "_SHARED_KOKORO", [])
    _fake(monkeypatch, "kokoro_onnx", Kokoro=type("Kokoro", (), {"__init__": _opens_a_session}))

    assert tts.TTSService()._get_kokoro() is not None
    assert ort == ["telemetry off", "session"]


def test_whisper_switches_telemetry_off_before_its_voice_filter(ort, monkeypatch, tmp_path):
    import row_bot.voice as voice

    monkeypatch.setattr(voice, "_WHISPER_CACHE_DIR", tmp_path / "whisper")
    _fake(monkeypatch, "faster_whisper", WhisperModel=type("WhisperModel", (), {"__init__": _opens_a_session}))

    voice.VoiceService()._ensure_whisper(allow_download=False)
    assert ort == ["telemetry off", "session"]


def test_silero_and_smart_turn_switch_telemetry_off_before_their_sessions(ort, monkeypatch):
    from row_bot.voice.engine import pipecat_boundary

    class Analyzer:
        def __init__(self, *args, **kwargs) -> None:
            _opens_a_session()

        def set_sample_rate(self, rate: int) -> None:
            pass

        def num_frames_required(self) -> int:
            return 512

    monkeypatch.setattr(pipecat_boundary, "bridge_logging", lambda: None)
    _fake(monkeypatch, "pipecat.audio.vad.silero", SileroVADAnalyzer=Analyzer)
    _fake(monkeypatch, "pipecat.audio.turn.smart_turn.local_smart_turn_v3", LocalSmartTurnAnalyzerV3=Analyzer)

    pipecat_boundary.SileroActivity()
    pipecat_boundary.SmartTurn()
    # Off once, before the first session; the second analyzer finds it already off.
    assert ort == ["telemetry off", "session", "session"]


def test_without_onnxruntime_nothing_breaks(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # import fails
    monkeypatch.setattr(onnxruntime_telemetry, "_switched_off", False)

    onnxruntime_telemetry.switch_off()
    onnxruntime_telemetry.switch_off()
