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


class SpeechToTextProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        ...


class SpeechOutputProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...

    def speak_now(self, text: str) -> None:
        ...


class RealtimeVoiceProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...
