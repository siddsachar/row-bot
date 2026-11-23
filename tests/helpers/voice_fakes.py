"""Fakes for tests of the voice transports and of Row-Bot Voice's duplex engine.

``fake_browser_speech`` stands in for ``BrowserLocalVoiceService`` without
models, ffmpeg or audio devices. Speech input readiness follows the fake
voice's ``whisper_model_available`` at call time, so a test can block or fail
it. The rest stand in for Silero VAD, Smart Turn, speech-to-text, Kokoro and
the client socket, with a fake clock that only tests and fake models move.
"""
from __future__ import annotations

from collections.abc import Callable
import threading
from types import SimpleNamespace
from typing import Any

import numpy as np

from row_bot.voice.provider_base import VoiceProviderStatus
from row_bot.voice.streaming_base import SpeechAudio, TranscriptUpdate


def fake_browser_speech(voice: Any, *, transcribe: Callable[..., str] | None = None,
                        synthesize: Callable[..., Any] | None = None) -> SimpleNamespace:
    def speech_input_status(mode: str) -> VoiceProviderStatus:
        return VoiceProviderStatus("fake_speech", "Fake speech", voice.whisper_model_available(),
                                   unavailable_code="whisper_model_missing")

    service = SimpleNamespace(voice_service=voice, speech_input_status=speech_input_status)
    if transcribe is not None:
        service.transcribe = transcribe
    if synthesize is not None:
        service.synthesize = synthesize
    return service


# ── Row-Bot Voice (the duplex engine) ────────────────────────────────────────

FRAME_MS = 20
FRAME_SAMPLES = 320
VOICED = b"\x10\x27" * FRAME_SAMPLES  # a loud frame: amplitude 10000
SILENT = b"\0\0" * FRAME_SAMPLES


class FakeClock:
    """The latency clock: tests and fake models move it, nothing waits on it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms / 1000


class FakeSleep:
    """``asyncio.sleep`` on the fake clock: a sleeper wakes once a test moves the clock past its time."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._sleepers: list[tuple[float, Any]] = []

    async def __call__(self, seconds: float) -> None:
        import asyncio

        waiter = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.clock.now + seconds, waiter))
        await waiter

    def advance(self, ms: float) -> None:
        self.clock.advance(ms)
        for due, waiter in list(self._sleepers):
            if due <= self.clock.now:
                self._sleepers.remove((due, waiter))
                if not waiter.done():
                    waiter.set_result(None)


class FakeActivity:
    """Speech wherever the audio is loud: Silero's role without the model."""

    window_samples = 512

    def confidence(self, pcm16: bytes) -> float:
        return 1.0 if np.abs(np.frombuffer(pcm16, dtype="<i2")).max(initial=0) > 1000 else 0.0


class FakeEndOfTurn:
    def __init__(self, probability: float = 0.9) -> None:
        self.probability = probability
        self.calls = 0

    async def complete_probability(self, pcm16: bytes) -> float:
        self.calls += 1
        return self.probability


class FakeSpeechStream:
    def __init__(self, owner: FakeSpeechToText) -> None:
        self.owner = owner
        self.audio = bytearray()
        self.closed = False

    def push(self, pcm16: bytes) -> None:
        self.audio.extend(pcm16)

    def partial(self) -> TranscriptUpdate:
        self.owner.partials += 1
        return TranscriptUpdate(self.owner.partial_text, final=False)

    def finish(self) -> TranscriptUpdate:
        self.owner.clock.advance(self.owner.finish_ms)
        text = self.owner.finals.pop(0) if self.owner.finals else ""
        self.owner.finished.append((text, len(self.audio)))
        return TranscriptUpdate(text, final=True, language=self.owner.heard_language)

    def close(self) -> None:
        self.closed = True


class FakeSpeechToText:
    provider_id = "fake_stt"

    def __init__(self, clock: FakeClock, *finals: str, partial_text: str = "", finish_ms: float = 0) -> None:
        self.clock = clock
        self.finals = list(finals)
        self.partial_text = partial_text
        self.finish_ms = finish_ms
        self.finished: list[tuple[str, int]] = []
        self.partials = 0
        self.warmed = False
        self.streams: list[FakeSpeechStream] = []
        # The language each utterance was opened in, and the one every final reports it heard.
        self.opened: list[str | None] = []
        self.heard_language: str | None = None

    def warm(self) -> None:
        self.warmed = True

    def open(self, *, language: str | None = None) -> FakeSpeechStream:
        self.opened.append(language)
        stream = FakeSpeechStream(self)
        self.streams.append(stream)
        return stream


class FakeSentenceSpeech:
    """Speech exactly as long as its text says (10 ms a character, at 24 kHz)."""

    provider_id = "fake_tts"

    def __init__(self, clock: FakeClock, *, synth_ms: float = 0) -> None:
        self.clock = clock
        self.synth_ms = synth_ms
        self.spoken: list[str] = []
        # The language hint each sentence was asked in.
        self.languages: list[str | None] = []
        # Set: the voice can't speak this language, so every sentence comes back unspoken.
        self.unspoken: str | None = None
        self.warmed = False
        self.gate: threading.Event | None = None

    def warm(self) -> None:
        self.warmed = True

    def synthesize(self, text: str, *, language: str | None = None) -> SpeechAudio:
        self.languages.append(language)
        if self.gate is not None:
            self.gate.wait(5)
        self.clock.advance(self.synth_ms)
        if self.unspoken:
            return SpeechAudio(b"", 24_000, unspoken=self.unspoken)
        self.spoken.append(text)
        return SpeechAudio(b"\x01\x00" * (240 * len(text)), 24_000)


class FakeLink:
    """The client end of the socket: what arrived, at what audio time."""

    def __init__(self) -> None:
        self.session: Any = None
        self.events: list[dict] = []
        self.audio: list[tuple[int, int]] = []
        self.timeline: list[tuple[str, int]] = []

    def _at(self) -> int:
        return self.session.tracker.now_ms if self.session is not None else 0

    async def send_event(self, event: dict) -> None:
        self.events.append(event)
        self.timeline.append((event["type"], self._at()))

    async def send_audio(self, segment: int, pcm16: bytes) -> None:
        self.audio.append((segment, len(pcm16)))
        self.timeline.append(("audio", self._at()))

    def of(self, kind: str) -> list[dict]:
        return [event for event in self.events if event["type"] == kind]

    def states(self) -> list[str]:
        return [event["state"] for event in self.of("state")]


class FakeHost:
    def __init__(self, outcome: Any = None, *, sees: bool = True) -> None:
        self.turns: list[str] = []
        self.stills: list[Any] = []  # the still each turn carried (None for none)
        self.spoken: list[str] = []
        self.outcome = outcome
        self.keepalives = 0
        # A spoken answer to an approval: what the host says back, and what it was asked.
        self.approval_result = "answered"
        self.approvals: list[tuple[str, bool]] = []
        self.sees = sees

    def take_turn(self, text: str, utterance_id: str, still: Any = None) -> Any:
        from row_bot.voice.engine.session import TurnOutcome

        self.turns.append(text)
        self.stills.append(still)
        return self.outcome or TurnOutcome("submitted")

    def sees_images(self) -> bool:
        return self.sees

    def drops(self, text: str) -> bool:
        heard = " ".join(text.lower().split())
        return any(heard == " ".join(said.lower().split()) for said in self.spoken)

    def spoke(self, text: str) -> None:
        self.spoken.append(text)

    def keepalive(self) -> None:
        self.keepalives += 1

    def answer_approval(self, approval_id: str, approve: bool, utterance_id: str) -> str:
        self.approvals.append((approval_id, approve))
        return self.approval_result


def fake_engine_parts(clock: FakeClock, stt: FakeSpeechToText, tts: FakeSentenceSpeech | None = None,
                      end_of_turn: FakeEndOfTurn | None = None) -> Any:
    from row_bot.voice.engine.session import EngineParts

    return EngineParts(stt, tts or FakeSentenceSpeech(clock), FakeActivity(), end_of_turn or FakeEndOfTurn())
