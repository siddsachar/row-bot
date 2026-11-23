"""Hearing the person: speech start, pauses and the end of a turn.

Time here is audio time (samples received), never the wall clock, so turn
taking and barge-in behave the same in a test as on a live microphone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from row_bot.voice.streaming_base import INPUT_SAMPLE_RATE

Eagerness = Literal["relaxed", "normal", "eager"]
EAGERNESS: tuple[Eagerness, ...] = ("relaxed", "normal", "eager")


class VoiceActivity(Protocol):
    window_samples: int

    def confidence(self, pcm16: bytes) -> float:
        ...


class EndOfTurn(Protocol):
    async def complete_probability(self, pcm16: bytes) -> float:
        ...


@dataclass(frozen=True)
class TurnSettings:
    """How readily Row-Bot decides the person has finished speaking."""

    # A pause this long asks Smart Turn whether the thought is complete.
    pause_ms: int = 200
    # Speech this long counts as the person starting to talk.
    start_ms: int = 200
    # Silero confidence for a voiced window.
    confidence: float = 0.6
    # Smart Turn probability that ends the turn at a pause.
    complete_at: float = 0.5
    # Silence that ends the turn even when Smart Turn hears an unfinished thought.
    max_silence_ms: int = 1600
    # Audio kept from before speech starts, so the first word is not clipped.
    preroll_ms: int = 300

    @classmethod
    def for_eagerness(cls, eagerness: str) -> "TurnSettings":
        if eagerness == "relaxed":
            return cls(complete_at=0.7, max_silence_ms=2600)
        if eagerness == "eager":
            return cls(complete_at=0.35, max_silence_ms=900)
        return cls()


@dataclass(frozen=True)
class Heard:
    """One VAD decision: ``kind`` is what changed at ``at_ms`` (audio time)."""

    kind: Literal["voice", "speech_started", "paused", "resumed", "silence_limit"]
    at_ms: int


class SpeechTracker:
    """Speech start and pause detection over Silero windows.

    ``feed`` takes 16 kHz PCM in any frame size and returns what happened, in
    order. ``voice`` reports each voiced window (for ducking during playback).
    """

    def __init__(self, activity: VoiceActivity, settings: TurnSettings) -> None:
        self._activity = activity
        self.settings = settings
        self._window_bytes = activity.window_samples * 2
        self.window_ms = activity.window_samples * 1000 / INPUT_SAMPLE_RATE
        self._pending = bytearray()
        self.samples = 0
        self.state: Literal["quiet", "starting", "speaking", "paused"] = "quiet"
        self._voiced_ms = 0.0
        self._silent_ms = 0.0
        self.speech_started_ms = 0
        self.last_voice_ms = 0
        self.last_level = 0.0

    @property
    def now_ms(self) -> int:
        return self.samples * 1000 // INPUT_SAMPLE_RATE

    @property
    def speech_ms(self) -> int:
        """How long the current speech has lasted, up to its last voiced window."""
        if self.state in {"quiet", "starting"}:
            return int(self._voiced_ms)
        return max(0, self.last_voice_ms - self.speech_started_ms)

    def reset(self) -> None:
        self.state = "quiet"
        self._voiced_ms = self._silent_ms = 0.0

    def feed(self, pcm16: bytes) -> list[Heard]:
        events: list[Heard] = []
        self._pending.extend(pcm16)
        while len(self._pending) >= self._window_bytes:
            window = bytes(self._pending[:self._window_bytes])
            del self._pending[:self._window_bytes]
            self.samples += self._window_bytes // 2
            voiced = self._activity.confidence(window) >= self.settings.confidence
            now = self.now_ms
            if voiced:
                self.last_voice_ms = now
                events.append(Heard("voice", now))
            if self.state == "quiet":
                if voiced:
                    self.state = "starting"
                    self._voiced_ms = self.window_ms
                    self.speech_started_ms = int(now - self.window_ms)
            elif self.state == "starting":
                if voiced:
                    self._voiced_ms += self.window_ms
                    if self._voiced_ms >= self.settings.start_ms:
                        self.state = "speaking"
                        self._silent_ms = 0.0
                        events.append(Heard("speech_started", self.speech_started_ms))
                else:
                    self.reset()
            elif self.state == "speaking":
                if voiced:
                    self._silent_ms = 0.0
                else:
                    self._silent_ms += self.window_ms
                    if self._silent_ms >= self.settings.pause_ms:
                        self.state = "paused"
                        events.append(Heard("paused", self.last_voice_ms))
            elif self.state == "paused":
                if voiced:
                    self.state = "speaking"
                    self._silent_ms = 0.0
                    events.append(Heard("resumed", now))
                else:
                    self._silent_ms += self.window_ms
                    if self._silent_ms >= self.settings.max_silence_ms:
                        events.append(Heard("silence_limit", now))
        return events
