"""Voice files and transcripts for video work (5.3.1 B): Row-Bot's local voice reads a script whole, and local
Whisper transcribes a recording or a clip with each word's time. Nothing leaves this computer.
"""
from __future__ import annotations

import io
import re
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

MAX_SPEAK_CHARS = 20_000
# A voice file's own speed stays near normal: Kokoro sounds harsh dragged out or rushed.
SPEAK_SPEEDS = (0.9, 1.5)
MAX_TRANSCRIBE_SECONDS = 2 * 60 * 60


class SoundError(ValueError):
    """Why a voice file or a transcript can't be made, in plain words."""


@dataclass(frozen=True)
class Speech:
    wav: bytes
    seconds: float
    voice: str


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    words: tuple[tuple[float, float, str], ...] = field(default_factory=tuple)


# About how fast the voices speak: what a line's length is guessed from before it is voiced.
WORDS_PER_SECOND = 2.6


def speaking_seconds(text: str) -> float:
    """About how long *text* takes to say."""
    return len(str(text or "").split()) / WORDS_PER_SECOND


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate() or 1)


# Said the way people say them (R10): the voice read "Row-Bot" as "robot" and "5.3.3" as "five point three
# three". A pronunciation the caller gives goes first.
_SAID = ((re.compile(r"\bRow-Bot\b", re.IGNORECASE), "Row Bot"),
         (re.compile(r"\bv?(\d+)\.(\d+)\.(\d+)\b"), lambda found: " point ".join(found.groups())))


def spoken(text: str) -> str:
    """*text* as the voice should read it: Row-Bot's name and version numbers."""
    for pattern, said in _SAID:
        text = pattern.sub(said, text)
    return text


def speak(text: str, *, voice: str = "", speed: float = 0.0, pronunciations: dict[str, str] | None = None) -> Speech:
    """The whole *text* in a local voice: never cut to a spoken reply's few sentences."""
    from row_bot.tts import VOICE_CATALOG, TTSService

    text = str(text or "").strip()
    if not text:
        raise SoundError("Give the words to speak.")
    if len(text) > MAX_SPEAK_CHARS:
        raise SoundError(f"That's too long for one voice file: keep it under {MAX_SPEAK_CHARS:,} characters, or "
                         "make one file per scene.")
    if voice and voice not in VOICE_CATALOG:
        raise SoundError(f"There's no voice called {voice}. Voices: {', '.join(sorted(VOICE_CATALOG))}.")
    if speed and not SPEAK_SPEEDS[0] <= speed <= SPEAK_SPEEDS[1]:
        raise SoundError(f"Speed is between {SPEAK_SPEEDS[0]:g} and {SPEAK_SPEEDS[1]:g} (1 is normal). To fill more "
                         "time, write more words or cut the video shorter: a slowed voice sounds harsh.")
    tts = TTSService()
    if not tts.is_installed():
        raise SoundError("Speaking needs Row-Bot's voice (Kokoro). Install it in Settings › Voice, then try again.")
    tts.reload_settings()
    for word, said in (pronunciations or {}).items():
        if str(word).strip() and str(said).strip():
            text = re.sub(rf"\b{re.escape(str(word).strip())}\b", str(said).strip(), text, flags=re.IGNORECASE)
    text = spoken(text)
    chosen = voice or tts.voice
    data = tts.synthesize_wav_bytes(text, voice=chosen, speed=speed or None, truncate=False)
    if not data:
        raise SoundError("There was nothing to say once code and formatting were left out.")
    return Speech(data, wav_seconds(data), chosen)


def decoded_speech(path: Path) -> bytes:
    """The first two hours of a file's sound as 16 kHz mono 16-bit PCM, read with PyAV (the decoder Whisper itself
    uses), so transcribing needs no ffmpeg."""
    import av

    limit = 16000 * 2 * MAX_TRANSCRIBE_SECONDS
    decoded = bytearray()
    try:
        with av.open(str(path), metadata_errors="ignore") as container:
            if not container.streams.audio:
                raise SoundError("That file has no sound Row-Bot can read.")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for piece in resampler.resample(frame):
                    decoded += piece.to_ndarray().tobytes()
                if len(decoded) >= limit:
                    break
            else:
                for piece in resampler.resample(None):
                    decoded += piece.to_ndarray().tobytes()
    except (av.error.FFmpegError, OSError, ValueError):
        raise SoundError("That file has no sound Row-Bot can read.") from None
    return bytes(decoded[:limit])


# A peak below this is silence: nothing a viewer would hear.
SILENT_PEAK = 0.001  # -60 dBFS


def has_sound(path: Path) -> bool | None:
    """Whether a video or sound file has something to hear: a sound track that isn't silence. A screen recording
    has none, so a video cut only from recordings plays silent, there and on X. None when it can't be read."""
    import av

    try:
        with av.open(str(path), metadata_errors="ignore") as container:
            if not container.streams.audio:
                return False
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for piece in resampler.resample(frame):
                    if abs(piece.to_ndarray().astype("int32")).max(initial=0) > SILENT_PEAK * 32768:
                        return True
    except (av.error.FFmpegError, OSError, ValueError):
        return None
    return False


def transcribe(path: Path) -> tuple[list[Segment], str]:
    """Each stretch of speech in an audio or video file, with each word's time, and the language heard."""
    from row_bot.voice import get_voice_service

    voice = get_voice_service()
    if not voice.whisper_model_available():
        raise SoundError("Transcribing needs the Whisper speech model. Install it in Settings › Voice, then try "
                         "again.")
    decoded = decoded_speech(path)
    if len(decoded) < 3200:
        raise SoundError("That file has no sound Row-Bot can read.")
    return voice.transcribe_words(decoded)


def transcribe_bytes(data: bytes, suffix: str) -> tuple[list[Segment], str]:
    with tempfile.TemporaryDirectory(prefix="row-bot-transcribe-") as folder:
        path = Path(folder) / f"source{suffix or '.bin'}"
        path.write_bytes(data)
        return transcribe(path)


def clock(seconds: float, *, millis: bool = False) -> str:
    whole = int(seconds)
    hours, minutes, secs = whole // 3600, whole % 3600 // 60, whole % 60
    if millis:
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{int(round((seconds - whole) * 1000)):03d}"
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def srt(segments: list[Segment]) -> str:
    """Subtitles, one cue per stretch of speech."""
    return "\n".join(f"{index}\n{clock(segment.start, millis=True)} --> {clock(segment.end, millis=True)}\n"
                     f"{segment.text}\n" for index, segment in enumerate(segments, start=1))
