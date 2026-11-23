"""Row-Bot Voice: a full-duplex spoken conversation around the normal agent.

One ``DuplexSession`` per voice session. Audio from the person arrives as
16 kHz PCM frames; Silero VAD and Smart Turn decide when a turn ends; the
selected speech-to-text engine writes it down; the transcript goes through
the conversation's normal admission (same tools, memory and approvals); the
answer's tokens are spoken sentence by sentence as they stream; and talking
over Row-Bot ducks its voice at once and stops it on a confirmed barge-in.
While the person shares their camera or screen, the end of each spoken turn
asks the browser for one fresh still, which goes with that turn only.

Turn taking runs on audio time and latency marks on an injected clock, so the
whole engine is deterministic under test. Model calls (speech-to-text,
text-to-speech, admission) run off the event loop.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
import logging
import re
import time
from typing import Any, Literal, Protocol
from uuid import uuid4

from row_bot.voice.engine.latency import LatencyLog, TurnMarks, recent_turns
from row_bot.voice.engine.sentences import SentenceAggregator
from row_bot.voice.engine.spoken import (
    NEEDS_A_TAP,
    PROGRESS_GAP_SECONDS,
    STILL_WORKING,
    STILL_WORKING_SECONDS,
    approval_summary,
    progress_line,
    spoken_answer,
    status_line,
)
from row_bot.voice.engine.speech import MAX_UTTERANCE_SECONDS
from row_bot.voice.engine.stills import SHARE_SOURCES, ShareSource, Still, checked_still
from row_bot.voice.engine.turns import EndOfTurn, SpeechTracker, TurnSettings, VoiceActivity
from row_bot.voice.streaming_base import (
    INPUT_SAMPLE_RATE,
    SentenceSpeech,
    SpeechAudio,
    SpeechToTextStream,
    StreamingSpeechToText,
    TranscriptUpdate,
)
from row_bot.voice.provider_base import VoiceProviderError
from row_bot.voice.speech_policy import ON_SCREEN_POINTER

_LOG = logging.getLogger(__name__)

#: Largest audio message accepted from the client (about 0.5 s of 16 kHz PCM).
MAX_AUDIO_MESSAGE_BYTES = 16 * 1024
#: Smart Turn looks at the last eight seconds of a turn.
_TURN_AUDIO_BYTES = 8 * INPUT_SAMPLE_RATE * 2
#: Assistant audio goes down in pieces of about a quarter second.
_AUDIO_CHUNK_SECONDS = 0.25
#: A streaming speech model's words so far are read at most this often (audio time).
PARTIAL_EVERY_MS = 100
#: Words that keep someone listening rather than taking the turn.
BACKCHANNELS = frozenset({
    "mhm", "mm", "mmm", "hmm", "hm", "uh", "um", "uh huh", "uh-huh", "mm-hmm", "mhmm", "yeah", "yep", "yes",
    "ok", "okay", "right", "sure", "i see", "oh", "ah", "aha", "cool", "nice", "got it",
})
_WORDS = re.compile(r"[a-z0-9']+(?:-[a-z0-9']+)?")

OrbState = Literal["connecting", "listening", "thinking", "speaking", "waiting", "muted", "paused"]
# What a spoken piece is: the answer, a control's reply, a progress line, an approval's summary, or a
# result that came back later. Only answers and replies count toward a turn's latency.
SpeechKind = Literal["answer", "control", "progress", "approval", "later"]
# Fixed lines are synthesised once per session.
_FIXED_KINDS = frozenset({"progress", "control"})
# How long an approval's summary waits for the client's card title before saying its own.
TITLE_WAIT_SECONDS = 1.0


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
    # Which control it was ("status", "cancel", "follow_up"): a status is answered from the run's progress.
    control: str = ""


class VoiceHost(Protocol):
    """The conversation side: admission, the echo filter and the lease."""

    def take_turn(self, text: str, utterance_id: str, still: Still | None = None) -> TurnOutcome:
        """Admit the person's words: a new turn (with the shared still, if any), or input to the running
        one. Blocking."""
        ...

    def sees_images(self) -> bool:
        """False when the chat's model is known not to see images: nothing shared is sent. Blocking."""
        ...

    def drops(self, text: str) -> bool:
        """True for phantom transcripts and Row-Bot's own words heard back."""
        ...

    def spoke(self, text: str) -> None:
        ...

    def keepalive(self) -> None:
        ...

    def answer_approval(self, approval_id: str, approve: bool, utterance_id: str) -> str:
        """Answer the conversation's one waiting approval by voice, through the same path as a tap. Blocking.

        "answered", "tap" (only the card may answer it) or "gone" (nothing of this id waits now).
        """
        ...


@dataclass(frozen=True)
class EngineParts:
    stt: StreamingSpeechToText
    tts: SentenceSpeech
    activity: VoiceActivity
    end_of_turn: EndOfTurn
    # Chosen cloud parts running locally for now because their key is missing: {part, id, company}.
    fallbacks: tuple[dict[str, str], ...] = ()


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
    # How long a turn's end waits for a fresh still while the person shares (alongside speech-to-text).
    still_wait_seconds: float = 1.5
    # What the person speaks (Settings › Voice › Language): a language code, or None to detect it.
    language: str | None = None


def words_of(text: str) -> list[str]:
    return _WORDS.findall(str(text or "").lower())


def is_backchannel(text: str) -> bool:
    words = words_of(text)
    if not words:
        return True
    joined = " ".join(words)
    return joined in BACKCHANNELS or all(word in BACKCHANNELS for word in words)


@dataclass
class _Burst:
    """Tool starts with no answer text between them: one progress line at most."""

    started_at: float
    said: bool = False


@dataclass
class _Approval:
    approval_id: str
    voice: bool
    # The card's title in the tool's own words, said if the client never sends the title its card shows.
    fallback: str
    titled: bool = False
    # Its summary went to the client (or could not be spoken), and when it finished, in audio time: a
    # spoken answer counts only when it starts after that, never while Row-Bot is talking.
    sent: bool = False
    armed_ms: float | None = None


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
                 log: LatencyLog | None = None,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        self.parts = parts
        self.link = link
        self.host = host
        self.settings = settings or SessionSettings()
        self.clock = clock
        self._sleep = sleep
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
        self._utterance_at = 0.0
        self._utterance_over_voice = False
        self._turn_audio = bytearray()
        # The language the person was last heard speaking: the hint for the voice that answers.
        self._heard_language: str | None = self.settings.language
        # A streaming model's partial transcripts: live captions and words for barge-in.
        self._live_partials = bool(getattr(parts.stt, "live_partials", False))
        self._reading_partial = False
        self._partial_at_ms = 0
        self._partial_shown = ""
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
        self._speech: asyncio.Queue[tuple[int, int, str, SpeechKind]] = asyncio.Queue()
        self._fixed_audio: dict[str, SpeechAudio] = {}
        # When something was last said (queued, or its playback ended): progress lines keep their distance.
        self._said_at = float("-inf")
        self._said_ms = float("-inf")  # The same, in audio time: what the person's words are timed against.
        self._burst: _Burst | None = None
        self._open_tools: set[str] = set()
        self._tool = ""
        # Said at the next pause: an approval's summary, results that came back later.
        self._deferred: list[tuple[str, SpeechKind]] = []
        self._approval: _Approval | None = None
        self._marks: TurnMarks | None = None
        self._marks_segment = 0
        self._tasks: set[asyncio.Task] = set()
        self._timers: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaker: asyncio.Task | None = None
        self.out_sample_rate = 24_000
        # Sharing the camera or screen: independent of mute; a still is held only until its turn.
        self.sees_images = True
        self.sharing: ShareSource | None = None
        self._still: Still | None = None
        self._still_expected = 0
        self._still_arrived: asyncio.Event | None = None

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.link.send_event({"type": "state", "state": "connecting"})
        await asyncio.gather(asyncio.to_thread(self.parts.stt.warm), asyncio.to_thread(self.parts.tts.warm))
        self.sees_images = bool(await asyncio.to_thread(self.host.sees_images))
        self._speaker = asyncio.create_task(self._speak_loop(), name="row-bot-voice-speaker")
        self._idle_from_ms = self.tracker.now_ms
        await self.link.send_event({"type": "ready", "input_sample_rate": INPUT_SAMPLE_RATE,
                                    "output_sample_rate": self.out_sample_rate,
                                    "push_to_talk": self.settings.push_to_talk,
                                    "captions": self.settings.captions,
                                    "sees_images": self.sees_images,
                                    "fallbacks": list(self.parts.fallbacks)})
        await self._publish_state()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._speech_epoch += 1
        self.sharing, self._still = None, None
        if self._utterance is not None:
            self._utterance.close()
            self._utterance = None
        if self._speaker is not None:
            self._speaker.cancel()
        for task in [*self._tasks, *self._timers]:
            task.cancel()
        pending = [task for task in (self._speaker, *self._tasks, *self._timers) if task is not None]
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

    def _timer(self, seconds: float, callback: Callable[..., Awaitable[None]], *args: Any) -> None:
        """Run *callback* after *seconds*; never awaited by ``idle`` and cancelled on close."""
        async def later() -> None:
            await self._sleep(seconds)
            if not self.closed:
                await callback(*args)

        task = asyncio.create_task(later())
        self._timers.add(task)
        task.add_done_callback(self._timers.discard)

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
        self._deliver()
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
            self._follow_partials()
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
        self._deliver()
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
        self._utterance = self.parts.stt.open(language=self.settings.language)
        self._utterance_id = str(uuid4())
        # When the voice began, in audio time, and whether Row-Bot was talking then.
        self._utterance_at = float(self.tracker.speech_started_ms)
        self._utterance_over_voice = self._audible
        self._turn_audio = bytearray()
        self._partial_shown = ""
        self._push(bytes(self._preroll))
        self._idle_from_ms = self.tracker.now_ms

    def _follow_partials(self) -> None:
        if (not self._live_partials or self._reading_partial or self._utterance is None
                or self.tracker.now_ms - self._partial_at_ms < PARTIAL_EVERY_MS):
            return
        self._reading_partial = True
        self._partial_at_ms = self.tracker.now_ms
        self._spawn(self._read_partial(self._utterance))

    async def _read_partial(self, stream: SpeechToTextStream) -> None:
        try:
            heard = await asyncio.to_thread(stream.partial)
        finally:
            self._reading_partial = False
        if heard is None or stream is not self._utterance or self.closed:
            return
        text = " ".join(heard.text.split())
        barge = self._barge
        if barge is not None and not barge.confirmed:
            # Words over Row-Bot's voice confirm (or rule out) a barge-in; they are never a caption.
            barge.heard_words = bool(text) and not is_backchannel(text) and not self.host.drops(text)
            return
        if text and text != self._partial_shown:
            self._partial_shown = text
            await self.link.send_event({"type": "caption", "role": "user", "text": text, "final": False})

    async def _clear_partial(self) -> None:
        if self._partial_shown:
            self._partial_shown = ""
            await self.link.send_event({"type": "caption", "role": "user", "text": "", "final": True})

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
        if self._approval is not None and self._approval.armed_ms is None:
            self._approval.armed_ms = self.tracker.now_ms  # Nothing of its summary plays after this.
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
        # Over Row-Bot's voice: it was talking when this began, or said or finished something since.
        heard_at = self._utterance_at
        over_voice = self._utterance_over_voice or heard_at < self._said_ms
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
        if not self._partial_shown:  # A live caption stays until the final words replace it.
            await self.link.send_event({"type": "caption", "role": "user", "text": "", "final": False})
        self._partial_shown = ""
        looking = None
        if self.sharing is not None:
            # A fresh still of what the person shows, while their words are written down.
            looking = self._still_arrived = asyncio.Event()
            await self.link.send_event({"type": "look"})
        self._spawn(self._take_turn(stream, utterance_id, marks, heard_at, over_voice, looking))

    async def _take_turn(self, stream: SpeechToTextStream, utterance_id: str, marks: TurnMarks,
                         heard_at: float = 0.0, over_voice: bool = False,
                         looking: asyncio.Event | None = None) -> None:
        async def fresh_still() -> None:
            if looking is not None:
                try:
                    await asyncio.wait_for(looking.wait(), timeout=self.settings.still_wait_seconds)
                except TimeoutError:
                    pass

        async def transcript() -> Any:
            try:
                heard = await asyncio.to_thread(stream.finish)
            except VoiceProviderError as exc:  # A cloud service that did not answer: the bar says so.
                await self.link.send_event({"type": "error", "code": exc.code})
                heard = TranscriptUpdate("", final=True)
            finally:
                stream.close()
            marks.mark("transcript_final", self.clock())
            return heard

        heard, _ = await asyncio.gather(transcript(), fresh_still())
        # The newest still goes with this turn only; the next turn asks for its own.
        still, self._still = (self._still if self.sharing is not None else None), None
        text = " ".join(heard.text.split())
        pending = self._approval
        # A yes or no while an approval waits is its answer (its summary's own words are no echo here).
        answer = spoken_answer(text) if pending is not None and not self.closed else None
        if answer is None and (not text or self.host.drops(text) or self.closed):
            self._thinking = False
            await self.link.send_event({"type": "caption", "role": "user", "text": "", "final": True})
            await self._publish_state()
            return
        self._heard_language = heard.language or self._heard_language
        await self.link.send_event({"type": "caption", "role": "user", "text": text, "final": True,
                                    "language": heard.language})
        if pending is not None and answer is not None:
            await self._answer_approval(pending, answer, heard_at, over_voice, utterance_id)
            return
        self._marks = marks
        self._marks_segment = 0
        outcome = await asyncio.to_thread(self.host.take_turn, text, utterance_id, still)
        marks.mark("agent_admitted", self.clock())
        if outcome.kind != "rejected":
            self._heard_since_silence = True
        if outcome.kind == "rejected":
            self._thinking = False
            self._marks = None
            await self.link.send_event({"type": "error", "code": outcome.code or "voice_admission_failed"})
        elif outcome.kind == "control":
            self._thinking = False
            # "How's it going?" is answered from the run's own progress; the run goes on.
            speakable = (status_line(self._tool if self._open_tools else None) if outcome.control == "status"
                         else outcome.speakable)
            if speakable:
                self._enqueue(speakable, "control")
        await self._publish_state()

    # ── approvals by voice (VD4) ────────────────────────────────────────

    async def _answer_approval(self, pending: _Approval, answer: str, heard_at: float, over_voice: bool,
                               utterance_id: str) -> None:
        try:
            # Only a plain yes or no, begun after its summary finished and not over Row-Bot's voice, counts:
            # anything else (an echo, a "yes, but…") is ignored, and the card still waits. Silence never answers.
            if answer == "unclear" or pending.armed_ms is None or heard_at < pending.armed_ms or over_voice:
                return
            if not pending.voice:
                self._enqueue(NEEDS_A_TAP, "control")
                return
            result = await asyncio.to_thread(self.host.answer_approval, pending.approval_id, answer == "yes",
                                             utterance_id)
            if result == "tap":
                self._enqueue(NEEDS_A_TAP, "control")
            else:
                await self._clear_approval(pending)
        finally:
            self._thinking = False
            await self._publish_state()

    async def _ask_approval(self, notice: Mapping[str, Any]) -> None:
        approval = _Approval(str(notice["approval_id"]), bool(notice.get("voice")), str(notice.get("fallback") or ""))
        self._approval = approval
        self._deferred = [item for item in self._deferred if item[1] != "approval"]
        # The client words the card's title exactly as its card does and sends it back; the summary waits for it
        # (a moment), then says the fallback.
        await self.link.send_event({"type": "approval", "approval_id": approval.approval_id,
                                    "voice": approval.voice, "card": dict(notice.get("card") or {})})
        self._timer(TITLE_WAIT_SECONDS, self._title_approval, approval, "")

    async def _title_approval(self, approval: _Approval, title: str) -> None:
        if self._approval is not approval or approval.titled:
            return
        approval.titled = True
        self._deferred.append((approval_summary(title or approval.fallback, approval.voice), "approval"))
        await self._publish_state()

    async def _clear_approval(self, only: _Approval | None = None) -> None:
        if self._approval is None or (only is not None and self._approval is not only):
            return
        self._approval = None
        self._deferred = [item for item in self._deferred if item[1] != "approval"]
        await self.link.send_event({"type": "approval", "approval_id": ""})

    # ── spoken progress and later results (V3) ──────────────────────────

    def _quiet(self) -> bool:
        """Nobody is talking and nothing is waiting to be said."""
        return (not self.closed and not self.paused and self._utterance is None and self.tracker.state == "quiet"
                and not self._audible and self._speech.empty())

    def _at_pause(self) -> bool:
        return self._quiet() and not self._thinking and not self._run_active

    def _deliver(self) -> None:
        """Say what waits for a pause (an approval's summary, a later result) once there is one."""
        if not self._deferred or not self._at_pause():
            return
        waiting, self._deferred = self._deferred, []
        for text, kind in waiting:
            self._enqueue(text, kind)

    def _tool_started(self, payload: Any) -> None:
        tool = str(payload.get("tool") or "") if isinstance(payload, Mapping) else ""
        call = str(payload.get("id") or "") if isinstance(payload, Mapping) else ""
        self._open_tools.add(call or tool or f"tool-{len(self._open_tools)}")
        self._tool = tool
        if self._burst is not None:
            return
        burst = self._burst = _Burst(self.clock())
        line = progress_line(tool)
        if line and self._quiet() and self.clock() - self._said_at >= PROGRESS_GAP_SECONDS:
            burst.said = True
            self._enqueue(line, "progress")
        self._timer(STILL_WORKING_SECONDS, self._still_working, burst)

    async def _still_working(self, burst: _Burst) -> None:
        # Nothing said since this burst began, and a tool still runs: one line, never over anyone.
        if (self._burst is burst and not burst.said and self._open_tools and self._said_at <= burst.started_at
                and self._quiet()):
            burst.said = True
            self._enqueue(STILL_WORKING, "progress")
            await self._publish_state()

    def _later(self, text: str) -> None:
        """A result that came back after its turn ended (a helper's, in a later wake): said at the next pause."""
        aggregator = SentenceAggregator()
        pieces = aggregator.push(text) + aggregator.flush()
        self._deferred.extend((piece, "later") for piece in pieces or [ON_SCREEN_POINTER])

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
        # The run went on (or ended) after its card: the approval no longer waits on voice.
        if self._approval is not None and (kind in {"token", "tool_call", "done", "error"}
                                           or (kind == "finished" and payload != "waiting_approval")):
            await self._clear_approval()
        if silenced:
            self._run_active = self._thinking = False
            self._waiting = kind == "interrupt" or (kind == "finished" and payload == "waiting_approval")
        elif kind == "later_answer":
            self._later(str(payload or ""))
        elif kind == "token":
            self._run_active = True
            self._waiting = False
            self._burst = None  # Answer text ends a burst of tool starts.
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
            if kind == "tool_call":
                for piece in self._aggregator.flush():  # What it said before the call is whole now.
                    self._enqueue(piece)
                self._tool_started(payload)
        elif kind == "tool_done" and isinstance(payload, Mapping):
            self._open_tools.discard(str(payload.get("id") or payload.get("tool") or ""))
        if terminal:
            self._burst = None
            self._open_tools.clear()
        if kind == "interrupt" and isinstance(payload, Mapping) and payload.get("approval_id"):
            await self._ask_approval(payload)
        self._idle_from_ms = self.tracker.now_ms
        await self._publish_state()

    def _enqueue(self, text: str, kind: SpeechKind = "answer") -> None:
        self._segment += 1
        if self._marks is not None and kind in {"answer", "control"}:
            self._marks.mark("first_sentence", self.clock())
            if not self._marks_segment:
                self._marks_segment = self._segment
        self._said_at = self.clock()
        self._said_ms = self.tracker.now_ms
        self._speech.put_nowait((self._speech_epoch, self._segment, text, kind))

    async def _speak_loop(self) -> None:
        while True:
            epoch, segment, text, kind = await self._speech.get()
            sent = False
            try:
                # A progress line whose burst ended (the answer began) is stale: the answer goes first.
                if epoch != self._speech_epoch or (kind == "progress" and self._burst is None):
                    continue
                audio = self._fixed_audio.get(text) if kind in _FIXED_KINDS else None
                stream = getattr(self.parts.tts, "stream", None)
                if audio is None and stream is not None and kind not in _FIXED_KINDS:
                    # A streaming voice: the sentence's first piece goes out while the rest is made.
                    sent = await self._speak_streamed(epoch, segment, text, kind,
                                                      stream(text, language=self._heard_language))
                    continue
                if audio is None:
                    audio = await asyncio.to_thread(self.parts.tts.synthesize, text,
                                                    language=self._heard_language)
                    if kind in _FIXED_KINDS and audio.pcm16 and len(self._fixed_audio) < 32:
                        self._fixed_audio[text] = audio
                if audio.unspoken and epoch == self._speech_epoch and not self.closed:
                    await self._unspoken(segment, text, kind, audio.unspoken)
                    continue
                if (epoch != self._speech_epoch or self.closed or not audio.pcm16
                        or (kind == "progress" and self._burst is None)):
                    continue
                self.out_sample_rate = audio.sample_rate
                self.host.spoke(text)
                self._audible = True
                self._thinking = False
                await self.link.send_event({"type": "caption", "role": "assistant", "text": text,
                                            "segment": segment, "sample_rate": audio.sample_rate,
                                            "kind": kind, "final": True})
                sent = True
                step = int(audio.sample_rate * _AUDIO_CHUNK_SECONDS) * 2
                if self._marks is not None and segment == self._marks_segment:
                    self._marks.mark("first_audio_sent", self.clock())
                for offset in range(0, len(audio.pcm16), step):
                    if epoch != self._speech_epoch:
                        break
                    await self.link.send_audio(segment, audio.pcm16[offset:offset + step])
                await self._publish_state()
            except VoiceProviderError as exc:  # A cloud voice that failed: on screen, and the bar says so.
                await self.link.send_event({"type": "error", "code": exc.code})
            except Exception as exc:  # A sentence that cannot be spoken stays on screen.
                _LOG.warning("Row-Bot Voice could not speak a sentence: %s", type(exc).__name__)
            finally:
                approval = self._approval
                if kind == "approval" and approval is not None and not approval.sent:
                    approval.sent = True
                    if not sent:
                        approval.armed_ms = self.tracker.now_ms  # Unspoken: nothing to wait for.
                self._speech.task_done()

    async def _unspoken(self, segment: int, text: str, kind: SpeechKind, language: str) -> None:
        # A language the voice can't speak: the words stay on screen, and the bar says why.
        await self.link.send_event({"type": "caption", "role": "assistant", "text": text, "segment": segment,
                                    "kind": kind, "final": True, "spoken": False})
        await self.link.send_event({"type": "unspoken", "language": language})

    async def _speak_streamed(self, epoch: int, segment: int, text: str, kind: SpeechKind,
                              pieces: Iterator[SpeechAudio]) -> bool:
        """Speak *pieces* as they arrive; a barge-in (or the session's end) closes the request."""
        def take() -> SpeechAudio | None:
            return next(pieces, None)

        sent = False
        try:
            while True:
                piece = await asyncio.to_thread(take)
                if piece is None or epoch != self._speech_epoch or self.closed or (
                        kind == "progress" and self._burst is None):
                    return sent
                if piece.unspoken:
                    await self._unspoken(segment, text, kind, piece.unspoken)
                    return sent
                if not piece.pcm16:
                    continue
                if not sent:
                    self.out_sample_rate = piece.sample_rate
                    self.host.spoke(text)
                    self._audible = True
                    self._thinking = False
                    await self.link.send_event({"type": "caption", "role": "assistant", "text": text,
                                                "segment": segment, "sample_rate": piece.sample_rate,
                                                "kind": kind, "final": True})
                    sent = True
                    if self._marks is not None and segment == self._marks_segment:
                        self._marks.mark("first_audio_sent", self.clock())
                    await self._publish_state()
                await self.link.send_audio(segment, piece.pcm16)
        finally:
            close = getattr(pieces, "close", None)
            if close is not None:
                try:
                    await asyncio.to_thread(close)
                except ValueError:
                    pass  # Still reading in its thread (the session is closing): it ends with that read.

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
                await self._clear_partial()
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
        elif kind == "approval_title":
            approval = self._approval
            title = message.get("title")
            if (approval is not None and message.get("approval_id") == approval.approval_id
                    and isinstance(title, str) and len(title) <= 400):
                await self._title_approval(approval, title)
        elif kind == "share":
            await self._share(message.get("source"))
        elif kind == "still":
            size = message.get("bytes")
            # The still itself is the next binary message (checked when it arrives).
            self._still_expected = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else 0
            if not self._still_expected:
                raise ValueError("voice_still_invalid")
        await self._publish_state()
        return True

    async def binary(self, data: bytes) -> None:
        """A binary message: the still a ``still`` message announced, else microphone audio."""
        expected, self._still_expected = self._still_expected, 0
        if expected:
            await self.still(data, expected)
        else:
            await self.audio(data)

    async def _share(self, source: Any) -> None:
        """Start (``camera`` or ``screen``) or stop sharing; never while the model can't see images."""
        wanted = source if source in SHARE_SOURCES and self.sees_images and not self.paused else None
        if source and wanted is None:
            await self.link.send_event({"type": "error", "code": "voice_share_unavailable"})
        if wanted != self.sharing:
            self.sharing = wanted
            self._still = None
            await self.link.send_event({"type": "sharing", "source": wanted})

    async def still(self, data: bytes, expected: int | None = None) -> None:
        """Hold the newest still while sharing; an invalid one is refused and dropped."""
        if self.closed or self.sharing is None:
            return
        try:
            if expected is not None and len(data) != expected:
                raise ValueError("voice_still_invalid")
            self._still = checked_still(data, self.sharing)
        except ValueError:
            await self.link.send_event({"type": "error", "code": "voice_still_refused"})
            return
        if self._still_arrived is not None:
            self._still_arrived.set()

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
            self._said_at = self.clock()
            self._said_ms = self.tracker.now_ms
            approval = self._approval
            if approval is not None and approval.sent and approval.armed_ms is None:
                approval.armed_ms = self.tracker.now_ms
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
        await self._clear_partial()
        await self._share(None)  # A paused session shares nothing.
        await self._publish_state()

    async def tick(self) -> bool:
        """Periodic housekeeping; False when a long pause should end the session."""
        self.host.keepalive()
        return not (self.paused and self.clock() - self._paused_at >= self.settings.idle_end_seconds)
