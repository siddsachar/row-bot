"""Row-Bot's streaming speech seams for Row-Bot Voice.

The duplex engine (``row_bot.voice.engine``) talks only to these protocols, so
a speech-to-text or text-to-speech engine can change without touching turn
taking, barge-in or the agent path. Every method here is blocking and the
engine calls it off its event loop. None of them may open a host microphone
or speaker, or download a model: installation is an explicit, consented
Settings action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: The engine's audio in: 16 kHz mono signed 16-bit little-endian PCM.
INPUT_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class TranscriptUpdate:
    """What a speech-to-text stream heard so far, or in all (``final``)."""

    text: str
    final: bool
    # The language heard (a BCP 47 tag): the model's own, or what it detected; None when unknown.
    language: str | None = None


class SpeechToTextStream(Protocol):
    """One utterance: audio goes in as it arrives, text comes out."""

    def push(self, pcm16: bytes) -> None:
        """Add audio. Cheap and non-blocking; a streaming engine may decode here."""
        ...

    def partial(self) -> TranscriptUpdate | None:
        """The words heard so far, or None when there is nothing new. May block."""
        ...

    def finish(self) -> TranscriptUpdate:
        """The final transcript of the utterance. Blocks until it is ready."""
        ...

    def close(self) -> None:
        """Drop the utterance's audio. Safe to call more than once."""
        ...


class StreamingSpeechToText(Protocol):
    provider_id: str
    # True when ``partial`` is cheap (the model decodes as audio arrives): the engine then reads
    # partials as the person speaks, for live captions and to hear words during a barge-in.
    live_partials: bool

    def warm(self) -> None:
        """Load the model now, so the first turn does not pay for it."""
        ...

    def open(self, *, language: str | None = None) -> SpeechToTextStream:
        """One utterance in *language* (Settings › Voice › Language; None detects it)."""
        ...


@dataclass(frozen=True)
class SpeechAudio:
    """Synthesised speech for one sentence or clause: mono s16le PCM.

    No audio with ``unspoken`` set: the text is in a language the voice cannot speak (its code).
    """

    pcm16: bytes
    sample_rate: int
    unspoken: str | None = None


class SentenceSpeech(Protocol):
    """Text-to-speech, one sentence or clause at a time.

    A voice that streams (a cloud voice) also has ``stream(text)``: an iterator of ``SpeechAudio``
    pieces as they are made, whose ``close()`` cancels the rest. The engine then sends the first
    piece before the sentence is finished, and closes the iterator at a barge-in.
    """

    provider_id: str

    def warm(self) -> None:
        ...

    def synthesize(self, text: str, *, language: str | None = None) -> SpeechAudio:
        """Speech for ``text``; empty PCM when there is nothing to say. *language* is a hint: the
        language the person was heard speaking."""
        ...
