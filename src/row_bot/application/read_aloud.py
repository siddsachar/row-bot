"""Read aloud: a saved reply spoken with the chosen speech output, one part at a time.

The browser names the reply; its words are read here, from the conversation's
saved checkpoint, so a page can never have Row-Bot speak text it supplies. The
first part is a sentence or so, so speech starts quickly; the browser asks for
each next part while the one before it plays. Synthesis goes through the browser
voice service, with its per-session rate limit and concurrency slots.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from row_bot.application.client_platform import ClientPlatformError
from row_bot.voice.speech_policy import clean_for_speech, split_sentences

# The first part is short so the voice starts at once; later parts are a few sentences, ready before the one
# playing ends, and far fewer requests than the per-minute limit.
FIRST_PART_CHARS = 160
PART_CHARS = 600
# A longer reply is read up to here.
MAX_READ_CHARS = 60_000


@dataclass(frozen=True)
class ReadAloudPart:
    audio: bytes
    content_type: str
    part: int
    parts: int


def speech_parts(text: str) -> list[str]:
    """A reply's speakable words in order: whole sentences, the first part short, the rest up to PART_CHARS."""
    pieces: list[str] = []
    for line in clean_for_speech(text).splitlines():
        for sentence in split_sentences(line):
            pieces.extend(_wrapped(sentence, PART_CHARS))
    parts: list[str] = []
    current = ""
    for piece in pieces:
        limit = PART_CHARS if parts else FIRST_PART_CHARS
        if current and len(current) + 1 + len(piece) > limit:
            parts.append(current)
            current = piece
        else:
            current = f"{current} {piece}" if current else piece
    if current:
        parts.append(current)
    return parts


def _wrapped(sentence: str, limit: int) -> list[str]:
    """A sentence longer than *limit*, cut between words (a word longer than that is cut where it must be)."""
    pieces: list[str] = []
    current = ""
    for word in sentence.split():
        while len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:limit])
            word = word[limit:]
        if current and len(current) + 1 + len(word) > limit:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        pieces.append(current)
    return pieces


def _reply_text(conversation_id: str, message_id: str) -> str:
    from row_bot.runtime.checkpoint_reader import open_checkpoint

    with open_checkpoint(conversation_id) as reader:
        if reader is None:
            raise ClientPlatformError("not_found")
        record = next((record for _, record in reader.records() if record["message_id"] == message_id), None)
        if record is None or record["role"] != "assistant":
            raise ClientPlatformError("not_found")
        text = ""
        for chunk in reader.public_text_chunks(record):
            text += chunk
            if len(text) >= MAX_READ_CHARS:
                break
    return text[:MAX_READ_CHARS]


def read_aloud(service: Any, conversation_id: str, message_id: str, part: int, *, session_key: str,
               validate: Callable[[], None]) -> ReadAloudPart:
    """Part *part* of a saved reply as speech for the requesting browser."""
    transport = getattr(service, "dictation", None)
    if transport is None:
        raise ClientPlatformError("capability_unavailable")
    service._metadata(conversation_id)
    parts = speech_parts(_reply_text(conversation_id, message_id))
    if not parts:
        raise ClientPlatformError("nothing_to_read")
    if not 0 <= part < len(parts):
        raise ClientPlatformError("not_found")
    validate()
    speech = transport._browser_service().synthesize(session_key, parts[part], validate=validate)
    return ReadAloudPart(speech.audio, speech.content_type, part, len(parts))


def read_aloud_reason() -> str:
    """Whether Read aloud can speak now: "available", or the code that says what it needs. Never loads a model."""
    from row_bot.tts import TTSService
    from row_bot.voice.provider_base import VoiceProviderError
    from row_bot.voice.providers import speech_output_provider

    try:
        status = speech_output_provider(tts_service=TTSService()).status()
    except VoiceProviderError as error:
        return error.code
    return "available" if status.ready else status.unavailable_code
