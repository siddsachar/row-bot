from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


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
    """A Live voice front: a cloud speech-to-speech model.

    ``credential`` names the person's provider key in Providers (never stored
    here, never sent to the browser); ``transport`` names how the session runs.
    OpenAI Realtime (``openai_realtime``) mints an ephemeral secret the host
    keeps for the browser call's SDP exchange (``create_client_secret``).
    A relayed front (``gemini_live``, ``grok_voice``) is opened by the host
    (``open_relay``), which carries its audio and events over Row-Bot's socket.
    """

    provider_id: str
    display_name: str
    credential: str
    transport: str

    def status(self) -> VoiceProviderStatus:
        ...


def provider_api_key(credential: str) -> str:
    """The person's saved key for a provider in Providers ("" when none or unreadable)."""
    from row_bot.providers.auth_store import get_provider_secret

    try:
        return str(get_provider_secret(credential) or "")
    except Exception:
        return ""


def live_voice_status(provider: Any, configured: bool) -> VoiceProviderStatus:
    return VoiceProviderStatus(
        provider_id=provider.provider_id,
        display_name=provider.display_name,
        ready=configured,
        reason="" if configured else "realtime_key_missing",
        local=False,
        unavailable_code="realtime_key_missing",
    )
