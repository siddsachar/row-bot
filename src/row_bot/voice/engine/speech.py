"""Today's local speech engines behind the streaming seams.

``SegmentedSpeechToText`` turns a whole-utterance engine (faster-whisper or
SenseVoice, whichever the person selected) into a ``StreamingSpeechToText``:
audio is buffered as it arrives, ``partial`` transcribes what has arrived so
far, and ``finish`` transcribes the whole utterance. The streaming models
(``sherpa_speech.py``) sit behind the same interface.

``ProviderSentenceSpeech`` speaks one sentence at a time through the selected
speech-output provider (Kokoro), so saved pronunciations and voice settings
apply exactly as they do everywhere else.
"""
from __future__ import annotations

import io
import threading
import wave

from row_bot.voice.provider_base import (
    SpeechOutputProvider,
    SpeechToTextProvider,
    VoiceProviderError,
)
from row_bot.voice.streaming_base import (
    INPUT_SAMPLE_RATE,
    SpeechAudio,
    TranscriptUpdate,
)

#: The longest utterance kept, matching browser dictation's five minutes.
MAX_UTTERANCE_SECONDS = 300
MAX_UTTERANCE_BYTES = MAX_UTTERANCE_SECONDS * INPUT_SAMPLE_RATE * 2
# A partial transcript is worth a decode only once this much new audio arrived.
PARTIAL_STEP_BYTES = INPUT_SAMPLE_RATE * 2 * 6 // 10
_WARM_PCM = b"\0\0" * (INPUT_SAMPLE_RATE // 2)


class _SegmentedStream:
    def __init__(self, provider: SpeechToTextProvider) -> None:
        self._provider = provider
        self._audio = bytearray()
        self._lock = threading.Lock()
        self._partial_at = 0
        self._closed = False

    def push(self, pcm16: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            room = MAX_UTTERANCE_BYTES - len(self._audio)
            if room > 0:
                self._audio.extend(pcm16[:room])

    def _snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._audio)

    def partial(self) -> TranscriptUpdate | None:
        audio = self._snapshot()
        if self._closed or len(audio) - self._partial_at < PARTIAL_STEP_BYTES:
            return None
        self._partial_at = len(audio)
        return TranscriptUpdate(self._provider.transcribe_bytes(audio).strip(), final=False)

    def finish(self) -> TranscriptUpdate:
        audio = self._snapshot()
        self.close()
        if not audio:
            return TranscriptUpdate("", final=True)
        return TranscriptUpdate(self._provider.transcribe_bytes(audio).strip(), final=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._audio = bytearray()


class SegmentedSpeechToText:
    # Each partial is a whole decode of the utterance so far: read only to check a barge-in.
    live_partials = False

    def __init__(self, provider: SpeechToTextProvider) -> None:
        self._provider = provider
        self.provider_id = provider.provider_id

    def warm(self) -> None:
        status = self._provider.status()
        if not status.ready:
            raise VoiceProviderError(status.unavailable_code)
        self._provider.transcribe_bytes(_WARM_PCM)

    def open(self, *, language: str | None = None) -> _SegmentedStream:
        return _SegmentedStream(self._provider)


def wav_to_pcm(data: bytes) -> SpeechAudio:
    """Mono s16le PCM and its rate from a WAV the speech provider returned."""
    if not data:
        return SpeechAudio(b"", 24_000)
    with wave.open(io.BytesIO(data), "rb") as reader:
        if reader.getsampwidth() != 2:
            raise VoiceProviderError("voice_output_unavailable")
        channels = reader.getnchannels()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if channels != 1:
        import numpy as np

        frames = np.frombuffer(frames, dtype="<i2").reshape(-1, channels)[:, 0].tobytes()
    return SpeechAudio(frames, rate)


class ProviderSentenceSpeech:
    def __init__(self, provider: SpeechOutputProvider) -> None:
        self._provider = provider
        self.provider_id = provider.provider_id

    def warm(self) -> None:
        status = self._provider.status()
        if not status.ready:
            raise VoiceProviderError(status.unavailable_code)
        self._provider.synthesize("Hi.")

    def synthesize(self, text: str) -> SpeechAudio:
        clean = str(text or "").strip()
        if not clean:
            return SpeechAudio(b"", 24_000)
        return wav_to_pcm(self._provider.synthesize(clean).audio)
