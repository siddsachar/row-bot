from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VoiceProviderStatus:
    provider_id: str
    display_name: str
    ready: bool
    reason: str = ""
    local: bool = False
    # The public error code a voice session reports when it needs this
    # provider and it is not ready. Never a message: only codes leave the app.
    unavailable_code: str = "voice_provider_unavailable"


@dataclass(frozen=True)
class SynthesizedSpeech:
    """Encoded audio for the requesting browser to play."""

    audio: bytes
    content_type: str


class VoiceProviderError(RuntimeError):
    """A provider failure carrying only a public error code, never audio or text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SpeechToTextProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        """Transcribe 16 kHz mono signed 16-bit little-endian PCM."""
        ...


class SpeechOutputProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...

    def synthesize(self, text: str) -> SynthesizedSpeech:
        """Return audio for the browser to play; never use host audio devices."""
        ...


class RealtimeVoiceProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...
