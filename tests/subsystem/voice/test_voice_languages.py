"""V8 Languages: the person's language reaches every speech-to-text path, an engine that can't understand it
says so in Settings without switching engines, and the answer is spoken by a voice for its language or left
on screen when no voice speaks it."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from row_bot.voice import languages
from row_bot.voice.engine.session import SessionSettings
from row_bot.voice.engine.speech import ProviderSentenceSpeech, SegmentedSpeechToText
from row_bot.voice.provider_base import SynthesizedSpeech
from row_bot.voice.providers import LocalKokoroOutput, speech_to_text_provider
from row_bot.voice.runtime import VoiceRuntimeSettings
from tests.helpers.voice_fakes import (
    FRAME_MS, SILENT, VOICED, FakeClock, FakeHost, FakeLink, FakeSentenceSpeech, FakeSleep, FakeSpeechToText,
    fake_engine_parts,
)

pytestmark = pytest.mark.subsystem


class Rig:
    """A live voice session on fakes: what the person says, and what reaches the bar."""

    def __init__(self, *finals: str, settings: SessionSettings | None = None) -> None:
        from row_bot.voice.engine.session import DuplexSession

        self.clock = FakeClock()
        self.stt = FakeSpeechToText(self.clock, *finals)
        self.tts = FakeSentenceSpeech(self.clock)
        self.link = FakeLink()
        self.host = FakeHost()
        self.session = DuplexSession(fake_engine_parts(self.clock, self.stt, self.tts), self.link, self.host,
                                     settings=settings, clock=self.clock, sleep=FakeSleep(self.clock))
        self.link.session = self.session

    async def say(self) -> None:
        for voiced, ms in ((True, 600), (False, 400)):
            for _ in range(ms // FRAME_MS):
                self.clock.advance(FRAME_MS)
                await self.session.audio(VOICED if voiced else SILENT)
                await asyncio.sleep(0)
        await self.session.idle()

    def answer(self, text: str) -> None:
        self.session.agent_event("token", text, "pass-1")
        self.session.agent_event("done", None, "pass-1")


@pytest.fixture(autouse=True)
def _nothing_heard_yet():
    languages.forget_heard()
    yield
    languages.forget_heard()


class _Whisper:
    """VoiceService's Whisper: records the language it is asked for and says what it 'detected'."""

    def __init__(self, detected: str = "fr") -> None:
        self.asked: list[str | None] = []
        self.detected = detected

    def sync_saved_models(self) -> None:
        pass

    def whisper_model_available(self) -> bool:
        return True

    def transcribe_pcm16(self, pcm: bytes, *, allow_download: bool, language: str | None) -> str:
        return self.transcribe_pcm16_heard(pcm, allow_download=allow_download, language=language)[0]

    def transcribe_pcm16_heard(self, pcm: bytes, *, allow_download: bool, language: str | None):
        self.asked.append(language)
        return "bonjour", language or self.detected


def _settings(**saved) -> VoiceRuntimeSettings:
    return VoiceRuntimeSettings(**saved)


@pytest.mark.parametrize("mode", ["dictate", "talk", "voice_note"])
@pytest.mark.parametrize(("chosen", "asked"), [("fr", "fr"), ("auto", None)])
def test_the_chosen_language_or_detection_reaches_whisper_on_every_path(mode, chosen, asked):
    whisper = _Whisper()
    provider = speech_to_text_provider(mode, voice_service=whisper, settings=_settings(speech_language=chosen))
    assert provider.transcribe_bytes(b"\0\0") == "bonjour"
    assert whisper.asked == [asked]


def test_talks_segmented_stream_asks_in_the_sessions_language_and_reports_what_it_heard():
    whisper = _Whisper(detected="es")
    provider = speech_to_text_provider("talk", voice_service=whisper, settings=_settings(speech_language="auto"))
    stt = SegmentedSpeechToText(provider)
    stream = stt.open(language="fr")
    stream.push(b"\x01\x00" * 16_000)
    heard = stream.finish()
    assert (heard.text, heard.language, whisper.asked) == ("bonjour", "fr", ["fr"])
    detecting = stt.open(language=None)
    detecting.push(b"\x01\x00" * 16_000)
    assert detecting.finish().language == "es"
    assert whisper.asked[-1] is None


def test_the_saved_language_reaches_the_live_session_and_its_streaming_seam():
    async def scenario():
        rig = Rig("Quel temps fait-il ?", settings=SessionSettings(language="fr"))
        rig.stt.heard_language = "fr"
        await rig.session.start()
        await rig.say()
        assert rig.stt.opened == ["fr"]
        final = [c for c in rig.link.of("caption") if c["role"] == "user" and c["final"]][-1]
        assert final["language"] == "fr"
        await rig.session.close()
    asyncio.run(scenario())


def test_the_session_settings_follow_the_saved_language(tmp_path, monkeypatch):
    from row_bot.voice.client_duplex import session_settings

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    assert session_settings().language is None
    (tmp_path / "voice_runtime_settings.json").write_text(json.dumps({"speech_language": "de"}), "utf-8")
    assert session_settings().language == "de"
    (tmp_path / "voice_runtime_settings.json").write_text(json.dumps({"speech_language": "xx"}), "utf-8")
    assert session_settings().language is None


class _FunASR:
    def __init__(self, tagged: str) -> None:
        self.tagged = tagged
        self.languages: list[str] = []

    def generate(self, *, input: str, language: str, use_itn: bool):
        self.languages.append(language)
        return [{"text": self.tagged}]


def _sensevoice(tmp_path, model: _FunASR):
    from row_bot.voice.local_provider import LocalFunASRProvider

    provider = LocalFunASRProvider(model_factory=lambda **_: model,
                                   postprocessor=lambda raw: raw.rsplit("|>", 1)[-1].strip())
    provider._model = model
    return provider


@pytest.mark.parametrize(("chosen", "asked", "reported"), [
    ("ja", "ja", "ja"),
    # SenseVoice can't do French: it detects, and Settings says so (no other engine is used).
    ("fr", "auto", "en"),
    (None, "auto", "en"),
])
def test_sensevoice_is_asked_for_a_language_it_knows_and_reports_what_it_heard(tmp_path, chosen, asked, reported):
    model = _FunASR("<|en|><|NEUTRAL|><|Speech|><|withitn|>Hello there.")
    provider = _sensevoice(tmp_path, model)
    heard = provider.transcribe_heard(b"\x01\x00" * 32_000, language=chosen)
    assert model.languages == [asked]
    assert heard.text == "Hello there."
    assert heard.language == reported


# 16 kHz mono 16-bit audio: two seconds (long enough to tell its language) and one second (too short).
LONG = b"\x01\x00" * 32_000
SHORT = b"\x01\x00" * 16_000


class _WhisperModel:
    """faster-whisper's model: detects *detected* with *probability* when asked for no language."""

    def __init__(self, detected: str, probability: float) -> None:
        self.detected, self.probability = detected, probability
        self.asked: list[str | None] = []

    def transcribe(self, audio, *, beam_size, language, vad_filter):
        self.asked.append(language)
        info = SimpleNamespace(language=language or self.detected,
                               language_probability=1.0 if language else self.probability)
        return iter([SimpleNamespace(text=" mm-hmm")]), info


def _whisper(monkeypatch, detected: str, probability: float):
    from row_bot.voice import VoiceService

    service = VoiceService()
    model = _WhisperModel(detected, probability)
    monkeypatch.setattr(service, "_ensure_whisper", lambda **_: model)
    return service, model


def test_the_first_unsure_utterance_is_heard_in_the_interface_language(monkeypatch):
    service, model = _whisper(monkeypatch, "cy", 0.3)
    assert service.transcribe_pcm16_heard(SHORT, language=None) == ("mm-hmm", "en")
    # Too short to tell: no detection at all, one pass in the interface's language.
    assert model.asked == ["en"]
    monkeypatch.setattr(languages, "UI_LANGUAGE", "fr")
    assert service.transcribe_pcm16_heard(LONG, language=None)[1] == "fr"
    # Unsure on longer audio: the guess is dropped before decoding and the audio read in the kept language.
    assert model.asked == ["en", None, "fr"]


def test_a_confident_french_clip_switches_and_a_later_unsure_one_keeps_french(monkeypatch):
    service, model = _whisper(monkeypatch, "fr", 0.92)
    assert service.transcribe_pcm16_heard(LONG, language=None)[1] == "fr"
    assert model.asked == [None]
    model.detected, model.probability = "nl", 0.31
    assert service.transcribe_pcm16_heard(LONG, language=None)[1] == "fr"
    assert service.transcribe_pcm16_heard(SHORT, language=None)[1] == "fr"
    assert model.asked == [None, None, "fr", "fr"]
    # A chosen language is used as it is.
    assert service.transcribe_pcm16_heard(SHORT, language="de")[1] == "de"


def test_confident_detections_tell_settings_an_english_only_engine_hears_another_language(monkeypatch):
    service, _model = _whisper(monkeypatch, "es", 0.9)
    for _ in range(languages.HEARD_OFTEN - 1):
        service.transcribe_pcm16(LONG, language=None)
    assert languages.heard_often(frozenset({"en"})) is None
    service.transcribe_pcm16(LONG, language=None)
    assert languages.heard_often(frozenset({"en"})) == "es"
    # Chosen languages and unsure guesses are not detections.
    languages.forget_heard()
    for _ in range(languages.HEARD_OFTEN):
        service.transcribe_pcm16(LONG, language="es")
        service.transcribe_pcm16(SHORT, language=None)
    assert languages.heard_often(frozenset({"en"})) is None


def test_a_short_sensevoice_clip_keeps_the_last_language_heard(tmp_path):
    languages.note_heard("ja")
    model = _FunASR("<|ja|><|NEUTRAL|><|Speech|><|withitn|>はい。")
    provider = _sensevoice(tmp_path, model)
    assert provider.transcribe_heard(SHORT, language=None).language == "ja"
    assert model.languages == ["ja"]


def test_an_english_only_talk_model_keeps_listening_when_another_language_is_chosen():
    from row_bot.voice.engine.sherpa_speech import LocalStreamingSpeechProvider

    provider = speech_to_text_provider(
        "talk", voice_service=_Whisper(),
        settings=_settings(talk_model="local-moonshine-base-en", speech_language="fr"))
    # No silent switch to another engine: Settings says Moonshine understands English only.
    assert isinstance(provider, LocalStreamingSpeechProvider)
    assert languages.engine_languages("local-moonshine-base-en") == frozenset({"en"})
    assert languages.engine_languages("local-nemotron-streaming-en") == frozenset({"en"})
    assert languages.engine_languages(languages.WHISPER_ENGINE) is None


# ── Speaking the answer ─────────────────────────────────────────────────────

class _Kokoro:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def create(self, text: str, *, voice: str, speed: float, lang: str):
        self.calls.append((text, voice, lang))
        return np.zeros(240, dtype=np.float32), 24_000


@pytest.fixture
def kokoro(tmp_path, monkeypatch):
    from row_bot import tts

    settings = tmp_path / "tts_settings.json"
    settings.write_text(json.dumps({"voice": "bf_emma", "language_voices": {"es": "em_alex", "fr": "nobody"}}),
                        "utf-8")
    monkeypatch.setattr(tts, "_SETTINGS_PATH", settings)
    model = _Kokoro()
    monkeypatch.setattr(tts.TTSService, "_get_kokoro", lambda self: model)
    monkeypatch.setattr(tts.TTSService, "is_installed", lambda self: True)
    return SimpleNamespace(output=LocalKokoroOutput(tts.TTSService()), model=model)


def test_a_french_answer_gets_a_french_voice_and_an_english_one_keeps_the_persons_voice(kokoro):
    kokoro.output.synthesize("Bonjour ! Il fait beau aujourd'hui et je suis là pour vous aider.")
    kokoro.output.synthesize("It is sunny today, and the rain will stay away for the week.")
    assert [(voice, lang) for _text, voice, lang in kokoro.model.calls] == [
        ("ff_siwis", "fr-fr"),  # an unknown pick falls back to the language's preset voice
        ("bf_emma", "en-gb"),   # the person's own (British) voice
    ]


def test_the_voice_picked_for_a_language_speaks_it(kokoro):
    kokoro.output.synthesize("Hola, ¿qué tal? Hoy hace sol y no va a llover.")
    assert kokoro.model.calls[-1][1:] == ("em_alex", "es")


def test_a_short_answer_follows_the_language_the_person_spoke(kokoro):
    kokoro.output.synthesize("D'accord.", language="fr")
    kokoro.output.synthesize("D'accord.")
    assert [voice for _text, voice, _lang in kokoro.model.calls] == ["ff_siwis", "bf_emma"]


def test_a_language_kokoro_cant_speak_is_left_on_screen_without_an_error(kokoro):
    speech = kokoro.output.synthesize("Das ist nicht gut, und ich bin heute sehr müde.")
    assert speech == SynthesizedSpeech(b"", "audio/wav", unspoken="de")
    arabic = ProviderSentenceSpeech(kokoro.output).synthesize("مرحبا، كيف حالك اليوم؟")
    assert (arabic.pcm16, arabic.unspoken) == (b"", "ar")
    # A hint alone never silences an answer whose words are unclear.
    assert kokoro.output.synthesize("OK.", language="de").audio
    assert not [call for call in kokoro.model.calls if call[0].startswith(("Das", "مرحبا"))]


def test_an_unspoken_answer_stays_in_the_bar_with_the_reason():
    async def scenario():
        rig = Rig("Wie spät ist es?")
        rig.stt.heard_language = "de"
        rig.tts.unspoken = "de"
        await rig.session.start()
        await rig.say()
        rig.answer("Es ist drei Uhr am Nachmittag. ")
        await rig.session.idle()
        assert rig.tts.languages == ["de"]  # the language heard is the voice's hint
        said = [c for c in rig.link.of("caption") if c["role"] == "assistant"]
        assert said == [{"type": "caption", "role": "assistant", "text": "Es ist drei Uhr am Nachmittag.",
                         "segment": said[0]["segment"], "kind": "answer", "final": True, "spoken": False}]
        assert rig.link.of("unspoken") == [{"type": "unspoken", "language": "de"}]
        assert not rig.link.audio
        assert "error" not in [event["type"] for event in rig.link.events]
        await rig.session.close()
    asyncio.run(scenario())


def test_read_aloud_and_voice_notes_leave_an_unspeakable_reply_as_text(kokoro, monkeypatch):
    from row_bot.channels import voice_replies
    from row_bot.voice.browser_local import BrowserLocalVoiceService, BrowserVoiceError

    german = "Das ist nicht gut, und ich bin heute sehr müde."
    service = BrowserLocalVoiceService(voice_service=SimpleNamespace(), tts_service=SimpleNamespace())
    monkeypatch.setattr("row_bot.voice.browser_local.speech_output_provider", lambda **_: kokoro.output)
    with pytest.raises(BrowserVoiceError) as refused:
        service.synthesize("session", german)
    assert (refused.value.code, refused.value.status_code) == ("voice_language_unspoken", 422)
    monkeypatch.setattr(voice_replies, "_speech_output", lambda: kokoro.output)
    assert voice_replies.make_voice_note(german, max_bytes=1_000_000) is None
    assert not kokoro.model.calls


# ── The language of a piece of text ─────────────────────────────────────────

@pytest.mark.parametrize(("text", "language"), [
    ("The meeting moved to Thursday, and the room is on the second floor.", "en"),
    ("La réunion est jeudi et la salle est au deuxième étage.", "fr"),
    ("La reunión es el jueves y la sala está en el segundo piso.", "es"),
    ("Die Besprechung ist am Donnerstag und der Raum ist im zweiten Stock.", "de"),
    ("La riunione è giovedì e la sala è al secondo piano, non al primo.", "it"),
    ("A reunião é na quinta e a sala está no segundo andar, não no primeiro.", "pt"),
    ("会議は木曜日に移りました。", "ja"),
    ("会议改到星期四了。", "zh"),
    ("회의는 목요일로 옮겨졌습니다.", "ko"),
    ("बैठक गुरुवार को है।", "hi"),
    ("הפגישה עברה ליום חמישי.", "he"),
    ("الاجتماع يوم الخميس.", "ar"),
    ("Встреча перенесена на четверг.", "ru"),
    # A borrowed word or a name never changes the language on its own.
    ("I had a croissant at the café in Zürich with my friend.", "en"),
    ("OK.", None),
    ("", None),
])
def test_the_language_of_a_sentence_is_guessed_only_when_it_is_clear(text, language):
    assert languages.text_language(text) == language
