"""Resolve the speech providers that browser Talk and Dictation use.

Resolution happens for each request at the admitted worker boundary, so a
changed Voice setting applies to the next utterance or reply. Every provider
here works on bytes: none opens a host microphone or speaker, and none
downloads a model (installation is an explicit Settings action).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from row_bot.voice.provider_base import (
    Heard,
    SpeechOutputProvider,
    SpeechToTextProvider,
    SynthesizedSpeech,
    VoiceProviderError,
    VoiceProviderStatus,
)
from row_bot.voice import languages
from row_bot.voice.runtime import VoiceRuntimeSettings, load_voice_runtime_settings

if TYPE_CHECKING:
    from row_bot.tts import TTSService
    from row_bot.voice import VoiceService

LOCAL_PROVIDER = "local"
REALTIME_TALK_PROVIDER = "openai_realtime"
SENSEVOICE_MODEL = "local-funasr-sensevoice"

# "voice_note": a voice note sent on a chat app. It uses Dictate's engine.
SpeechMode = Literal["dictate", "talk", "voice_note"]


class LocalWhisperProvider:
    provider_id = "local_whisper"
    display_name = "Local Whisper"

    def __init__(self, voice_service: VoiceService, *, language: str | None) -> None:
        self._voice = voice_service
        # The saved language (None detects it), for whole recordings.
        self.language = language

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
        return self._voice.transcribe_pcm16(audio_bytes, allow_download=False, language=self.language)

    def transcribe_heard(self, audio_bytes: bytes, *, language: str | None) -> Heard:
        text, heard = self._voice.transcribe_pcm16_heard(audio_bytes, allow_download=False, language=language)
        return Heard(text, heard)


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

    def synthesize(self, text: str, *, language: str | None = None) -> SynthesizedSpeech:
        from row_bot.sound import respelled

        self._tts.reload_settings()
        # The voice for the text's language: the person's own, the one they picked for it, or its preset.
        spoken, voice = self._tts.voice_for(text, hint=language)
        if voice is None:
            return SynthesizedSpeech(b"", "audio/wav", unspoken=spoken)
        # Only the voice hears the person's pronunciations; captions and the chat keep the written words.
        return SynthesizedSpeech(self._tts.synthesize_wav_bytes(respelled(text), voice=voice), "audio/wav")


def speech_to_text_provider(
    mode: SpeechMode,
    *,
    voice_service: VoiceService,
    settings: VoiceRuntimeSettings | None = None,
) -> SpeechToTextProvider:
    """The speech-to-text provider selected for Dictate or Talk, or for chat-app voice notes, in the saved
    language (Settings › Voice › Language; detected when it is automatic)."""
    settings = settings or load_voice_runtime_settings()
    language = languages.requested(settings.speech_language)
    if mode in ("dictate", "voice_note"):
        provider_id, model = settings.dictation_provider, settings.dictation_model
    else:
        provider_id, model = settings.talk_provider, settings.talk_model
        if provider_id == REALTIME_TALK_PROVIDER:
            # Browser Talk is then Realtime's local fallback, which listens with
            # Whisper; talk_model names the realtime model, not a local one.
            provider_id, model = LOCAL_PROVIDER, ""
    if provider_id != LOCAL_PROVIDER:
        raise VoiceProviderError("voice_provider_unavailable")
    from row_bot.voice import speech_models

    streaming = speech_models.by_engine(model)
    if streaming is not None:
        from row_bot.voice.engine.sherpa_speech import LocalStreamingSpeechProvider

        return LocalStreamingSpeechProvider(streaming)
    voice_service.sync_saved_models()
    if model == SENSEVOICE_MODEL:
        return voice_service.sensevoice_provider(language=language)
    return LocalWhisperProvider(voice_service, language=language)


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
