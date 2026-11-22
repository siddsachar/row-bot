"""Row-Bot Voice while it works (V3): spoken progress, later results, status, and approvals by voice.

Fake speech, agent and socket; turn taking runs on audio time and timers on a fake clock.
"""
from __future__ import annotations

import asyncio

import pytest

from row_bot.voice.engine.session import TurnOutcome
from row_bot.voice.engine.spoken import NEEDS_A_TAP, STILL_WORKING, progress_line, spoken_answer, status_line
from tests.subsystem.voice.test_duplex_engine import Rig, run

pytestmark = pytest.mark.subsystem


def _tool(rig: Rig, tool: str, call: str, pass_id: str = "pass-1") -> None:
    rig.session.agent_event("tool_call", {"tool": tool, "id": call}, pass_id)


def _done(rig: Rig, call: str, pass_id: str = "pass-1") -> None:
    rig.session.agent_event("tool_done", {"tool": "", "id": call}, pass_id)


async def _heard(rig: Rig) -> None:
    """The client finished playing everything it was sent."""
    await rig.session.idle()
    await rig.session.control({"type": "playback", "state": "idle"})
    await rig.session.idle()


async def _wait(rig: Rig, ms: float) -> None:
    """Time passes: timers that are due run."""
    rig.sleep.advance(ms)
    for _ in range(3):
        await asyncio.sleep(0)
    await rig.session.idle(speech=False)


def _captions(rig: Rig, kind: str) -> list[str]:
    return [c["text"] for c in rig.link.of("caption") if c["role"] == "assistant" and c.get("kind") == kind]


def test_a_tool_start_says_one_templated_line_per_burst_and_keeps_its_distance():
    async def scenario():
        rig = Rig("What's on today?")
        await rig.session.start()
        await rig.say()
        _tool(rig, "search_events", "call-1")
        _tool(rig, "web_search", "call-2")  # Same burst: no second line.
        await rig.session.idle()
        assert rig.tts.spoken == ["Checking your calendar…"]
        assert _captions(rig, "progress") == ["Checking your calendar…"]
        # It is heard only: the agent was asked nothing more.
        assert rig.host.turns == ["What's on today?"]
        await _heard(rig)
        _done(rig, "call-1")
        _done(rig, "call-2")
        rig.answer("You have two meetings.")
        await _heard(rig)
        # A new burst right after speech keeps its distance ...
        rig.clock.advance(1000)
        _tool(rig, "read_url", "call-3", "pass-2")
        await rig.session.idle()
        assert rig.tts.spoken[-1] == "You have two meetings."
        _done(rig, "call-3", "pass-2")
        rig.answer("The first is at ten.", pass_id="pass-2")
        await _heard(rig)
        # ... and once a few quiet seconds have passed, the next tool start speaks again.
        rig.clock.advance(4500)
        _tool(rig, "read_url", "call-4", "pass-3")
        await rig.session.idle()
        assert rig.tts.spoken[-1] == "Looking at the page…"
        assert rig.tts.spoken.count("Checking your calendar…") == 1
        await rig.session.close()
    run(scenario())


def test_progress_is_never_said_over_the_person_or_during_the_answer():
    async def scenario():
        rig = Rig("Find me a flight", "")
        await rig.session.start()
        await rig.say()
        # During the answer: it is already talking, so no line.
        rig.answer("Sure, let me look that up for you. ", done=False)
        await rig.session.idle()
        _tool(rig, "web_search", "call-1")
        await rig.session.idle()
        assert rig.tts.spoken == ["Sure, let me look that up for you."]
        await _heard(rig)
        _done(rig, "call-1")
        rig.session.agent_event("token", "Found some. ", "pass-1")
        await _heard(rig)
        rig.clock.advance(5000)
        # Over the person: they are talking, so no line (nor a still-working one while they talk).
        await rig.feed(300, voiced=True)
        _tool(rig, "web_search", "call-2")
        await rig.session.idle(speech=False)
        await _wait(rig, 2500)
        await rig.session.idle(speech=False)
        assert "Searching the web…" not in rig.tts.spoken and STILL_WORKING not in rig.tts.spoken
        await rig.session.close()
    run(scenario())


def test_a_tool_running_over_two_seconds_with_nothing_said_gets_one_still_working_line():
    async def scenario():
        rig = Rig("Work out my taxes")
        await rig.session.start()
        await rig.say()
        rig.answer("Okay.")
        await _heard(rig)
        rig.clock.advance(3000)
        # Spoken three seconds ago: too soon for a progress line at the start ...
        _tool(rig, "web_search", "call-1", "pass-2")
        await rig.session.idle()
        assert rig.tts.spoken == ["Okay."]
        await _wait(rig, 1000)
        await rig.session.idle()
        assert rig.tts.spoken == ["Okay."]
        # ... but two seconds in, with the tool still running and nothing said, one line.
        await _wait(rig, 1100)
        await rig.session.idle()
        assert rig.tts.spoken == ["Okay.", STILL_WORKING]
        await _wait(rig, 10_000)
        await rig.session.idle()
        assert rig.tts.spoken.count(STILL_WORKING) == 1
        await rig.session.close()

    async def finished_in_time():
        rig = Rig("Quick sum")
        await rig.session.start()
        await rig.say()
        _tool(rig, "calculate", "call-1")
        _done(rig, "call-1")
        rig.answer("It is four.")
        await _wait(rig, 2500)
        await rig.session.idle()
        assert rig.tts.spoken == ["It is four."]
        await rig.session.close()
    run(scenario())
    run(finished_in_time())


def test_the_progress_line_is_quick_and_never_counted_as_the_answer():
    async def scenario():
        rig = Rig("Weather?", synth_ms=100)
        await rig.session.start()
        await rig.say()
        started = rig.clock.now
        _tool(rig, "get_current_weather", "call-1")
        await rig.session.idle()
        # Tool start to the line's audio is its synthesis only.
        assert rig.tts.spoken == ["Checking the weather…"]
        assert round((rig.clock.now - started) * 1000) == 100
        line = rig.link.of("caption")[-1]["segment"]
        await rig.session.control({"type": "playback", "state": "playing", "segment": line, "delay_ms": 20})
        assert not rig.link.of("latency")
        await rig.session.control({"type": "playback", "state": "idle"})
        _done(rig, "call-1")
        rig.answer("Sunny today. ")
        await rig.session.idle()
        answer = rig.link.of("caption")[-1]["segment"]
        await rig.session.control({"type": "playback", "state": "playing", "segment": answer, "delay_ms": 20})
        # The turn's latency is its answer's.
        assert len(rig.link.of("latency")) == 1
        assert rig.tts.spoken == ["Checking the weather…", "Sunny today."]
        await rig.session.close()
    run(scenario())


def test_an_answer_that_comes_before_the_line_is_spoken_goes_first():
    async def scenario():
        rig = Rig("Weather?")
        await rig.session.start()
        await rig.say()
        _tool(rig, "get_current_weather", "call-1")
        _done(rig, "call-1")
        rig.answer("Sunny today.")
        await rig.session.idle()
        assert rig.tts.spoken == ["Sunny today."]
        await rig.session.close()
    run(scenario())


def test_a_status_question_during_a_run_is_answered_from_its_progress_and_the_run_goes_on():
    async def scenario():
        rig = Rig("Research flights to Oslo", "How's it going?")
        await rig.session.start()
        await rig.say()
        _tool(rig, "web_search", "call-1")
        await _heard(rig)
        rig.host.outcome = TurnOutcome("control", speakable="I'm still working on it.", control="status")
        await rig.say()
        assert rig.host.turns[-1] == "How's it going?"
        assert rig.tts.spoken[-1] == "I'm still searching the web."
        assert not rig.link.of("flush")
        _done(rig, "call-1")
        rig.answer("The cheapest is on Friday. ")
        await rig.session.idle()
        assert rig.tts.spoken[-1] == "The cheapest is on Friday."
        await rig.session.close()
    run(scenario())


def test_a_later_answer_is_said_at_the_next_pause_never_over_the_person():
    async def scenario():
        rig = Rig("Ask two helpers", "")
        await rig.session.start()
        await rig.say()
        rig.answer("I've asked two helpers. ")
        await rig.session.idle()
        # It arrives while Row-Bot is still talking: it waits.
        rig.session.agent_event("later_answer", "Both helpers are done. **Oslo** is cheaper.", "")
        await rig.session.idle()
        assert rig.tts.spoken == ["I've asked two helpers."]
        await _heard(rig)
        assert rig.tts.spoken == ["I've asked two helpers.", "Both helpers are done.", "Oslo is cheaper."]
        assert _captions(rig, "later") == ["Both helpers are done.", "Oslo is cheaper."]
        await _heard(rig)
        # While the person talks, it waits for them to finish.
        await rig.feed(300, voiced=True)
        rig.session.agent_event("later_answer", "One more thing came back.", "")
        await rig.session.idle(speech=False)
        assert rig.tts.spoken[-1] == "Oslo is cheaper."
        await rig.feed(600, voiced=False)
        await rig.session.idle()
        assert rig.tts.spoken[-1] == "One more thing came back."
        await rig.session.close()
    run(scenario())


def test_a_later_answer_is_not_said_once_voice_has_ended_or_paused():
    async def scenario():
        rig = Rig()
        await rig.session.start()
        await rig.session.close()
        rig.session.agent_event("later_answer", "Too late to say.", "")
        assert rig.tts.spoken == []

    async def paused():
        from row_bot.voice.engine.session import SessionSettings
        rig = Rig(settings=SessionSettings(idle_pause_ms=1000))
        await rig.session.start()
        await rig.feed(1100, voiced=False)
        rig.session.agent_event("later_answer", "Nobody is listening.", "")
        await rig.session.idle()
        assert rig.tts.spoken == []
        await rig.session.close()
    run(scenario())
    run(paused())


# ── Approvals by voice ──────────────────────────────────────────────────────

REVERSIBLE = {"approval_id": "approval-1", "action": "Move / rename file?", "voice": True,
              "summary": "I need your OK. Move / rename file? Say yes, or tap Approve."}
DESTRUCTIVE = {"approval_id": "approval-2", "action": "Send email?", "voice": False,
               "summary": "I need your OK. Send email? Tap Approve on the screen."}


async def _asks(rig: Rig, notice: dict) -> None:
    await rig.session.start()
    await rig.say()
    _tool(rig, "workspace_move_file", "call-1")
    await _heard(rig)
    rig.session.agent_event("interrupt", notice, "pass-1")
    rig.session.agent_event("finished", "waiting_approval", "pass-1")
    await rig.session.idle()


def test_a_reversible_action_is_approved_by_a_plain_yes_after_its_summary():
    async def scenario():
        rig = Rig("Tidy my files", "Yes, go ahead.")
        await _asks(rig, REVERSIBLE)
        assert rig.tts.spoken[-1] == REVERSIBLE["summary"]
        assert rig.link.of("approval")[-1] == {"type": "approval", "approval_id": "approval-1",
                                               "action": "Move / rename file?", "voice": True}
        await _heard(rig)
        await rig.say()
        assert rig.host.approvals == [("approval-1", True)]
        # Never a turn of its own: the run goes on from the card.
        assert rig.host.turns == ["Tidy my files"]
        assert rig.link.of("approval")[-1] == {"type": "approval", "approval_id": ""}
        await rig.session.close()
    run(scenario())


def test_a_spoken_no_rejects_it_the_same_way():
    async def scenario():
        rig = Rig("Tidy my files", "No.")
        await _asks(rig, REVERSIBLE)
        await _heard(rig)
        await rig.say()
        assert rig.host.approvals == [("approval-1", False)]
        await rig.session.close()
    run(scenario())


def test_a_destructive_action_needs_a_tap_whatever_is_said():
    async def scenario(words: str):
        rig = Rig("Email Sam", words)
        await _asks(rig, DESTRUCTIVE)
        assert rig.tts.spoken[-1] == DESTRUCTIVE["summary"]
        await _heard(rig)
        await rig.say()
        assert rig.host.approvals == []
        assert rig.tts.spoken[-1] == NEEDS_A_TAP
        assert rig.host.turns == ["Email Sam"]
        await rig.session.close()
    run(scenario("Yes, approve."))
    run(scenario("No."))


def test_the_host_can_still_refuse_a_spoken_yes():
    async def scenario():
        rig = Rig("Tidy my files", "Yes")
        rig.host.approval_result = "tap"  # Two approvals wait now: which one "yes" means is not certain.
        await _asks(rig, REVERSIBLE)
        await _heard(rig)
        await rig.say()
        assert rig.host.approvals == [("approval-1", True)]
        assert rig.tts.spoken[-1] == NEEDS_A_TAP
        await rig.session.close()
    run(scenario())


def test_a_yes_during_the_summary_or_over_its_voice_is_ignored():
    async def scenario():
        rig = Rig("Tidy my files", "Yes", "Yes", "Yes")
        await _asks(rig, REVERSIBLE)
        assert rig.session.orb() == "speaking"
        # Begun while the summary still plays (Row-Bot's own "yes" heard back, or talking over it), and
        # finished after it: not an answer.
        await rig.feed(160, voiced=True)
        await rig.session.control({"type": "playback", "state": "idle"})
        await rig.feed(500, voiced=True)
        await rig.feed(500, voiced=False)
        await rig.session.idle()
        assert [text for text, _ in rig.stt.finished] == ["Tidy my files", "Yes"]
        assert rig.host.approvals == []
        # Begun while a later result plays: not an answer either.
        rig.session.agent_event("later_answer", "One helper finished the draft.", "")
        await rig.session.idle()
        assert rig.session.orb() == "speaking"
        await rig.feed(160, voiced=True)
        await rig.session.control({"type": "playback", "state": "idle"})
        await rig.feed(500, voiced=True)
        await rig.feed(500, voiced=False)
        await rig.session.idle()
        assert len(rig.stt.finished) == 3 and rig.host.approvals == []
        # Said in the quiet afterwards, the same word answers it.
        await rig.say()
        assert rig.host.approvals == [("approval-1", True)]
        await rig.session.close()
    run(scenario())


@pytest.mark.parametrize("words", ["Yes no", "Yes, but…", "Okay, cancel it", "Yes wait"])
def test_an_unclear_answer_is_ignored_and_the_card_still_waits(words):
    async def scenario():
        rig = Rig("Tidy my files", words)
        await _asks(rig, REVERSIBLE)
        await _heard(rig)
        await rig.say()
        assert rig.host.approvals == [] and rig.host.turns == ["Tidy my files"]
        assert rig.link.of("approval")[-1]["approval_id"] == "approval-1"
        await rig.session.close()
    run(scenario())


def test_silence_never_answers_an_approval():
    async def scenario():
        rig = Rig("Tidy my files")
        await _asks(rig, REVERSIBLE)
        await _heard(rig)
        await _wait(rig, 120_000)
        await rig.feed(3000, voiced=False)
        await rig.session.idle()
        assert rig.host.approvals == [] and rig.session.orb() == "waiting"
        await rig.session.close()
    run(scenario())


def test_the_approval_clears_once_the_run_goes_on_from_a_tap():
    async def scenario():
        rig = Rig("Tidy my files", "Yes")
        await _asks(rig, REVERSIBLE)
        await _heard(rig)
        rig.session.agent_event("token", "Moved it. ", "pass-2")  # The card was tapped; the run went on.
        await _heard(rig)
        assert rig.link.of("approval")[-1] == {"type": "approval", "approval_id": ""}
        rig.session.agent_event("done", None, "pass-2")
        await rig.say()
        assert rig.host.approvals == [] and rig.host.turns[-1] == "Yes"
        await rig.session.close()
    run(scenario())


# ── The words ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("words, answer", [
    ("Yes.", "yes"), ("Yeah, go ahead", "yes"), ("Okay, do it please", "yes"), ("Approve", "yes"),
    ("No", "no"), ("Cancel that.", "no"), ("Don't do it", "no"), ("Nope", "no"),
    ("Yes no", "unclear"), ("Yes, but", "unclear"), ("Okay wait", "unclear"),
    ("Yes, and then book the hotel in Oslo for Friday", None), ("What time is it?", None), ("", None),
])
def test_a_spoken_answer_is_a_short_plain_yes_or_no(words, answer):
    assert spoken_answer(words) == answer


def test_progress_and_status_lines_come_from_the_tool_name():
    assert progress_line("search_events") == "Checking your calendar…"
    assert progress_line("outlook_search_mail") == "Checking your email…"
    assert progress_line("workspace_read_file") == "Reading that file…"
    assert progress_line("mcp_notion_create_page") == "Working on it…"
    assert progress_line("get_current_datetime") is None
    assert status_line("web_search") == "I'm still searching the web."
    assert status_line(None) == "I'm still working on it."
