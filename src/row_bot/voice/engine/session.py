"""Row-Bot Voice: a full-duplex spoken conversation around the normal agent.

One ``DuplexSession`` per voice session. Audio from the person arrives as
16 kHz PCM frames; Silero VAD and Smart Turn decide when a turn ends; the
selected speech-to-text engine writes it down; the transcript goes through
the conversation's normal admission (same tools, memory and approvals); the
answer's tokens are spoken sentence by sentence as they stream; and talking
over Row-Bot ducks its voice at once and stops it on a confirmed barge-in.

Turn taking runs on audio time and latency marks on an injected clock, so the
whole engine is deterministic under test. Model calls (speech-to-text,
text-to-speech, admission) run off the event loop.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import time
from typing import Any, Literal, Protocol
from uuid import uuid4

from row_bot.voice.engine.latency import LatencyLog, TurnMarks, recent_turns
from row_bot.voice.engine.sentences import SentenceAggregator
from row_bot.voice.engine.speech import MAX_UTTERANCE_SECONDS
from row_bot.voice.engine.turns import EndOfTurn, SpeechTracker, TurnSettings, VoiceActivity
from row_bot.voice.streaming_base import (
    INPUT_SAMPLE_RATE,
    SentenceSpeech,
    SpeechToTextStream,
    StreamingSpeechToText,
)

_LOG = logging.getLogger(__name__)

#: Largest audio message accepted from the client (about 0.5 s of 16 kHz PCM).
MAX_AUDIO_MESSAGE_BYTES = 16 * 1024
#: Smart Turn looks at the last eight seconds of a turn.
_TURN_AUDIO_BYTES = 8 * INPUT_SAMPLE_RATE * 2
#: Assistant audio goes down in pieces of about a quarter second.
_AUDIO_CHUNK_SECONDS = 0.25
#: Words that keep someone listening rather than taking the turn.
BACKCHANNELS = frozenset({
    "mhm", "mm", "mmm", "hmm", "hm", "uh", "um", "uh huh", "uh-huh", "mm-hmm", "mhmm", "yeah", "yep", "yes",
    "ok", "okay", "right", "sure", "i see", "oh", "ah", "aha", "cool", "nice", "got it",
})
_WORDS = re.compile(r"[a-z0-9']+(?:-[a-z0-9']+)?")

OrbState = Literal["connecting", "listening", "thinking", "speaking", "waiting", "muted", "paused"]


class VoiceLink(Protocol):
    """The client connection: JSON events and the assistant's audio."""

    async def send_event(self, event: dict[str, Any]) -> None:
        ...

    async def send_audio(self, segment: int, pcm16: bytes) -> None:
        ...


@dataclass(frozen=True)
class TurnOutcome:
    kind: Literal["submitted", "control", "rejected"]
    # Fixed words to say back (a control's answer), never model text.
    speakable: str = ""
    code: str = ""


class VoiceHost(Protocol):
    """The conversation side: admission, the echo filter and the lease."""

    def take_turn(self, text: str, utterance_id: str) -> TurnOutcome:
        """Admit the person's words: a new turn, or input to the running one. Blocking."""
        ...

    def drops(self, text: str) -> bool:
        """True for phantom transcripts and Row-Bot's own words heard back."""
        ...

    def spoke(self, text: str) -> None:
        ...

    def keepalive(self) -> None:
        ...


@dataclass(frozen=True)
class EngineParts:
    stt: StreamingSpeechToText
    tts: SentenceSpeech
    activity: VoiceActivity
    end_of_turn: EndOfTurn


@dataclass(frozen=True)
class SessionSettings:
    turns: TurnSettings = TurnSettings()
    # Sustained speech over Row-Bot's voice that can stop it (with words, when heard).
    barge_in_ms: int = 200
    # Speech this long stops Row-Bot even if no words were made out.
    barge_in_long_ms: int = 500
    # Voiced audio this long ducks Row-Bot's voice at once.
    duck_ms: int = 60
    # Silence with nothing happening that pauses the session.
    idle_pause_ms: int = 5 * 60 * 1000
    # A pause this long ends the session.
    idle_end_seconds: float = 30 * 60.0
    push_to_talk: bool = False
    # The saved captions setting: the voice bar's starting choice.
    captions: bool = True


def words_of(text: str) -> list[str]:
    return _WORDS.findall(str(text or "").lower())


def is_backchannel(text: str) -> bool:
    words = words_of(text)
    if not words:
        return True
    joined = " ".join(words)
    return joined in BACKCHANNELS or all(word in BACKCHANNELS for word in words)


class _BargeIn:
    def __init__(self) -> None:
        self.confirmed = False
        self.checking = False
        self.heard_words = False
        self.checked = False


class DuplexSession:
    def __init__(self, parts: EngineParts, link: VoiceLink, host: VoiceHost, *,
                 settings: SessionSettings | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 log: LatencyLog | None = None) -> None:
        self.parts = parts
        self.link = link
        self.host = host
        self.settings = settings or SessionSettings()
        self.clock = clock
        self.log = log or LatencyLog()
        self.tracker = SpeechTracker(parts.activity, self.settings.turns)
        self.closed = False
        self.muted = False
        self.paused = False
        self.held = False  # push to talk: the person is holding the talk control
        self._paused_at = 0.0
        self._state: OrbState = "connecting"
        frames = (self.settings.turns.preroll_ms + self.settings.turns.start_ms + 64) * INPUT_SAMPLE_RATE * 2 // 1000
        self._preroll = bytearray()
        self._preroll_bytes = frames
        self._utterance: SpeechToTextStream | None = None
        self._utterance_id = ""
        self._turn_audio = bytearray()
        self._last_voice_at = 0.0
        self._voice_run_ms = 0
        self._pause_generation = 0
        self._barge: _BargeIn | None = None
        self._ducked = False
        self._idle_from_ms = 0
        self._thinking = False
        self._waiting = False
        self._run_active = False
        self._audible = False
        self._current_pass = ""
        self._current_segment = ""
        # A barge-in silences the answer being spoken: its pass, from the segment it was in. Once the
        # person's next words are admitted (a steer, say), that pass's later segments are its new answer.
        self._silenced_pass = ""
        self._silenced_segment = ""
        self._heard_since_silence = False
        self._aggregator = SentenceAggregator()
        self._speech_epoch = 0
        self._segment = 0
        self._speech: asyncio.Queue[tuple[int, int, str]] = asyncio.Queue()
        self._marks: TurnMarks | None = None
        self._marks_segment = 0
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaker: asyncio.Task | None = None
        self.out_sample_rate = 24_000

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.link.send_event({"type": "state", "state": "connecting"})
        await asyncio.gather(asyncio.to_thread(self.parts.stt.warm), asyncio.to_thread(self.parts.tts.warm))
        self._speaker = asyncio.create_task(self._speak_loop(), name="row-bot-voice-speaker")
        self._idle_from_ms = self.tracker.now_ms
        await self.link.send_event({"type": "ready", "input_sample_rate": INPUT_SAMPLE_RATE,
                                    "output_sample_rate": self.out_sample_rate,
                                    "push_to_talk": self.settings.push_to_talk,
                                    "captions": self.settings.captions})
        await self._publish_state()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._speech_epoch += 1
        if self._utterance is not None:
            self._utterance.close()
            self._utterance = None
        if self._speaker is not None:
            self._speaker.cancel()
        for task in list(self._tasks):
            task.cancel()
        pending = [task for task in (self._speaker, *self._tasks) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._record_marks()

    async def idle(self, *, speech: bool = True) -> None:
        """Wait until every piece of work this session started has finished
        (with ``speech`` False, everything but speaking queued sentences)."""
        while True:
            tasks = [task for task in self._tasks if not task.done()]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                continue
            if speech:
                await self._speech.join()
            await asyncio.sleep(0)
            if not any(not task.done() for task in self._tasks) and (not speech or self._speech.empty()):
                return

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.warning("Row-Bot Voice step failed: %s", type(task.exception()).__name__)

    # ── state ───────────────────────────────────────────────────────────

    def orb(self) -> OrbState:
        if self.paused:
            return "paused"
        if self._audible:
            return "speaking"
        if self._waiting:
            return "waiting"
        if self._thinking or self._run_active or self._speech.qsize():
            return "thinking"
        if self.muted:
            return "muted"
        return "listening"

    async def _publish_state(self) -> None:
        state = self.orb()
        if state != self._state and not self.closed:
            self._state = state
            await self.link.send_event({"type": "state", "state": state, "muted": self.muted})

    # ── the person's audio ──────────────────────────────────────────────

    async def audio(self, pcm16: bytes) -> None:
        if self.closed or self.paused or self.muted or not pcm16:
            return
        if len(pcm16) > MAX_AUDIO_MESSAGE_BYTES or len(pcm16) % 2:
            raise ValueError("voice_frame_invalid")
        if self.settings.push_to_talk and not self.held and self._utterance is None:
            self.tracker.samples += len(pcm16) // 2
            return
        self._preroll.extend(pcm16)
        if len(self._preroll) > self._preroll_bytes:
            del self._preroll[:len(self._preroll) - self._preroll_bytes]
        heard = self.tracker.feed(pcm16)
        if self._utterance is not None:
            self._push(pcm16)
        for event in heard:
            if event.kind == "voice":
                self._last_voice_at = self.clock()
                self._voice_run_ms += round(self.tracker.window_ms)
                if self._audible and not self._ducked and self._voice_run_ms >= self.settings.duck_ms:
                    self._ducked = True
                    await self.link.send_event({"type": "duck", "on": True})
            elif event.kind == "speech_started":
                self._open_utterance()
                if self._audible:
                    self._barge = _BargeIn()
            elif event.kind == "paused":
                await self._on_pause()
            elif event.kind == "resumed":
                self._pause_generation += 1
            elif event.kind == "silence_limit" and self._utterance is not None and not self.settings.push_to_talk:
                if self._barge is None or self._barge.confirmed:
                    await self._end_turn()
        if self.tracker.state == "quiet":
            self._voice_run_ms = 0
            if self._ducked and self._barge is None and self._utterance is None:
                self._ducked = False
                await self.link.send_event({"type": "duck", "on": False})
        await self._check_barge_in()
        if self._utterance is not None and self.tracker.now_ms - self.tracker.speech_started_ms >= MAX_UTTERANCE_SECONDS * 1000:
            await self._end_turn()
        busy = self._utterance is not None or self._audible or self._thinking or self._run_active
        if busy:
            self._idle_from_ms = self.tracker.now_ms
        elif self.tracker.now_ms - self._idle_from_ms >= self.settings.idle_pause_ms:
            await self._pause()

    def _push(self, pcm16: bytes) -> None:
        assert self._utterance is not None
        self._utterance.push(pcm16)
        self._turn_audio.extend(pcm16)
        if len(self._turn_audio) > _TURN_AUDIO_BYTES:
            del self._turn_audio[:len(self._turn_audio) - _TURN_AUDIO_BYTES]

    def _open_utterance(self) -> None:
        if self._utterance is not None:
            return
        self._record_marks()
        self._utterance = self.parts.stt.open()
        self._utterance_id = str(uuid4())
        self._turn_audio = bytearray()
        self._push(bytes(self._preroll))
        self._idle_from_ms = self.tracker.now_ms

    def _drop_utterance(self) -> None:
        if self._utterance is not None:
            self._utterance.close()
        self._utterance = None
        self._turn_audio = bytearray()
        self._pause_generation += 1
        self.tracker.reset()

    async def _on_pause(self) -> None:
        self._pause_generation += 1
        if self._utterance is None:
            return
        barge = self._barge
        if barge is not None and not barge.confirmed:
            # Speech over Row-Bot ended before it counted: words decide it now.
            if not barge.checking and not barge.checked:
                self._spawn(self._check_words(barge))
            return
        if self.settings.push_to_talk:
            return
        self._spawn(self._judge_turn(self._pause_generation, bytes(self._turn_audio)))

    async def _judge_turn(self, generation: int, audio: bytes) -> None:
        probability = await self.parts.end_of_turn.complete_probability(audio)
        if generation != self._pause_generation or self.tracker.state != "paused" or self._utterance is None:
            return
        if probability >= self.settings.turns.complete_at:
            await self._end_turn()

    # ── barge-in ────────────────────────────────────────────────────────

    async def _check_barge_in(self) -> None:
        barge = self._barge
        if barge is None or barge.confirmed or self._utterance is None:
            return
        if not self._audible:
            # Row-Bot stopped speaking on its own: this is simply the next turn.
            self._barge = None
            return
        speech = self.tracker.speech_ms if self.tracker.state == "speaking" else 0
        if speech >= self.settings.barge_in_long_ms:
            await self._confirm_barge_in()
        elif speech >= self.settings.barge_in_ms:
            if barge.heard_words:
                await self._confirm_barge_in()
            elif not barge.checking and not barge.checked:
                self._spawn(self._check_words(barge))

    async def _check_words(self, barge: _BargeIn) -> None:
        stream = self._utterance
        if stream is None:
            return
        barge.checking = True
        try:
            heard = await asyncio.to_thread(stream.partial)
        finally:
            barge.checking = False
            barge.checked = True
        if self._barge is not barge or self._utterance is not stream or barge.confirmed:
            return
        text = heard.text if heard is not None else ""
        barge.heard_words = bool(text) and not is_backchannel(text) and not self.host.drops(text)
        if barge.heard_words:
            await self._confirm_barge_in()
            if self.tracker.state == "paused" and not self.settings.push_to_talk:
                self._spawn(self._judge_turn(self._pause_generation, bytes(self._turn_audio)))
        elif self.tracker.state != "speaking":
            # "Mhm", a cough or Row-Bot's own voice: keep talking at full volume.
            self._barge = None
            self._drop_utterance()
            if self._ducked:
                self._ducked = False
                await self.link.send_event({"type": "duck", "on": False})
        else:
            barge.checked = False  # Still talking: look again as more words arrive.

    async def _confirm_barge_in(self) -> None:
        if self._barge is not None:
            self._barge.confirmed = True
        await self._stop_speaking(reason="barge_in")

    async def _stop_speaking(self, *, reason: str) -> None:
        self._speech_epoch += 1
        while not self._speech.empty():
            self._speech.get_nowait()
            self._speech.task_done()
        self._aggregator.reset()
        if self._current_pass:
            # The rest of this answer stays on screen; it is no longer spoken.
            self._silenced_pass = self._current_pass
            self._silenced_segment = self._current_segment
            self._heard_since_silence = False
        self._audible = False
        self._ducked = False
        await self.link.send_event({"type": "flush", "reason": reason})
        await self._publish_state()

    # ── a finished turn ─────────────────────────────────────────────────

    async def _end_turn(self) -> None:
        stream = self._utterance
        if stream is None:
            return
        utterance_id = self._utterance_id
        self._utterance = None
        self._barge = None
        self._turn_audio = bytearray()
        self._pause_generation += 1
        self.tracker.reset()
        marks = TurnMarks()
        marks.mark("speech_end", self._last_voice_at or self.clock())
        marks.mark("turn_end", self.clock())
        self._thinking = True
        await self._publish_state()
        await self.link.send_event({"type": "caption", "role": "user", "text": "", "final": False})
        self._spawn(self._take_turn(stream, utterance_id, marks))

    async def _take_turn(self, stream: SpeechToTextStream, utterance_id: str, marks: TurnMarks) -> None:
        try:
            heard = await asyncio.to_thread(stream.finish)
        finally:
            stream.close()
        marks.mark("transcript_final", self.clock())
        text = " ".join(heard.text.split())
        if not text or self.host.drops(text) or self.closed:
            self._thinking = False
            await self.link.send_event({"type": "caption", "role": "user", "text": "", "final": True})
            await self._publish_state()
            return
        await self.link.send_event({"type": "caption", "role": "user", "text": text, "final": True})
        self._marks = marks
        self._marks_segment = 0
        outcome = await asyncio.to_thread(self.host.take_turn, text, utterance_id)
        marks.mark("agent_admitted", self.clock())
        if outcome.kind != "rejected":
            self._heard_since_silence = True
        if outcome.kind == "rejected":
            self._thinking = False
            self._marks = None
            await self.link.send_event({"type": "error", "code": outcome.code or "voice_admission_failed"})
        elif outcome.kind == "control":
            self._thinking = False
            if outcome.speakable:
                self._enqueue(outcome.speakable)
        await self._publish_state()

    # ── the agent's answer ──────────────────────────────────────────────

    def agent_event(self, kind: str, payload: Any, pass_id: str, segment_id: str = "") -> None:
        """Called from the run's thread for every event of this conversation."""
        loop = self._loop
        if loop is None or self.closed:
            return
        try:
            loop.call_soon_threadsafe(self._spawn, self._on_agent_event(kind, payload, pass_id, segment_id))
        except RuntimeError:
            pass  # The loop closed with the session.

    def _silenced(self, pass_id: str, segment_id: str) -> bool:
        if self.paused:
            return True  # A paused session speaks nothing; typed turns are text only.
        if not pass_id or pass_id != self._silenced_pass:
            return False
        return segment_id == self._silenced_segment or not self._heard_since_silence

    async def _on_agent_event(self, kind: str, payload: Any, pass_id: str, segment_id: str = "") -> None:
        terminal = kind in {"done", "interrupt", "error", "finished"}
        silenced = self._silenced(pass_id, segment_id)
        if self.closed or (silenced and not terminal):
            return
        if not silenced:
            if pass_id and pass_id != self._current_pass:
                self._current_pass = pass_id
                self._aggregator.reset()
            elif kind == "token" and segment_id != self._current_segment:
                for piece in self._aggregator.flush():
                    self._enqueue(piece)
            if kind == "token":
                self._current_segment = segment_id
        if silenced:
            self._run_active = self._thinking = False
            self._waiting = kind == "interrupt" or (kind == "finished" and payload == "waiting_approval")
        elif kind == "token":
            self._run_active = True
            self._waiting = False
            if self._marks is not None:
                self._marks.mark("first_token", self.clock())
            for piece in self._aggregator.push(str(payload or "")):
                self._enqueue(piece)
        elif terminal:
            for piece in self._aggregator.flush():
                self._enqueue(piece)
            self._run_active = False
            self._thinking = False
            self._waiting = kind == "interrupt" or (kind == "finished" and payload == "waiting_approval")
        elif kind in {"tool_call", "tool_start", "platform_segment"}:
            self._run_active = True
            self._waiting = False
        self._idle_from_ms = self.tracker.now_ms
        await self._publish_state()

    def _enqueue(self, text: str) -> None:
        self._segment += 1
        if self._marks is not None:
            self._marks.mark("first_sentence", self.clock())
            if not self._marks_segment:
                self._marks_segment = self._segment
        self._speech.put_nowait((self._speech_epoch, self._segment, text))

    async def _speak_loop(self) -> None:
        while True:
            epoch, segment, text = await self._speech.get()
            try:
                if epoch != self._speech_epoch:
                    continue
                audio = await asyncio.to_thread(self.parts.tts.synthesize, text)
                if epoch != self._speech_epoch or self.closed or not audio.pcm16:
                    continue
                self.out_sample_rate = audio.sample_rate
                self.host.spoke(text)
                self._audible = True
                self._thinking = False
                await self.link.send_event({"type": "caption", "role": "assistant", "text": text,
                                            "segment": segment, "sample_rate": audio.sample_rate,
                                            "final": True})
                step = int(audio.sample_rate * _AUDIO_CHUNK_SECONDS) * 2
                if self._marks is not None and segment == self._marks_segment:
                    self._marks.mark("first_audio_sent", self.clock())
                for offset in range(0, len(audio.pcm16), step):
                    if epoch != self._speech_epoch:
                        break
                    await self.link.send_audio(segment, audio.pcm16[offset:offset + step])
                await self._publish_state()
            except Exception as exc:  # A sentence that cannot be spoken stays on screen.
                _LOG.warning("Row-Bot Voice could not speak a sentence: %s", type(exc).__name__)
            finally:
                self._speech.task_done()

    def _record_marks(self) -> None:
        marks, self._marks = self._marks, None
        if marks is not None and marks.has("first_audio_sent"):
            breakdown = marks.breakdown()
            self.log.record(breakdown)
            recent_turns.record(breakdown)

    # ── client messages ─────────────────────────────────────────────────

    async def control(self, message: dict[str, Any]) -> bool:
        """Handle one client message; False when the session should end."""
        kind = message.get("type")
        if kind == "end":
            return False
        if kind == "mute":
            self.muted = bool(message.get("muted"))
            if self.muted and self._utterance is not None:
                self._barge = None
                self._drop_utterance()
        elif kind == "hold":
            self.held = bool(message.get("held"))
            if not self.held and self._utterance is not None and self.settings.push_to_talk:
                await self._end_turn()
        elif kind == "stop_speaking":
            await self._stop_speaking(reason="stopped")
        elif kind == "resume":
            self.paused = False
            self._idle_from_ms = self.tracker.now_ms
            self.tracker.reset()
        elif kind == "playback":
            await self._playback(message)
        await self._publish_state()
        return True

    async def _playback(self, message: dict[str, Any]) -> None:
        state = message.get("state")
        if state == "playing":
            segment = message.get("segment")
            delay = message.get("delay_ms")
            marks = self._marks
            if (marks is not None and segment == self._marks_segment and marks.has("first_audio_sent")
                    and isinstance(delay, (int, float)) and 0 <= delay <= 60_000):
                marks.mark("first_audio_played", marks.marks["first_audio_sent"] + delay / 1000)
                breakdown = marks.breakdown()
                self._marks = None
                self.log.record(breakdown)
                recent_turns.record(breakdown)
                await self.link.send_event({"type": "latency", "last": breakdown, "summary": self.log.summary()})
        elif state == "idle":
            self._audible = False
            if self._ducked and self._barge is None:
                self._ducked = False
            self._idle_from_ms = self.tracker.now_ms
            self.host.keepalive()

    async def _pause(self) -> None:
        if self.paused:
            return
        self.paused = True
        self._paused_at = self.clock()
        self._drop_utterance()
        await self._publish_state()

    async def tick(self) -> bool:
        """Periodic housekeeping; False when a long pause should end the session."""
        self.host.keepalive()
        return not (self.paused and self.clock() - self._paused_at >= self.settings.idle_end_seconds)
