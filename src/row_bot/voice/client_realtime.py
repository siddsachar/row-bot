"""Live voice sessions over the coordinator's exclusive voice lease.

Starting one is an authenticated POST: it takes the lease, builds the front's
context and returns a single-use ticket for the Live voice WebSocket on Row-Bot's
own origin, which carries the session's events (and, for Gemini Live and Grok
Voice, its audio: the host relays them to the provider). All transient state
lives in the lease. Provider keys and credentials never reach the browser, nor
command receipts, diagnostics or saved conversation data. The injected
application callbacks keep normal chat admission and cancellation authority.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import logging
import math
import time
from typing import Any
from uuid import uuid4

from row_bot.voice.agent_bridge import VoiceAgentBridge
from row_bot.voice.client_transport import (
    ClientDictationTransport, DictationError, DictationHandle, DictationOperation,
    DictationOwner, REALTIME_LEASE_SECONDS, _CONVERSATION, _uuid,
)
from row_bot.voice.tickets import RedeemedTicket, VoiceTicket, VoiceTickets

logger = logging.getLogger(__name__)

_EVENTS = frozenset({"connected", "disconnected", "fatal_error", "speech_started", "speech_stopped",
                     "transcript_final", "assistant_transcript_final", "function_call_ready",
                     "output_started", "output_item_started", "response_done",
                     "response_cancelled", "output_audio_done", "barge_in_cancelled",
                     "provider_response_create_sent"})
# Who made a provider response: the front on its own ("front"), or Row-Bot asking it to speak.
_ORIGINS = frozenset({"", "front", "row_bot"})
_CONSULT = "row_bot_agent_consult"
# The fronts the host relays over its own WebSocket to the provider (audio included).
RELAYED = frozenset({"gemini_live", "grok_voice"})


@dataclass(frozen=True)
class RealtimeSnapshot:
    schema_version: int
    handle: DictationHandle
    state: str
    quiesced: bool
    expires_in_ms: int
    run_id: str | None
    transport: str = "openai_realtime"


@dataclass(frozen=True)
class RealtimeStart:
    snapshot: RealtimeSnapshot
    # OpenAI's ephemeral secret, kept by the host for the SDP exchange; never sent to the browser.
    client_secret: str | None = field(repr=False)
    secret_expires_in_ms: int
    ticket: str = field(default="", repr=False)
    ticket_expires_in_ms: int = 0


@dataclass(frozen=True)
class RealtimeEvent:
    event_id: str
    type: str
    generation_id: str = ""
    response_id: str = ""
    output_item_id: str = ""
    item_id: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    text: str = ""
    origin: str = ""


@dataclass(frozen=True)
class RealtimeEventResult:
    snapshot: RealtimeSnapshot
    event_id: str
    accepted: bool
    caption: str = ""
    call_id: str = ""
    function_output: str = ""
    silent: bool = False


@dataclass
class _RealtimeState:
    secret: str = field(repr=False)
    secret_expires: float
    connected: bool = False
    events: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    last_event: str = ""
    last_digest: str = ""
    last_result: RealtimeEventResult | None = None
    transcript: str = ""
    transcript_item: str = ""
    consult_digest: str = ""
    consult_run_id: str = ""
    provider: Any = field(default=None, repr=False)
    exchange_attempted: bool = False
    transport: str = "openai_realtime"
    # A relayed front's session instructions, for the host's provider socket.
    instructions: str = field(default="", repr=False)
    # The consult call each admitted run answers, by run id.
    pending_call: str = ""
    run_calls: dict[str, str] = field(default_factory=dict)
    # The front's own lines: its speech per response, the responses that called a tool,
    # the person's words since its last reply, and finished turns waiting to be saved.
    front_text: dict[str, str] = field(default_factory=dict, repr=False)
    tool_responses: set[str] = field(default_factory=set)
    front_user: str = field(default="", repr=False)
    front_turns: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def clear_private(self) -> None:
        self.secret = self.transcript = self.transcript_item = self.last_digest = ""
        self.consult_digest = self.consult_run_id = ""
        self.instructions = self.front_user = ""
        self.front_text, self.front_turns = {}, []
        self.provider = None
        self.last_result = None


def _generation_id(generation: Any) -> str:
    return str(getattr(generation, "generation_id", "") or "")


def _provider_error(exc: Exception) -> DictationError:
    """Read only status codes, never provider response text or credential data."""
    current: BaseException | None = exc
    for _ in range(3):
        status = getattr(getattr(current, "response", None), "status_code", None)
        if status in {401, 403}:
            return DictationError("realtime_auth_unavailable")
        if status == 429:
            return DictationError("realtime_quota_or_rate_limit")
        current = getattr(current, "__cause__", None)
        if current is None:
            break
    return DictationError("realtime_provider_unavailable")


class ClientRealtimeTransport:
    """One Live voice session: its front, the consults it starts, and the front's saved lines.

    ``session_context`` builds the front's instructions when the session starts
    (the chat's recent turns, persona and memories); ``save_front`` saves the
    front's own finished turns in the chat while no run is writing to it.
    """

    def __init__(self, *, dictation: ClientDictationTransport, provider_factory: Callable[[str], Any],
                 submit: Callable[[DictationOwner, str, str, Callable[[], None]], str],
                 active_generation: Callable[[DictationOwner], Any],
                 control_generation: Callable[[DictationOwner, Any, str, str, str, Callable[[], None]], dict],
                 session_context: Callable[[DictationOwner], str] | None = None,
                 save_front: Callable[[DictationOwner, list[tuple[str, str]], Callable[[], None]], None] | None = None,
                 run_view: Callable[[DictationOwner, DictationHandle, Callable[[], None]], dict] | None = None,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self._transport = dictation
        self.run_view = run_view
        self._tickets = VoiceTickets(lambda: self._transport._clock())
        self._live: dict[str, Callable[[], None]] = {}
        # How often a live socket checks its sign-in and lease, and reads its run.
        self.tick_seconds = 5.0
        self.poll_seconds = 0.5
        self._provider = provider_factory
        self._submit = submit
        self._active = active_generation
        self._control = control_generation
        self._session_context = session_context
        self._save_front = save_front
        self._wall_clock = wall_clock

    @property
    def coordinator(self):
        return self._transport.coordinator

    def _lease(self, owner: DictationOwner, handle: DictationHandle, *, active: bool = True):
        lease = self._transport._lease(owner, handle, active=active)
        if lease.mode != "talk" or lease.transport != "realtime":
            raise DictationError("action_denied")
        if lease.realtime is not None and self._transport._clock() >= lease.realtime.secret_expires:
            lease.realtime.secret = ""
        return lease

    def _snapshot(self, lease) -> RealtimeSnapshot:
        live = lease.handle.lease_id in self._live
        return RealtimeSnapshot(1, lease.handle,
            ("stopping" if lease.starting or lease.operation or live else "stopped") if lease.revoked
            else self.coordinator.realtime_state,
            not lease.starting and lease.operation is None and not live,
            max(0, int((min(lease.expires, lease.started_at + 600) - self._transport._clock()) * 1000)),
            lease.run_id or None, lease.realtime.transport if lease.realtime is not None else "openai_realtime")

    def start(self, owner: DictationOwner, *, request_id: str, validate: Callable[[], None],
              chat_context: dict | None = None, live_provider: str = "openai", csrf: str = "") -> RealtimeStart:
        def prepare(admitted: Callable[[], None]) -> None:
            admitted()
            try:
                provider = self._provider(live_provider)
            except DictationError:
                raise
            except Exception as exc:
                raise _provider_error(exc) from None
            admitted()
            if not provider.status().ready:
                raise DictationError("realtime_key_missing")
            instructions = self._session_context(owner) if self._session_context is not None else ""
            admitted()
            transport = str(getattr(provider, "transport", "openai_realtime"))
            secret, remaining = "", 60.0
            if transport == "openai_realtime":
                try:
                    value = provider.create_client_secret(instructions=instructions, expires_after_seconds=60)
                except DictationError:
                    raise
                except Exception as exc:
                    # Provider HTTP bodies can contain private request/context data.
                    raise _provider_error(exc) from None
                admitted()
                secret = value.get("value") if isinstance(value, dict) else None
                expiry = value.get("expires_at") if isinstance(value, dict) else None
                if (not isinstance(secret, str) or not 1 <= len(secret) <= 4096 or
                        type(expiry) not in {int, float} or not math.isfinite(expiry)):
                    raise DictationError("realtime_credential_unavailable")
                remaining = min(60.0, expiry - self._wall_clock())
                if remaining <= 0:
                    raise DictationError("realtime_credential_expired")
            elif transport not in RELAYED:
                raise DictationError("voice_provider_unavailable")
            generation = self._active(owner)
            admitted()
            with self.coordinator._dictation_lock:
                lease = self.coordinator._dictation_lease
                lease.realtime = _RealtimeState(secret, self._transport._clock() + remaining, provider=provider,
                                                transport=transport,
                                                instructions=instructions if transport in RELAYED else "")
                lease.run_id = _generation_id(generation)
                self.coordinator.mark_realtime_latency("talk_activation")
                if lease.run_id:
                    self.coordinator.set_active_row_bot_generation(lease.run_id)

        context = None if chat_context is None else {**chat_context, "live_provider": live_provider}
        snapshot = self._transport._start(owner, request_id=request_id, validate=validate,
                                           mode="talk", _transport="realtime", _prepare=prepare, _chat_context=context)
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, snapshot.handle)
            state = lease.realtime
            started = self._snapshot(lease)
            secret, expires = state.secret or None, max(0, int((state.secret_expires - self._transport._clock()) * 1000))
        ticket = self._tickets.issue(owner, started.handle, csrf)
        return RealtimeStart(started, secret, expires, ticket.ticket, ticket.expires_in_ms)

    # ── the Live voice socket ───────────────────────────────────────────

    def ticket(self, owner: DictationOwner, handle: DictationHandle, *, csrf: str,
               validate: Callable[[], None]) -> VoiceTicket:
        """A fresh single-use ticket for the session's socket (a reconnect)."""
        validate()
        with self.coordinator._dictation_lock:
            self._lease(owner, handle)
        return self._tickets.issue(owner, handle, csrf)

    def redeem(self, ticket: object) -> RedeemedTicket:
        item = self._tickets.redeem(ticket)
        with self.coordinator._dictation_lock:
            self._lease(item.owner, item.handle)
        return item

    def attach(self, redeemed: RedeemedTicket, end: Callable[[], None]) -> tuple[str, Any, str]:
        """Bind a socket to its lease (``end`` closes it when the lease stops).

        Returns the session's transport, its front and, for a relayed front, its instructions.
        """
        with self.coordinator._dictation_lock:
            lease = self._lease(redeemed.owner, redeemed.handle)
            if redeemed.handle.lease_id in self._live:
                raise DictationError("voice_session_busy")
            self._live[redeemed.handle.lease_id] = end
            state = lease.realtime
            return state.transport, state.provider, state.instructions

    def detach(self, redeemed: RedeemedTicket, *, stop: bool) -> None:
        with self.coordinator._dictation_lock:
            self._live.pop(redeemed.handle.lease_id, None)
            lease = self.coordinator._dictation_lease
            if stop and lease is not None and lease.handle == redeemed.handle:
                self._transport._invalidate(lease)

    def generation(self, owner: DictationOwner, handle: DictationHandle) -> str:
        """The run the session's events belong to: the active one, else its last."""
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle)
            return _generation_id(self._active(owner)) or lease.run_id

    def keepalive(self, owner: DictationOwner, handle: DictationHandle) -> None:
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle)
            lease.expires = min(self._transport._clock() + REALTIME_LEASE_SECONDS, lease.started_at + 600)

    def snapshot(self, owner: DictationOwner, handle: DictationHandle, *,
                 validate: Callable[[], None]) -> RealtimeSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            return self._snapshot(self._lease(owner, handle, active=False))

    def exchange(self, owner: DictationOwner, handle: DictationHandle, *, offer: bytes,
                 validate: Callable[[], None]) -> bytes:
        """Use the exact lease credential once; Stop retains the slot until drain."""
        if not isinstance(offer, bytes) or not offer.startswith(b"v=0") or len(offer) > 1024 * 1024:
            raise DictationError("invalid_voice_sdp")
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle)
            state = lease.realtime
            if not lease.ready or lease.operation is not None:
                raise DictationError("voice_session_busy")
            if state.transport != "openai_realtime":
                raise DictationError("action_denied")
            if state.exchange_attempted or state.connected:
                raise DictationError("voice_exchange_consumed")
            if not state.secret or self._transport._clock() >= state.secret_expires:
                raise DictationError("realtime_credential_expired")
            secret, provider = state.secret, state.provider
            state.exchange_attempted = True
            state.secret = ""
            operation = DictationOperation(str(uuid4()), owner, handle, "", "application/sdp", False)
            lease.operation, lease.phase = operation, "exchange"
        authority_error: BaseException | None = None
        def admitted() -> None:
            nonlocal authority_error
            try:
                validate()
                with self.coordinator._dictation_lock:
                    self._lease(owner, handle)
                    self._transport._operation(operation)
            except BaseException as exc:
                authority_error = exc
                raise
        try:
            admitted()
            try:
                answer = provider.exchange_sdp(offer, client_secret=secret, validate=admitted)
            except DictationError:
                raise
            except Exception as exc:
                # Preserve authority rejection identity while redacting provider
                # errors. Revalidate before classifying any transport exception.
                if authority_error is not None:
                    raise authority_error
                admitted()
                raise _provider_error(exc) from None
            admitted()
            if not isinstance(answer, bytes) or not answer.startswith(b"v=0") or len(answer) > 1024 * 1024:
                raise DictationError("invalid_voice_sdp")
            with self.coordinator._dictation_lock:
                self._transport._operation(operation)
                lease.state = "completed"
            return answer
        except BaseException:
            with self.coordinator._dictation_lock:
                self._transport._invalidate(lease)
            raise
        finally:
            secret = ""
            with self.coordinator._dictation_lock:
                state.provider = None
                self._transport._release(lease, operation)

    async def event(self, owner: DictationOwner, handle: DictationHandle, event: RealtimeEvent, *,
                    validate: Callable[[], None]) -> RealtimeEventResult:
        if not isinstance(event, RealtimeEvent) or event.type not in _EVENTS:
            raise DictationError("invalid_voice_event")
        _uuid(event.event_id)
        for name in ("generation_id", "response_id", "output_item_id", "item_id", "call_id", "name"):
            value = getattr(event, name)
            if not isinstance(value, str) or (value and not _CONVERSATION.fullmatch(value)):
                raise DictationError("invalid_voice_event")
        if (not isinstance(event.text, str) or len(event.text) > 4000 or
                not isinstance(event.arguments, str) or len(event.arguments) > 8192 or event.origin not in _ORIGINS):
            raise DictationError("invalid_voice_event")
        encoded = json.dumps(asdict(event), ensure_ascii=False).encode("utf-8")
        if len(encoded) > 16_384:
            raise DictationError("voice_event_too_large")
        digest = hashlib.sha256(encoded).hexdigest()
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle)
            state = lease.realtime
            if not lease.ready or lease.operation is not None:
                raise DictationError("voice_session_busy")
            generation = self._active(owner)
            expected_id = _generation_id(generation) or lease.run_id
            if event.event_id in state.events:
                if state.last_event == event.event_id and state.last_digest == digest and state.last_result:
                    if expected_id != (state.last_result.snapshot.run_id or ""):
                        return RealtimeEventResult(self._snapshot(lease), event.event_id, False)
                    return replace(state.last_result, snapshot=self._snapshot(lease))
                raise DictationError("voice_event_consumed")
            if len(state.events) >= 1000:
                raise DictationError("voice_session_limit")
            if event.generation_id != expected_id:
                return RealtimeEventResult(self._snapshot(lease), event.event_id, False)
            operation = DictationOperation(str(uuid4()), owner, handle, event.event_id, "", False)
            lease.operation = operation
            lease.phase = "event"
            state.events.add(event.event_id)

        # After a spoken cancel the run may end at any moment, even before this event is answered.
        stopping = False

        def admitted() -> None:
            validate()
            with self.coordinator._dictation_lock:
                self._lease(owner, handle)
                self._transport._operation(operation)
            current = self._active(owner)
            if current is not generation and not (stopping and current is None):
                raise DictationError("voice_run_changed")

        control_result: dict | None = None

        def control(action: str, text: str) -> dict:
            nonlocal control_result, stopping
            admitted()
            if len(text) > 4000:
                raise DictationError("voice_event_too_large")
            control_result = self._control(owner, generation, action, text, event.event_id, admitted)
            stopping = isinstance(control_result, dict) and control_result.get("control") == "cancel"
            admitted()
            if not isinstance(control_result, dict):
                raise DictationError("voice_operation_failed")
            return control_result

        async def submit(text: str, **kwargs: Any) -> None:
            nonlocal generation, control_result
            admitted()
            if len(text) > 4000:
                raise DictationError("voice_event_too_large")
            digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
            if state.consult_digest == digest and state.consult_run_id == lease.run_id:
                control_result = {"status": "already_running", "speakable": ""}
                return
            if generation is not None:
                control("input", text)
                state.consult_digest, state.consult_run_id = digest, lease.run_id
                return
            run_id = self._submit(owner, text, event.event_id, admitted)
            validate()
            with self.coordinator._dictation_lock:
                self._lease(owner, handle)
                self._transport._operation(operation)
                current = self._active(owner)
                completed = lease.admitted_run
                finished_owned = (current is None and completed is not None
                    and _generation_id(completed) == run_id and completed.server_epoch == owner.server_epoch
                    and completed.conversation_id == owner.conversation_id and completed.producer_done.is_set())
                if (not isinstance(run_id, str) or not _CONVERSATION.fullmatch(run_id)
                        or (_generation_id(current) != run_id and not finished_owned)):
                    raise DictationError("voice_run_changed")
                generation = current
                lease.run_id = run_id
                state.consult_digest, state.consult_run_id = digest, run_id
                if state.pending_call and len(state.run_calls) < 200:
                    # The run's final answer is this call's result (``run_calls``, read by the run view).
                    state.run_calls[run_id] = state.pending_call
                self.coordinator.set_active_row_bot_generation(run_id)
                self.coordinator.set_realtime_state("thinking", session_id=handle.voice_session_id)

        def queue(call: dict[str, Any]) -> None:
            admitted()
            digest = hashlib.sha256(" ".join(str(call.get("request") or "").split()).encode()).hexdigest()
            if generation is None and not (state.consult_digest == digest and state.consult_run_id == lease.run_id):
                self.coordinator.queue_realtime_tool_call(call)

        accepted = True
        caption = ""
        result: dict[str, Any] = {}
        try:
            admitted()
            kind = event.type
            if kind == "connected":
                state.connected = True
                state.secret = ""
                self.coordinator.set_realtime_state("listening", session_id=handle.voice_session_id)
            elif kind in {"disconnected", "fatal_error"}:
                with self.coordinator._dictation_lock:
                    self._transport._invalidate(lease)
            elif kind == "speech_started":
                self.coordinator.record_realtime_speech_started(session_id=handle.voice_session_id)
            elif kind == "speech_stopped":
                self.coordinator.record_realtime_speech_stopped(session_id=handle.voice_session_id)
            elif kind == "transcript_final":
                caption = self.coordinator.record_realtime_transcript(
                    event.text, item_id=event.item_id, session_id=handle.voice_session_id) or ""
                accepted = bool(caption)
                if caption and event.item_id != state.transcript_item:
                    state.consult_digest = state.consult_run_id = ""
                state.transcript, state.transcript_item = caption, event.item_id
                if caption:
                    state.front_user = f"{state.front_user} {caption}".strip()[:4000]
            elif kind == "assistant_transcript_final":
                self.coordinator.record_assistant_output(event.text, session_id=handle.voice_session_id)
                caption = event.text
                # Only the front's own speech is its line: Row-Bot's answers are saved by their runs.
                rid = event.response_id
                if (event.origin == "front" and rid and rid not in state.tool_responses and event.text.strip()
                        and (rid in state.front_text or len(state.front_text) < 50)):
                    state.front_text[rid] = f"{state.front_text.get(rid, '')} {event.text.strip()}".strip()[:4000]
            elif kind == "function_call_ready":
                if not event.call_id or event.call_id in state.calls:
                    raise DictationError("voice_event_consumed")
                if len(state.calls) >= 200:
                    raise DictationError("voice_session_limit")
                state.calls.add(event.call_id)
                self.coordinator.mark_realtime_latency("function_call_ready")
                # A response that calls a tool hands the turn over: its preamble is not a saved line,
                # and the person's words reach the chat through the consult (or were not addressed).
                if event.response_id:
                    state.tool_responses.add(event.response_id)
                    state.front_text.pop(event.response_id, None)
                state.front_user = ""
                if event.name == _CONSULT:
                    state.pending_call = event.call_id
                # The retained bridge owns allowed tool/consult policy. New-client
                # active-run effects belong to canonical command admission, never
                # its legacy in-memory voice_control_queue mutation path.
                bridge = VoiceAgentBridge(send_message=submit, active_generation=lambda: None,
                    surface="normal_chat", thread_id=owner.conversation_id)
                if event.name == "row_bot_agent_control":
                    parsed = bridge._parse_arguments(event.arguments)
                    action = str(parsed.get("action") or "status").strip().lower()
                    text = str(parsed.get("text") or "").strip()
                    result = {"output": json.dumps(control(action if action in {"status", "cancel"} else "input",
                                                           text or action), ensure_ascii=False)}
                else:
                    result = await bridge.handle_realtime_function_call(name=event.name, call_id=event.call_id,
                                                                        arguments=event.arguments, queue_consult=queue)
                state.pending_call = ""
                if control_result is not None:
                    result = {"output": json.dumps(control_result, ensure_ascii=False),
                              "silent": control_result.get("status") == "already_running"}
            elif kind in {"output_started", "output_item_started"}:
                self.coordinator.record_realtime_output_started(response_id=event.response_id,
                    output_item_id=event.output_item_id, session_id=handle.voice_session_id)
                if event.origin == "row_bot":
                    self.coordinator.mark_realtime_latency("first_answer_audio")
            elif kind == "provider_response_create_sent":
                if event.origin == "row_bot":
                    self.coordinator.mark_realtime_latency("provider_response_create")
            elif kind == "barge_in_cancelled":
                accepted = self.coordinator.record_barge_in(reason="user_speech_started", session_id=handle.voice_session_id)
            elif kind in {"response_done", "response_cancelled", "output_audio_done"}:
                self.coordinator.record_realtime_output_done(session_id=handle.voice_session_id)
                line = state.front_text.pop(event.response_id, "") if kind != "output_audio_done" else ""
                if line and event.response_id not in state.tool_responses and len(state.front_turns) < 50:
                    state.front_turns.append((state.front_user, line))
                    state.front_user = ""
            if state.front_turns and self._save_front is not None and self._active(owner) is None:
                # A run writes the chat while it works; the front's lines wait until none is active.
                turns, state.front_turns = state.front_turns, []
                self._save(owner, turns, admitted)
            output = str(result.get("output") or "")
            if len(output.encode("utf-8")) > 8192:
                raise DictationError("voice_output_too_large")
            if not lease.revoked:
                admitted()
            with self.coordinator._dictation_lock:
                lease.state = "completed"
                self._transport._release(lease, operation)
                response = RealtimeEventResult(self._snapshot(lease), event.event_id, accepted, caption,
                    event.call_id if output else "", output, bool(result.get("silent")))
                if not lease.revoked:
                    state.last_event, state.last_digest, state.last_result = event.event_id, digest, response
                return response
        except BaseException:
            with self.coordinator._dictation_lock:
                self._transport._invalidate(lease)
            raise
        finally:
            with self.coordinator._dictation_lock:
                self._transport._release(lease, operation)

    def _save(self, owner: DictationOwner, turns: list[tuple[str, str]], validate: Callable[[], None]) -> None:
        try:
            self._save_front(owner, turns, validate)
        except DictationError:
            raise
        except Exception:
            # A line that can't be saved never ends the conversation; it is logged, not retried.
            logger.warning("Live voice could not save %d front line(s)", len(turns), exc_info=True)

    def stop(self, owner: DictationOwner, handle: DictationHandle, *, validate: Callable[[], None]) -> RealtimeSnapshot:
        validate()
        turns: list[tuple[str, str]] = []
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            state = lease.realtime
            if (state is not None and state.front_turns and not lease.revoked and self._save_front is not None
                    and self._active(owner) is None):
                turns, state.front_turns = state.front_turns, []
            self._transport._invalidate(lease)
            end = self._live.get(handle.lease_id)
        if end is not None:
            end()
        self._tickets.drop(handle)
        if turns:
            self._save(owner, turns, validate)
        with self.coordinator._dictation_lock:
            return self._snapshot(lease)
