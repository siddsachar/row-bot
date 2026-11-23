"""Chat apps speak back (V9): voice notes in, voice notes out.

A voice note is transcribed by the speech model chosen for Dictate, with the language detected, and
nothing downloads. When the channel's switch is on and Kokoro can speak, a voice note is answered with a
spoken turn (``voice_mode``) and a native OGG/Opus voice note after the text. Fake transports, fake
speech models; the encoding and decoding are real (PyAV).
"""

from __future__ import annotations

import asyncio
import base64
import io
import wave
from types import SimpleNamespace
from typing import Any

import av
import numpy as np
import pytest

from row_bot.channels import media, voice_replies
from row_bot.channels.media import InboundAttachment
from row_bot.channels.streaming import ChannelDeliveryResult
from row_bot.voice.provider_base import SynthesizedSpeech, VoiceProviderStatus
from row_bot.voice.providers import SENSEVOICE_MODEL
from row_bot.voice.runtime import VoiceRuntimeSettings

pytestmark = pytest.mark.subsystem


def _wav(seconds: float, rate: int = 24_000) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    samples = (np.sin(2 * np.pi * 220 * t) * 0.3 * 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())
    return out.getvalue()


def _opus_info(data: bytes) -> tuple[str, int, str, float]:
    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        return container.format.name, stream.rate, stream.codec_context.name, container.duration / 1e6


# ── Transcription follows the chosen engine ───────────────────────────


class _Recognizer:
    def __init__(self, provider_id: str, *, ready: bool = True) -> None:
        self.provider_id = provider_id
        self.display_name = provider_id
        self.ready = ready
        self.heard: list[bytes] = []

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(self.provider_id, self.display_name, self.ready, local=True)

    def transcribe_bytes(self, audio_bytes: bytes) -> str:
        self.heard.append(audio_bytes)
        return f"heard by {self.provider_id}"


class _Voice:
    """The local speech models: Whisper's state, SenseVoice, and every install or download attempt."""

    def __init__(self, *, whisper: bool = True, sensevoice: _Recognizer | None = None) -> None:
        self.whisper = whisper
        self.sensevoice = sensevoice or _Recognizer("local_funasr")
        self.whisper_calls: list[dict[str, Any]] = []
        self.installs: list[str] = []

    def sync_saved_models(self) -> None:
        return None

    def sensevoice_provider(self, *, language: str | None = None) -> _Recognizer:
        return self.sensevoice

    def whisper_model_available(self) -> bool:
        return self.whisper

    def transcribe_pcm16(self, audio_bytes: bytes, **kwargs: Any) -> str:
        self.whisper_calls.append({"pcm": len(audio_bytes), **kwargs})
        return "heard by whisper"

    def install_whisper_model(self) -> None:
        self.installs.append("whisper")

    def install_sensevoice_model(self) -> None:
        self.installs.append("sensevoice")

    def _ensure_whisper(self, **_kwargs: Any) -> None:
        self.installs.append("whisper-load")


@pytest.fixture
def speech(monkeypatch):
    """Choose Dictate's engine and the local models a voice note meets."""
    import row_bot.voice as voice_module
    from row_bot.voice import providers

    state = SimpleNamespace(voice=_Voice(), settings=VoiceRuntimeSettings())
    monkeypatch.setattr(voice_module, "get_voice_service", lambda: state.voice)
    monkeypatch.setattr(providers, "load_voice_runtime_settings", lambda: state.settings)
    return state


def _voice_note(seconds: float = 1.0) -> bytes:
    return voice_replies.ogg_opus(_wav(seconds)).audio


def test_sensevoice_chosen_for_dictate_transcribes_voice_notes(speech) -> None:
    speech.settings = VoiceRuntimeSettings(dictation_model=SENSEVOICE_MODEL)

    text = media.transcribe_audio(_voice_note(1.0), ".ogg")

    assert text == "heard by local_funasr"
    (pcm,) = speech.voice.sensevoice.heard
    assert abs(len(pcm) - 32_000) < 3_200  # one second of 16 kHz mono 16-bit speech, decoded by PyAV
    assert speech.voice.whisper_calls == []


def test_whisper_detects_the_voice_notes_language_instead_of_assuming_english(speech) -> None:
    text = media.transcribe_audio(_voice_note(1.0), ".ogg")

    assert text == "heard by whisper"
    (call,) = speech.voice.whisper_calls
    assert call["language"] is None
    assert call["allow_download"] is False


def test_no_speech_model_says_so_and_downloads_nothing(speech, monkeypatch) -> None:
    from row_bot.channels import telegram

    speech.voice.whisper = False
    turns: list[str] = []

    async def run_agent(_update, _context, text: str, **_kwargs: Any) -> None:
        turns.append(text)

    async def react(*_args: Any) -> None:
        return None

    replies: list[str] = []

    class _File:
        mime_type = "audio/ogg"

        async def get_file(self):
            return self

        async def download_as_bytearray(self) -> bytearray:
            return bytearray(_voice_note(0.5))

    async def reply_text(text: str, **_kwargs: Any) -> None:
        replies.append(text)

    monkeypatch.setattr(telegram, "_is_authorised", lambda _update: True)
    monkeypatch.setattr(telegram, "_run_agent_for_message", run_agent)
    monkeypatch.setattr(telegram, "_react", react)
    message = SimpleNamespace(voice=_File(), audio=None, caption="", media_group_id=None, reply_text=reply_text)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=77), message=message)

    asyncio.run(telegram._handle_voice(update, SimpleNamespace(chat_data={})))

    assert turns == []
    assert replies == [media.VOICE_NOTES_NEED_A_MODEL]
    assert speech.voice.installs == [] and speech.voice.whisper_calls == []


def test_the_missing_model_notice_goes_out_only_through_the_channel_and_is_never_saved(
    speech, monkeypatch, tmp_path, reload_for_data_dir,
) -> None:
    """A first voice note from a new chat: the notice is sent, but no turn runs and nothing is saved."""
    from row_bot.channels import whatsapp

    _paths, threads = reload_for_data_dir(tmp_path / "data", "row_bot.data_paths", "row_bot.threads")
    assert threads._list_threads() == []  # no conversation yet
    speech.voice.whisper = False
    sent: list[str] = []
    monkeypatch.setattr(whatsapp, "_send_message_sync", lambda _chat, text, **_kw: sent.append(text))
    monkeypatch.setattr(whatsapp, "_run_text_turn", lambda *_a, **_kw: pytest.fail("no turn may run"))

    whatsapp._run_media_batch("15550001@s.whatsapp.net", [
        (InboundAttachment("audio.ogg", _voice_note(0.5), "audio/ogg", voice_note=True), "", None),
    ])

    assert sent == [media.VOICE_NOTES_NEED_A_MODEL]
    for thread_id, *_rest in threads._list_threads():
        assert threads.get_latest_checkpoint_messages(thread_id) == []
    assert speech.voice.installs == []


def test_a_captioned_voice_note_without_a_model_still_answers_the_words(speech) -> None:
    speech.voice.whisper = False

    turn = media.build_attachment_turn(
        [InboundAttachment("voice.ogg", _voice_note(0.5), "audio/ogg", voice_note=True)], "and this one?",
    )

    assert turn.usable is True and turn.voice_note is False
    assert ("[Attached audio: voice.ogg - not transcribed: no speech model is installed; "
            "the person installs one in Settings › Voice]") in turn.text


def test_a_plugin_channels_voice_note_without_a_model_gets_the_same_honest_line(speech) -> None:
    from row_bot.plugins.channel_runtime import process_plugin_channel_attachment
    from row_bot.plugins.api import ChannelAttachment

    speech.voice.whisper = False

    result = process_plugin_channel_attachment(
        ChannelAttachment(filename="note.ogg", content_type="audio/ogg", kind="audio", data=_voice_note(0.5)),
    )

    assert result.error == media.VOICE_NOTES_NEED_A_MODEL
    assert speech.voice.installs == []


# ── Replies: voice note out, text always ──────────────────────────────


class _Kokoro:
    provider_id = "local_kokoro"
    display_name = "Kokoro"

    def __init__(self, *, ready: bool = True, fails: bool = False) -> None:
        self.ready = ready
        self.fails = fails
        self.said: list[str] = []

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(self.provider_id, self.display_name, self.ready, local=True)

    def synthesize(self, text: str, *, language: str | None = None) -> SynthesizedSpeech:
        self.said.append(text)
        if self.fails:
            raise RuntimeError("synthetic synthesis failure")
        return SynthesizedSpeech(_wav(0.08 * len(text.split())), "audio/wav")


@pytest.fixture
def kokoro(monkeypatch, tmp_path):
    """Kokoro behind the speech-output seam, and the channels' saved switches in a scratch file."""
    from row_bot.channels import config as channel_config

    monkeypatch.setattr(channel_config, "_CONFIG_PATH", tmp_path / "channels_config.json")
    voice = _Kokoro()
    holder = SimpleNamespace(voice=voice)
    monkeypatch.setattr(voice_replies, "_speech_output", lambda: holder.voice)
    return holder


ANSWER = "Here is the plan. First we pack, then we drive.\n\n```python\nprint('hi')\n```\nSee https://example.test/a"


class _Chat:
    def __init__(self) -> None:
        self.id = 77
        self.sent: list[tuple[str, Any]] = []

    async def send_action(self, action: str) -> None:
        self.sent.append(("action", action))

    async def send_message(self, text: str, **_kwargs: Any) -> None:
        self.sent.append(("text", text))

    async def send_voice(self, **kwargs: Any) -> None:
        self.sent.append(("voice", kwargs))


def _telegram(monkeypatch, *, answer: str = ANSWER) -> SimpleNamespace:
    from row_bot.channels import telegram

    seen = SimpleNamespace(configs=[], chat=_Chat())

    async def stream(_chat, user_text: str, config: dict, **_kwargs: Any):
        seen.configs.append(config)
        delivery = ChannelDeliveryResult(
            delivered=False, streamed=False, finalized=False, fallback_sent=False,
            transport="fake", final_text=answer,
        )
        return answer, None, [], [], delivery

    async def react(*_args: Any) -> None:
        return None

    monkeypatch.setattr(telegram, "_stream_agent_turn_to_telegram", stream)
    monkeypatch.setattr(telegram, "_react", react)
    monkeypatch.setattr(telegram, "_thread_exists", lambda _tid: True)
    monkeypatch.setattr(telegram, "_refresh_thread_model_override", lambda config: config)
    monkeypatch.setattr(telegram, "_is_authorised", lambda _update: True)
    monkeypatch.setattr(media, "transcribe_audio", lambda _data, _ext: "how do we get there")
    return seen


def _telegram_update(seen: SimpleNamespace, *, voice: bool = False, audio: bool = False, text: str = ""):
    class _File:
        mime_type = "audio/ogg"

        async def get_file(self):
            return self

        async def download_as_bytearray(self) -> bytearray:
            return bytearray(b"voice-bytes")

    async def reply_text(reply: str, **_kwargs: Any) -> None:
        seen.chat.sent.append(("reply", reply))

    message = SimpleNamespace(
        voice=_File() if voice else None, audio=_File() if audio else None, text=text, caption="",
        media_group_id=None, message_id=5, reply_to_message=None, reply_text=reply_text,
    )
    update = SimpleNamespace(effective_chat=seen.chat, message=message, effective_user=SimpleNamespace(id=1))
    context = SimpleNamespace(chat_data={"thread_config": {"configurable": {"thread_id": "tg_77"}}})
    return update, context


def _voice_mode(config: dict) -> bool:
    return (config.get("configurable") or {}).get("voice_mode") is True


def test_a_telegram_voice_note_is_answered_spoken_with_text_then_a_voice_note(kokoro, monkeypatch) -> None:
    from row_bot.channels import telegram

    seen = _telegram(monkeypatch)
    update, context = _telegram_update(seen, voice=True)

    asyncio.run(telegram._handle_voice(update, context))

    (config,) = seen.configs
    assert _voice_mode(config)
    # The cached chat config is never made spoken: the next typed turn is typed.
    assert "voice_mode" not in context.chat_data["thread_config"]["configurable"]
    kinds = [kind for kind, _value in seen.chat.sent]
    assert kinds.index("text") < kinds.index("voice")
    assert any("print(" in value for kind, value in seen.chat.sent if kind == "text")  # code arrives as text
    (voice,) = [value for kind, value in seen.chat.sent if kind == "voice"]
    assert voice["filename"] == "reply.ogg" and voice["duration"] >= 1
    assert voice["voice"][:4] == b"OggS" and b"OpusHead" in voice["voice"][:64]
    assert _opus_info(voice["voice"])[:3] == ("ogg", 48_000, "opus")
    # The whole prose is said; the code and link stay in the text.
    (said,) = kokoro.voice.said
    assert said == "Here is the plan. First we pack, then we drive.\nSee."
    assert ("action", "record_voice") in seen.chat.sent


def test_switch_off_answers_a_voice_note_with_text_only(kokoro, monkeypatch) -> None:
    from row_bot.channels import config as channel_config
    from row_bot.channels import telegram

    channel_config.set("telegram", voice_replies.VOICE_REPLIES_KEY, False)
    seen = _telegram(monkeypatch)
    update, context = _telegram_update(seen, voice=True)

    asyncio.run(telegram._handle_voice(update, context))

    (config,) = seen.configs
    assert not _voice_mode(config)
    assert [kind for kind, _value in seen.chat.sent if kind == "voice"] == []
    assert kokoro.voice.said == []


def test_a_typed_message_or_an_audio_file_is_answered_with_text_only(kokoro, monkeypatch) -> None:
    from row_bot.channels import telegram

    seen = _telegram(monkeypatch)
    typed, context = _telegram_update(seen, text="how do we get there")
    asyncio.run(telegram._handle_message(typed, context))
    song, context = _telegram_update(seen, audio=True)
    asyncio.run(telegram._handle_voice(song, context))

    assert [_voice_mode(config) for config in seen.configs] == [False, False]
    assert [kind for kind, _value in seen.chat.sent if kind == "voice"] == []
    assert kokoro.voice.said == []


def test_without_kokoro_a_voice_note_is_answered_as_a_typed_turn_with_text(kokoro, monkeypatch) -> None:
    from row_bot.channels import telegram

    kokoro.voice = _Kokoro(ready=False)
    seen = _telegram(monkeypatch)
    update, context = _telegram_update(seen, voice=True)

    asyncio.run(telegram._handle_voice(update, context))

    (config,) = seen.configs
    assert not _voice_mode(config)
    assert [kind for kind, _value in seen.chat.sent if kind == "voice"] == []
    assert [kind for kind, _value in seen.chat.sent if kind == "text"]
    assert "Needs a voice that can speak" in voice_replies.voice_replies_field().help_text


def test_a_failed_synthesis_leaves_the_text_alone_without_an_error_message(kokoro, monkeypatch) -> None:
    from row_bot.channels import telegram

    kokoro.voice = _Kokoro(fails=True)
    seen = _telegram(monkeypatch)
    update, context = _telegram_update(seen, voice=True)

    asyncio.run(telegram._handle_voice(update, context))

    texts = [value for kind, value in seen.chat.sent if kind == "text"]
    assert [kind for kind, _value in seen.chat.sent if kind == "voice"] == []
    assert len(texts) == 1 and "Error" not in texts[0]


def test_a_long_answer_is_said_whole_or_goes_as_text_when_over_the_limit(kokoro) -> None:
    long_answer = " ".join(f"Sentence number {index} is here." for index in range(200))

    note = voice_replies.make_voice_note(long_answer, max_bytes=voice_replies.WHATSAPP_MAX_BYTES)
    assert note is not None
    (said,) = kokoro.voice.said
    assert said.count("Sentence number") == 200  # never cut

    kokoro.voice.said.clear()
    assert voice_replies.make_voice_note(long_answer, max_bytes=10_000) is None
    assert kokoro.voice.said == []  # judged from the words: nothing synthesized for nothing


def test_an_answer_with_nothing_to_say_sends_no_voice_note(kokoro) -> None:
    assert voice_replies.make_voice_note("```\ncode only\n```\nhttps://example.test", max_bytes=1_000_000) is None
    assert kokoro.voice.said == []


# ── WhatsApp ──────────────────────────────────────────────────────────


def _whatsapp(monkeypatch) -> SimpleNamespace:
    from row_bot.channels import whatsapp

    seen = SimpleNamespace(configs=[], sent=[])

    def stream(_chat: str, _body: str, config: dict):
        seen.configs.append(config)
        delivery = ChannelDeliveryResult(
            delivered=True, streamed=True, finalized=True, fallback_sent=False,
            transport="fake", final_text=ANSWER,
        )
        seen.sent.append(("text", ANSWER))  # streamed while it was written
        return ANSWER, None, [], [], delivery

    def send_media(_chat: str, path: str, caption: str | None = None) -> None:
        from pathlib import Path

        seen.sent.append(("media", Path(path).name, Path(path).read_bytes()))

    monkeypatch.setattr(whatsapp, "_run_whatsapp_stream_turn_sync", stream)
    monkeypatch.setattr(whatsapp, "_get_or_create_thread", lambda _chat: "wa_thread")
    monkeypatch.setattr(whatsapp, "_react_sync", lambda *_args: None)
    monkeypatch.setattr(whatsapp, "_typing_sync", lambda _chat, presence="composing": seen.sent.append(("presence", presence)))
    monkeypatch.setattr(whatsapp, "_send_media_sync", send_media)
    monkeypatch.setattr(whatsapp, "_send_message_sync", lambda _chat, text, **_kw: seen.sent.append(("message", text)))
    monkeypatch.setattr(whatsapp, "_send_raw_sync", lambda _chat, text: seen.sent.append(("raw", text)))
    return seen


def test_a_whatsapp_voice_note_gets_its_text_then_an_ogg_opus_voice_note(kokoro, monkeypatch) -> None:
    from row_bot.channels import whatsapp

    seen = _whatsapp(monkeypatch)

    whatsapp._run_text_turn("chat@s.whatsapp.net", "[Voice message transcription]: hi", None, voice_reply=True)

    (config,) = seen.configs
    assert _voice_mode(config)
    kinds = [item[0] for item in seen.sent]
    assert kinds.index("text") < kinds.index("media")
    assert ("presence", "recording") in seen.sent
    ((_kind, name, audio),) = [item for item in seen.sent if item[0] == "media"]
    assert name == "reply.ogg"  # the bridge sends .ogg as a voice note (ptt)
    assert _opus_info(audio)[:3] == ("ogg", 48_000, "opus")


def test_only_a_whatsapp_voice_note_with_the_switch_on_asks_for_a_voice_reply(kokoro, monkeypatch) -> None:
    from row_bot.channels import config as channel_config
    from row_bot.channels import whatsapp

    turns: list[tuple[str, bool]] = []
    monkeypatch.setattr(media, "transcribe_audio", lambda _data, _ext: "hello")
    monkeypatch.setattr(whatsapp, "_get_or_create_thread", lambda _chat: "wa_thread")
    monkeypatch.setattr(whatsapp, "_run_text_turn", lambda _chat, text, _key, *, voice_reply=False: turns.append(
        (text, voice_reply)))

    def batch(voice_note: bool) -> None:
        whatsapp._run_media_batch("chat", [(InboundAttachment("audio.ogg", b"x", "audio/ogg", voice_note=voice_note),
                                            "", None)])

    batch(True)
    batch(False)  # an audio file, not a recorded note
    channel_config.set("whatsapp", voice_replies.VOICE_REPLIES_KEY, False)
    batch(True)

    assert [voice for _text, voice in turns] == [True, False, False]


def test_the_bridges_voice_note_flag_marks_the_attachment(monkeypatch) -> None:
    from row_bot.channels import whatsapp

    queued: list[Any] = []

    class _Batches:
        def begin(self, _key: str) -> int:
            return 1

        def add(self, _key: str, _order: int, item: Any) -> None:
            queued.append(item[0])

    monkeypatch.setattr(whatsapp, "_media_batches", _Batches())
    for flag in (True, False):
        whatsapp._queue_media("chat", {"mediaData": base64.b64encode(b"x").decode(), "filename": "audio.ogg",
                                       "mediaType": "audio/ogg", "isVoiceNote": flag}, "")

    assert [attachment.voice_note for attachment in queued] == [True, False]
