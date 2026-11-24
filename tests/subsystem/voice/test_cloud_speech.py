"""Cloud speech parts for Row-Bot Voice, against fake provider sockets and HTTP.

Each adapter is driven through the seams the engine uses (``StreamingSpeechToText`` and
``SentenceSpeech``) and the speech output provider Read aloud and voice notes use. Nothing
here reaches a network: sockets are fakes, HTTP goes to ``httpx.MockTransport``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import queue
import threading
from typing import Any

import httpx
import pytest

from row_bot.voice import cloud_speech
from row_bot.voice.cloud_speech import option
from row_bot.voice.cloud_stt import CHUNK_BYTES, CloudSpeechToText
from row_bot.voice.cloud_tts import CloudSpeechOutput, design_voice
from row_bot.voice.provider_base import VoiceProviderError
from row_bot.voice.runtime import VoiceRuntimeSettings
from row_bot.voice.streaming_base import SpeechAudio

pytestmark = pytest.mark.subsystem

KEY = "sk-synthetic-cloud-key-0001"
SPEECH = b"\x10\x27" * (CHUNK_BYTES // 2)  # 100 ms of loud audio


class FakeSocket:
    """A provider's socket: what Row-Bot sent, and the provider's replies to it."""

    def __init__(self, url: str, headers: dict[str, str], react) -> None:
        self.url, self.headers, self._react = url, headers, react
        self.sent: list[Any] = []
        self.inbox: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        self.audio_bytes = 0

    def send(self, message: str | bytes) -> None:
        self.sent.append(message)
        for reply in self._react(self, message) or ():
            self.inbox.put(json.dumps(reply))

    def recv(self, timeout: float | None = None) -> str:
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def close(self) -> None:
        self.closed.set()

    def json_sent(self) -> list[dict]:
        return [json.loads(item) for item in self.sent if isinstance(item, str)]


class Connector:
    def __init__(self, react) -> None:
        self.react = react
        self.sockets: list[FakeSocket] = []

    def __call__(self, url: str, headers: dict[str, str]) -> FakeSocket:
        socket = FakeSocket(url, headers, self.react)
        self.sockets.append(socket)
        return socket


def _audio_of(message: str | bytes) -> int:
    if isinstance(message, bytes):
        return len(message)
    data = json.loads(message)
    encoded = (data.get("audio") if isinstance(data.get("audio"), str)
               else ((data.get("realtimeInput") or {}).get("audio") or {}).get("data"))
    return len(base64.b64decode(encoded)) if encoded else 0


def deepgram(socket: FakeSocket, message):
    if isinstance(message, bytes):
        socket.audio_bytes += len(message)
        return [{"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": "book a"}]}}]
    if json.loads(message)["type"] == "Finalize":
        return [{"type": "Results", "is_final": True, "from_finalize": True,
                 "channel": {"alternatives": [{"transcript": "Book a table for two."}]}}]
    return []


def assemblyai(socket: FakeSocket, message):
    if isinstance(message, bytes):
        socket.audio_bytes += len(message)
        return [{"type": "Turn", "turn_order": 0, "transcript": "book a", "end_of_turn": False}]
    if json.loads(message)["type"] == "Terminate":
        return [{"type": "Turn", "turn_order": 0, "transcript": "Book a table for two.", "end_of_turn": True},
                {"type": "Termination"}]
    return []


def openai(socket: FakeSocket, message):
    data = json.loads(message)
    if data["type"] == "input_audio_buffer.append":
        first = not socket.audio_bytes
        socket.audio_bytes += _audio_of(message)
        # Deltas add to the words so far.
        return [{"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1",
                 "delta": "book a" if first else ""}]
    if data["type"] == "input_audio_buffer.commit":
        return [{"type": "conversation.item.input_audio_transcription.completed", "item_id": "i1",
                 "transcript": "Book a table for two."}]
    return []


def gemini(socket: FakeSocket, message):
    data = json.loads(message)
    if "setup" in data:
        return [{"setupComplete": {}}]
    realtime = data.get("realtimeInput") or {}
    if "audio" in realtime:
        socket.audio_bytes += _audio_of(message)
        return [{"serverContent": {"interimInputTranscription": {"text": "book a"}}}]
    if "activityEnd" in realtime:
        return [{"serverContent": {"inputTranscription": {"text": "Book a table for two."}}},
                {"serverContent": {"turnComplete": True}}]
    return []


LISTENERS = {"deepgram": deepgram, "assemblyai": assemblyai, "openai_transcribe": openai,
             "gemini_transcribe": gemini}


def _listener(option_id: str, react=None, *, key: str = KEY) -> tuple[CloudSpeechToText, Connector]:
    connector = Connector(react or LISTENERS[option_id])
    found = option(option_id, "listen")
    assert found is not None
    return CloudSpeechToText(found, connect=connector, key=lambda _: key), connector


def _wait_for_partial(stream) -> Any:
    seen = threading.Event()
    for _ in range(300):
        heard = stream.partial()
        if heard is not None:
            return heard
        seen.wait(0.01)
    return None


@pytest.mark.parametrize("option_id", sorted(LISTENERS))
def test_each_cloud_listener_streams_partials_then_a_final_with_the_key_in_a_header_only(option_id, caplog):
    caplog.set_level(logging.DEBUG)
    stt, connector = _listener(option_id)
    assert stt.live_partials and stt.status().ready
    stt.warm()  # Nothing to load and no call: no socket opens before anyone speaks.
    assert connector.sockets == []
    stream = stt.open()
    for _ in range(3):
        stream.push(SPEECH)
    partial = _wait_for_partial(stream)
    assert partial is not None and partial.text == "book a" and not partial.final
    final = stream.finish()
    assert final.final and final.text == "Book a table for two."
    socket = connector.sockets[0]
    assert socket.closed.is_set()
    # The person's key travels in a header, never in the address; OpenAI hears 24 kHz audio.
    assert KEY in " ".join(socket.headers.values()) and KEY not in socket.url
    assert socket.audio_bytes == (3 * CHUNK_BYTES * 3 // 2 if option_id == "openai_transcribe" else 3 * CHUNK_BYTES)
    assert KEY not in caplog.text


def test_an_utterance_dropped_at_a_barge_in_closes_its_socket_without_finishing():
    stt, connector = _listener("deepgram")
    stream = stt.open()
    stream.push(SPEECH)
    assert _wait_for_partial(stream) is not None
    stream.close()
    socket = connector.sockets[0]
    assert socket.closed.wait(2)
    assert not any(item.get("type") == "Finalize" for item in socket.json_sent())


def test_a_language_is_passed_to_each_listener_and_none_means_detect():
    for option_id, expect in (("deepgram", "language=fr"), ("assemblyai", "language_codes=fr")):
        stt, connector = _listener(option_id)
        stt.open(language="fr").finish()
        assert expect in connector.sockets[0].url
        stt.open().finish()
        assert "language=multi" in connector.sockets[1].url or "language_detection=true" in connector.sockets[1].url
    stt, connector = _listener("openai_transcribe")
    stt.open(language="de").finish()
    update = connector.sockets[0].json_sent()[0]
    assert update["session"]["audio"]["input"]["transcription"] == {"model": "gpt-live-transcribe",
                                                                    "languages": ["de"]}
    stt, connector = _listener("gemini_transcribe")
    stt.open().finish()
    setup = connector.sockets[0].json_sent()[0]["setup"]
    assert setup["inputAudioTranscription"]["languageCodes"] == []


def test_a_listener_without_a_key_never_connects():
    stt, connector = _listener("assemblyai", key="")
    status = stt.status()
    assert not status.ready and status.unavailable_code == "cloud_key_missing"
    with pytest.raises(VoiceProviderError) as missing:
        stt.open()
    assert missing.value.code == "cloud_key_missing"
    with pytest.raises(VoiceProviderError):
        stt.warm()
    assert connector.sockets == []


def test_a_listener_that_cannot_connect_fails_with_a_code_and_logs_no_key(caplog):
    caplog.set_level(logging.DEBUG)

    def refused(url, headers):
        raise ConnectionRefusedError(f"refused {headers}")

    found = option("deepgram", "listen")
    stt = CloudSpeechToText(found, connect=refused, key=lambda _: KEY)
    stream = stt.open()
    stream.push(SPEECH)
    with pytest.raises(VoiceProviderError) as failed:
        stream.finish()
    assert failed.value.code == "cloud_speech_failed"
    assert KEY not in caplog.text


# ── speaking ─────────────────────────────────────────────────────────────


class Http:
    """``httpx.MockTransport`` that records requests and answers with *pieces* of audio."""

    def __init__(self, respond) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def __call__(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._respond(request)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def bodies(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


def pcm_pieces(*sizes: int):
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=iter([b"\x01" * size for size in sizes]))
    return respond


def gemini_events(*pieces: bytes):
    def respond(request: httpx.Request) -> httpx.Response:
        lines = [f"data: {json.dumps({'event_type': 'step.delta', 'delta': {'type': 'audio', 'data': base64.b64encode(piece).decode()}})}\n\n"
                 for piece in pieces]
        return httpx.Response(200, content=iter([line.encode() for line in lines]))
    return respond


def _speaker(option_id: str, http: Http, *, voice: str = "", style: str = "", key: str = KEY) -> CloudSpeechOutput:
    found = option(option_id, "speak")
    assert found is not None
    return CloudSpeechOutput(found, voice=voice or found.voices[0][0], style=style, client=http,
                             key=lambda _: key)


def test_elevenlabs_streams_24_khz_pcm_in_whole_samples_with_its_key_in_a_header():
    http = Http(pcm_pieces(3, 5, 2))  # Odd pieces: no sample is ever split.
    speech = _speaker("elevenlabs", http, voice="JBFqnCBsd6RMkjVDRZzb").sentence_speech()
    pieces = list(speech.stream("Hello there."))
    assert [len(piece.pcm16) for piece in pieces] == [2, 6, 2]
    assert all(piece.sample_rate == 24_000 for piece in pieces)
    request = http.requests[0]
    assert request.url.path == "/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb/stream"
    assert request.url.params["output_format"] == "pcm_24000"
    assert request.headers["xi-api-key"] == KEY and KEY not in str(request.url)
    # The text's own language goes with it.
    assert http.bodies()[0] == {"text": "Hello there.", "model_id": "eleven_flash_v2_5", "language_code": "en"}


def test_cartesia_and_gemini_requests_and_gemini_style_from_the_description():
    http = Http(pcm_pieces(4))
    _speaker("cartesia", http, voice="62ae83ad-4f6a-430b-af41-a9bede9286ca").synthesize("Hi.")
    request = http.requests[0]
    assert request.headers["Authorization"] == f"Bearer {KEY}" and request.headers["Cartesia-Version"]
    body = http.bodies()[0]
    assert body["transcript"] == "Hi." and body["voice"] == {"id": "62ae83ad-4f6a-430b-af41-a9bede9286ca"}
    assert body["output_format"] == {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 24_000}

    http = Http(gemini_events(b"\x02\x00" * 3, b"\x03\x00"))
    speech = _speaker("gemini_tts", http, voice="Sulafat", style="warm and unhurried").sentence_speech()
    assert b"".join(piece.pcm16 for piece in speech.stream("Good morning.")) == b"\x02\x00" * 3 + b"\x03\x00"
    request = http.requests[0]
    assert request.headers["x-goog-api-key"] == KEY
    body = http.bodies()[0]
    content = body["input"][0]["content"][0]
    assert content["text"] == "Good morning."  # The words alone; how they sound is the style.
    assert content["annotations"] == [{"type": "speech_metadata", "style": "warm and unhurried"}]
    assert body["generation_config"] == {"speech_config": [{"voice": "Sulafat"}]} and body["stream"] is True


def test_saved_pronunciations_apply_to_cloud_voices(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.sound import remember_pronunciations

    remember_pronunciations({"Qwen": "Kwen"})
    http = Http(pcm_pieces(2))
    wav = _speaker("elevenlabs", http).synthesize("Qwen is fast.")
    assert http.bodies()[0]["text"] == "Kwen is fast."
    assert wav.content_type == "audio/wav" and wav.audio.startswith(b"RIFF")


def test_a_cloud_voice_that_fails_raises_a_code_and_logs_neither_key_nor_words(caplog):
    caplog.set_level(logging.DEBUG)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": f"bad key {KEY} for 'Private words.'"})

    with pytest.raises(VoiceProviderError) as failed:
        _speaker("cartesia", Http(refuse)).synthesize("Private words.")
    assert failed.value.code == "cloud_voice_failed"
    assert KEY not in caplog.text and "Private words" not in caplog.text


def test_closing_a_cloud_sentence_closes_its_request():
    closed = threading.Event()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"\x01\x00" * 10
            yield b"\x01\x00" * 10

        def close(self) -> None:
            closed.set()

    http = Http(lambda request: httpx.Response(200, stream=Body()))
    pieces = _speaker("elevenlabs", http).sentence_speech().stream("A long sentence.")
    assert next(pieces).pcm16
    pieces.close()
    assert closed.is_set()


def test_a_cloud_voice_speaks_the_languages_its_company_lists_and_says_which_it_cant():
    http = Http(pcm_pieces(4))
    hebrew = "שלום, מה שלומך היום? הכול בסדר."
    eleven = _speaker("elevenlabs", http)
    # ElevenLabs Flash doesn't speak Hebrew: nothing is sent, and the answer is marked unspoken.
    assert eleven.synthesize(hebrew).unspoken == "he"
    piece = next(eleven.sentence_speech().stream(hebrew))
    assert piece.unspoken == "he" and not piece.pcm16
    assert http.requests == []
    # Cartesia does, and is told so.
    cartesia = _speaker("cartesia", http)
    assert cartesia.synthesize(hebrew).audio.startswith(b"RIFF")
    assert http.bodies()[-1]["language"] == "he"
    # A short reply with no clear language takes the language the person was heard speaking.
    eleven.synthesize("Ja.", language="de")
    assert http.bodies()[-1]["language_code"] == "de"


def test_a_listener_reports_the_language_it_heard_for_a_whole_recording():
    def heard_in_french(socket: FakeSocket, message):
        if isinstance(message, bytes):
            return []
        if json.loads(message)["type"] == "Terminate":
            return [{"type": "Turn", "turn_order": 0, "transcript": "Bonjour.", "end_of_turn": True,
                     "language_code": "fr"}, {"type": "Termination"}]
        return []

    stt, connector = _listener("assemblyai", heard_in_french)
    heard = stt.transcribe_heard(SPEECH * 3, language=None)
    assert (heard.text, heard.language) == ("Bonjour.", "fr")
    assert "language_detection=true" in connector.sockets[0].url


# ── describe a voice; no cloning ──────────────────────────────────────────


def test_describe_a_voice_on_elevenlabs_designs_from_words_only_and_saves_it():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/text-to-voice/design":
            return httpx.Response(200, json={"previews": [{"generated_voice_id": "gen-1", "audio_base_64": "AAAA"}]})
        return httpx.Response(200, json={"voice_id": "voice-made-1"})

    http = Http(respond)
    assert design_voice("A calm, warm voice with a slight smile.", client=http, key=KEY) == "voice-made-1"
    assert [request.url.path for request in http.requests] == ["/v1/text-to-voice/design", "/v1/text-to-voice"]
    design, saved = http.bodies()
    assert design == {"voice_description": "A calm, warm voice with a slight smile.",
                      "model_id": "eleven_multilingual_ttv_v2", "auto_generate_text": True}
    assert saved == {"voice_name": "Row-Bot described voice", "voice_description": design["voice_description"],
                     "generated_voice_id": "gen-1"}
    # Words only: no recording of anyone's voice goes with either request.
    assert not any("audio" in key or "reference" in key for body in (design, saved) for key in body)
    assert all(request.headers["xi-api-key"] == KEY for request in http.requests)


def test_the_designed_voice_is_spoken_only_once_chosen():
    found = option("elevenlabs", "speak")
    settings = VoiceRuntimeSettings(designed_voices={"elevenlabs": "voice-made-1"})
    assert cloud_speech.chosen_voice(found, settings) == found.voices[0][0]
    assert "designed" in cloud_speech.voice_choices(found, settings)
    settings.cloud_voices["elevenlabs"] = cloud_speech.DESIGNED_VOICE
    assert cloud_speech.chosen_voice(found, settings) == "voice-made-1"


# ── selection, fallback and the session ───────────────────────────────────


@pytest.fixture
def keys(tmp_path, monkeypatch):
    """A scratch profile whose saved keys are a dict (the key store's reads only)."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    saved: dict[str, str] = {}
    from row_bot import api_keys
    from row_bot.voice import provider_base

    monkeypatch.setattr(api_keys, "get_key", lambda name: saved.get(name, ""))
    monkeypatch.setattr(provider_base, "provider_api_key", lambda provider: saved.get(f"provider:{provider}", ""))
    return saved


def test_a_chosen_cloud_part_is_used_with_its_key_and_replaced_by_the_local_one_without(keys):
    from types import SimpleNamespace

    from row_bot.voice.providers import LocalKokoroOutput, speech_output_provider, speech_to_text_provider

    voice = SimpleNamespace(sync_saved_models=lambda: None, whisper_model_available=lambda: True)
    settings = VoiceRuntimeSettings(talk_provider="deepgram", speech_output_provider="elevenlabs")
    assert cloud_speech.fallbacks(settings) == [
        {"part": "listen", "id": "deepgram", "company": "Deepgram"},
        {"part": "speak", "id": "elevenlabs", "company": "ElevenLabs"}]
    listen = speech_to_text_provider("talk", voice_service=voice, settings=settings)
    speak = speech_output_provider(tts_service=SimpleNamespace(), settings=settings)
    assert listen.provider_id == "local_whisper" and isinstance(speak, LocalKokoroOutput)

    keys["DEEPGRAM_API_KEY"] = KEY
    keys["ELEVENLABS_API_KEY"] = KEY
    assert cloud_speech.fallbacks(settings) == []
    # With its key, Talk listens with the cloud service and never touches Whisper.
    untouched = SimpleNamespace(sync_saved_models=lambda: pytest.fail("Whisper was loaded"))
    assert speech_to_text_provider("talk", voice_service=untouched, settings=settings).provider_id == "cloud_deepgram"
    assert speech_output_provider(tts_service=SimpleNamespace(), settings=settings).provider_id == "cloud_elevenlabs"
    # The saved language reaches the cloud listener.
    french = VoiceRuntimeSettings(talk_provider="deepgram", speech_language="fr")
    assert speech_to_text_provider("talk", voice_service=voice, settings=french).language == "fr"
    # Dictation and chat-app voice notes keep listening locally.
    assert speech_to_text_provider("dictate", voice_service=voice, settings=settings).provider_id == "local_whisper"

    # OpenAI and Google keys are the ones in Providers.
    google = VoiceRuntimeSettings(talk_provider="gemini_transcribe", speech_output_provider="gemini_tts")
    assert [row["company"] for row in cloud_speech.fallbacks(google)] == ["Google", "Google"]
    keys["provider:google"] = KEY
    assert cloud_speech.fallbacks(google) == []


def test_the_voice_bar_hears_which_cloud_parts_run_locally_for_now():
    from tests.helpers.voice_fakes import FakeClock, FakeHost, FakeLink, FakeSpeechToText, fake_engine_parts
    from row_bot.voice.engine.session import DuplexSession, EngineParts

    clock = FakeClock()
    parts = fake_engine_parts(clock, FakeSpeechToText(clock))
    parts = EngineParts(parts.stt, parts.tts, parts.activity, parts.end_of_turn,
                        fallbacks=({"part": "speak", "id": "cartesia", "company": "Cartesia"},))
    link = FakeLink()

    async def scenario():
        session = DuplexSession(parts, link, FakeHost(), clock=clock)
        link.session = session
        await session.start()
        await session.close()

    asyncio.run(scenario())
    assert link.of("ready")[0]["fallbacks"] == [{"part": "speak", "id": "cartesia", "company": "Cartesia"}]


class StreamingVoice:
    """A cloud voice in the engine: each sentence in three pieces, the later ones on a gate."""

    provider_id = "fake_cloud"
    sample_rate = 24_000

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.closed: list[str] = []
        self.spoken: list[str] = []

    def warm(self) -> None:
        pass

    def synthesize(self, text: str) -> SpeechAudio:
        return SpeechAudio(b"\x01\x00" * 240, 24_000)

    def stream(self, text: str, *, language: str | None = None):
        self.spoken.append(text)
        try:
            yield SpeechAudio(b"\x01\x00" * 240, 24_000)
            self.gate.wait(5)
            yield SpeechAudio(b"\x01\x00" * 240, 24_000)
            yield SpeechAudio(b"\x01\x00" * 240, 24_000)
        finally:
            self.closed.append(text)


def test_a_cloud_sentence_in_a_language_the_voice_cant_speak_stays_on_screen():
    from tests.helpers.voice_fakes import FakeClock, FakeHost, FakeLink, FakeSpeechToText, fake_engine_parts
    from row_bot.voice.engine.session import DuplexSession, EngineParts

    class Unspoken(StreamingVoice):
        def stream(self, text: str, *, language: str | None = None):
            self.hints = language
            yield SpeechAudio(b"", 24_000, unspoken="he")

    clock = FakeClock()
    voice = Unspoken()
    base = fake_engine_parts(clock, FakeSpeechToText(clock))
    link = FakeLink()

    async def scenario():
        session = DuplexSession(EngineParts(base.stt, voice, base.activity, base.end_of_turn), link, FakeHost(),
                                clock=clock)
        link.session = session
        await session.start()
        session.agent_event("token", "Something the voice can't say. ", "pass-1")
        session.agent_event("done", None, "pass-1")
        await session.idle()
        await session.close()

    asyncio.run(scenario())
    assert link.audio == []
    assert link.of("caption")[-1]["spoken"] is False
    assert link.of("unspoken") == [{"type": "unspoken", "language": "he"}]


def test_the_engine_sends_a_cloud_sentence_first_piece_early_and_closes_it_at_a_barge_in():
    from tests.helpers.voice_fakes import FakeClock, FakeHost, FakeLink, FakeSpeechToText, fake_engine_parts
    from row_bot.voice.engine.session import DuplexSession, EngineParts

    clock = FakeClock()
    voice = StreamingVoice()
    base = fake_engine_parts(clock, FakeSpeechToText(clock))
    link = FakeLink()

    async def scenario():
        session = DuplexSession(EngineParts(base.stt, voice, base.activity, base.end_of_turn), link, FakeHost(),
                                clock=clock)
        link.session = session
        await session.start()
        session.agent_event("token", "First sentence here. Second one follows. ", "pass-1")
        for _ in range(200):
            if link.audio:
                break
            await asyncio.sleep(0.005)
        # The first piece is out while the rest of the sentence is still being made.
        assert link.audio == [(1, 480)]
        assert link.of("caption")[-1]["text"] == "First sentence here."
        await session.control({"type": "stop_speaking"})
        voice.gate.set()
        await session.idle()
        await session.close()

    asyncio.run(scenario())
    assert voice.closed == ["First sentence here."]  # Its request closed; the second never asked.
    assert voice.spoken == ["First sentence here."]
    assert [segment for segment, _ in link.audio] == [1]
