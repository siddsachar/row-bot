"""Cloud speech-to-text for Row-Bot Voice, behind the ``StreamingSpeechToText`` seam.

Deepgram (Nova-3), AssemblyAI (STT 3.7 Realtime), OpenAI (gpt-live-transcribe) and Gemini
(3.5 Transcribe Live), each over its own streaming WebSocket, opened from the host with the
person's key in a header (never in a URL, a log or the browser). Plain ``websockets``; no
vendor SDK.

One utterance is one socket: it opens when the person starts speaking, carries only that
utterance's audio, and closes when its final transcript arrives (or the turn is dropped), so
nothing is sent while nobody speaks. ``push`` only buffers; a thread per utterance sends the
audio in 100 ms pieces, reads the provider's partial and final words, and ends the socket.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
import json
import logging
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlencode

import numpy as np

from row_bot.voice.cloud_speech import CloudSpeechOption, api_key
from row_bot.voice.provider_base import Heard as HeardText, VoiceProviderError, VoiceProviderStatus
from row_bot.voice.streaming_base import INPUT_SAMPLE_RATE, TranscriptUpdate

logger = logging.getLogger(__name__)

#: The longest utterance kept, as for the local engines (five minutes).
MAX_UTTERANCE_BYTES = 300 * INPUT_SAMPLE_RATE * 2
#: Audio goes out in 100 ms pieces (AssemblyAI takes 20 ms to 1 s; Gemini asks for about 100 ms).
CHUNK_BYTES = INPUT_SAMPLE_RATE * 2 // 10
MIN_CHUNK_BYTES = INPUT_SAMPLE_RATE * 2 // 50
#: How long a final transcript may take once the utterance ended, and the quiet that means it is all in.
FINAL_SECONDS = 4.0
SETTLE_SECONDS = 0.6
CONNECT_SECONDS = 10.0
POLL_SECONDS = 0.02
KEY_MISSING = "cloud_key_missing"
FAILED = "cloud_speech_failed"


class Socket(Protocol):
    def send(self, message: str | bytes) -> None: ...
    def recv(self, timeout: float | None = None) -> str | bytes: ...
    def close(self) -> None: ...


Connect = Callable[[str, dict[str, str]], Socket]


def connect_socket(url: str, headers: dict[str, str]) -> Socket:
    """The provider's WebSocket, from the host; the key travels in *headers* only."""
    from websockets.sync.client import connect

    return connect(url, additional_headers=headers, max_size=1 << 20, open_timeout=CONNECT_SECONDS,
                   close_timeout=2)


class Heard:
    """The words so far, in the order they were heard: final pieces, and the one still changing."""

    def __init__(self) -> None:
        self.parts: dict[Any, str] = {}
        self.final: set[Any] = set()
        self.language: str | None = None
        self.final_after_finish = False
        self.done = False

    def put(self, key: Any, text: str, *, final: bool) -> None:
        self.parts[key] = text
        if final:
            self.final.add(key)
        else:
            self.final.discard(key)

    def text(self) -> str:
        return " ".join(" ".join(part.split()) for part in self.parts.values() if part.strip())

    def settled(self) -> bool:
        return self.final_after_finish and all(key in self.final for key in self.parts)


def _upsample_to_24k(pcm16: bytes) -> bytes:
    """16 kHz to 24 kHz (OpenAI takes 24 kHz PCM only), by linear interpolation."""
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
    if not samples.size:
        return b""
    count = samples.size * 3 // 2
    positions = np.arange(count, dtype=np.float32) * (2 / 3)
    return np.interp(positions, np.arange(samples.size), samples).round().astype("<i2").tobytes()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Dialect:
    """One provider's streaming protocol."""

    needs_ready = False

    def url(self, language: str | None) -> str:
        raise NotImplementedError

    def headers(self, key: str) -> dict[str, str]:
        raise NotImplementedError

    def opening(self, language: str | None) -> list[str]:
        return []

    def started(self) -> list[str]:
        return []

    def audio(self, pcm16: bytes) -> str | bytes:
        return pcm16

    def finishing(self) -> list[str]:
        return []

    def closing(self) -> list[str]:
        return []

    def read(self, message: dict[str, Any], heard: Heard) -> bool:
        """Take one message into *heard*; True when the provider is now ready for audio."""
        raise NotImplementedError


class Deepgram(Dialect):
    def __init__(self) -> None:
        self._segment = 0

    def url(self, language: str | None) -> str:
        params = {"model": "nova-3", "encoding": "linear16", "sample_rate": INPUT_SAMPLE_RATE, "channels": 1,
                  "interim_results": "true", "punctuate": "true", "smart_format": "true",
                  "language": language or "multi"}
        return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"

    def headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Token {key}"}

    def finishing(self) -> list[str]:
        return [json.dumps({"type": "Finalize"})]

    def closing(self) -> list[str]:
        return [json.dumps({"type": "CloseStream"})]

    def read(self, message: dict[str, Any], heard: Heard) -> bool:
        if message.get("type") != "Results":
            return False
        alternatives = (message.get("channel") or {}).get("alternatives") or [{}]
        best = alternatives[0] if isinstance(alternatives[0], dict) else {}
        languages = best.get("languages")
        if isinstance(languages, list) and languages:
            heard.language = str(languages[0])
        text = str(best.get("transcript") or "")
        if message.get("is_final"):
            heard.put(self._segment, text, final=True)
            self._segment += 1
        else:
            heard.put(self._segment, text, final=False)
        if message.get("from_finalize"):
            heard.done = True
        return False


class AssemblyAI(Dialect):
    def url(self, language: str | None) -> str:
        params: dict[str, Any] = {"sample_rate": INPUT_SAMPLE_RATE, "encoding": "pcm_s16le",
                                  "speech_model": "assemblyai-stt-3-7-realtime"}
        if language:
            params["language_codes"] = language
        else:
            params["language_detection"] = "true"
        return f"wss://streaming.assemblyai.com/v3/ws?{urlencode(params)}"

    def headers(self, key: str) -> dict[str, str]:
        return {"Authorization": key}

    def finishing(self) -> list[str]:
        return [json.dumps({"type": "Terminate"})]

    def read(self, message: dict[str, Any], heard: Heard) -> bool:
        kind = message.get("type")
        if kind == "Turn":
            if message.get("language_code"):
                heard.language = str(message["language_code"])
            heard.put(int(message.get("turn_order") or 0), str(message.get("transcript") or ""),
                      final=bool(message.get("end_of_turn")))
        elif kind == "Termination":
            heard.done = True
        return False


class OpenAITranscribe(Dialect):
    def __init__(self) -> None:
        self._language: str | None = None

    def url(self, language: str | None) -> str:
        return "wss://api.openai.com/v1/realtime?intent=transcription"

    def headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    def opening(self, language: str | None) -> list[str]:
        transcription: dict[str, Any] = {"model": "gpt-live-transcribe"}
        if language:
            transcription["languages"] = [language]
        return [json.dumps({"type": "session.update", "session": {"type": "transcription", "audio": {"input": {
            "format": {"type": "audio/pcm", "rate": 24_000},
            "transcription": transcription,
            "turn_detection": None,
        }}}})]

    def audio(self, pcm16: bytes) -> str:
        return json.dumps({"type": "input_audio_buffer.append", "audio": _b64(_upsample_to_24k(pcm16))})

    def finishing(self) -> list[str]:
        return [json.dumps({"type": "input_audio_buffer.commit"})]

    def read(self, message: dict[str, Any], heard: Heard) -> bool:
        kind = str(message.get("type") or "")
        item = message.get("item_id") or "item"
        if kind == "conversation.item.input_audio_transcription.delta":
            heard.put(item, heard.parts.get(item, "") + str(message.get("delta") or ""), final=False)
        elif kind == "conversation.item.input_audio_transcription.completed":
            heard.put(item, str(message.get("transcript") or ""), final=True)
        elif kind == "error":
            code = str((message.get("error") or {}).get("code") or "")
            if code == "input_audio_buffer_commit_empty":
                heard.done = True  # Nothing was said.
            else:
                raise VoiceProviderError(FAILED)
        return False


class GeminiTranscribe(Dialect):
    needs_ready = True

    def __init__(self) -> None:
        self._segment = 0

    def url(self, language: str | None) -> str:
        from row_bot.voice.gemini_live import GEMINI_LIVE_URL

        return GEMINI_LIVE_URL

    def headers(self, key: str) -> dict[str, str]:
        return {"x-goog-api-key": key}

    def opening(self, language: str | None) -> list[str]:
        return [json.dumps({"setup": {
            "model": "models/gemini-3.5-transcribe-live",
            "generationConfig": {"responseModalities": ["TEXT"]},
            # An empty list detects the language.
            "inputAudioTranscription": {"languageCodes": [language] if language else []},
            # Row-Bot decides where a turn ends; the utterance is one activity.
            "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
        }})]

    def started(self) -> list[str]:
        return [json.dumps({"realtimeInput": {"activityStart": {}}})]

    def audio(self, pcm16: bytes) -> str:
        return json.dumps({"realtimeInput": {"audio": {"data": _b64(pcm16),
                                                       "mimeType": f"audio/pcm;rate={INPUT_SAMPLE_RATE}"}}})

    def finishing(self) -> list[str]:
        return [json.dumps({"realtimeInput": {"activityEnd": {}}})]

    def read(self, message: dict[str, Any], heard: Heard) -> bool:
        if "setupComplete" in message:
            return True
        content = message.get("serverContent") or {}
        interim = content.get("interimInputTranscription") or {}
        final = content.get("inputTranscription") or {}
        if interim.get("text") is not None:
            heard.put(("interim", self._segment), str(interim.get("text") or ""), final=False)
        if final.get("text") is not None:
            heard.parts.pop(("interim", self._segment), None)
            heard.put(self._segment, str(final.get("text") or ""), final=True)
            if final.get("languageCode"):
                heard.language = str(final["languageCode"])
            self._segment += 1
        if content.get("turnComplete"):
            heard.done = True
        return False


DIALECTS: dict[str, Callable[[], Dialect]] = {
    "deepgram": Deepgram, "assemblyai": AssemblyAI, "openai_transcribe": OpenAITranscribe,
    "gemini_transcribe": GeminiTranscribe,
}


class CloudSpeechStream:
    """One utterance on one provider socket."""

    def __init__(self, dialect: Dialect, key: str, language: str | None, connect: Connect, *,
                 final_seconds: float = FINAL_SECONDS, settle_seconds: float = SETTLE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._dialect = dialect
        self._language = language
        self._connect = connect
        self._final_seconds = final_seconds
        self._settle_seconds = settle_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._accepted = 0
        self._heard = Heard()
        self._shown = ""
        self._failed = False
        self._finishing = threading.Event()
        self._closed = threading.Event()
        self._ended = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(key,), name="row-bot-cloud-stt", daemon=True)
        self._thread.start()

    # ── the seam ────────────────────────────────────────────────────────

    def push(self, pcm16: bytes) -> None:
        with self._lock:
            if self._closed.is_set() or self._finishing.is_set():
                return
            room = MAX_UTTERANCE_BYTES - self._accepted
            if room > 0:
                piece = pcm16[:room]
                self._pending.extend(piece)
                self._accepted += len(piece)

    def partial(self) -> TranscriptUpdate | None:
        with self._lock:
            text = self._heard.text()
            if not text or text == self._shown:
                return None
            self._shown = text
            return TranscriptUpdate(text, final=False, language=self._heard.language or self._language)

    def finish(self) -> TranscriptUpdate:
        self._finishing.set()
        self._ended.wait(self._final_seconds + CONNECT_SECONDS)
        self.close()
        with self._lock:
            text, language, failed = self._heard.text(), self._heard.language or self._language, self._failed
        if failed and not text:
            raise VoiceProviderError(FAILED)
        return TranscriptUpdate(text, final=True, language=language)

    def close(self) -> None:
        self._closed.set()

    # ── the socket ──────────────────────────────────────────────────────

    def _take_audio(self, *, everything: bool) -> list[bytes]:
        with self._lock:
            pieces = []
            while len(self._pending) >= CHUNK_BYTES:
                pieces.append(bytes(self._pending[:CHUNK_BYTES]))
                del self._pending[:CHUNK_BYTES]
            if everything and self._pending:
                rest = bytes(self._pending)
                pieces.append(rest + b"\0" * max(0, MIN_CHUNK_BYTES - len(rest)))
                self._pending.clear()
            return pieces

    def _run(self, key: str) -> None:
        socket: Socket | None = None
        try:
            socket = self._connect(self._dialect.url(self._language), self._dialect.headers(key))
            for message in self._dialect.opening(self._language):
                socket.send(message)
            ready = not self._dialect.needs_ready
            started = False
            finish_sent_at: float | None = None
            heard_at = self._clock()
            while not self._closed.is_set():
                if ready and not started:
                    started = True
                    for message in self._dialect.started():
                        socket.send(message)
                finishing = self._finishing.is_set()
                if ready:
                    for piece in self._take_audio(everything=finishing):
                        socket.send(self._dialect.audio(piece))
                    if finishing and finish_sent_at is None:
                        for message in self._dialect.finishing():
                            socket.send(message)
                        finish_sent_at = heard_at = self._clock()
                try:
                    raw = socket.recv(timeout=POLL_SECONDS)
                except TimeoutError:
                    now = self._clock()
                    if finish_sent_at is not None and (now - heard_at >= self._settle_seconds
                                                       or now - finish_sent_at >= self._final_seconds):
                        break
                    continue
                message = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                if not isinstance(message, dict):
                    continue
                with self._lock:
                    finals = set(self._heard.final)
                    if self._dialect.read(message, self._heard):
                        ready = True
                    if finish_sent_at is not None:
                        heard_at = self._clock()
                        # Words made final after the utterance ended: the rest of it is in.
                        if self._heard.final - finals:
                            self._heard.final_after_finish = True
                    done = self._heard.done or self._heard.settled()
                if done:
                    break
            for message in self._dialect.closing():
                socket.send(message)
        except Exception as exc:
            from websockets.exceptions import ConnectionClosedOK

            if not (isinstance(exc, ConnectionClosedOK) and self._finishing.is_set()):
                # Only the kind of failure: a message could quote audio, words or a header.
                logger.warning("Cloud speech-to-text stopped: %s", type(exc).__name__)
                with self._lock:
                    self._failed = True
        finally:
            if socket is not None:
                try:
                    socket.close()
                except Exception:
                    pass
            self._ended.set()


class CloudSpeechToText:
    """A cloud listening option as a speech-to-text provider and a streaming engine."""

    live_partials = True

    def __init__(self, option: CloudSpeechOption, *, language: str | None = None,
                 connect: Connect | None = None, key: Callable[[CloudSpeechOption], str] = api_key) -> None:
        self.option = option
        self.provider_id = f"cloud_{option.id}"
        self.display_name = option.label
        self.language = language
        self._connect = connect
        self._key = key

    def status(self) -> VoiceProviderStatus:
        ready = bool(self._key(self.option))
        return VoiceProviderStatus(self.provider_id, self.display_name, ready,
                                   "" if ready else f"Needs your {self.option.company} key.",
                                   local=False, unavailable_code=KEY_MISSING)

    def streaming(self) -> CloudSpeechToText:
        return self

    def warm(self) -> None:
        # Nothing to load, and no call to make: a session costs nothing until someone speaks.
        if not self._key(self.option):
            raise VoiceProviderError(KEY_MISSING)

    def open(self, *, language: str | None = None) -> CloudSpeechStream:
        key = self._key(self.option)
        if not key:
            raise VoiceProviderError(KEY_MISSING)
        return CloudSpeechStream(DIALECTS[self.option.id](), key, language or self.language,
                                 self._connect or connect_socket)

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        return self.transcribe_heard(audio_bytes, language=self.language).text

    def transcribe_heard(self, audio_bytes: bytes, *, language: str | None) -> HeardText:
        """A whole recording through the same stream, with the language the provider heard."""
        stream = self.open(language=language)
        for offset in range(0, len(audio_bytes), CHUNK_BYTES):
            stream.push(audio_bytes[offset:offset + CHUNK_BYTES])
        heard = stream.finish()
        return HeardText(heard.text, heard.language)
