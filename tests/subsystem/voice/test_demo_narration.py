"""Narration for Show Row-Bot how (C3): transcribed on this computer only, never kept as audio.

A fake ffmpeg (the decoder's runner) and a fake local speech model stand in: nothing is installed
or downloaded. What's checked: the bounds; without a local model or ffmpeg there is no narration
(and never a cloud one, even when Dictate or Talk use one); the words go to the steps they were
said around; the audio reaches ffmpeg through a pipe and no file is written; a secret step keeps
no words; secrets and long numbers said aloud are taken out.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from row_bot.browser import demo
from row_bot.voice import browser_local, narration, providers
from row_bot.voice.browser_local import BrowserLocalVoiceService, BrowserVoiceError
from row_bot.voice.provider_base import VoiceProviderStatus
from row_bot.voice.runtime import VoiceRuntimeSettings

pytestmark = pytest.mark.subsystem

CHAT = "conversation-a"
SECOND = 16_000 * 2


class SpeechModel:
    """A local model that "hears" the clip's own marker bytes: each second of the fake PCM carries
    one letter, so what it returns shows exactly which stretch of audio it was given."""

    provider_id = "local_whisper"
    display_name = "Local Whisper"

    def __init__(self, *, ready: bool = True, local: bool = True) -> None:
        self.ready, self.local = ready, local
        self.heard: list[str] = []

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(self.provider_id, self.display_name, self.ready, local=self.local,
                                   unavailable_code="whisper_model_missing")

    def transcribe_bytes(self, pcm: bytes) -> str:
        letters = "".join(chr(pcm[index]) for index in range(0, len(pcm), SECOND))
        self.heard.append(letters)
        return " ".join(f"word{letter}" for letter in letters)


class Voice:
    def __init__(self, model: SpeechModel) -> None:
        self.model = model

    def sync_saved_models(self) -> None:
        return None

    def whisper_model_available(self) -> bool:
        return self.model.ready

    def sensevoice_provider(self) -> SpeechModel:
        return self.model


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "_DEMOS", {})
    clock = [500.0]
    monkeypatch.setattr(demo, "_clock", lambda: clock[0])
    model = SpeechModel()
    piped: list[dict] = []
    seconds = [30]

    def ffmpeg(command, **options):
        piped.append({"command": command, "input": options.get("input")})
        # Each second of decoded audio carries one letter, A, B, C…
        pcm = b"".join(bytes([65 + index % 26]) * SECOND for index in range(seconds[0]))
        return SimpleNamespace(returncode=0, stdout=pcm)

    service = BrowserLocalVoiceService(voice_service=Voice(model), tts_service=SimpleNamespace(),
                                       runner=ffmpeg, ffmpeg_path="ffmpeg")
    browser_local._set_browser_local_voice_service_for_tests(service)
    monkeypatch.setattr(providers, "load_voice_runtime_settings", lambda: VoiceRuntimeSettings())
    monkeypatch.setattr(providers.LocalWhisperProvider, "status", lambda self: model.status())
    monkeypatch.setattr(providers.LocalWhisperProvider, "transcribe_bytes", lambda self, pcm: model.transcribe_bytes(pcm))
    monkeypatch.chdir(tmp_path)
    yield SimpleNamespace(model=model, piped=piped, seconds=seconds, clock=clock, service=service, folder=tmp_path)
    browser_local._set_browser_local_voice_service_for_tests(None)


def _demo_with_steps(setup, *times: float) -> demo.Demo:
    recorded = demo.start(CHAT, url="https://example.org/", title="Home")
    for at in times:
        setup.clock[0] = 500.0 + at
        demo._add(recorded, demo.DemoStep(0, 0.0, "click", role="button", name=f"At {at:g}"))
    return recorded


def test_windows_give_each_step_what_was_said_just_before_it_until_the_next():
    # Steps at 0, 10 and 20 s; a clip from 0 to 30 s.
    assert narration.windows([0.0, 10.0, 20.0], 0.0, 30.0) == [(0, 0.0, 7.0), (1, 7.0, 17.0), (2, 17.0, 30.0)]
    # A clip that starts late covers only the steps it overlaps; its last second joins the stretch before.
    assert narration.windows([0.0, 10.0, 20.0], 12.0, 18.0) == [(1, 12.0, 18.0)]
    # Steps a second apart: their stretches join the next one (keeping the earlier step), so each
    # transcription has enough audio to hear words.
    assert narration.windows([0.0, 4.0, 5.0, 6.0, 15.0], 0.0, 20.0) == [(0, 0.0, 2.0), (2, 2.0, 12.0),
                                                                     (4, 12.0, 20.0)]


def test_narration_goes_to_the_steps_it_was_said_around_and_no_audio_is_kept(setup, monkeypatch):
    import tempfile
    from pathlib import Path

    def no_files(*_args, **_kwargs):
        raise AssertionError("narration wrote a file")

    for target, name in ((tempfile, "mkstemp"), (tempfile, "NamedTemporaryFile"), (Path, "write_bytes")):
        monkeypatch.setattr(target, name, no_files)
    recorded = _demo_with_steps(setup, 10.0, 20.0)

    view = narration.transcribe(CHAT, recorded.id, b"webm-bytes", "audio/webm;codecs=opus", 0.0, session_key="s1")

    opened, first, second = view["steps"]
    assert opened["narration"] == " ".join(f"word{letter}" for letter in "ABCDEFG")  # 0 to 7 s
    assert first["narration"] == " ".join(f"word{letter}" for letter in "HIJKLMNOPQ")  # 7 to 17 s
    assert second["narration"].startswith("wordR") and view["narrated"] is True
    [call] = setup.piped
    assert call["input"] == b"webm-bytes" and "pipe:0" in call["command"]  # Through a pipe, in memory.
    assert call["command"][call["command"].index("-t") + 1] == str(narration.MAX_SECONDS)
    assert list(setup.folder.iterdir()) == []  # Nothing was written to disk.


def test_a_clip_started_later_in_the_demo_is_placed_by_its_offset(setup):
    recorded = _demo_with_steps(setup, 10.0, 40.0)
    setup.seconds[0] = 10

    view = narration.transcribe(CHAT, recorded.id, b"clip", "audio/ogg", 33.0, session_key="s1")

    assert [step["narration"] for step in view["steps"]] == ["", "wordA wordB wordC wordD", "wordE wordF wordG "
                                                             "wordH wordI wordJ"]


def test_a_secret_step_keeps_no_words_and_secrets_said_aloud_are_taken_out(setup, monkeypatch):
    recorded = _demo_with_steps(setup, 10.0)
    recorded.steps[1].kind, recorded.steps[1].secret = "type", "password"
    said = iter(["my card is 4000 1234 5678 9010 and key sk-abcdefghijklmnopqrstu", "my password is hunter2"])
    monkeypatch.setattr(setup.model, "transcribe_bytes", lambda pcm: next(said))

    view = narration.transcribe(CHAT, recorded.id, b"clip", "audio/webm", 0.0, session_key="s1")

    assert view["steps"][1]["narration"] == ""
    assert "4000" not in view["steps"][0]["narration"] and "sk-abc" not in view["steps"][0]["narration"]
    assert "[number]" in view["steps"][0]["narration"]


@pytest.mark.parametrize(("size", "kind", "code"), [
    (0, "audio/webm", "empty_audio"),
    (1, "video/mp4", "unsupported_audio_type"),
    (narration.MAX_BYTES + 1, "audio/webm", "payload_too_large"),
], ids=["empty", "not-audio", "over-64-mb"])
def test_clips_are_bounded(setup, size, kind, code):
    recorded = _demo_with_steps(setup)

    with pytest.raises(BrowserVoiceError) as refused:
        narration.transcribe(CHAT, recorded.id, b"x" * size, kind, 0.0, session_key="s1")

    assert refused.value.code == code and setup.piped == []


def test_a_clip_ffmpeg_cant_read_is_refused(setup):
    recorded = _demo_with_steps(setup)
    setup.service._runner = lambda command, **options: SimpleNamespace(returncode=1, stdout=b"")

    with pytest.raises(BrowserVoiceError) as refused:
        narration.transcribe(CHAT, recorded.id, b"junk", "audio/webm", 0.0, session_key="s1")

    assert refused.value.code == "malformed_audio" and recorded.steps[0].narration == ""


def test_without_a_local_speech_model_or_ffmpeg_there_is_no_narration(setup, monkeypatch):
    recorded = _demo_with_steps(setup)
    setup.model.ready = False
    assert narration.status() == {"ready": False, "code": "narration_no_model"}
    with pytest.raises(BrowserVoiceError) as refused:
        narration.transcribe(CHAT, recorded.id, b"clip", "audio/webm", 0.0, session_key="s1")
    assert refused.value.code == "narration_no_model" and setup.piped == []

    setup.model.ready = True
    setup.service._ffmpeg_path = None
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert narration.status() == {"ready": False, "code": "narration_no_ffmpeg"}


def test_narration_is_local_even_when_dictate_and_talk_use_a_cloud_service(setup, monkeypatch):
    monkeypatch.setattr(providers, "load_voice_runtime_settings", lambda: VoiceRuntimeSettings(
        dictation_provider="openai", talk_provider="openai_realtime"))
    recorded = _demo_with_steps(setup)

    narration.transcribe(CHAT, recorded.id, b"clip", "audio/webm", 0.0, session_key="s1")

    assert setup.model.heard  # Local Whisper heard it; no cloud service was asked.
    setup.model.local = False
    assert narration.status()["ready"] is False  # A model that isn't on this computer never narrates.


def test_a_clip_for_another_demo_is_refused(setup):
    _demo_with_steps(setup)

    with pytest.raises(demo.DemoError) as refused:
        narration.transcribe(CHAT, "f" * 32, b"clip", "audio/webm", 0.0, session_key="s1")

    assert refused.value.code == "demo_changed" and setup.piped == []


def test_the_decoder_runs_with_its_own_pipe_not_a_shell(setup):
    recorded = _demo_with_steps(setup)
    seen: list[dict] = []

    def runner(command, **options):
        seen.append(options)
        return SimpleNamespace(returncode=0, stdout=b"A" * SECOND * 3)

    setup.service._runner = runner
    narration.transcribe(CHAT, recorded.id, b"clip", "audio/webm", 0.0, session_key="s1")

    assert seen[0]["stdout"] is subprocess.PIPE and "shell" not in seen[0]
