from __future__ import annotations

import io
import json
import subprocess
import sys
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from row_bot.voice.browser_local import (
    BrowserLocalVoiceService,
    BrowserVoiceError,
    MAX_INPUT_BYTES,
)
from row_bot.voice.provider_base import (
    SynthesizedSpeech,
    VoiceProviderError,
    VoiceProviderStatus,
)


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\0\0" * 160)
    return output.getvalue()


class _SenseVoice:
    provider_id = "local_funasr"
    display_name = "SenseVoice"

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[bytes] = []

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(self.provider_id, self.display_name, self.ready,
                                   unavailable_code="sensevoice_unavailable")

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        self.calls.append(audio_bytes)
        return "sensevoice text"


class _Voice:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[tuple[bytes, bool]] = []
        self.sensevoice = _SenseVoice()

    def whisper_model_available(self) -> bool:
        return self.ready

    def sync_saved_models(self) -> None:
        pass

    def sensevoice_provider(self) -> _SenseVoice:
        return self.sensevoice

    def transcribe_pcm16(self, pcm: bytes, *, allow_download: bool, language: str | None = "en") -> str:
        self.calls.append((pcm, allow_download))
        return "hello browser"

    def install_whisper_model(self) -> None:
        self.ready = True


class _TTS:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.texts: list[str] = []
        self.voices: list[str | None] = []
        self.output = _wav()

    def is_installed(self) -> bool:
        return self.ready

    def reload_settings(self) -> None:
        pass

    def synthesize_wav_bytes(self, text: str, *, voice: str | None = None, **_options) -> bytes:
        self.texts.append(text)
        self.voices.append(voice)
        return self.output

    def download_model(self, _progress=None) -> None:
        self.ready = True


def _runner(_command, **_kwargs):
    return SimpleNamespace(returncode=0, stdout=b"\0" * 3_200, stderr=b"")


def test_browser_audio_is_decoded_and_transcribed_without_download() -> None:
    voice = _Voice()
    service = BrowserLocalVoiceService(
        voice_service=voice,
        tts_service=_TTS(),
        runner=_runner,
        ffmpeg_path="ffmpeg",
    )

    assert service.transcribe("session-a", b"encoded", "audio/webm;codecs=opus") == (
        "hello browser"
    )
    assert voice.calls == [(b"\0" * 3_200, False)]


@pytest.mark.parametrize(
    ("payload", "content_type", "code"),
    [
        (b"x", "application/octet-stream", "unsupported_audio_type"),
        (b"", "audio/webm", "empty_audio"),
        (b"x" * (MAX_INPUT_BYTES + 1), "audio/webm", "audio_too_large"),
    ],
    ids=("unsupported-type", "empty", "too-large"),
)
def test_browser_audio_rejects_invalid_inputs(
    payload: bytes,
    content_type: str,
    code: str,
) -> None:
    service = BrowserLocalVoiceService(
        voice_service=_Voice(),
        tts_service=_TTS(),
        runner=_runner,
        ffmpeg_path="ffmpeg",
    )
    with pytest.raises(BrowserVoiceError, match=code):
        service.transcribe("session-a", payload, content_type)


def test_decode_timeout_is_privacy_safe() -> None:
    def timeout_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["ffmpeg"], 15, output=b"private-audio")

    service = BrowserLocalVoiceService(
        voice_service=_Voice(),
        tts_service=_TTS(),
        runner=timeout_runner,
        ffmpeg_path="ffmpeg",
    )
    with pytest.raises(BrowserVoiceError, match="audio_decode_timeout") as raised:
        service.transcribe("session-a", b"encoded", "audio/webm")
    assert "private-audio" not in str(raised.value)


def test_missing_models_are_explicit_and_synthesis_is_session_scoped() -> None:
    service = BrowserLocalVoiceService(
        voice_service=_Voice(ready=False),
        tts_service=_TTS(ready=False),
        runner=_runner,
        ffmpeg_path="ffmpeg",
    )
    with pytest.raises(BrowserVoiceError, match="whisper_model_missing"):
        service.transcribe("session-a", b"encoded", "audio/webm")
    with pytest.raises(BrowserVoiceError, match="kokoro_model_missing"):
        service.synthesize("session-b", "Hello")

    service.install_whisper("session-a")
    service.install_kokoro("session-b")
    assert service.status()["whisper_ready"] is True
    assert service.status()["kokoro_ready"] is True
    assert service.synthesize("session-b", "Hello") == SynthesizedSpeech(_wav(), "audio/wav")


def test_kokoro_runtime_temp_is_created_only_under_app_data(
    tmp_path,
    monkeypatch,
) -> None:
    import row_bot.tts as tts

    runtime_tmp = tmp_path / "runtime-tmp"
    monkeypatch.setattr(tts, "_ROW_BOT_DIR", tmp_path)
    monkeypatch.setenv("TMPDIR", str(runtime_tmp))
    monkeypatch.setattr(tts.tempfile, "tempdir", None)

    tts._prepare_configured_runtime_tmp()

    assert runtime_tmp.is_dir()
    assert tts.tempfile.tempdir == str(runtime_tmp.resolve())


def _service(voice=None, tts=None, runner=_runner) -> BrowserLocalVoiceService:
    return BrowserLocalVoiceService(
        voice_service=voice or _Voice(),
        tts_service=tts or _TTS(),
        runner=runner,
        ffmpeg_path="ffmpeg",
    )


def _save_runtime(monkeypatch, tmp_path, **values) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "voice_runtime_settings.json").write_text(json.dumps(values), encoding="utf-8")


def test_dictate_and_talk_each_transcribe_with_their_saved_model(tmp_path, monkeypatch) -> None:
    _save_runtime(
        monkeypatch,
        tmp_path,
        dictation_model="local-funasr-sensevoice",
        talk_model="local-whisper",
    )
    voice = _Voice()
    service = _service(voice)

    assert service.transcribe("session-a", b"encoded", "audio/webm", mode="dictate") == (
        "sensevoice text"
    )
    assert service.transcribe("session-a", b"encoded", "audio/webm", mode="talk") == (
        "hello browser"
    )
    assert voice.sensevoice.calls == [b"\0" * 3_200]
    assert voice.calls == [(b"\0" * 3_200, False)]


def test_realtime_talk_falls_back_to_whisper_whatever_dictation_uses(tmp_path, monkeypatch) -> None:
    _save_runtime(
        monkeypatch,
        tmp_path,
        talk_provider="openai_realtime",
        talk_model="gpt-realtime-2",
        dictation_model="local-funasr-sensevoice",
    )
    voice = _Voice()

    assert _service(voice).transcribe("session-a", b"encoded", "audio/webm", mode="talk") == (
        "hello browser"
    )
    assert not voice.sensevoice.calls


def test_a_selected_model_that_is_not_ready_reports_its_own_code(tmp_path, monkeypatch) -> None:
    _save_runtime(monkeypatch, tmp_path, dictation_model="local-funasr-sensevoice")
    voice = _Voice()
    voice.sensevoice.ready = False
    decoded = []

    def runner(command, **kwargs):
        decoded.append(command)
        return _runner(command, **kwargs)

    service = _service(voice, runner=runner)
    status = service.speech_input_status("dictate")
    assert not status.ready and status.unavailable_code == "sensevoice_unavailable"
    # An installed Whisper never stands in for the model the user chose.
    with pytest.raises(BrowserVoiceError, match="sensevoice_unavailable"):
        service.transcribe("session-a", b"encoded", "audio/webm", mode="dictate")
    assert not decoded and not voice.calls and not voice.sensevoice.calls


def test_an_unknown_provider_is_reported_and_never_replaced(tmp_path, monkeypatch) -> None:
    _save_runtime(
        monkeypatch,
        tmp_path,
        dictation_provider="custom_openai_gateway",
        speech_output_provider="custom_openai_gateway",
    )
    voice, tts = _Voice(), _TTS()
    service = _service(voice, tts)

    for attempt in (
        lambda: service.speech_input_status("dictate"),
        lambda: service.transcribe("session-a", b"encoded", "audio/webm", mode="dictate"),
        lambda: service.synthesize("session-b", "Hello"),
    ):
        with pytest.raises(VoiceProviderError, match="voice_provider_unavailable"):
            attempt()
    assert not voice.calls and not voice.sensevoice.calls and not tts.texts


@pytest.mark.parametrize(
    "output",
    [b"not audio at all", b"OggS" + b"\0" * 60],
    ids=("unknown", "undeclared-ogg"),
)
def test_speech_output_must_be_the_audio_it_declares(tmp_path, monkeypatch, output) -> None:
    _save_runtime(monkeypatch, tmp_path)
    tts = _TTS()
    tts.output = output
    with pytest.raises(BrowserVoiceError, match="voice_output_unavailable"):
        _service(tts=tts).synthesize("session-b", "Hello")


def test_saved_voice_and_speed_apply_to_the_next_spoken_reply(tmp_path, monkeypatch) -> None:
    import row_bot.tts as tts

    created = []

    class Kokoro:
        def __init__(self, model: str, voices: str) -> None:
            pass

        def create(self, text, *, voice, speed, lang):
            created.append((voice, speed))
            return np.zeros(160, dtype=np.float32), 24_000

    for name in ("_MODEL_PATH", "_VOICES_PATH"):
        path = tmp_path / name
        path.write_bytes(b"synthetic model")
        monkeypatch.setattr(tts, name, path)
    settings = tmp_path / "tts_settings.json"
    monkeypatch.setattr(tts, "_SETTINGS_PATH", settings)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=Kokoro))
    _save_runtime(monkeypatch, tmp_path)
    service = _service(tts=tts.TTSService())

    first = service.synthesize("session-b", "Hello")
    settings.write_text(json.dumps({"voice": "bf_emma", "speed": 1.5}), encoding="utf-8")
    second = service.synthesize("session-b", "Hello")

    assert created == [("af_heart", 1.0), ("bf_emma", 1.5)]
    assert first.content_type == second.content_type == "audio/wav"
    assert second.audio[:4] == b"RIFF" and second.audio[8:12] == b"WAVE"


def test_a_long_answer_is_spoken_whole_in_pieces_joined_into_one_recording(tmp_path, monkeypatch) -> None:
    """VD7: no sentence cap. Every sentence reaches Kokoro, in pieces of about a minute each."""
    import row_bot.tts as tts

    said: list[str] = []

    class Kokoro:
        def __init__(self, model: str, voices: str) -> None:
            pass

        def create(self, text, *, voice, speed, lang):
            said.append(text)
            return np.zeros(len(text), dtype=np.float32), 24_000

    for name in ("_MODEL_PATH", "_VOICES_PATH"):
        path = tmp_path / name
        path.write_bytes(b"synthetic model")
        monkeypatch.setattr(tts, name, path)
    monkeypatch.setattr(tts, "_SETTINGS_PATH", tmp_path / "tts_settings.json")
    monkeypatch.setattr(tts, "_SHARED_KOKORO", [])
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=Kokoro))
    _save_runtime(monkeypatch, tmp_path)
    answer = " ".join(f"Sentence number {index} says one more useful thing." for index in range(1, 61))

    speech = _service(tts=tts.TTSService()).synthesize("session-b", answer)

    assert len(said) > 1 and " ".join(said) == answer
    with wave.open(io.BytesIO(speech.audio), "rb") as recording:
        assert recording.getframerate() == 24_000
        assert recording.getnframes() == sum(len(piece) for piece in said)


def test_speech_longer_than_the_talk_client_plays_is_refused_never_cut(tmp_path, monkeypatch) -> None:
    _save_runtime(monkeypatch, tmp_path)
    tts = _TTS()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(b"\0\0" * 1_500_000)  # 3 MB a piece
    tts.output = output.getvalue()
    answer = "This sentence is long enough to need its own piece of speech. " * 60

    with pytest.raises(BrowserVoiceError, match="audio_output_too_large"):
        _service(tts=tts).synthesize("session-b", answer)
    assert len(tts.texts) == 3  # it stops at the piece that goes over, never sends a shortened answer
