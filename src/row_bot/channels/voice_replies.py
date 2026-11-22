"""Voice-note replies on chat apps (V9, VD9).

When someone sends a voice note on Telegram or WhatsApp and the channel's "Reply to voice notes with a
voice note" switch is on (it is until the person turns it off), the turn is spoken: it runs with
``voice_mode``, so the answer is written for listening (the turn context's voice line), and the answer
goes back as a native voice note right after its text. The text always goes, so code, links and tables
still arrive. The voice is the speech output chosen in Settings › Voice (Kokoro, with the person's saved
pronunciations); the whole answer is said, without what is better read than heard.

Without Kokoro, or when making the note fails or it would be over the platform's size limit, the reply
is the text alone. Nothing downloads here.
"""
from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass
from typing import Any

from row_bot.channels.base import ConfigField

log = logging.getLogger("row_bot.channels.voice_replies")

VOICE_REPLIES_KEY = "voice_replies"
VOICE_REPLIES_LABEL = "Reply to voice notes with a voice note"
_HELP_READY = (
    "When someone sends a voice note, the answer comes back as a voice note too, after its text. "
    "Uses Row-Bot's voice (Kokoro)."
)
_HELP_NO_KOKORO = (
    "Needs Row-Bot's voice (Kokoro), which isn't installed: answers stay text until you install it "
    "in Settings › Voice."
)

# Voice notes are OGG/Opus on both Telegram and WhatsApp: mono, 48 kHz, speech bitrate.
CONTENT_TYPE = "audio/ogg"
OPUS_RATE = 48_000
OPUS_BITRATE = 32_000
# The largest voice note each platform takes: Telegram's Bot API sends up to 50 MB, WhatsApp audio up
# to 16 MB. At 32 kbit/s that is over three hours and an hour of speech; a longer answer goes as text.
TELEGRAM_MAX_BYTES = 50 * 1024 * 1024
WHATSAPP_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class VoiceNote:
    """An answer said aloud, ready to send as a voice note."""

    audio: bytes
    seconds: float
    content_type: str = CONTENT_TYPE


def _speech_output() -> Any:
    from row_bot.tts import TTSService
    from row_bot.voice.providers import speech_output_provider

    return speech_output_provider(tts_service=TTSService())


def speech_output_ready() -> bool:
    """Whether the chosen speech output (Kokoro) is installed and can speak."""
    try:
        return _speech_output().status().ready is True
    except Exception:
        return False


def voice_replies_field() -> ConfigField:
    """The channel's switch, saved in its channel config; its note says when Kokoro is missing."""
    return ConfigField(
        key=VOICE_REPLIES_KEY,
        label=VOICE_REPLIES_LABEL,
        field_type="toggle",
        storage="config",
        default=True,
        help_text=_HELP_READY if speech_output_ready() else _HELP_NO_KOKORO,
    )


def voice_replies_on(channel: str) -> bool:
    """The channel's switch: on unless the person turned it off."""
    from row_bot.channels import config

    return config.get(channel, VOICE_REPLIES_KEY, True) is not False


def reply_by_voice(channel: str, *, voice_note_in: bool) -> bool:
    """Whether this turn is spoken: the person sent a voice note, the switch is on and Kokoro can speak."""
    return bool(voice_note_in) and voice_replies_on(channel) and speech_output_ready()


def spoken_config(config: dict) -> dict:
    """*config* for a spoken turn: ``voice_mode`` set for this turn only, never saved with the chat."""
    return {**config, "configurable": {**(config.get("configurable") or {}), "voice_mode": True}}


def make_voice_note(answer: str, *, max_bytes: int) -> VoiceNote | None:
    """*answer* said aloud as an OGG/Opus voice note; None sends the text alone.

    None when there is nothing to say once code, tables and links are left out, when the note would be
    over *max_bytes* (judged from the words before any synthesis, then from the encoded note), or when
    synthesis or encoding fails.
    """
    from row_bot.sound import speaking_seconds
    from row_bot.voice.speech_policy import clean_for_speech

    words = clean_for_speech(answer)
    if not words:
        return None
    if speaking_seconds(words) * OPUS_BITRATE / 8 > max_bytes:
        log.info("An answer is too long for one voice note; it goes as text")
        return None
    try:
        note = ogg_opus(_speech_output().synthesize(words).audio)
    except Exception:
        log.warning("A voice-note reply could not be made; the text goes alone", exc_info=True)
        return None
    if not note.audio:
        return None
    if len(note.audio) > max_bytes:
        log.info("A voice note came out over the platform's limit; the text goes alone")
        return None
    return note


def ogg_opus(wav: bytes) -> VoiceNote:
    """16-bit WAV (Kokoro's output) as a mono 48 kHz OGG/Opus voice note."""
    import av
    import numpy as np

    with wave.open(io.BytesIO(wav), "rb") as reader:
        if reader.getsampwidth() != 2:
            raise ValueError("expected 16-bit audio")
        rate, channels = reader.getframerate(), reader.getnchannels()
        frames = reader.readframes(reader.getnframes())
    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels)[:, 0]
    if not len(samples):
        return VoiceNote(b"", 0.0)
    frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples).reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = rate
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OPUS_RATE)
    output = io.BytesIO()
    with av.open(output, "w", format="ogg") as container:
        stream = container.add_stream("libopus", rate=OPUS_RATE, layout="mono")
        stream.bit_rate = OPUS_BITRATE
        for piece in [*resampler.resample(frame), *resampler.resample(None)]:
            for packet in stream.encode(piece):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return VoiceNote(output.getvalue(), len(samples) / float(rate))
