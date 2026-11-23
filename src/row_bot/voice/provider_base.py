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
    """Encoded audio for the requesting browser to play.

    No audio with ``unspoken`` set: the text is in a language this voice cannot speak (its code), so it
    stays on screen only.
    """

    audio: bytes
    content_type: str
    unspoken: str | None = None


@dataclass(frozen=True)
class Heard:
    """A transcript and its language (a code; None when unknown)."""

    text: str
    language: str | None = None


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
        """Transcribe 16 kHz mono signed 16-bit little-endian PCM in the language it was made for."""
        ...


class LanguageSpeechToText(SpeechToTextProvider, Protocol):
    """A provider that takes a language for each recording and reports the one it heard (every local
    engine). A provider without it is asked through ``transcribe_bytes`` and reports no language."""

    def transcribe_heard(self, audio_bytes: bytes, *, language: str | None) -> Heard:
        """*language* None detects it."""
        ...


def transcribe_heard(provider: SpeechToTextProvider, audio_bytes: bytes, *, language: str | None) -> Heard:
    """*audio_bytes* written down in *language* (None detects it), with the language heard."""
    heard = getattr(provider, "transcribe_heard", None)
    if heard is None:
        return Heard(provider.transcribe_bytes(audio_bytes))
    return heard(audio_bytes, language=language)


class SpeechOutputProvider(Protocol):
    provider_id: str
    display_name: str

    def status(self) -> VoiceProviderStatus:
        ...

    def synthesize(self, text: str, *, language: str | None = None) -> SynthesizedSpeech:
        """Return audio for the browser to play; never use host audio devices.

        *language* is a hint (what the person was heard speaking): the text's own language decides when it
        is clear.
        """
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
