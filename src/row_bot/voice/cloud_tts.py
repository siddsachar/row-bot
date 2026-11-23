"""Cloud text-to-speech for Row-Bot Voice, Read aloud and voice-note replies.

ElevenLabs (Flash v2.5), Cartesia (Sonic 3.6) and Gemini (3.8 Flash TTS), each asked from the
host with the person's key in a header, one sentence or clause at a time, for 24 kHz mono
16-bit PCM that streams back as it is made: Row-Bot Voice sends the first piece to the browser
before the rest of the sentence is spoken (``stream``), and a barge-in closes the request.
Plain ``httpx``; no vendor SDK. Saved pronunciations apply, as they do to Kokoro.

Voices are presets, or one described in words: Gemini's delivery ``style`` goes with each
request, and ElevenLabs makes a voice from a description (``design_voice``). Nothing here
takes a recording of anyone's voice.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
import io
import json
import logging
from typing import Any
import wave

import httpx

from row_bot.voice.cloud_speech import CloudSpeechOption, api_key
from row_bot.voice.provider_base import SynthesizedSpeech, VoiceProviderError, VoiceProviderStatus
from row_bot.voice.streaming_base import SpeechAudio

logger = logging.getLogger(__name__)

OUTPUT_RATE = 24_000
KEY_MISSING = "cloud_key_missing"
FAILED = "cloud_voice_failed"
DESIGN_FAILED = "voice_design_failed"
ELEVENLABS_API = "https://api.elevenlabs.io/v1"
ELEVENLABS_MODEL = "eleven_flash_v2_5"
CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-08-14"
CARTESIA_MODEL = "sonic-3.6"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_MODEL = "gemini-3.8-flash-tts"
DESIGNED_VOICE_NAME = "Row-Bot described voice"


def http_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))


Request = tuple[str, dict[str, str], dict[str, Any]]


def elevenlabs_request(text: str, voice: str, key: str, language: str | None = None) -> Request:
    body: dict[str, Any] = {"text": text, "model_id": ELEVENLABS_MODEL}
    if language:
        body["language_code"] = language
    return (f"{ELEVENLABS_API}/text-to-speech/{voice}/stream?output_format=pcm_{OUTPUT_RATE}",
            {"xi-api-key": key}, body)


def cartesia_request(text: str, voice: str, key: str, language: str | None = None) -> Request:
    body: dict[str, Any] = {"model_id": CARTESIA_MODEL, "transcript": text, "voice": {"id": voice},
                            "output_format": {"container": "raw", "encoding": "pcm_s16le",
                                              "sample_rate": OUTPUT_RATE}}
    if language:
        body["language"] = language
    return CARTESIA_URL, {"Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION}, body


def gemini_request(text: str, voice: str, key: str, style: str = "") -> Request:
    # The text is read as a verbatim transcript; how it sounds goes in the style annotation.
    content: dict[str, Any] = {"type": "text", "text": text}
    if style:
        content["annotations"] = [{"type": "speech_metadata", "style": style}]
    return GEMINI_URL, {"x-goog-api-key": key}, {
        "model": GEMINI_MODEL,
        "input": [{"type": "user_input", "content": [content]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
        "stream": True,
    }


def _gemini_audio(lines: Iterator[str]) -> Iterator[bytes]:
    """The PCM in Gemini's server-sent events: each ``step.delta`` of type audio."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("error"):
            raise VoiceProviderError(FAILED)
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "audio" and delta.get("data"):
            yield base64.b64decode(delta["data"])


def wav_bytes(pcm16: bytes, sample_rate: int = OUTPUT_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm16)
    return out.getvalue()


class CloudSpeechOutput:
    """A cloud speaking option as Row-Bot's speech output provider."""

    def __init__(self, option: CloudSpeechOption, *, voice: str, style: str = "",
                 client: Callable[[], httpx.Client] | None = None,
                 key: Callable[[CloudSpeechOption], str] = api_key) -> None:
        self.option = option
        self.provider_id = f"cloud_{option.id}"
        self.display_name = option.label
        self.voice = voice
        self.style = style
        self._client_factory = client
        self._client: httpx.Client | None = None
        self._key = key

    def status(self) -> VoiceProviderStatus:
        ready = bool(self._key(self.option))
        return VoiceProviderStatus(self.provider_id, self.display_name, ready,
                                   "" if ready else f"Needs your {self.option.company} key.",
                                   local=False, unavailable_code=KEY_MISSING)

    def _request(self, text: str, key: str, language: str | None) -> Request:
        if self.option.id == "elevenlabs":
            return elevenlabs_request(text, self.voice, key, language)
        if self.option.id == "cartesia":
            return cartesia_request(text, self.voice, key, language)
        return gemini_request(text, self.voice, key, self.style)  # Gemini tells the language itself.

    def language_of(self, text: str, hint: str | None) -> tuple[str | None, bool]:
        """The language *text* is spoken in (its own when clear, else *hint*: what the person was heard
        speaking; None when neither says) and whether this voice speaks it."""
        from row_bot.voice.languages import text_language

        found = text_language(text) or hint
        return found, found is None or found in self.option.languages

    def pcm(self, text: str, *, language: str | None = None) -> Iterator[bytes]:
        """*text* said aloud, as 24 kHz PCM pieces while they arrive (whole samples only).

        Closing the iterator closes the request, so a barge-in stops paying for the rest."""
        from row_bot.sound import respelled

        clean = str(text or "").strip()
        if not clean:
            return
        key = self._key(self.option)
        if not key:
            raise VoiceProviderError(KEY_MISSING)
        # Only the voice hears the person's pronunciations; captions and the chat keep the written words.
        url, headers, body = self._request(respelled(clean), key, language)
        if self._client is None:
            self._client = (self._client_factory or http_client)()
        carry = b""
        try:
            with self._client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    # Only the status: a provider's error text can quote the words.
                    logger.warning("%s speech failed: HTTP %s", self.option.company, response.status_code)
                    raise VoiceProviderError(FAILED)
                pieces = (_gemini_audio(response.iter_lines()) if self.option.id == "gemini_tts"
                          else response.iter_bytes())
                for piece in pieces:
                    data = carry + piece
                    whole = len(data) - len(data) % 2
                    carry = data[whole:]
                    if whole:
                        yield data[:whole]
        except httpx.HTTPError as exc:
            logger.warning("%s speech failed: %s", self.option.company, type(exc).__name__)
            raise VoiceProviderError(FAILED) from None

    def synthesize(self, text: str, *, language: str | None = None) -> SynthesizedSpeech:
        found, speaks = self.language_of(text, language)
        if not speaks:
            return SynthesizedSpeech(b"", "audio/wav", unspoken=found)
        return SynthesizedSpeech(wav_bytes(b"".join(self.pcm(text, language=found))), "audio/wav")

    def sentence_speech(self) -> CloudSentenceSpeech:
        return CloudSentenceSpeech(self)


class CloudSentenceSpeech:
    """The ``SentenceSpeech`` seam over a cloud voice, with its pieces as they arrive."""

    sample_rate = OUTPUT_RATE

    def __init__(self, output: CloudSpeechOutput) -> None:
        self._output = output
        self.provider_id = output.provider_id

    def warm(self) -> None:
        # No call to make: a session costs nothing until Row-Bot speaks.
        status = self._output.status()
        if not status.ready:
            raise VoiceProviderError(status.unavailable_code)

    def stream(self, text: str, *, language: str | None = None) -> Iterator[SpeechAudio]:
        """*text*'s speech as it is made; one empty piece with ``unspoken`` set when this voice can't
        speak its language (*language* is the hint: what the person was heard speaking)."""
        found, speaks = self._output.language_of(text, language)
        if not speaks:
            yield SpeechAudio(b"", OUTPUT_RATE, unspoken=found)
            return
        for piece in self._output.pcm(text, language=found):
            yield SpeechAudio(piece, OUTPUT_RATE)

    def synthesize(self, text: str, *, language: str | None = None) -> SpeechAudio:
        found, speaks = self._output.language_of(text, language)
        if not speaks:
            return SpeechAudio(b"", OUTPUT_RATE, unspoken=found)
        return SpeechAudio(b"".join(self._output.pcm(text, language=found)), OUTPUT_RATE)


def design_voice(description: str, *, client: Callable[[], httpx.Client] | None = None,
                 key: str | None = None) -> str:
    """A new ElevenLabs voice made from *description* (voice design, from words only), saved in the
    person's ElevenLabs account; returns its id. Uses the first of the previews ElevenLabs makes."""
    from row_bot.voice.cloud_speech import option

    found = option("elevenlabs", "speak")
    secret = key if key is not None else (api_key(found) if found is not None else "")
    if not secret:
        raise VoiceProviderError(KEY_MISSING)
    headers = {"xi-api-key": secret}
    try:
        with (client or http_client)() as http:
            designed = http.post(f"{ELEVENLABS_API}/text-to-voice/design", headers=headers, json={
                "voice_description": description, "model_id": "eleven_multilingual_ttv_v2",
                "auto_generate_text": True})
            if designed.status_code != 200:
                logger.warning("ElevenLabs voice design failed: HTTP %s", designed.status_code)
                raise VoiceProviderError(DESIGN_FAILED)
            previews = designed.json().get("previews") or []
            generated = previews[0].get("generated_voice_id") if previews and isinstance(previews[0], dict) else ""
            if not generated:
                raise VoiceProviderError(DESIGN_FAILED)
            saved = http.post(f"{ELEVENLABS_API}/text-to-voice", headers=headers, json={
                "voice_name": DESIGNED_VOICE_NAME, "voice_description": description,
                "generated_voice_id": generated})
            if saved.status_code != 200:
                logger.warning("ElevenLabs voice save failed: HTTP %s", saved.status_code)
                raise VoiceProviderError(DESIGN_FAILED)
            voice_id = str(saved.json().get("voice_id") or "")
    except httpx.HTTPError as exc:
        logger.warning("ElevenLabs voice design failed: %s", type(exc).__name__)
        raise VoiceProviderError(DESIGN_FAILED) from None
    except ValueError:
        raise VoiceProviderError(DESIGN_FAILED) from None
    if not voice_id:
        raise VoiceProviderError(DESIGN_FAILED)
    return voice_id
