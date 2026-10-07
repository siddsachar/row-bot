"""A fake browser speech service for tests of the voice transports.

It stands in for ``BrowserLocalVoiceService`` without models, ffmpeg or audio
devices. Speech input readiness follows the fake voice's
``whisper_model_available`` at call time, so a test can block or fail it.
"""
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from row_bot.voice.provider_base import VoiceProviderStatus


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
