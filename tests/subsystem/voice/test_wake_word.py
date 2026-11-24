"""The wake word's listener: a streaming ONNX model, its threshold, refractory period and quiet while voice runs.

The model is the stand-in in ``tests/fixtures/wake_standin.py`` (the shipped
format, but it hears loudness, not words), run through ONNX Runtime. No
microphone, network or real model is used; the clock is a fake.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest

from row_bot.voice.wake import WakeDetector, WakeListening, WakeModel, WakeOptions, run_wake_socket
from tests.fixtures.wake_standin import write_standin
from tests.helpers.voice_fakes import FakeClock

pytestmark = pytest.mark.subsystem


def _frames(amplitude: float, ms: int) -> list[bytes]:
    """20 ms frames of 16 kHz PCM at a steady level (alternating sign, so its mean level is the amplitude)."""
    value = int(amplitude * 32767)
    frame = np.array([value, -value] * 160, np.int16).tobytes()
    return [frame] * (ms // 20)


@pytest.fixture
def model(tmp_path):
    return WakeModel.load(write_standin(tmp_path))


def _hears(detector: WakeDetector, frames: list[bytes]) -> int:
    return sum(detector.hear(frame) for frame in frames)


def test_silence_never_wakes_and_the_wake_word_does_within_one_step(model):
    detector = WakeDetector(model, clock=FakeClock())
    assert _hears(detector, _frames(0.0, 4000)) == 0
    # The stand-in "hears the wake word" in a loud step: four 20 ms frames make one 80 ms step.
    loud = _frames(0.3, 80)
    assert [detector.hear(frame) for frame in loud] == [False, False, False, True]


def test_a_score_under_the_threshold_never_wakes(model):
    # A steady quiet level settles at a score of 0.4 in the stand-in.
    quiet = _frames(0.1, 4000)
    assert _hears(WakeDetector(model, clock=FakeClock()), quiet) == 0
    assert _hears(WakeDetector(model, clock=FakeClock(), threshold=0.3), quiet) == 1


def test_after_a_detection_it_stays_quiet_for_the_refractory_period(model):
    clock = FakeClock()
    detector = WakeDetector(model, clock=clock, refractory=2.0)
    assert _hears(detector, _frames(0.3, 80)) == 1
    clock.advance(1900)
    assert _hears(detector, _frames(0.3, 400)) == 0
    clock.advance(200)
    assert _hears(detector, _frames(0.3, 80)) == 1


def test_a_model_that_does_not_stream_is_refused():
    class Session:
        def get_inputs(self):
            return [type("Input", (), {"name": "audio", "shape": [1, 16000], "type": "tensor(float)"})()]

        def get_outputs(self):
            return []

    with pytest.raises(ValueError, match="wake_model_invalid"):
        WakeModel(Session())


class _Socket:
    """The window's side of one wake socket: frames in, the host's events out."""

    def __init__(self, messages: list) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        for message in messages:
            self.incoming.put_nowait(message)
        self.events: list[dict] = []

    async def send_event(self, event: dict) -> None:
        self.events.append(event)

    async def receive(self):
        return await self.incoming.get()


def _run(listening: WakeListening, messages: list, *, revalidate=None) -> list[dict]:
    async def scenario():
        socket = _Socket([*messages, None])

        async def valid() -> None:
            return None

        await run_wake_socket(listening, send_event=socket.send_event, receive=socket.receive,
                              revalidate=revalidate or valid)
        return socket.events

    return asyncio.run(scenario())


def _listening(tmp_path, *, busy=lambda: False, enabled=lambda: True) -> WakeListening:
    return WakeListening(voice_busy=busy, clock=FakeClock(),
                         options=WakeOptions(enabled=enabled, model_path=write_standin(tmp_path)))


def test_the_socket_reports_ready_then_each_detection(tmp_path):
    events = _run(_listening(tmp_path), [*_frames(0.0, 400), *_frames(0.3, 80)])
    assert events == [{"type": "ready", "input_sample_rate": 16000}, {"type": "wake"}]


def test_it_hears_nothing_while_voice_or_dictation_holds_the_microphone(tmp_path):
    holding = {"value": True}
    listening = _listening(tmp_path, busy=lambda: holding["value"])
    assert _run(listening, _frames(0.3, 2000)) == [{"type": "ready", "input_sample_rate": 16000}]


def test_an_oversized_frame_or_unknown_command_ends_the_socket(tmp_path):
    with pytest.raises(ValueError, match="wake_frame_too_large"):
        _run(_listening(tmp_path), [b"\0" * 40000])
    with pytest.raises(ValueError, match="invalid_command"):
        _run(_listening(tmp_path), [{"type": "speak"}])


def test_turning_the_wake_word_off_ends_listening(tmp_path):
    enabled = {"value": True}
    listening = _listening(tmp_path, enabled=lambda: enabled["value"])
    listening.tick_seconds = 0.01

    async def scenario():
        socket = _Socket([])

        async def valid() -> None:
            return None

        task = asyncio.create_task(run_wake_socket(listening, send_event=socket.send_event,
                                                   receive=socket.receive, revalidate=valid))
        await asyncio.sleep(0)
        enabled["value"] = False
        await asyncio.wait_for(task, timeout=5)
        return socket.events

    assert asyncio.run(scenario()) == [{"type": "ready", "input_sample_rate": 16000}]


def test_no_ticket_while_off_or_without_the_model(tmp_path):
    from row_bot.voice.client_transport import DictationError, DictationOwner

    owner = DictationOwner("session", "binding", "", "epoch")
    with pytest.raises(DictationError, match="wake_word_off"):
        _listening(tmp_path, enabled=lambda: False).ticket(owner, "csrf")
    missing = WakeListening(voice_busy=lambda: False, clock=FakeClock(),
                            options=WakeOptions(enabled=lambda: True, model_path=tmp_path / "absent.onnx"))
    assert missing.reason() == "wake_model_missing"
    with pytest.raises(DictationError, match="wake_model_missing"):
        missing.ticket(owner, "csrf")
