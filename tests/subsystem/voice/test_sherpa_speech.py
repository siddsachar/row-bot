"""The streaming speech-to-text adapters behind Row-Bot Voice's seam, with fake sherpa-onnx
recognizers: partials grow in order and the final follows, warm-up loads a model once, a closed
(barged-in) stream yields nothing, long Moonshine turns are committed at quiet points, and the
duplex engine shows live captions and hears words over its own voice. No real model or network."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from row_bot.voice import speech_models
from row_bot.voice.engine import sherpa_speech
from row_bot.voice.engine.session import DuplexSession
from row_bot.voice.provider_base import VoiceProviderError
from row_bot.voice.streaming_base import INPUT_SAMPLE_RATE
from tests.helpers.voice_fakes import (
    FRAME_MS, SILENT, VOICED, FakeClock, FakeEndOfTurn, FakeHost, FakeLink, FakeSentenceSpeech,
    fake_engine_parts,
)

pytestmark = pytest.mark.subsystem

WORDS = "turn on the kitchen lights please and dim the hall".split()
CHUNK = 2560  # 160 ms: the streaming model decodes a chunk at a time


def _installed(root: Path, model: speech_models.SpeechModel) -> speech_models.SpeechModel:
    small = replace(model, files=tuple((name, 4) for name, _size in model.files))
    staging = root / "staging"
    staging.mkdir(parents=True)
    for name, size in small.files:
        (staging / name).write_bytes(b"x" * size)
    speech_models.publish(small, staging, root)
    return small


class FakeOnlineStream:
    def __init__(self) -> None:
        self.samples = np.zeros(0, np.float32)
        self.decoded = 0
        self.words: list[str] = []
        self.finished = False

    def accept_waveform(self, rate: int, samples: np.ndarray) -> None:
        assert rate == INPUT_SAMPLE_RATE and samples.dtype == np.float32
        self.samples = np.concatenate([self.samples, samples])

    def input_finished(self) -> None:
        self.finished = True


class FakeOnlineRecognizer:
    """A word for each loud 160 ms chunk, in order: what a streaming transducer does, without one."""

    def __init__(self) -> None:
        self.decodes = 0

    def create_stream(self) -> FakeOnlineStream:
        return FakeOnlineStream()

    def is_ready(self, stream: FakeOnlineStream) -> bool:
        return len(stream.samples) - stream.decoded >= CHUNK

    def decode_stream(self, stream: FakeOnlineStream) -> None:
        self.decodes += 1
        chunk = stream.samples[stream.decoded:stream.decoded + CHUNK]
        stream.decoded += CHUNK
        if np.abs(chunk).max(initial=0) > 0.1 and len(stream.words) < len(WORDS):
            stream.words.append(WORDS[len(stream.words)])

    def get_result(self, stream: FakeOnlineStream) -> str:
        return " ".join(stream.words)


class FakeOfflineStream:
    def __init__(self, owner: FakeOfflineRecognizer) -> None:
        self.owner = owner
        self.samples = np.zeros(0, np.float32)
        self.result = type("Result", (), {"text": ""})()

    def accept_waveform(self, rate: int, samples: np.ndarray) -> None:
        self.samples = np.concatenate([self.samples, samples])


class FakeOfflineRecognizer:
    """Moonshine's role: one word per loud half second of whatever it is given, and a record of
    how much audio each decode read."""

    def __init__(self) -> None:
        self.reads: list[int] = []

    def create_stream(self) -> FakeOfflineStream:
        return FakeOfflineStream(self)

    def decode_stream(self, stream: FakeOfflineStream) -> None:
        self.reads.append(len(stream.samples))
        loud = int(np.sum(np.abs(stream.samples) > 0.1))
        stream.result.text = " ".join(WORDS[: loud // (INPUT_SAMPLE_RATE // 2)])


def _loader(recognizer):
    calls = []

    def load(model, folder, threads):
        calls.append((model.id, Path(folder), threads))
        return recognizer

    return load, calls


LOUD = (np.full(320, 10_000, np.int16)).astype("<i2").tobytes()  # 20 ms of loud audio
QUIET = b"\0\0" * 320


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(sherpa_speech, "_loaded", {})


def test_partials_grow_in_order_and_the_final_has_every_word(tmp_path):
    model = _installed(tmp_path, speech_models.NEMOTRON_STREAMING_EN)
    recognizer = FakeOnlineRecognizer()
    load, calls = _loader(recognizer)
    engine = sherpa_speech.streaming_engine(model, root=tmp_path, load=load, threads=2)
    assert engine.live_partials is True

    stream = engine.open()
    seen = []
    for _ in range(5):
        for _frame in range(8):  # 160 ms
            stream.push(LOUD)
        update = stream.partial()
        assert update is not None and not update.final
        seen.append(update.text)
    assert seen == [" ".join(WORDS[:count]) for count in range(1, 6)]
    # The language is the model's own; nothing assumes one.
    assert update.language == model.language

    stream.push(LOUD * 8)
    final = stream.finish()
    assert final.final and final.text == " ".join(WORDS[:6])
    # The tail of silence let the last chunk through, and the stream is done.
    assert stream.partial() is None
    assert calls == [(model.id, speech_models.model_dir(model, tmp_path), 2)]


def test_warm_up_loads_the_model_once_for_every_session(tmp_path):
    model = _installed(tmp_path, speech_models.NEMOTRON_STREAMING_EN)
    load, calls = _loader(FakeOnlineRecognizer())
    first = sherpa_speech.streaming_engine(model, root=tmp_path, load=load)
    second = sherpa_speech.streaming_engine(model, root=tmp_path, load=load)

    first.warm()
    second.warm()
    second.open().finish()
    assert len(calls) == 1

    sherpa_speech.unload(model)  # before Remove deletes the files
    second.warm()
    assert len(calls) == 2


def test_a_model_that_is_not_downloaded_reports_its_code_and_loads_nothing(tmp_path):
    load, calls = _loader(FakeOnlineRecognizer())
    engine = sherpa_speech.streaming_engine(speech_models.NEMOTRON_STREAMING_EN, root=tmp_path, load=load)

    with pytest.raises(VoiceProviderError) as missing:
        engine.warm()
    assert missing.value.code == "speech_model_missing" and calls == []
    provider = sherpa_speech.LocalStreamingSpeechProvider(speech_models.NEMOTRON_STREAMING_EN, root=tmp_path,
                                                          load=load)
    status = provider.status()
    assert not status.ready and status.unavailable_code == "speech_model_missing" and status.local


@pytest.mark.parametrize("model", [speech_models.NEMOTRON_STREAMING_EN, speech_models.MOONSHINE_BASE_EN])
def test_a_stream_dropped_at_a_barge_in_hears_nothing_more(tmp_path, model):
    model = _installed(tmp_path, model)
    recognizer = FakeOnlineRecognizer() if model.family == "transducer" else FakeOfflineRecognizer()
    load, _calls = _loader(recognizer)
    stream = sherpa_speech.streaming_engine(model, root=tmp_path, load=load).open()
    stream.push(LOUD * 50)

    stream.close()
    stream.close()  # safe twice
    stream.push(LOUD * 50)
    assert stream.partial() is None
    assert stream.finish().text == ""


def test_moonshine_reads_partials_only_as_new_audio_arrives_and_the_final_reuses_the_last_read(tmp_path):
    model = _installed(tmp_path, speech_models.MOONSHINE_BASE_EN)
    recognizer = FakeOfflineRecognizer()
    load, _calls = _loader(recognizer)
    stream = sherpa_speech.streaming_engine(model, root=tmp_path, load=load).open()

    stream.push(LOUD * 10)  # 200 ms: not worth a read yet
    assert stream.partial().text == "" and recognizer.reads == []
    stream.push(LOUD * 40)  # one second in all
    assert stream.partial().text == "turn on"
    assert stream.partial().text == "turn on" and len(recognizer.reads) == 1

    # The pause after speaking holds no words: nothing is read again, not even for the final.
    stream.push(QUIET * 30)
    assert stream.partial().text == "turn on"
    final = stream.finish()
    assert final.text == "turn on" and len(recognizer.reads) == 1


def test_moonshine_reads_again_when_words_follow_a_pause(tmp_path):
    model = _installed(tmp_path, speech_models.MOONSHINE_BASE_EN)
    recognizer = FakeOfflineRecognizer()
    load, _calls = _loader(recognizer)
    stream = sherpa_speech.streaming_engine(model, root=tmp_path, load=load).open()

    stream.push(LOUD * 50)
    assert stream.partial().text == "turn on"
    stream.push(QUIET * 10)
    stream.push(LOUD * 25)
    assert stream.finish().text == "turn on the"
    assert len(recognizer.reads) == 2


def test_a_long_moonshine_turn_is_committed_at_a_quiet_point_and_no_read_grows_past_it(tmp_path):
    model = _installed(tmp_path, speech_models.MOONSHINE_BASE_EN)
    recognizer = FakeOfflineRecognizer()
    load, _calls = _loader(recognizer)
    stream = sherpa_speech.streaming_engine(model, root=tmp_path, load=load).open()

    # 18 s of speech, a 300 ms pause, then 6 s more: 24.3 s in one turn.
    stream.push(LOUD * 900)
    stream.push(QUIET * 15)
    stream.push(LOUD * 300)
    final = stream.finish()

    limit = sherpa_speech.COMMIT_BYTES // 2
    assert recognizer.reads and max(recognizer.reads) <= limit
    # The cut fell inside the pause: the first read is the speech before it, whole.
    assert 18 * INPUT_SAMPLE_RATE <= recognizer.reads[0] <= 18.3 * INPUT_SAMPLE_RATE
    # Both stretches are in the final, in order (the fake hears at most ten words in each).
    assert final.text.split() == WORDS + WORDS


def test_dictating_a_recording_goes_through_the_same_stream(tmp_path):
    model = _installed(tmp_path, speech_models.NEMOTRON_STREAMING_EN)
    load, _calls = _loader(FakeOnlineRecognizer())
    provider = sherpa_speech.LocalStreamingSpeechProvider(model, root=tmp_path, load=load)

    assert provider.transcribe_bytes(LOUD * 8 * 3) == "turn on the"


# ── In the duplex engine ────────────────────────────────────────────────────


class LiveSpeech:
    """A streaming model stand-in: its words so far are whatever the test sets."""

    provider_id = "fake_live"
    live_partials = True

    def __init__(self, final: str) -> None:
        self.heard = ""
        self.final = final
        self.partials = 0

    def warm(self) -> None:
        pass

    def open(self, *, language=None):
        owner = self

        class Stream:
            def push(self, pcm16: bytes) -> None:
                pass

            def partial(self):
                from row_bot.voice.streaming_base import TranscriptUpdate

                owner.partials += 1
                return TranscriptUpdate(owner.heard, final=False)

            def finish(self):
                from row_bot.voice.streaming_base import TranscriptUpdate

                return TranscriptUpdate(owner.final, final=True)

            def close(self) -> None:
                pass

        return Stream()


def _session(stt):
    clock = FakeClock()
    link = FakeLink()
    host = FakeHost()
    session = DuplexSession(fake_engine_parts(clock, stt, FakeSentenceSpeech(clock), FakeEndOfTurn(0.9)),
                            link, host, clock=clock)
    link.session = session
    return session, link, host, clock


async def _feed(session, clock, ms: int, *, voiced: bool, heard=None, stt=None) -> None:
    for index in range(ms // FRAME_MS):
        if heard is not None and stt is not None:
            stt.heard = heard(index * FRAME_MS)
        clock.advance(FRAME_MS)
        await session.audio(VOICED if voiced else SILENT)
        await session.idle(speech=False)


def test_the_persons_words_show_as_live_captions_while_they_speak_then_the_final_replaces_them():
    async def scenario():
        stt = LiveSpeech("Turn on the kitchen lights.")
        session, link, host, clock = _session(stt)
        await session.start()
        words = WORDS[:5]
        await _feed(session, clock, 1000, voiced=True, stt=stt,
                    heard=lambda at: " ".join(words[: 1 + at // 200]))
        live = [event["text"] for event in link.of("caption") if event["role"] == "user" and not event["final"]]
        # Each caption adds words to the one before, none repeats, and the last has them all.
        assert len(live) >= 3 and len(set(live)) == len(live)
        assert all(later.startswith(earlier) for earlier, later in zip(live, live[1:]))
        assert live[-1] == "turn on the kitchen lights"
        # Partials are read at most every 100 ms of audio, not for every frame.
        assert stt.partials <= 1000 // 100 + 1
        await _feed(session, clock, 400, voiced=False)
        await session.idle()
        captions = [event for event in link.of("caption") if event["role"] == "user"]
        # No blank placeholder between the live words and the final ones.
        assert captions[-1] == {"type": "caption", "role": "user", "text": "Turn on the kitchen lights.",
                                "final": True}
        assert not any(event["text"] == "" for event in captions)
        assert host.turns == ["Turn on the kitchen lights."]
        await session.close()
    asyncio.run(scenario())


def test_words_heard_over_row_bots_voice_confirm_a_barge_in_and_are_not_shown_as_captions():
    async def scenario():
        stt = LiveSpeech("Explain the plan")
        session, link, host, clock = _session(stt)
        await session.start()
        await _feed(session, clock, 600, voiced=True)
        await _feed(session, clock, 400, voiced=False)
        await session.idle()
        session.agent_event("token", "Here is a long first sentence about the plan. And more", "pass-1")
        await session.idle()
        assert session.orb() == "speaking"
        captions_before = len(link.of("caption"))
        stt.heard, stt.final = "actually wait", "Actually, wait."
        started = session.tracker.now_ms
        await _feed(session, clock, 300, voiced=True)
        flushes = [at for kind, at in link.timeline if kind == "flush"]
        assert flushes and flushes[0] - started <= session.settings.barge_in_ms + 3 * FRAME_MS
        user_live = [event for event in link.of("caption")[captions_before:]
                     if event["role"] == "user" and not event["final"] and event["text"]]
        # Until it counted as a barge-in the words were evidence, not the person's caption.
        assert all(event["text"] == "actually wait" for event in user_live)
        await _feed(session, clock, 400, voiced=False)
        await session.idle()
        assert host.turns == ["Explain the plan", "Actually, wait."]
        await session.close()
    asyncio.run(scenario())


def test_a_backchannel_heard_live_never_stops_row_bot():
    async def scenario():
        stt = LiveSpeech("Explain the plan")
        session, link, host, clock = _session(stt)
        await session.start()
        await _feed(session, clock, 600, voiced=True)
        await _feed(session, clock, 400, voiced=False)
        await session.idle()
        session.agent_event("token", "Here is a long first sentence about the plan. And more", "pass-1")
        await session.idle()
        stt.heard = "mhm"
        await _feed(session, clock, 360, voiced=True)
        await _feed(session, clock, 300, voiced=False)
        await session.idle()
        assert not link.of("flush") and host.turns == ["Explain the plan"]
        assert not [event for event in link.of("caption") if event["role"] == "user" and event["text"] == "mhm"]
        await session.close()
    asyncio.run(scenario())
