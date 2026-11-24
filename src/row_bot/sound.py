"""Voice files and transcripts for video work (5.3.1 B): Row-Bot's local voice reads a script whole, and local
Whisper transcribes a recording or a clip with each word's time. Nothing leaves this computer.
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import uuid
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
# three". The app's own name is the only name here: every other one is the person's (``pronunciations``).
_SAID = ((re.compile(r"\bRow-Bot\b", re.IGNORECASE), "Row Bot"),
         (re.compile(r"\bv?(\d+)\.(\d+)\.(\d+)\b"), lambda found: " point ".join(found.groups())))
# The person's own pronunciations, taught once and used for every voice file after (M9). Read only here, when a
# voice file is made: never shown to the model.
PRONUNCIATIONS_FILE = "pronunciations.json"
MAX_PRONUNCIATIONS = 500
MAX_WORD_CHARS = 80


def spoken(text: str) -> str:
    """*text* as the voice should read it: Row-Bot's name and version numbers."""
    for pattern, said in _SAID:
        text = pattern.sub(said, text)
    return text


def _pronunciations_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / PRONUNCIATIONS_FILE


def _clean_pronunciations(given: dict[str, str] | None) -> dict[str, str]:
    """Written word -> how to say it, trimmed; an empty way to say it means "forget the word"."""
    found: dict[str, str] = {}
    for word, said in (given or {}).items():
        word, said = " ".join(str(word).split())[:MAX_WORD_CHARS], " ".join(str(said or "").split())[:MAX_WORD_CHARS]
        if word:
            found[word] = said
    return found


def saved_pronunciations() -> dict[str, str]:
    """The person's saved pronunciations ({} when there are none, or the file can't be read)."""
    try:
        data = json.loads(_pronunciations_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {word: said for word, said in _clean_pronunciations(data if isinstance(data, dict) else {}).items()
            if said}


def remember_pronunciations(given: dict[str, str]) -> list[str]:
    """Keep these for every later voice file (an empty way to say a word forgets it): the words changed."""
    changes = _clean_pronunciations(given)
    kept = {word: said for word, said in saved_pronunciations().items()
            if word.casefold() not in {key.casefold() for key in changes}}
    kept.update({word: said for word, said in changes.items() if said})
    if len(kept) > MAX_PRONUNCIATIONS:
        raise SoundError(f"At most {MAX_PRONUNCIATIONS} pronunciations are kept: forget some first.")
    path = _pronunciations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.part")
    partial.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    os.replace(partial, path)
    return list(changes)


def respelled(text: str, pronunciations: dict[str, str] | None = None) -> str:
    """*text* as the voice should say it: the given pronunciations, then the saved ones, then Row-Bot's name and
    version numbers. Only the voice hears it; captions and subtitles keep the written words."""
    chosen = {word.casefold(): said for word, said in saved_pronunciations().items()}
    chosen.update({word.casefold(): said for word, said in _clean_pronunciations(pronunciations).items() if said})
    if chosen:
        # One pass, longest first, so a word said one way is never said again another way.
        words = sorted(chosen, key=len, reverse=True)
        pattern = re.compile("(?<!\\w)(?:" + "|".join(map(re.escape, words)) + ")(?!\\w)", re.IGNORECASE)
        text = pattern.sub(lambda found: chosen[found.group(0).casefold()], text)
    return spoken(text)


def written(segments: list[Segment]) -> list[Segment]:
    """A transcript with the saved pronunciations back as the words they spell: a voice file said "Kwen" for
    Qwen, and its transcript and subtitles say Qwen."""
    from dataclasses import replace

    back = {said.casefold(): word for word, said in saved_pronunciations().items()
            if said.casefold() != word.casefold()}
    if not back:
        return segments
    pattern = re.compile("(?<!\\w)(?:" + "|".join(map(re.escape, sorted(back, key=len, reverse=True)))
                         + ")(?!\\w)", re.IGNORECASE)
    return [replace(segment, text=pattern.sub(lambda found: back[found.group(0).casefold()], segment.text))
            for segment in segments]


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
    text = respelled(text, pronunciations)
    chosen = voice or tts.voice
    data = tts.synthesize_wav_bytes(text, voice=chosen, speed=speed or None)
    if not data:
        raise SoundError("There was nothing to say once code and formatting were left out.")
    return Speech(data, wav_seconds(data), chosen)


def decoded_speech(path: Path, start: float = 0.0, end: float | None = None) -> bytes:
    """The first two hours of a file's sound (from *start* to *end* seconds when given) as 16 kHz mono 16-bit PCM,
    read with PyAV (the decoder Whisper itself uses), so transcribing needs no ffmpeg."""
    import av

    seconds = MAX_TRANSCRIBE_SECONDS if end is None else max(0.0, min(end - start, MAX_TRANSCRIBE_SECONDS))
    limit = int(16000 * seconds) * 2
    decoded = bytearray()
    try:
        with av.open(str(path), metadata_errors="ignore") as container:
            if not container.streams.audio:
                raise SoundError("That file has no sound Row-Bot can read.")
            if start > 0:
                container.seek(int(start * av.time_base), backward=True)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                if frame.time is not None and frame.time + frame.samples / (frame.sample_rate or 1) <= start:
                    continue  # Before the part asked for (a seek lands on the frame before it).
                for piece in resampler.resample(frame):
                    samples = piece.to_ndarray().tobytes()
                    if not decoded and frame.time is not None and frame.time < start:
                        samples = samples[int(round(float(start - frame.time) * 16000)) * 2:]
                    decoded += samples
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


def can_transcribe() -> bool:
    """Whether the local Whisper speech model is here to transcribe with (nothing is downloaded to find out)."""
    from row_bot.voice import get_voice_service

    return get_voice_service().whisper_model_available()


def transcribe(path: Path, start: float = 0.0, end: float | None = None) -> tuple[list[Segment], str]:
    """Each stretch of speech in an audio or video file (from *start* to *end* seconds when given; its times are
    still the file's), with each word's time, and the language heard."""
    from dataclasses import replace

    from row_bot.voice import get_voice_service

    voice = get_voice_service()
    if not voice.whisper_model_available():
        raise SoundError("Transcribing needs the Whisper speech model. Install it in Settings › Voice, then try "
                         "again.")
    decoded = decoded_speech(path, start, end)
    if len(decoded) < 3200:
        raise SoundError("That file has no sound Row-Bot can read.")
    segments, language = voice.transcribe_words(decoded)
    if start:
        segments = [replace(segment, start=segment.start + start, end=segment.end + start,
                            words=tuple((begin + start, until + start, word) for begin, until, word in segment.words))
                    for segment in segments]
    return segments, language


def transcribe_bytes(data: bytes, suffix: str, start: float = 0.0,
                     end: float | None = None) -> tuple[list[Segment], str]:
    with tempfile.TemporaryDirectory(prefix="row-bot-transcribe-") as folder:
        path = Path(folder) / f"source{suffix or '.bin'}"
        path.write_bytes(data)
        return transcribe(path, start, end)


def heard(shelf: Path, key: str, source: tuple[Path | None, bytes, str], start: float = 0.0,
          end: float | None = None) -> list[tuple[float, float, str]] | None:
    """The words the local speech model hears in a sound (a voice file, a clip or its part, a narration), each with
    its time in the file, kept on *shelf* by *key*, so the same sound is heard once (C7's captions, C4's cuts and
    moments). *source*: its path, or its bytes and suffix. None when nothing could be heard."""
    import hashlib
    import logging

    kept = shelf / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"
    try:
        return [(float(start), float(end), str(word)) for start, end, word in json.loads(kept.read_text("utf-8"))]
    except (OSError, ValueError, TypeError):
        pass
    path, data, suffix = source
    try:
        segments, _language = (transcribe(path, start, end) if path is not None
                               else transcribe_bytes(data, suffix, start, end))
    except (SoundError, OSError, ValueError):
        logging.getLogger(__name__).info("No words were heard in a sound")
        return None
    words = [word for segment in segments for word in segment.words]
    shelf.mkdir(parents=True, exist_ok=True)
    partial = kept.with_name(f"{kept.name}.{uuid.uuid4().hex[:6]}.part")
    partial.write_text(json.dumps(words), encoding="utf-8", newline="\n")
    os.replace(partial, kept)
    return words


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
