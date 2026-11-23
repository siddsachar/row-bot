"""Narration for Show Row-Bot how (C3): what the person says while showing a task, transcribed here.

The microphone is off by default; the client records a clip while it is on and uploads it once,
with when it started in the demo. The clip is decoded with ffmpeg through pipes and transcribed by
the local speech model chosen for Dictate (Whisper or SenseVoice), or Whisper when Dictate uses a
cloud service: narration is only ever local, whatever Talk or Realtime use. Nothing downloads here,
and the audio is never written to disk: it lives in memory for the call and only the cleaned
words are kept, on the steps they were said around (from ``LEAD_SECONDS`` before a step until
``LEAD_SECONDS`` before the next).
"""

from __future__ import annotations

import logging
from typing import Any

from row_bot.voice.browser_local import ALLOWED_AUDIO_TYPES, BrowserVoiceError

logger = logging.getLogger(__name__)

MAX_SECONDS = 15 * 60
MAX_BYTES = 64 * 1024 * 1024
LEAD_SECONDS = 3.0
# Stretches shorter than this are joined to the next, so each transcription has room to hear words.
MIN_CHUNK_SECONDS = 2.0
_BYTES_PER_SECOND = 16_000 * 2


def _voice() -> Any:
    from row_bot.voice.browser_local import get_browser_local_voice_service

    return get_browser_local_voice_service()


def _provider(voice_service: Any) -> Any:
    """The local speech-to-text model: Dictate's when it is local, else Whisper. Never a cloud one."""

    from row_bot.voice.provider_base import VoiceProviderError
    from row_bot.voice.providers import LocalWhisperProvider, speech_to_text_provider

    try:
        provider = speech_to_text_provider("dictate", voice_service=voice_service)
    except VoiceProviderError:
        from row_bot.voice import languages
        from row_bot.voice.runtime import load_voice_runtime_settings

        voice_service.sync_saved_models()
        language = languages.requested(load_voice_runtime_settings().speech_language)
        provider = LocalWhisperProvider(voice_service, language=language)
    return provider


def status() -> dict[str, Any]:
    """Whether narration can be transcribed here: ``code`` says why not (no speech model, no ffmpeg)."""

    try:
        service = _voice()
        model = _provider(service.voice_service).status()
    except Exception:
        logger.debug("Narration's speech model couldn't be checked", exc_info=True)
        return {"ready": False, "code": "narration_unavailable"}
    if not model.ready or not model.local:
        return {"ready": False, "code": "narration_no_model"}
    if not service.ffmpeg_command():  # the decoder Dictate uses
        return {"ready": False, "code": "narration_no_ffmpeg"}
    return {"ready": True, "code": ""}


def windows(times: list[float], start: float, end: float) -> list[tuple[int, float, float]]:
    """Which part of a clip (``start``..``end``, seconds into the demo) goes with which step: each step
    gets what was said from ``LEAD_SECONDS`` before it until ``LEAD_SECONDS`` before the next; a
    stretch shorter than ``MIN_CHUNK_SECONDS`` joins the next one (and keeps the earlier step)."""

    found: list[list[Any]] = []
    for index, at in enumerate(times):
        begin = 0.0 if index == 0 else max(0.0, at - LEAD_SECONDS)
        until = times[index + 1] - LEAD_SECONDS if index + 1 < len(times) else float("inf")
        low, high = max(begin, start), min(until, end)
        if high <= low:
            continue
        if found and found[-1][2] - found[-1][1] < MIN_CHUNK_SECONDS:
            found[-1][2] = high
        else:
            found.append([index, low, high])
    if len(found) > 1 and found[-1][2] - found[-1][1] < MIN_CHUNK_SECONDS:
        last = found.pop()
        found[-1][2] = last[2]
    return [(index, low, high) for index, low, high in found]


def transcribe(conversation_id: str, demo_id: str, audio: bytes, content_type: str, offset: float,
               *, session_key: str) -> dict[str, Any]:
    """Transcribe one narration clip into the demo's steps; returns the demo's page."""

    from row_bot.browser import demo as demos

    mime = str(content_type or "").partition(";")[0].strip().lower()
    if mime not in ALLOWED_AUDIO_TYPES:
        raise BrowserVoiceError("unsupported_audio_type", status_code=415)
    if not audio:
        raise BrowserVoiceError("empty_audio")
    if len(audio) > MAX_BYTES:
        raise BrowserVoiceError("payload_too_large", status_code=413)
    ready = status()
    if not ready["ready"]:
        raise BrowserVoiceError(ready["code"], status_code=503 if ready["code"] == "narration_no_ffmpeg" else 409)
    recorded = demos.current(conversation_id)
    if recorded is None:
        raise demos.DemoError("demo_missing")
    if recorded.id != demo_id:
        raise demos.DemoError("demo_changed")
    service = _voice()
    with service._job(session_key):  # noqa: SLF001 - Dictate's rate and concurrency limits
        provider = _provider(service.voice_service)
        pcm = service._decode(audio, max_seconds=MAX_SECONDS)  # noqa: SLF001 - in memory, through pipes
        del audio
        steps = [(step.id, step.at) for step in recorded.steps]
        clip_start = max(0.0, float(offset))
        clip_end = clip_start + len(pcm) / _BYTES_PER_SECOND
        said: dict[int, str] = {}
        for index, low, high in windows([at for _id, at in steps], clip_start, clip_end):
            first = int((low - clip_start) * _BYTES_PER_SECOND) & ~1
            last = int((high - clip_start) * _BYTES_PER_SECOND) & ~1
            words = str(provider.transcribe_bytes(pcm[first:last]) or "").strip()
            if words:
                step_id = steps[index][0]
                said[step_id] = f"{said.get(step_id, '')} {words}".strip()
        del pcm
    return demos.view(demos.add_narration(conversation_id, demo_id, said)) or {}
