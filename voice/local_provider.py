from __future__ import annotations

from tts import TTSService
from voice import VoiceService
from voice.provider_base import VoiceProviderStatus


class LocalWhisperProvider:
    provider_id = "local_whisper"
    display_name = "Local Whisper"

    def __init__(self, voice_service: VoiceService) -> None:
        self.voice_service = voice_service

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            ready=True,
            reason=f"Configured model size: {self.voice_service.whisper_size}",
            local=True,
        )

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        return self.voice_service.transcribe_bytes(audio_bytes)


class LocalKokoroProvider:
    provider_id = "local_kokoro"
    display_name = "Local Kokoro"

    def __init__(self, tts_service: TTSService) -> None:
        self.tts_service = tts_service

    def status(self) -> VoiceProviderStatus:
        installed = self.tts_service.is_installed()
        return VoiceProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            ready=installed,
            reason="Installed locally." if installed else "Kokoro model files are not installed.",
            local=True,
        )

    def speak_now(self, text: str) -> None:
        self.tts_service.speak_now(text)


def local_voice_provider_statuses(
    voice_service: VoiceService,
    tts_service: TTSService,
) -> list[VoiceProviderStatus]:
    return [
        LocalWhisperProvider(voice_service).status(),
        LocalKokoroProvider(tts_service).status(),
    ]
