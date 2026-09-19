"""Authenticated realtime transport over the existing coordinator/agent bridge.

All transient state lives in the coordinator lease. Provider credentials never
enter command receipts, diagnostics or saved conversation data. The injected
application callbacks keep normal chat admission and cancellation authority.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
import time
from typing import Any
from uuid import uuid4

from row_bot.voice.agent_bridge import VoiceAgentBridge
from row_bot.voice.client_transport import (
    ClientDictationTransport, DictationError, DictationHandle, DictationOperation,
    DictationOwner, LEASE_SECONDS, _CONVERSATION, _uuid,
)

_EVENTS = frozenset({"connected", "disconnected", "fatal_error", "speech_started", "speech_stopped",
                     "transcript_final", "assistant_transcript_final", "function_call_ready",
                     "consult_fallback_needed", "output_started", "output_item_started", "response_done",
                     "response_cancelled", "output_audio_done", "barge_in_cancelled"})


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
    client_secret: str | None = field(repr=False)
    secret_expires_in_ms: int


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

    def clear_private(self) -> None:
        self.secret = self.transcript = self.transcript_item = self.last_digest = ""
        self.consult_digest = self.consult_run_id = ""
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
    def __init__(self, *, dictation: ClientDictationTransport, provider_factory: Callable[[], Any],
                 submit: Callable[[DictationOwner, str, str, Callable[[], None]], str],
                 active_generation: Callable[[DictationOwner], Any],
                 control_generation: Callable[[DictationOwner, Any, str, str, str, Callable[[], None]], dict],
                 wall_clock: Callable[[], float] = time.time) -> None:
        self._transport = dictation
        self._provider = provider_factory
        self._submit = submit
        self._active = active_generation
        self._control = control_generation
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
        return RealtimeSnapshot(1, lease.handle,
            ("stopping" if lease.starting or lease.operation else "stopped") if lease.revoked
            else self.coordinator.realtime_state,
            not lease.starting and lease.operation is None,
            max(0, int((min(lease.expires, lease.started_at + 600) - self._transport._clock()) * 1000)),
            lease.run_id or None)

    def start(self, owner: DictationOwner, *, request_id: str, validate: Callable[[], None], chat_context: dict | None = None) -> RealtimeStart:
        def prepare(admitted: Callable[[], None]) -> None:
            admitted()
            try:
                provider = self._provider()
            except DictationError:
                raise
            except Exception as exc:
                raise _provider_error(exc) from None
            admitted()
            try:
                value = provider.create_client_secret(expires_after_seconds=60)
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
            generation = self._active(owner)
            admitted()
            with self.coordinator._dictation_lock:
                lease = self.coordinator._dictation_lease
                lease.realtime = _RealtimeState(secret, self._transport._clock() + remaining, provider=provider)
                lease.run_id = _generation_id(generation)
                self.coordinator.set_active_row_bot_generation(lease.run_id)

        snapshot = self._transport._start(owner, request_id=request_id, validate=validate,
                                           mode="talk", _transport="realtime", _prepare=prepare, _chat_context=chat_context)
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, snapshot.handle)
            state = lease.realtime
            return RealtimeStart(self._snapshot(lease), state.secret or None,
                                 max(0, int((state.secret_expires - self._transport._clock()) * 1000)))

    def snapshot(self, owner: DictationOwner, handle: DictationHandle, *, validate: Callable[[], None],
                 heartbeat: bool = False) -> RealtimeSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            if heartbeat and not lease.revoked:
                lease.expires = min(self._transport._clock() + LEASE_SECONDS, lease.started_at + 600)
            return self._snapshot(lease)

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
                not isinstance(event.arguments, str) or len(event.arguments) > 8192):
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

        def admitted() -> None:
            validate()
            with self.coordinator._dictation_lock:
                self._lease(owner, handle)
                self._transport._operation(operation)
            if self._active(owner) is not generation:
                raise DictationError("voice_run_changed")

        control_result: dict | None = None

        def control(action: str, text: str) -> dict:
            nonlocal generation, control_result
            admitted()
            if len(text) > 4000:
                raise DictationError("voice_event_too_large")
            control_result = self._control(owner, generation, action, text, event.event_id, admitted)
            validate()
            current = self._active(owner)
            if current is None and isinstance(control_result, dict) and control_result.get("control") == "cancel":
                generation = None
            elif current is not generation:
                raise DictationError("voice_run_changed")
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
                self.coordinator.set_active_row_bot_generation(run_id)

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
            elif kind == "assistant_transcript_final":
                self.coordinator.record_assistant_output(event.text, session_id=handle.voice_session_id)
                caption = event.text
            elif kind in {"function_call_ready", "consult_fallback_needed"}:
                if kind == "function_call_ready":
                    if not event.call_id or event.call_id in state.calls:
                        raise DictationError("voice_event_consumed")
                    if len(state.calls) >= 200:
                        raise DictationError("voice_session_limit")
                    state.calls.add(event.call_id)
                elif not state.transcript or event.text != state.transcript or event.item_id != state.transcript_item:
                    raise DictationError("voice_transcript_changed")
                # The retained bridge owns allowed tool/consult policy. New-client
                # active-run effects belong to canonical command admission, never
                # its legacy in-memory voice_control_queue mutation path.
                bridge = VoiceAgentBridge(send_message=submit, active_generation=lambda: None,
                    surface="normal_chat", thread_id=owner.conversation_id)
                if kind == "function_call_ready" and event.name == "row_bot_agent_control":
                    parsed = bridge._parse_arguments(event.arguments)
                    action = str(parsed.get("action") or "status").strip().lower()
                    text = str(parsed.get("text") or "").strip()
                    result = {"output": json.dumps(control(action if action in {"status", "cancel"} else "input",
                                                           text or action), ensure_ascii=False)}
                elif kind == "function_call_ready":
                    result = await bridge.handle_realtime_function_call(name=event.name, call_id=event.call_id,
                                                                        arguments=event.arguments, queue_consult=queue)
                else:
                    result = await bridge.force_consult_if_substantive(event.text, queue_consult=queue)
                    state.transcript = ""
                if control_result is not None:
                    result = {"output": json.dumps(control_result, ensure_ascii=False),
                              "silent": control_result.get("status") == "already_running"}
            elif kind in {"output_started", "output_item_started"}:
                self.coordinator.record_realtime_output_started(response_id=event.response_id,
                    output_item_id=event.output_item_id, session_id=handle.voice_session_id)
            elif kind == "barge_in_cancelled":
                accepted = self.coordinator.record_barge_in(reason="user_speech_started", session_id=handle.voice_session_id)
            elif kind in {"response_done", "response_cancelled", "output_audio_done"}:
                self.coordinator.record_realtime_output_done(session_id=handle.voice_session_id)
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

    def stop(self, owner: DictationOwner, handle: DictationHandle, *, validate: Callable[[], None]) -> RealtimeSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            self._transport._invalidate(lease)
            return self._snapshot(lease)
