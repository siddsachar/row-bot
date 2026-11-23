"""Streaming speech-to-text on sherpa-onnx, behind the ``StreamingSpeechToText`` seam.

Two families from the pinned catalog (``voice/speech_models.py``):

- **Nemotron Speech Streaming** is a streaming transducer: audio is decoded as it arrives, so a
  partial transcript costs nothing extra and the final one is ready a chunk or two after the
  person stops.
- **Moonshine** decodes whole stretches of audio fast: the stream re-reads what arrived since the
  last commit for each partial (at most every 0.4 s of new audio), and an utterance longer than
  20 s is committed at its quietest moment, so no decode ever grows past that.

``push`` only buffers (it runs on the engine's event loop); ``partial`` and ``finish`` decode and
run off the loop. A model loads once per process and is shared by every session; Remove unloads
it first. Importing this module imports nothing from sherpa-onnx; loading a model reads only the
files Row-Bot downloaded, and sherpa-onnx makes no network calls.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from row_bot.voice import speech_models
from row_bot.voice.provider_base import VoiceProviderError, VoiceProviderStatus
from row_bot.voice.speech_models import SpeechModel
from row_bot.voice.streaming_base import INPUT_SAMPLE_RATE, TranscriptUpdate

logger = logging.getLogger(__name__)

#: The longest utterance kept, as for the segmented engines (five minutes).
MAX_UTTERANCE_BYTES = 300 * INPUT_SAMPLE_RATE * 2
#: Silence fed after the last word so a streaming encoder sees its right context.
TAIL_SECONDS = 0.48
#: Moonshine: new audio worth a fresh partial decode.
PARTIAL_STEP_BYTES = INPUT_SAMPLE_RATE * 2 * 4 // 10
#: Moonshine: an uncommitted stretch this long is committed at its quietest point...
COMMIT_BYTES = 20 * INPUT_SAMPLE_RATE * 2
#: ...looked for in its last four seconds, in 100 ms windows.
COMMIT_SEARCH_BYTES = 4 * INPUT_SAMPLE_RATE * 2
_WINDOW_BYTES = INPUT_SAMPLE_RATE * 2 // 10
#: Audio quieter than this (RMS, about -40 dBFS) holds no words: the pause after speaking.
QUIET_RMS = 0.01
UNAVAILABLE = "speech_model_missing"

Loader = Callable[[SpeechModel, Path, int], Any]


def _samples(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0


def default_threads() -> int:
    """Threads for one decode: a share of this computer's cores, 1 to 4."""

    return max(1, min(4, (os.cpu_count() or 2) // 3))


def load_sherpa(model: SpeechModel, folder: Path, threads: int) -> Any:
    """The sherpa-onnx recognizer for ``model``'s files in ``folder`` (processor, or CUDA when the
    installed build has it and the model runs best on a graphics card)."""

    import sherpa_onnx

    provider = "cuda" if model.runs_best_on == "gpu" and speech_models.cuda_runtime() else "cpu"
    path = lambda name: str(folder / model.file(name))  # noqa: E731
    if model.family == "transducer":
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=path("encoder"), decoder=path("decoder"), joiner=path("joiner"), tokens=path("tokens.txt"),
            num_threads=threads, sample_rate=INPUT_SAMPLE_RATE, feature_dim=80,
            decoding_method="greedy_search", enable_endpoint_detection=False, provider=provider)
    return sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
        encoder=path("encoder"), decoder=path("decoder"), tokens=path("tokens.txt"),
        num_threads=threads, provider=provider)


_loaded: dict[tuple[str, str], Any] = {}
_loaded_lock = threading.Lock()


def unload(model: SpeechModel) -> None:
    """Forget ``model``'s loaded recognizer (before Remove deletes its files)."""

    with _loaded_lock:
        for key in [key for key in _loaded if key[0] == model.id]:
            del _loaded[key]


class _Base:
    live_partials = True

    def __init__(self, model: SpeechModel, *, root: Path | None = None, load: Loader = load_sherpa,
                 threads: int | None = None) -> None:
        self.model = model
        self.root = root
        self.provider_id = f"local_{model.id}"
        self._load = load
        self._threads = threads or default_threads()

    def recognizer(self) -> Any:
        """The loaded model (loaded now when no session has loaded it yet)."""

        folder = speech_models.model_dir(self.model, self.root)
        key = (self.model.id, str(folder))
        with _loaded_lock:
            found = _loaded.get(key)
            if found is None:
                if not speech_models.installed(self.model, self.root):
                    raise VoiceProviderError(UNAVAILABLE)
                found = _loaded[key] = self._load(self.model, folder, self._threads)
            return found

    def language(self, requested: str | None) -> str | None:
        # The model's own language when it has one; one that detects languages reports what it heard.
        return self.model.language or requested


class TransducerSpeech(_Base):
    """Nemotron Speech Streaming: decoded as the person speaks."""

    def warm(self) -> None:
        stream = self.open()
        stream.push(b"\0\0" * (INPUT_SAMPLE_RATE // 2))
        stream.finish()

    def open(self, *, language: str | None = None) -> _TransducerStream:
        return _TransducerStream(self.recognizer(), self.language(language))


class _TransducerStream:
    def __init__(self, recognizer: Any, language: str | None) -> None:
        self._recognizer = recognizer
        self._stream: Any = recognizer.create_stream()
        self._language = language
        self._pending = bytearray()
        self._received = 0
        self._lock = threading.Lock()
        self._decoding = threading.Lock()
        self._text = ""

    def push(self, pcm16: bytes) -> None:
        with self._lock:
            if self._stream is None:
                return
            take = pcm16[:max(0, MAX_UTTERANCE_BYTES - self._received)]
            self._pending.extend(take)
            self._received += len(take)

    def _decode(self, tail: bool = False) -> Any:
        with self._lock:
            stream, data = self._stream, bytes(self._pending)
            self._pending.clear()
        if stream is None:
            return None
        if data:
            stream.accept_waveform(INPUT_SAMPLE_RATE, _samples(data))
        if tail:
            stream.accept_waveform(INPUT_SAMPLE_RATE, np.zeros(int(TAIL_SECONDS * INPUT_SAMPLE_RATE), np.float32))
            stream.input_finished()
        while self._recognizer.is_ready(stream):
            self._recognizer.decode_stream(stream)
        return stream

    def partial(self) -> TranscriptUpdate | None:
        with self._decoding:
            stream = self._decode()
            if stream is None:
                return None
            self._text = str(self._recognizer.get_result(stream) or "").strip()
            return TranscriptUpdate(self._text, final=False, language=self._language)

    def finish(self) -> TranscriptUpdate:
        with self._decoding:
            stream = self._decode(tail=True)
            text = str(self._recognizer.get_result(stream) or "").strip() if stream is not None else ""
        self.close()
        return TranscriptUpdate(text, final=True, language=self._language)

    def close(self) -> None:
        with self._lock:
            self._stream = None
            self._pending = bytearray()


class MoonshineSpeech(_Base):
    """Moonshine: fast whole-stretch decodes, committed at quiet points on long utterances."""

    def warm(self) -> None:
        stream = self.open()
        stream.push(b"\0\0" * (INPUT_SAMPLE_RATE // 2))
        stream.finish()

    def open(self, *, language: str | None = None) -> _MoonshineStream:
        return _MoonshineStream(self.recognizer(), self.language(language))


def quietest_cut(audio: bytes) -> int:
    """Where to commit a long stretch: the start of its quietest 100 ms window in the last
    :data:`COMMIT_SEARCH_BYTES` before :data:`COMMIT_BYTES` (an even byte offset)."""

    end = min(len(audio), COMMIT_BYTES)
    start = max(0, end - COMMIT_SEARCH_BYTES)
    best, best_at = None, end
    for at in range(start, end - _WINDOW_BYTES + 1, _WINDOW_BYTES):
        window = np.frombuffer(audio[at:at + _WINDOW_BYTES], dtype="<i2").astype(np.float32)
        energy = float(np.mean(window * window)) if window.size else 0.0
        if best is None or energy < best:
            best, best_at = energy, at
    return best_at


def quiet(audio: bytes) -> bool:
    samples = _samples(audio)
    return not samples.size or float(np.sqrt(np.mean(samples * samples))) < QUIET_RMS


class _MoonshineStream:
    def __init__(self, recognizer: Any, language: str | None) -> None:
        self._recognizer = recognizer
        self._language = language
        self._audio = bytearray()
        self._received = 0
        self._committed: list[str] = []
        self._decoded_at = 0
        self._current = ""
        self._closed = False
        self._lock = threading.Lock()
        self._decoding = threading.Lock()

    def push(self, pcm16: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            take = pcm16[:max(0, MAX_UTTERANCE_BYTES - self._received)]
            self._audio.extend(take)
            self._received += len(take)

    def _read(self, audio: bytes) -> str:
        if not audio:
            return ""
        stream = self._recognizer.create_stream()
        stream.accept_waveform(INPUT_SAMPLE_RATE, _samples(audio))
        self._recognizer.decode_stream(stream)
        return str(stream.result.text or "").strip()

    def _commit_long(self) -> None:
        while True:
            with self._lock:
                if self._closed or len(self._audio) <= COMMIT_BYTES:
                    return
                cut = quietest_cut(bytes(self._audio))
                head = bytes(self._audio[:cut])
                del self._audio[:cut]
                self._decoded_at = 0
            text = self._read(head)
            if text:
                self._committed.append(text)

    def _text(self) -> str:
        return " ".join(part for part in (*self._committed, self._current) if part)

    def _settled(self, audio: bytes) -> bool:
        """Nothing but quiet arrived since the last read: its words still stand."""

        return 0 < self._decoded_at < len(audio) and quiet(audio[self._decoded_at:])

    def partial(self) -> TranscriptUpdate | None:
        with self._decoding:
            self._commit_long()
            with self._lock:
                if self._closed:
                    return None
                audio = bytes(self._audio)
            if self._settled(audio):
                self._decoded_at = len(audio)
            elif len(audio) - self._decoded_at >= PARTIAL_STEP_BYTES:
                self._decoded_at = len(audio)
                self._current = self._read(audio)
            return TranscriptUpdate(self._text(), final=False, language=self._language)

    def finish(self) -> TranscriptUpdate:
        with self._decoding:
            self._commit_long()
            with self._lock:
                closed, audio = self._closed, bytes(self._audio)
            if not closed and self._decoded_at != len(audio) and not self._settled(audio):
                # Otherwise a partial already read this audio, or all of it but a quiet end.
                self._current = self._read(audio)
            text = "" if closed else self._text()
        self.close()
        return TranscriptUpdate(text, final=True, language=self._language)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._audio = bytearray()


def streaming_engine(model: SpeechModel, *, root: Path | None = None, load: Loader = load_sherpa,
                     threads: int | None = None) -> TransducerSpeech | MoonshineSpeech:
    kind = TransducerSpeech if model.family == "transducer" else MoonshineSpeech
    return kind(model, root=root, load=load, threads=threads)


class LocalStreamingSpeechProvider:
    """A downloaded catalog model as a speech-to-text provider: readiness for Talk, and whole
    recordings (``transcribe_bytes``) through the same stream."""

    def __init__(self, model: SpeechModel, *, root: Path | None = None, load: Loader = load_sherpa) -> None:
        self.model = model
        self.provider_id = f"local_{model.id}"
        self.display_name = model.name
        self._engine = streaming_engine(model, root=root, load=load)
        self._root = root

    def status(self) -> VoiceProviderStatus:
        ready = speech_models.runtime_available() and speech_models.installed(self.model, self._root)
        return VoiceProviderStatus(self.provider_id, self.display_name, ready,
                                   "" if ready else f"{self.model.name} is not downloaded.",
                                   local=True, unavailable_code=UNAVAILABLE)

    def streaming(self) -> TransducerSpeech | MoonshineSpeech:
        return self._engine

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        stream = self._engine.open()
        for offset in range(0, len(audio_bytes), 32_000):
            stream.push(audio_bytes[offset:offset + 32_000])
        return stream.finish().text


__all__ = ["LocalStreamingSpeechProvider", "MoonshineSpeech", "TransducerSpeech", "default_threads",
           "load_sherpa", "quietest_cut", "streaming_engine", "unload"]
