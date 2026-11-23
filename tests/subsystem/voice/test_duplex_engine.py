"""Row-Bot Voice's duplex engine with fake speech, agent and socket.

Audio time drives turn taking and barge-in; a fake clock drives latency marks.
Nothing here sleeps, opens a device, loads a model or touches the network.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from row_bot.voice.engine.session import DuplexSession, SessionSettings, TurnOutcome
from row_bot.voice.engine.turns import TurnSettings
from tests.helpers.voice_fakes import (
    FRAME_MS, SILENT, VOICED, FakeClock, FakeEndOfTurn, FakeHost, FakeLink, FakeSentenceSpeech,
    FakeSpeechToText, fake_engine_parts,
)

pytestmark = pytest.mark.subsystem


class Rig:
    def __init__(self, *finals: str, partial: str = "", settings: SessionSettings | None = None,
                 finish_ms: float = 0, synth_ms: float = 0, probability: float = 0.9, host: FakeHost | None = None):
        self.clock = FakeClock()
        self.stt = FakeSpeechToText(self.clock, *finals, partial_text=partial, finish_ms=finish_ms)
        self.tts = FakeSentenceSpeech(self.clock, synth_ms=synth_ms)
        self.turn = FakeEndOfTurn(probability)
        self.link = FakeLink()
        self.host = host or FakeHost()
        self.session = DuplexSession(fake_engine_parts(self.clock, self.stt, self.tts, self.turn),
                                     self.link, self.host, settings=settings, clock=self.clock)
        self.link.session = self.session

    async def feed(self, ms: int, *, voiced: bool, settle: bool = False) -> None:
        for _ in range(ms // FRAME_MS):
            self.clock.advance(FRAME_MS)
            await self.session.audio(VOICED if voiced else SILENT)
            await asyncio.sleep(0)
            if settle:
                await self.session.idle(speech=False)

    async def say(self, ms: int = 600, then_silence: int = 400) -> None:
        await self.feed(ms, voiced=True)
        await self.feed(then_silence, voiced=False)
        await self.session.idle()

    def answer(self, *tokens: str, pass_id: str = "pass-1", done: bool = True) -> None:
        for token in tokens:
            self.session.agent_event("token", token, pass_id)
        if done:
            self.session.agent_event("done", None, pass_id)

    def spoken_segments(self) -> list[int]:
        return sorted({segment for segment, _ in self.link.audio})


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_spoken_turn_reaches_the_agent_and_its_answer_is_spoken_sentence_by_sentence():
    async def scenario():
        rig = Rig("What's the weather like?")
        await rig.session.start()
        assert rig.stt.warmed and rig.tts.warmed
        assert rig.link.states()[-1] == "listening"
        await rig.say()
        assert rig.host.turns == ["What's the weather like?"]
        assert rig.link.states()[-1] == "thinking"
        final = [c for c in rig.link.of("caption") if c["role"] == "user" and c["final"]]
        assert final[-1]["text"] == "What's the weather like?"
        # The first sentence is spoken while the answer is still streaming.
        rig.answer("Sunny and ", "warm today. ", "It will ", done=False)
        await rig.session.idle()
        assert rig.tts.spoken == ["Sunny and warm today."]
        assert rig.link.audio and rig.link.states()[-1] == "speaking"
        rig.answer("stay dry this week.")
        await rig.session.idle()
        assert rig.tts.spoken == ["Sunny and warm today.", "It will stay dry this week."]
        said = [c["text"] for c in rig.link.of("caption") if c["role"] == "assistant"]
        assert said == rig.tts.spoken
        # Audio arrives in order, segment by segment.
        segments = [segment for segment, _ in rig.link.audio]
        assert segments == sorted(segments)
        await rig.session.control({"type": "playback", "state": "idle"})
        assert rig.link.states()[-1] == "listening"
        await rig.session.close()
    run(scenario())


def test_a_long_answer_is_spoken_in_full_with_no_cap_or_app_pointer():
    async def scenario():
        rig = Rig("Tell me a story")
        await rig.session.start()
        await rig.say()
        sentences = [f"This is sentence number {index} of the story." for index in range(1, 31)]
        rig.answer(*(sentence + " " for sentence in sentences))
        await rig.session.idle()
        assert rig.tts.spoken == sentences
        assert not any("app" in text.lower() for text in rig.tts.spoken)
        await rig.session.close()
    run(scenario())


def test_a_first_clause_is_spoken_before_its_sentence_ends_and_code_is_never_spoken():
    async def scenario():
        rig = Rig("Show me")
        await rig.session.start()
        await rig.say()
        rig.answer("Sure thing, I can write that function for you, ", "and it is short", done=False)
        await rig.session.idle()
        assert rig.tts.spoken == ["Sure thing, I can write that function for you,"]
        rig.answer(":\n```python\nprint('hi')\n```\nIt prints a greeting.")
        await rig.session.idle()
        assert not any("python" in text or "'hi'" in text for text in rig.tts.spoken)
        assert rig.tts.spoken[-1] == "It prints a greeting."
        await rig.session.close()
    run(scenario())


def _speak_long_answer(rig: Rig) -> None:
    rig.answer(*(f"Here is a long sentence number {index} about the plan. " for index in range(1, 6)), done=False)


def test_talking_over_row_bot_ducks_at_once_and_a_confirmed_barge_in_stops_it_within_200_ms():
    async def scenario():
        rig = Rig("Explain the plan", "Actually, wait", partial="Actually, wait")
        await rig.session.start()
        await rig.say()
        rig.answer("Here is the first long sentence about the plan. ", "Then", done=False)
        await rig.session.idle()
        assert rig.link.audio and rig.session.orb() == "speaking"
        rig.tts.gate = threading.Event()  # Later sentences wait: speech is pending when the person cuts in.
        rig.answer(*(f" here is a long sentence number {index} about the plan." for index in range(2, 6)), done=False)
        started_ms = rig.session.tracker.now_ms
        await rig.feed(100, voiced=True, settle=True)
        ducks = [at for kind, at in rig.link.timeline if kind == "duck"]
        assert ducks and ducks[0] - started_ms <= 100
        assert not rig.link.of("flush")
        await rig.feed(300, voiced=True, settle=True)
        flushes = [at for kind, at in rig.link.timeline if kind == "flush"]
        assert flushes, "a confirmed barge-in must stop playback"
        threshold_ms = started_ms + rig.session.settings.barge_in_ms
        assert 0 <= flushes[0] - threshold_ms <= 200
        audio_before = len(rig.link.audio)
        rig.tts.gate.set()
        rig.answer("More of the old answer that nobody wants to hear now. ")
        await rig.feed(400, voiced=False)
        await rig.session.idle()
        # Pending and later speech of the interrupted answer is never sent.
        assert len(rig.link.audio) == audio_before
        assert rig.host.turns == ["Explain the plan", "Actually, wait"]
        await rig.session.close()
    run(scenario())


def test_a_backchannel_or_cough_only_ducks_and_row_bot_keeps_talking():
    async def scenario():
        rig = Rig("Explain the plan", partial="Mhm.")
        await rig.session.start()
        await rig.say()
        _speak_long_answer(rig)
        await rig.session.idle()
        assert rig.session.orb() == "speaking"
        await rig.feed(360, voiced=True)
        await rig.feed(300, voiced=False)
        await rig.session.idle()
        assert [event["on"] for event in rig.link.of("duck")] == [True, False]
        assert not rig.link.of("flush")
        assert rig.host.turns == ["Explain the plan"]
        ducks = len(rig.link.of("duck"))
        # A short noise with no words at all is ignored the same way.
        rig.stt.partial_text = ""
        await rig.feed(120, voiced=True)
        await rig.feed(300, voiced=False)
        await rig.session.idle()
        assert not rig.link.of("flush") and rig.host.turns == ["Explain the plan"]
        assert [event["on"] for event in rig.link.of("duck")[ducks:]] in ([], [True, False])
        await rig.session.close()
    run(scenario())


def test_after_a_barge_in_the_interrupted_run_is_silenced_and_the_next_answer_is_spoken():
    async def scenario():
        control = TurnOutcome("control", speakable="Stopping the conversation run.")
        rig = Rig("Explain the plan", "Stop", partial="Stop", host=FakeHost())
        await rig.session.start()
        await rig.say()
        _speak_long_answer(rig)
        await rig.session.idle()
        rig.host.outcome = control  # The run is still working: the host routes words as control.
        await rig.feed(400, voiced=True)
        await rig.feed(400, voiced=False)
        await rig.session.idle()
        assert rig.link.of("flush") and rig.host.turns[-1] == "Stop"
        assert rig.tts.spoken[-1] == "Stopping the conversation run."
        spoken = len(rig.tts.spoken)
        rig.answer("The old answer goes on. ", pass_id="pass-1")
        await rig.session.idle()
        assert len(rig.tts.spoken) == spoken
        rig.answer("A new answer is heard. ", pass_id="pass-2")
        await rig.session.idle()
        assert rig.tts.spoken[-1] == "A new answer is heard."
        await rig.session.close()
    run(scenario())


def test_a_steer_after_a_barge_in_is_answered_aloud_from_the_steer_on():
    async def scenario():
        rig = Rig("Explain the plan", "Make it shorter", partial="Make it shorter")
        await rig.session.start()
        await rig.say()
        rig.session.agent_event("token", "Here is the first long sentence about the plan. ", "pass-1", "seg-1")
        rig.session.agent_event("token", "Then", "pass-1", "seg-1")
        await rig.session.idle()
        assert rig.tts.spoken == ["Here is the first long sentence about the plan."]
        rig.host.outcome = TurnOutcome("control", speakable="Your follow-up is queued in this conversation.")
        await rig.feed(400, voiced=True)
        await rig.feed(400, voiced=False)
        await rig.session.idle()
        assert rig.link.of("flush") and rig.host.turns[-1] == "Make it shorter"
        # The interrupted segment keeps streaming on screen, unheard ...
        rig.session.agent_event("token", " the second part of the old answer. ", "pass-1", "seg-1")
        await rig.session.idle()
        assert "the second part" not in " ".join(rig.tts.spoken)
        # ... and the steered pass's new answer is spoken from the steer on.
        rig.session.agent_event("token", "Short version: ship on Friday. ", "pass-1", "seg-2")
        rig.session.agent_event("done", None, "pass-1", "seg-2")
        await rig.session.idle()
        assert rig.tts.spoken[-2:] == ["Your follow-up is queued in this conversation.", "Short version: ship on Friday."]
        await rig.session.close()
    run(scenario())


def test_a_paused_session_speaks_nothing():
    async def scenario():
        rig = Rig(settings=SessionSettings(idle_pause_ms=1000))
        await rig.session.start()
        await rig.feed(1100, voiced=False)
        assert rig.session.paused
        rig.answer("A typed answer while voice rests. ")
        await rig.session.idle()
        assert rig.tts.spoken == [] and not rig.link.audio
        await rig.session.close()
    run(scenario())


def test_the_engine_adds_almost_nothing_to_voice_to_voice_latency():
    async def scenario():
        rig = Rig("What time is it?", finish_ms=150, synth_ms=120)
        await rig.session.start()
        await rig.feed(600, voiced=True, settle=True)
        while not rig.host.turns:
            await rig.feed(FRAME_MS, voiced=False, settle=True)
        rig.clock.advance(300)  # The agent's first token, 300 ms after admission.
        rig.answer("It is noon. ", "Anything else?")
        await rig.session.idle()
        segment = min(rig.spoken_segments())
        await rig.session.control({"type": "playback", "state": "playing", "segment": segment, "delay_ms": 30})
        latency = rig.link.of("latency")[-1]["last"]
        model_ms = 150 + 300 + 120
        endpointing = latency["endpointing"]
        assert endpointing <= rig.session.settings.turns.pause_ms + 64
        overhead = latency["voice_to_voice"] - endpointing - model_ms
        assert 0 <= overhead <= 20, latency
        assert latency["transcription"] == 150 and latency["synthesis"] == 120
        assert latency["voice_to_ear"] == latency["voice_to_voice"] + 30
        summary = rig.link.of("latency")[-1]["summary"]
        assert summary["turns"] == 1 and summary["stages"]["voice_to_voice"]["p50"] == latency["voice_to_voice"]
        await rig.session.close()
    run(scenario())


def test_an_unfinished_thought_waits_for_the_silence_limit_by_eagerness():
    async def scenario(eagerness: str) -> int:
        settings = SessionSettings(turns=TurnSettings.for_eagerness(eagerness))
        rig = Rig("So I was thinking", probability=0.1, settings=settings)
        await rig.session.start()
        await rig.feed(600, voiced=True)
        waited = 0
        while not rig.host.turns and waited < 4000:
            await rig.feed(100, voiced=False)
            await rig.session.idle()
            waited += 100
        assert rig.turn.calls >= 1
        await rig.session.close()
        return waited
    relaxed, normal, eager = (run(scenario(name)) for name in ("relaxed", "normal", "eager"))
    assert eager < normal < relaxed <= 3000


def test_mute_drops_the_utterance_and_ignores_audio_until_unmuted():
    async def scenario():
        rig = Rig("Hello there")
        await rig.session.start()
        await rig.feed(400, voiced=True)
        await rig.session.control({"type": "mute", "muted": True})
        assert rig.link.states()[-1] == "muted"
        await rig.feed(600, voiced=True)
        await rig.feed(600, voiced=False)
        await rig.session.idle()
        assert rig.host.turns == [] and rig.stt.streams[0].closed
        await rig.session.control({"type": "mute", "muted": False})
        await rig.say()
        assert rig.host.turns == ["Hello there"]
        await rig.session.close()
    run(scenario())


def test_push_to_talk_listens_only_while_held():
    async def scenario():
        rig = Rig("Hold to talk works", settings=SessionSettings(push_to_talk=True))
        await rig.session.start()
        await rig.say()
        assert rig.host.turns == []
        await rig.session.control({"type": "hold", "held": True})
        await rig.feed(600, voiced=True)
        await rig.feed(2000, voiced=False)  # Pauses never end a held turn.
        assert rig.host.turns == []
        await rig.session.control({"type": "hold", "held": False})
        await rig.session.idle()
        assert rig.host.turns == ["Hold to talk works"]
        await rig.session.close()
    run(scenario())


def test_a_quiet_session_pauses_clearly_and_resumes_without_hidden_listening():
    async def scenario():
        rig = Rig("Back again", settings=SessionSettings(idle_pause_ms=2000, idle_end_seconds=60))
        await rig.session.start()
        await rig.feed(2100, voiced=False)
        assert rig.link.states()[-1] == "paused"
        await rig.say()
        assert rig.host.turns == []
        assert await rig.session.tick()
        rig.clock.advance(61_000)
        assert not await rig.session.tick()
        await rig.session.control({"type": "resume"})
        assert rig.link.states()[-1] == "listening"
        await rig.say()
        assert rig.host.turns == ["Back again"]
        await rig.session.close()
    run(scenario())


def test_its_own_voice_heard_back_and_phantom_transcripts_are_not_turns():
    async def scenario():
        rig = Rig("It is noon.", "")
        await rig.session.start()
        rig.host.spoke("It is noon.")
        await rig.say()
        await rig.say()
        assert rig.host.turns == []
        assert rig.link.states()[-1] == "listening"
        await rig.session.close()
    run(scenario())


def test_an_approval_waiting_shows_waiting_for_you():
    async def scenario():
        rig = Rig("Delete the old files")
        await rig.session.start()
        await rig.say()
        rig.session.agent_event("tool_call", {}, "pass-1")
        rig.session.agent_event("interrupt", None, "pass-1")
        rig.session.agent_event("finished", "waiting_approval", "pass-1")
        await rig.session.idle()
        assert rig.link.states()[-1] == "waiting"
        await rig.session.close()
    run(scenario())


def test_oversized_or_odd_audio_messages_are_refused():
    async def scenario():
        rig = Rig()
        await rig.session.start()
        with pytest.raises(ValueError):
            await rig.session.audio(b"\0" * (17 * 1024))
        with pytest.raises(ValueError):
            await rig.session.audio(b"\0\0\0")
        await rig.session.close()
    run(scenario())
