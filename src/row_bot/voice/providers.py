"""Resolve the speech providers that browser Talk and Dictation use.

Resolution happens for each request at the admitted worker boundary, so a
changed Voice setting applies to the next utterance or reply. Every provider
here works on bytes: none opens a host microphone or speaker, and none
downloads a model (installation is an explicit Settings action).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from row_bot.voice.provider_base import (
    SpeechOutputProvider,
    SpeechToTextProvider,
    SynthesizedSpeech,
    VoiceProviderError,
    VoiceProviderStatus,
)
from row_bot.voice.runtime import VoiceRuntimeSettings, load_voice_runtime_settings

if TYPE_CHECKING:
    from row_bot.tts import TTSService
    from row_bot.voice import VoiceService

LOCAL_PROVIDER = "local"
REALTIME_TALK_PROVIDER = "openai_realtime"
SENSEVOICE_MODEL = "local-funasr-sensevoice"

SpeechMode = Literal["dictate", "talk"]


class LocalWhisperProvider:
    provider_id = "local_whisper"
    display_name = "Local Whisper"

    def __init__(self, voice_service: VoiceService) -> None:
        self._voice = voice_service

    def status(self) -> VoiceProviderStatus:
        ready = self._voice.whisper_model_available()
        return VoiceProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            ready=ready,
            reason="" if ready else "The selected Whisper model is not installed.",
            local=True,
            unavailable_code="whisper_model_missing",
        )

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        return self._voice.transcribe_pcm16(audio_bytes, allow_download=False)


class LocalKokoroOutput:
    provider_id = "local_kokoro"
    display_name = "Kokoro"

    def __init__(self, tts_service: TTSService) -> None:
        self._tts = tts_service

    def status(self) -> VoiceProviderStatus:
        ready = self._tts.is_installed()
        return VoiceProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            ready=ready,
            reason="" if ready else "Kokoro is not installed.",
            local=True,
            unavailable_code="kokoro_model_missing",
        )

    def synthesize(self, text: str) -> SynthesizedSpeech:
        self._tts.reload_settings()
        return SynthesizedSpeech(self._tts.synthesize_wav_bytes(text), "audio/wav")


def speech_to_text_provider(
    mode: SpeechMode,
    *,
    voice_service: VoiceService,
    settings: VoiceRuntimeSettings | None = None,
) -> SpeechToTextProvider:
    """The speech-to-text provider selected for Dictate or Talk."""
    settings = settings or load_voice_runtime_settings()
    if mode == "dictate":
        provider_id, model = settings.dictation_provider, settings.dictation_model
    else:
        provider_id, model = settings.talk_provider, settings.talk_model
        if provider_id == REALTIME_TALK_PROVIDER:
            # Browser Talk is then Realtime's local fallback, which listens with
            # Whisper; talk_model names the realtime model, not a local one.
            provider_id, model = LOCAL_PROVIDER, ""
    if provider_id != LOCAL_PROVIDER:
        raise VoiceProviderError("voice_provider_unavailable")
    voice_service.sync_saved_models()
    if model == SENSEVOICE_MODEL:
        return voice_service.sensevoice_provider()
    return LocalWhisperProvider(voice_service)


def speech_output_provider(
    *,
    tts_service: TTSService,
    settings: VoiceRuntimeSettings | None = None,
) -> SpeechOutputProvider:
    """The speech output provider selected for spoken replies."""
    settings = settings or load_voice_runtime_settings()
    if settings.speech_output_provider != LOCAL_PROVIDER:
        raise VoiceProviderError("voice_provider_unavailable")
    return LocalKokoroOutput(tts_service)
