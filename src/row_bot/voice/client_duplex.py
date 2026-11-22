"""Row-Bot Voice sessions over the coordinator's exclusive voice lease.

Starting one is an authenticated POST: it takes the lease (mode ``talk``,
transport ``duplex``), freezes the conversation's model and approval policy
exactly as typed messages do, and returns a short-lived, single-use ticket.
The browser opens the voice WebSocket and presents that ticket in its first
message; the socket is then bound to that client session, conversation and
lease. Audio lives only in memory for the turn it belongs to, and so does a
still of a shared camera or screen until it is attached to its turn.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from typing import Any, Literal, Protocol

from row_bot.voice.client_transport import (
    LEASE_SECONDS,
    ClientDictationTransport,
    DictationError,
    DictationHandle,
    DictationOwner,
)
from row_bot.voice.provider_base import SynthesizedSpeech, VoiceProviderError
from row_bot.voice.tickets import RedeemedTicket, VoiceTicket, VoiceTickets

_LOG = logging.getLogger(__name__)
SPEECH_TEST_PHRASE = "Hello! This is your Row-Bot voice test."


@dataclass(frozen=True)
class TalkSnapshot:
    schema_version: int
    handle: DictationHandle
    state: Literal["starting", "listening", "stopping", "stopped"]
    quiesced: bool
    expires_in_ms: int
    transport: Literal["row_bot_voice"] = "row_bot_voice"


@dataclass(frozen=True)
class TalkStarted:
    snapshot: TalkSnapshot
    ticket: str
    ticket_expires_in_ms: int


class VoiceEngineFactory(Protocol):
    def readiness(self) -> str | None:
        """None when speech in and out are ready, else the public error code."""
        ...

    def build(self) -> Any:
        """The session's ``EngineParts``. Loads models; called off the event loop."""
        ...



class LocalVoiceEngine:
    """The selected local speech engines, Silero VAD and Smart Turn."""

    def __init__(self, browser_service: Callable[[], Any]) -> None:
        self._browser = browser_service

    def _providers(self) -> tuple[Any, Any]:
        from row_bot.voice.providers import speech_output_provider, speech_to_text_provider

        service = self._browser()
        return (speech_to_text_provider("talk", voice_service=service.voice_service),
                speech_output_provider(tts_service=service.tts_service))

    def readiness(self) -> str | None:
        try:
            listen, speak = self._providers()
        except VoiceProviderError as exc:
            return exc.code
        for provider in (listen, speak):
            status = provider.status()
            if not status.ready:
                return status.unavailable_code
        return None

    def build(self) -> Any:
        from row_bot.voice.engine.pipecat_boundary import SileroActivity, SmartTurn
        from row_bot.voice.engine.session import EngineParts
        from row_bot.voice.engine.speech import ProviderSentenceSpeech, SegmentedSpeechToText

        listen, speak = self._providers()
        # A streaming model decodes as the person speaks; Whisper and SenseVoice are segmented.
        stt = listen.streaming() if hasattr(listen, "streaming") else SegmentedSpeechToText(listen)
        return EngineParts(stt, ProviderSentenceSpeech(speak), SileroActivity(), SmartTurn())


class DuplexTalkTransport:
    def __init__(self, *, dictation: ClientDictationTransport, admission: Any,
                 engine: VoiceEngineFactory, listeners: dict[str, Callable[[tuple, Any], None]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._transport = dictation
        self._admission = admission
        self.engine = engine
        self._listeners = listeners
        self._clock = clock
        self._tickets = VoiceTickets(lambda: self._clock())
        self._live: dict[str, Callable[[], None]] = {}
        # How often a live socket checks its sign-in, lease and idle time.
        self.tick_seconds = 5.0

    @property
    def coordinator(self):
        return self._transport.coordinator

    # ── the lease ───────────────────────────────────────────────────────

    def _lease(self, owner: DictationOwner, handle: DictationHandle, *, active: bool = True):
        lease = self._transport._lease(owner, handle, active=active)
        if lease.mode != "talk" or lease.transport != "duplex":
            raise DictationError("action_denied")
        return lease

    def _snapshot(self, lease) -> TalkSnapshot:
        if lease.revoked:
            state = "stopping" if lease.starting or lease.handle.lease_id in self._live else "stopped"
        else:
            state = "starting" if lease.starting else "listening"
        return TalkSnapshot(2, lease.handle, state,
                            not lease.starting and lease.operation is None and lease.handle.lease_id not in self._live,
                            max(0, int((lease.expires - self._clock()) * 1000)))

    def _ready(self, validate: Callable[[], None]) -> None:
        validate()
        code = self.engine.readiness()
        if code:
            raise DictationError(code)

    def start(self, owner: DictationOwner, *, request_id: str, validate: Callable[[], None],
              chat_context: dict | None, csrf: str) -> TalkStarted:
        self._transport._start(owner, request_id=request_id, validate=validate, mode="talk",
                               _transport="duplex", _prepare=self._ready, _chat_context=chat_context)
        with self.coordinator._dictation_lock:
            lease = self.coordinator._dictation_lease
            snapshot = self._snapshot(self._lease(owner, lease.handle))
        ticket = self.ticket(owner, snapshot.handle, csrf=csrf, validate=validate)
        return TalkStarted(snapshot, ticket.ticket, ticket.expires_in_ms)

    def ticket(self, owner: DictationOwner, handle: DictationHandle, *, csrf: str,
               validate: Callable[[], None]) -> VoiceTicket:
        validate()
        with self.coordinator._dictation_lock:
            self._lease(owner, handle)
        return self._tickets.issue(owner, handle, csrf)

    def redeem(self, ticket: str) -> RedeemedTicket:
        """The ticket's session, once: a second use, an expired or unknown ticket is refused."""
        item = self._tickets.redeem(ticket)
        with self.coordinator._dictation_lock:
            self._lease(item.owner, item.handle)
        return item

    def stop(self, owner: DictationOwner, handle: DictationHandle, *,
             validate: Callable[[], None]) -> TalkSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            self._transport._invalidate(lease)
            end = self._live.get(handle.lease_id)
        if end is not None:
            end()
        self._tickets.drop(handle)
        with self.coordinator._dictation_lock:
            return self._snapshot(lease)

    def speech_test(self, client_session_id: str, *, validate: Callable[[], None]) -> SynthesizedSpeech:
        """Speak the fixed Voice settings test phrase for the requesting browser."""
        validate()
        return self._transport._browser_service().synthesize(
            client_session_id, SPEECH_TEST_PHRASE, validate=validate)

    # ── a live socket ───────────────────────────────────────────────────

    def attach(self, redeemed: RedeemedTicket, end: Callable[[], None]) -> None:
        """Bind a socket to its lease; ``end`` closes it when the lease is stopped."""
        with self.coordinator._dictation_lock:
            self._lease(redeemed.owner, redeemed.handle)
            if redeemed.handle.lease_id in self._live:
                raise DictationError("voice_session_busy")
            self._live[redeemed.handle.lease_id] = end

    def detach(self, redeemed: RedeemedTicket, *, stop: bool) -> None:
        with self.coordinator._dictation_lock:
            self._live.pop(redeemed.handle.lease_id, None)
            self._listeners.pop(redeemed.owner.conversation_id, None)
            lease = self.coordinator._dictation_lease
            if stop and lease is not None and lease.handle == redeemed.handle:
                self._transport._invalidate(lease)

    def listen(self, redeemed: RedeemedTicket, session: Any) -> None:
        """Hear every run event of the conversation while the socket is open."""
        def follow(event: tuple, handle: Any) -> None:
            kind = str(event[0]) if event else ""
            payload = event[1] if len(event) > 1 else None
            if kind == "interrupt":
                # The card it raised, as voice says it: a summary, and whether a spoken yes may answer it.
                from row_bot.voice.approvals import approval_notice
                payload = approval_notice(payload, str(getattr(handle, "approval_id", "") or ""))
            elif kind in {"tool_call", "tool_done"}:
                get = getattr(payload, "get", None)
                payload = {"tool": str(get("runtime_name") or get("raw_name") or ""),
                           "id": str(get("tool_call_id") or get("id") or "")} if callable(get) else {}
            session.agent_event(kind, payload, str(getattr(handle, "pass_id", "") or ""),
                                str(getattr(handle, "segment_id", "") or ""))

        # While the session listens (open, not paused), typed turns are spoken too, so they run in voice mode.
        follow.live = lambda: not session.closed and not session.paused  # type: ignore[attr-defined]
        with self.coordinator._dictation_lock:
            self._listeners[redeemed.owner.conversation_id] = follow

    def host(self, redeemed: RedeemedTicket, validate: Callable[[], None]) -> "ConversationVoiceHost":
        return ConversationVoiceHost(self, redeemed.owner, redeemed.handle, validate)


class ConversationVoiceHost:
    """A voice session's view of its conversation: normal admission, same approvals."""

    def __init__(self, transport: DuplexTalkTransport, owner: DictationOwner,
                 handle: DictationHandle, validate: Callable[[], None]) -> None:
        self._transport = transport
        self._owner = owner
        self._handle = handle
        self._validate = validate

    def _admitted(self) -> None:
        self._validate()
        with self._transport.coordinator._dictation_lock:
            self._transport._lease(self._owner, self._handle)

    def take_turn(self, text: str, utterance_id: str, still: Any = None) -> Any:
        from row_bot.voice.engine.session import TurnOutcome

        admission = self._transport._admission
        try:
            self._admitted()
            running = admission.active(self._owner)
            if running is None:
                admission.submit(self._owner, text, utterance_id, self._admitted, still=still)
                outcome = TurnOutcome("submitted")
            else:
                # The person spoke while a run works: steer it, stop it or ask how it is going (words only).
                result = admission.control(self._owner, running, "input", text, utterance_id, self._admitted)
                outcome = TurnOutcome("control", speakable=str(result.get("speakable") or ""),
                                      control=str(result.get("control") or ""))
            with self._transport.coordinator._dictation_lock:
                lease = self._transport._lease(self._owner, self._handle)
                lease.last_activity = self._transport._clock()
            return outcome
        except DictationError as exc:
            return TurnOutcome("rejected", code=exc.code)
        except Exception as exc:
            return TurnOutcome("rejected", code=str(getattr(exc, "code", "") or "voice_admission_failed"))

    def answer_approval(self, approval_id: str, approve: bool, utterance_id: str) -> str:
        try:
            self._admitted()
            return self._transport._admission.answer_approval(self._owner, approval_id, approve, utterance_id,
                                                              self._admitted)
        except Exception as exc:  # Refused or already answered: the card is the way now.
            _LOG.info("A spoken approval answer was not taken: %s", str(getattr(exc, "code", "") or type(exc).__name__))
            return "gone"

    def sees_images(self) -> bool:
        return self._transport._admission.sees_images(self._owner)

    def drops(self, text: str) -> bool:
        with self._transport.coordinator._dictation_lock:
            return self._transport.coordinator._should_drop_transcript(text)

    def spoke(self, text: str) -> None:
        with self._transport.coordinator._dictation_lock:
            self._transport.coordinator.record_assistant_output(text, session_id=self._handle.voice_session_id)

    def keepalive(self) -> None:
        with self._transport.coordinator._dictation_lock:
            lease = self._transport._lease(self._owner, self._handle)
            lease.expires = self._transport._clock() + LEASE_SECONDS


def session_settings() -> Any:
    """The saved Row-Bot Voice settings for a session starting now."""
    from row_bot.voice.engine.session import SessionSettings
    from row_bot.voice.engine.turns import TurnSettings
    from row_bot.voice.runtime import load_voice_runtime_settings

    saved = load_voice_runtime_settings()
    return SessionSettings(turns=TurnSettings.for_eagerness(saved.turn_eagerness),
                           push_to_talk=saved.push_to_talk, captions=saved.captions_enabled)


async def run_socket_session(transport: DuplexTalkTransport, redeemed: RedeemedTicket, *,
                             link: Any, receive: Callable[[], Any], validate: Callable[[], None],
                             revalidate: Callable[[], Any], settings: Any = None,
                             tick_seconds: float = 5.0) -> None:
    """Drive one socket: audio and control in, events and speech out, until it ends."""
    from row_bot.voice.engine.session import DuplexSession

    parts = await asyncio.to_thread(transport.engine.build)
    host = transport.host(redeemed, validate)
    session = DuplexSession(parts, link, host, settings=settings)
    ended = asyncio.Event()
    loop = asyncio.get_running_loop()
    stop_requested = False

    def end() -> None:
        loop.call_soon_threadsafe(ended.set)

    transport.attach(redeemed, end)

    async def tick() -> None:
        while not ended.is_set():
            try:
                await asyncio.wait_for(ended.wait(), timeout=tick_seconds)
                return
            except TimeoutError:
                pass
            try:
                await revalidate()
                if not await session.tick():
                    ended.set()
            except Exception:
                ended.set()

    async def pump() -> None:
        nonlocal stop_requested
        while not ended.is_set():
            message = await receive()
            if message is None:
                ended.set()
                return
            if isinstance(message, bytes):
                await session.binary(message)
            elif not await session.control(message):
                stop_requested = True
                ended.set()
                return

    workers: list[asyncio.Task] = []
    waiter: asyncio.Task | None = None
    try:
        transport.listen(redeemed, session)
        await session.start()
        workers = [asyncio.create_task(tick(), name="row-bot-voice-tick"),
                   asyncio.create_task(pump(), name="row-bot-voice-pump")]
        waiter = asyncio.create_task(ended.wait())
        done, _ = await asyncio.wait([waiter, *workers], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not waiter and not task.cancelled() and task.exception() is not None:
                raise task.exception()
    finally:
        for task in workers:
            task.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(*workers, *([waiter] if waiter is not None else []), return_exceptions=True)
        await session.close()
        close_end = getattr(parts.end_of_turn, "close", None)
        if close_end is not None:
            await close_end()
        transport.detach(redeemed, stop=stop_requested)
