"""Voice files, music and transcripts for video work (5.3.1 B), all made on this computer.

Each result is saved in the chat's files under its name, so the video tools and send_file use it by that name.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool

# What transcribe and the sound steps read: a sound file or a video.
_HEARD = frozenset({"audio", "video"})
_TRANSCRIPT_CHARS = 30_000


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def _save(name: str, data: bytes, conversation: str = "") -> str:
    """Save to the chat's files; the name it's saved as (or why it wasn't)."""
    from row_bot.application.attachments import AttachmentError, register_attachment

    conversation = conversation or _conversation()
    if not conversation:
        raise ValueError("This works in a conversation.")
    try:
        return str(register_attachment(conversation, name, data, role="working")["name"])
    except AttachmentError as error:
        if str(error) == "payload_too_large":
            raise ValueError("The result is too large to keep in the chat (over 200 MB): make it shorter.") from None
        raise ValueError("The result couldn't be saved in the chat.") from None


def _made(name: str) -> str:
    """A saved working file's identity: the result's first line (W4), which history masking keeps."""
    from row_bot.media_sources import identity

    return identity({"name": name, "role": "working", "version": 1})


def _name(value: str, default: str, suffix: str) -> str:
    from row_bot.tools.video_editing_tool import output_name

    return output_name(value, default=default, suffix=suffix)


class _SpeakInput(BaseModel):
    text: str = Field(description="The words to say, all of them (a scene's line or a whole script).")
    name: str = Field(default="", description="The voice file's name, e.g. scene-1.")
    voice: str = Field(default="", description="A Row-Bot voice id (empty: the one in Settings › Voice).")
    speed: float = Field(default=0.0, description="0.9 to 1.5; 0 keeps the saved speed. Never slow a voice to "
                                                "fill time: write more words or cut the video shorter.")
    pronunciations: dict[str, str] = Field(
        default_factory=dict, description='How to say words, e.g. {"Qwen": "Kwen"}: only the voice hears them; '
                                          'the text keeps the real spelling.')
    remember: bool = Field(default=False, description="Keep them for every later voice file and video line (a word "
                                                      "given an empty pronunciation is forgotten).")


def voice_consent(tool: str, purpose: str) -> tuple[str, str]:
    """(refusal, approval line): Row-Bot's voice, downloaded the first time it's needed after the person agrees
    (by the approval level), as ffmpeg is. Nothing to ask when it's there."""
    from row_bot import tts
    from row_bot.tools.approval_gate import approval_check

    service = tts.TTSService()
    if service.is_installed():
        return "", ""
    size = f"about {round(tts.VOICE_DOWNLOAD_BYTES / 1_000_000)} MB"
    refusal, line = approval_check(
        {"tool": tool, "label": "Download Row-Bot's voice",
         "question": f"{purpose} needs Row-Bot's voice (Kokoro, {size}) from GitHub. Download it now?",
         "description": (f"Row-Bot will download the Kokoro voice model and its voices ({size}) from "
                         f"{tts.VOICE_DOWNLOAD_HOST}, check both against the fingerprints saved in Row-Bot, and "
                         "keep them in Row-Bot's data folder. Kokoro is free (Apache-2.0) and runs on this "
                         "computer. This happens once."),
         "risk_class": "medium",
         "args": {"download": f"Kokoro voice ({size})", "from": tts.VOICE_DOWNLOAD_HOST}},
        risks=(f"downloads a voice model ({size}) from GitHub and keeps it in Row-Bot's data folder",),
        blocked_message=("BLOCKED: this needs Row-Bot's voice, and downloading it needs approval, but this chat is "
                         "on Block. Install it in Settings › Voice, or change the approval level."),
        cancelled_message="The person didn't allow downloading Row-Bot's voice; nothing was downloaded.",
    )
    if refusal:
        return refusal, ""
    try:
        service.download_model()
    except Exception as error:  # A network or fingerprint problem: said in words, nothing kept.
        return f"Row-Bot's voice couldn't be downloaded: {error}", ""
    return "", line


_WHISPER_SIZES = {"tiny": "about 39 MB", "base": "about 74 MB", "small": "about 244 MB", "medium": "about 769 MB"}


def speech_model_consent(tool: str) -> tuple[str, str]:
    """(refusal, approval line): the Whisper speech model, downloaded the first time a transcript needs it after
    the person agrees (by the approval level), as Row-Bot's voice is. Nothing to ask when it's there."""
    from row_bot.tools.approval_gate import approval_check
    from row_bot.voice import get_voice_service

    voice = get_voice_service()
    if voice.whisper_model_available():
        return "", ""
    model = f"Whisper {voice.whisper_size}"
    size = _WHISPER_SIZES.get(voice.whisper_size, "a few hundred MB")
    refusal, line = approval_check(
        {"tool": tool, "label": "Download the speech model",
         "question": f"Transcribing needs the speech model ({model}, {size}) from Hugging Face. Download it now?",
         "description": (f"Row-Bot will download the {model} speech model ({size}) from Hugging Face and keep it "
                         "in Row-Bot's data folder. It runs on this computer, so the sound never leaves it. "
                         "This happens once."),
         "risk_class": "medium",
         "args": {"download": f"{model} ({size})", "from": "huggingface.co"}},
        risks=(f"downloads a speech model ({size}) from Hugging Face and keeps it in Row-Bot's data folder",),
        blocked_message=("BLOCKED: transcribing needs the speech model, and downloading it needs approval, but this "
                         "chat is on Block. Install it in Settings › Voice, or change the approval level."),
        cancelled_message="The person didn't allow downloading the speech model; nothing was downloaded.",
    )
    if refusal:
        return refusal, ""
    try:
        voice.install_whisper_model()
    except Exception as error:  # A network problem: said in words, nothing half-kept is used.
        return f"The speech model couldn't be downloaded: {error}", ""
    return "", line


def _speak(text: str, name: str = "", voice: str = "", speed: float = 0.0,
           pronunciations: dict[str, str] | None = None, remember: bool = False) -> str:
    from row_bot import sound
    from row_bot.tools.approval_gate import with_approval

    refusal, line = voice_consent("speak", "Speaking")
    if refusal:
        return refusal
    try:
        # Kept before speaking, so a word taught here is said right in this file too.
        kept = sound.remember_pronunciations(pronunciations or {}) if remember and pronunciations else []
        speech = sound.speak(text, voice=voice, speed=speed, pronunciations=pronunciations)
        saved = _save(_name(name, "voice", ".wav"), speech.wav)
    except ValueError as error:
        return f"Not made: {error}"
    text = (f"{_made(saved)}\nVoice file: {speech.seconds:.1f} seconds, voice {speech.voice}. Use it by that name "
            "in the video tools or send_file.")
    taught = [word for word in kept if str((pronunciations or {}).get(word) or "").strip()]
    forgot = [word for word in kept if word not in taught]
    if taught:
        text += f" Saved how to say {', '.join(taught)} for every later voice file and video line."
    if forgot:
        text += f" Forgot how to say {', '.join(forgot)}."
    return with_approval(line, text) if line else text


class _MusicInput(BaseModel):
    seconds: float = Field(description="Its length in seconds (up to 20 minutes): the video's length.")
    mood: Literal["calm", "upbeat", "focused", "warm"] = Field(default="calm")
    beats: list[float] = Field(default_factory=list,
                               description="Seconds into the video where the music should build, e.g. [6, 20].")
    name: str = Field(default="", description="The music file's name.")


def _make_music(seconds: float, mood: str = "calm", beats: list[float] | None = None, name: str = "") -> str:
    from row_bot import music

    try:
        track = music.compose(float(seconds), mood=mood, beats=list(beats or []))  # type: ignore[arg-type]
        saved = _save(_name(name, f"music-{mood}", ".wav"), music.wav_bytes(track))
    except ValueError as error:
        return f"Not made: {error}"
    return (f"{_made(saved)}\nMusic file: {float(seconds):.1f} seconds, {mood}, original and royalty-free. Mix it "
            "under the voice in the video tools by that name.")


class _TranscribeInput(BaseModel):
    source: str = Field(description="A file in this chat (by name), a workspace path, a recording (rec-…) or a "
                                    "video edit (edit-…).")
    name: str = Field(default="", description="The subtitles file's name.")


def _source(value: str, conversation: str = "") -> tuple[str, Path | None, bytes | None, str]:
    """(label, path, bytes, suffix) of a sound file or a video, found as every tool finds files
    (``media_sources.find``)."""
    from row_bot import media_sources
    from row_bot.application.attachments import AttachmentError, read_attachment

    media = media_sources.find(value, conversation or _conversation(), kinds=_HEARD)[0]
    if media.path is not None:
        return media.label, media.path, None, media.path.suffix
    try:
        metadata, data = read_attachment(media.ref)
    except AttachmentError:
        raise media_sources.SourceError(f"{media.label} can't be read any more.") from None
    suffix = Path(str(metadata["name"])).suffix or mimetypes.guess_extension(str(metadata["mime_type"])) or ".mp4"
    return media.label, None, data, suffix


def _transcribe(source: str, name: str = "") -> str:
    from row_bot.media_sources import SourceError
    from row_bot.tools.approval_gate import with_approval

    try:
        label, path, data, suffix = _source(source)
    except (SourceError, ValueError) as error:
        return f"Not transcribed: {error}"
    refusal, line = speech_model_consent("transcribe")
    if refusal:
        return refusal
    text = _transcript(label, path, data, suffix, name)
    return with_approval(line, text) if line else text


def _transcript(label: str, path: Path | None, data: bytes | None, suffix: str, name: str) -> str:
    from row_bot import sound

    try:
        segments, language = sound.transcribe(path) if path is not None else sound.transcribe_bytes(data or b"", suffix)
    except ValueError as error:
        return f"Not transcribed: {error}"
    if not segments:
        return f"No speech was heard in {label}."
    segments = sound.written(segments)
    try:
        saved = _save(_name(name, Path(label).stem + "-subtitles", ".srt"), sound.srt(segments).encode("utf-8"))
    except ValueError as error:
        head, note = "", f" (the subtitles weren't saved: {error})"
    else:
        head, note = f"{_made(saved)}\n", ""
    lines = "\n".join(f"[{sound.clock(segment.start)}] {segment.text}" for segment in segments)
    if len(lines) > _TRANSCRIPT_CHARS:
        lines = lines[:_TRANSCRIPT_CHARS] + "\n[…the rest is in the subtitles file.]"
    words = sum(len(segment.words) for segment in segments)
    return (f"{head}Transcript of {label} ({sound.clock(segments[-1].end)}, {words} words"
            + (f", language {language}" if language else "") + f").{note}\n\n{lines}")


class SoundTool(BaseTool):

    @property
    def name(self) -> str:
        return "sound"

    @property
    def display_name(self) -> str:
        return "🎙️ Voice, music and transcripts"

    @property
    def description(self) -> str:
        return "Make voice files and original music, and transcribe audio and video, on this computer."

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_speak, name="speak", args_schema=_SpeakInput,
                description="Make a voice file of text in Row-Bot's local voice (all of it). Returns its length."),
            StructuredTool.from_function(
                func=_make_music, name="make_music", args_schema=_MusicInput,
                description="Make original, royalty-free background music for a video's length and beats."),
            StructuredTool.from_function(
                func=_transcribe, name="transcribe", args_schema=_TranscribeInput,
                description="Transcribe audio or video locally with timings, and save subtitles."),
        ]

    def execute(self, query: str) -> str:
        return _transcribe(query)


registry.register(SoundTool())
