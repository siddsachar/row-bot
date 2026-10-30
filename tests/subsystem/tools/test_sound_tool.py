"""Voice files, music and transcripts for video work (5.3.1 B), made on this computer and saved in the chat.

The voice and Whisper are fakes; the music is the real generator, short.
"""
from __future__ import annotations

import io
import subprocess
import wave
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _wav(seconds: float, rate: int = 24_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * int(seconds * rate))
    return buffer.getvalue()


@pytest.fixture
def chat(tmp_path, monkeypatch, reload_for_data_dir):
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    conversation = threads.create_thread("Demo video", seed_default_skills=False)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: conversation)
    return conversation


def _files(conversation: str) -> dict[str, dict]:
    from row_bot.application.attachments import list_conversation_files

    return {item["name"]: item for item in list_conversation_files(conversation)}


@pytest.fixture
def voice(monkeypatch):
    """Row-Bot's voice: it records what it was asked to say and how."""
    import row_bot.tts as tts

    said: list[dict] = []

    class Voice:
        voice = "af_heart"

        def is_installed(self):
            return True

        def reload_settings(self):
            pass

        def synthesize_wav_bytes(self, text, **options):
            said.append({"text": text, **options})
            return _wav(len(text) / 15)

    monkeypatch.setattr(tts, "TTSService", Voice)
    return said


def test_a_voice_file_reads_the_whole_script_with_its_pronunciations(chat, voice):
    from row_bot.tools.sound_tool import _speak

    script = "Row-Bot runs on Qwen. " * 40  # Far more than a spoken reply's three sentences.
    result = _speak(script, name="scene 1", voice="bf_emma", speed=1.1, pronunciations={"Qwen": "Kwen"})

    assert result.startswith("Voice file scene 1.wav: ") and "voice bf_emma" in result
    assert voice[0]["truncate"] is False and voice[0]["voice"] == "bf_emma" and voice[0]["speed"] == 1.1
    assert "Qwen" not in voice[0]["text"] and voice[0]["text"].count("Kwen") == 40
    assert _files(chat)["scene 1.wav"]["mime_type"] == "audio/wav"


def test_a_voice_file_needs_a_known_voice(chat, voice):
    from row_bot.tools.sound_tool import _speak

    assert _speak("Hello", voice="nobody").startswith("Not made: There's no voice called nobody.")
    assert voice == [] and _files(chat) == {}


def test_a_voice_file_is_not_slowed_to_fill_time(chat, voice):
    from row_bot.tools.sound_tool import _speak

    result = _speak("Row-Bot plans the week.", speed=0.72)

    assert result.startswith("Not made: Speed is between 0.9 and 1.5") and "write more words" in result
    assert voice == [] and _files(chat) == {}


def test_without_rowbots_voice_speaking_asks_once_to_download_it(chat, voice, monkeypatch):
    """V4: a scheduled video on a fresh profile can narrate: the voice downloads after one card, as ffmpeg does."""
    import row_bot.tts as tts
    from row_bot.tools import approval_gate
    from row_bot.tools.sound_tool import _speak

    installed = {"value": False}
    downloads: list[str] = []
    monkeypatch.setattr(tts.TTSService, "is_installed", lambda self: installed["value"])
    monkeypatch.setattr(tts.TTSService, "download_model",
                        lambda self, progress=None: downloads.append("kokoro") or installed.update(value=True),
                        raising=False)
    cards: list[dict] = []
    answer = {"value": ("The person didn't allow downloading Row-Bot's voice; nothing was downloaded.", "")}
    monkeypatch.setattr(approval_gate, "approval_check", lambda payload, **_: cards.append(payload) or answer["value"])

    assert _speak("Hello").startswith("The person didn't allow downloading Row-Bot's voice")
    assert downloads == [] and voice == []
    assert cards[0]["question"] == "Speaking needs Row-Bot's voice (Kokoro, about 206 MB) from GitHub. Download it now?"
    assert "check both against the fingerprints saved in Row-Bot" in cards[0]["description"]

    answer["value"] = (None, "Approval: asked; approved by you")
    made = _speak("Hello")
    assert made.startswith("Approval: asked; approved by you\nVoice file voice.wav: ") and downloads == ["kokoro"]
    assert _speak("Again").startswith("Voice file "), "once: it's there now, so nothing asks"
    assert len(cards) == 2


def test_a_voice_download_is_kept_only_when_its_fingerprint_matches(tmp_path, monkeypatch):
    import hashlib

    import requests

    import row_bot.tts as tts

    data = b"kokoro" * 1000

    class Response:
        headers = {"content-length": str(len(data))}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield from (data[i:i + chunk_size] for i in range(0, len(data), chunk_size))

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    target = tmp_path / "kokoro.onnx"
    with pytest.raises(RuntimeError, match="didn't match the fingerprint saved in Row-Bot"):
        tts._download_file("https://example.invalid/kokoro.onnx", target, size=len(data), sha256="0" * 64)
    assert list(tmp_path.iterdir()) == [], "nothing kept, not even part of it"
    tts._download_file("https://example.invalid/kokoro.onnx", target, size=len(data),
                       sha256=hashlib.sha256(data).hexdigest())
    assert target.read_bytes() == data


def test_music_is_saved_in_the_chat_for_the_videos_length(chat):
    from row_bot import music
    from row_bot.tools.sound_tool import _make_music

    result = _make_music(3, mood="upbeat", name="demo music")

    assert result.startswith("Music file demo music.wav: 3.0 seconds, upbeat")
    saved = _files(chat)["demo music.wav"]
    assert saved["mime_type"] == "audio/wav" and saved["size_bytes"] == 3 * music.SR * 4 + 44
    assert _make_music(0.5).startswith("Not made: music is between 1 second and 20 minutes long")


@pytest.mark.slow
def test_music_builds_at_the_videos_beats():
    from row_bot import music

    track = music.compose(20, mood="calm", beats=[6, 12])
    quiet, full = (float(np.sqrt((track[int(a * music.SR):int(b * music.SR)] ** 2).mean()))
                   for a, b in ((1.5, 4.5), (13, 15)))
    assert full > quiet * 1.5, "pads alone, then the groove from the second beat"


def test_a_clip_is_transcribed_with_timings_and_subtitles_are_saved(chat, monkeypatch):
    from row_bot import ffmpeg_runtime, sound, voice as voice_module
    from row_bot.application.attachments import register_attachment
    from row_bot.tools.sound_tool import _transcribe

    register_attachment(chat, "interview.m4a", b"synthetic audio " * 100)
    decoded: list[list[str]] = []

    def run(argv, **_options):
        decoded.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=b"\0\0" * 16000 * 3, stderr=b"")

    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: ffmpeg_runtime.Ffmpeg(
        executable="ffmpeg", source="row_bot", version="7.1", licence="GPL version 3 or later"))
    monkeypatch.setattr(sound.subprocess, "run", run)
    heard = [sound.Segment(0.0, 1.4, "Welcome to the show.", ((0.0, 0.5, "Welcome"), (0.5, 0.7, "to"))),
             sound.Segment(61.2, 63.0, "Thanks for having me.", ((61.2, 61.8, "Thanks"),))]
    monkeypatch.setattr(voice_module, "get_voice_service", lambda: SimpleNamespace(
        whisper_model_available=lambda: True, transcribe_words=lambda pcm: (heard, "en")))

    result = _transcribe("interview.m4a")

    assert result.startswith("Transcript of interview.m4a (1:03, 3 words, language en). "
                             "Subtitles: interview-subtitles.srt.")
    assert "[0:00] Welcome to the show.\n[1:01] Thanks for having me." in result
    assert "-ar" in decoded[0] and "16000" in decoded[0], "16 kHz mono for Whisper"
    from row_bot.application.attachments import read_attachment
    subtitles = read_attachment(_files(chat)["interview-subtitles.srt"]["attachment_ref"])[1].decode()
    assert subtitles.startswith("1\n00:00:00,000 --> 00:00:01,400\nWelcome to the show.\n")


def test_transcribing_says_what_is_missing(chat, monkeypatch):
    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import register_attachment
    from row_bot.tools.sound_tool import _transcribe

    register_attachment(chat, "talk.mp3", b"ID3 synthetic")
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: None)
    assert _transcribe("talk.mp3").startswith("Not transcribed: Transcribing needs ffmpeg.")
    assert _transcribe("nothing.mp3").startswith("Not transcribed: Couldn't find nothing.mp3")
