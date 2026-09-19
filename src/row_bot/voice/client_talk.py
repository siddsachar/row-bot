"""Talk over the coordinator's existing exclusive browser voice lease.

This adapter stores dependencies only. It never dispatches tools, starts a host
microphone, installs a model, or keeps audio after the admitted operation returns.
The injected submitter is the application's normal chat admission, including its
approval gates. Its validation callback must run inside that admission lock.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from row_bot.voice.browser_local import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MAX_TEXT_CHARS
from row_bot.voice.client_transport import (
    ClientDictationTransport, DictationError, DictationHandle, DictationOperation,
    DictationOwner, LEASE_SECONDS, _CONVERSATION,
)
from row_bot.voice.speech_policy import make_speakable_response

MAX_SESSION_SECONDS = 600.0
NO_SPEECH_SECONDS = 45.0


@dataclass(frozen=True)
class TalkSnapshot:
    schema_version: int
    handle: DictationHandle
    state: Literal["listening", "receiving", "transcribing", "thinking", "speaking", "stopping", "stopped"]
    quiesced: bool
    expires_in_ms: int
    run_id: str | None
    transport: Literal["browser_local"] = "browser_local"
    max_audio_bytes: int = MAX_INPUT_BYTES
    max_utterance_ms: int = 30_000


@dataclass(frozen=True)
class TalkResult:
    snapshot: TalkSnapshot
    utterance_id: str
    outcome: Literal["submitted", "no_speech"]
    text: str
    run_id: str | None


@dataclass(frozen=True)
class TalkSpeechSource:
    """Server-resolved final assistant output; never supplied by a media request."""
    text: str
    allow_long: bool = False


@dataclass(frozen=True)
class TalkAudio:
    snapshot: TalkSnapshot
    run_id: str
    output_id: str
    audio: bytes
    content_type: str = "audio/wav"


class ClientTalkTransport:
    def __init__(self, *, dictation: ClientDictationTransport,
                 submit: Callable[[DictationOwner, str, str, Callable[[], None]], str],
                 resolve_output: Callable[[DictationOwner, str, str, Callable[[], None]], TalkSpeechSource]) -> None:
        self._transport = dictation
        self._submit = submit
        self._resolve_output = resolve_output

    @property
    def coordinator(self):
        return self._transport.coordinator

    def _lease(self, owner: DictationOwner, handle: DictationHandle, *, active: bool = True):
        lease = self._transport._lease(owner, handle, active=active)
        if lease.mode != "talk" or lease.transport != "browser":
            raise DictationError("action_denied")
        if self._transport._clock() >= lease.started_at + MAX_SESSION_SECONDS:
            self._transport._invalidate(lease)
        if active and lease.revoked:
            raise DictationError("voice_session_expired")
        return lease

    def _snapshot(self, lease) -> TalkSnapshot:
        state = {"capturing": "listening", "completed": "listening"}.get(lease.state, lease.state)
        if not lease.revoked and lease.phase == "submit":
            state = "thinking"
        if not lease.revoked and lease.phase == "speech":
            state = "speaking"
        return TalkSnapshot(1, lease.handle, state,
                            not lease.starting and lease.operation is None,
                            max(0, int((min(lease.expires, lease.started_at + MAX_SESSION_SECONDS)
                                        - self._transport._clock()) * 1000)), lease.run_id or None)

    def start(self, owner: DictationOwner, *, request_id: str,
              validate: Callable[[], None], chat_context: dict | None = None) -> TalkSnapshot:
        result = self._transport._start(owner, request_id=request_id, validate=validate, mode="talk", _chat_context=chat_context)
        with self.coordinator._dictation_lock:
            return self._snapshot(self._lease(owner, result.handle))

    def snapshot(self, owner: DictationOwner, handle: DictationHandle, *,
                 validate: Callable[[], None], heartbeat: bool = False) -> TalkSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            if heartbeat and not lease.revoked:
                lease.expires = min(self._transport._clock() + LEASE_SECONDS,
                                    lease.started_at + MAX_SESSION_SECONDS)
            return self._snapshot(lease)

    def begin_receive(self, owner: DictationOwner, handle: DictationHandle, *,
                      utterance_id: str, content_type: str,
                      declared_size: int | None = None) -> DictationOperation:
        with self.coordinator._dictation_lock:
            self._lease(owner, handle)
            return self._transport.begin_receive(owner, handle, utterance_id=utterance_id,
                                                 content_type=content_type, declared_size=declared_size)

    def check_receive(self, operation: DictationOperation) -> None:
        with self.coordinator._dictation_lock:
            self._lease(operation.owner, operation.handle)
            self._transport.check_receive(operation)

    def abort_receive(self, operation: DictationOperation) -> None:
        self._transport.abort_receive(operation)

    def cancel_operation(self, operation: DictationOperation) -> None:
        self._transport.cancel_operation(operation)

    def complete_receive(self, operation: DictationOperation, *, audio: bytes,
                         validate: Callable[[], None]) -> TalkResult:
        def admitted() -> None:
            validate()
            with self.coordinator._dictation_lock:
                self._lease(operation.owner, operation.handle)

        result = self._transport.complete_receive(
            operation, audio=audio, validate=admitted,
            _accept=lambda text, guard: self._submit(operation.owner, text, operation.utterance_id, guard))
        with self.coordinator._dictation_lock:
            lease = self._lease(operation.owner, operation.handle)
            if not result.text and self._transport._clock() - lease.last_activity >= NO_SPEECH_SECONDS:
                self._transport._invalidate(lease)
            return TalkResult(self._snapshot(lease), operation.utterance_id,
                              "submitted" if result.text else "no_speech", result.text,
                              lease.run_id or None)

    def output(self, owner: DictationOwner, handle: DictationHandle, *,
               run_id: str, output_id: str, validate: Callable[[], None]) -> TalkAudio:
        for identifier in (run_id, output_id):
            if not isinstance(identifier, str) or not _CONVERSATION.fullmatch(identifier):
                raise DictationError("invalid_command")
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle)
            if lease.operation is not None or lease.starting:
                raise DictationError("voice_session_busy")
            if lease.run_id != run_id:
                raise DictationError("voice_run_changed")
            if run_id in lease.output_ids:
                raise DictationError("voice_output_consumed")
            # An uncertain synthesis/delivery cannot be safely replayed. Retain
            # only the consumed run identity, never private output or audio bytes.
            lease.output_ids.add(run_id)
            operation = DictationOperation(str(uuid4()), owner, handle, output_id, "audio/wav", False)
            lease.operation = operation
            lease.phase = "speech"

        def admitted() -> None:
            validate()
            with self.coordinator._dictation_lock:
                self._lease(owner, handle)
                self._transport._operation(operation)

        try:
            admitted()
            source = self._resolve_output(owner, run_id, output_id, admitted)
            if not isinstance(source, TalkSpeechSource) or not isinstance(source.text, str):
                raise DictationError("voice_output_unavailable")
            if len(source.text) > 256_000:
                raise DictationError("voice_output_too_large")
            text = make_speakable_response(source.text, allow_long=source.allow_long).text
            if len(text) > MAX_TEXT_CHARS:
                raise DictationError("voice_output_too_large")
            admitted()
            audio = self._transport._browser_service().synthesize(owner.client_session_id, text, validate=admitted)
            admitted()
            if not isinstance(audio, bytes) or not audio or len(audio) > MAX_OUTPUT_BYTES:
                raise DictationError("voice_output_unavailable")
            with self.coordinator._dictation_lock:
                self._transport._operation(operation)
                lease.last_activity = self._transport._clock()
                self.coordinator.record_assistant_output(text, session_id=handle.voice_session_id)
                lease.state = "completed"
                self._transport._release(lease, operation)
                return TalkAudio(self._snapshot(lease), run_id, output_id, audio)
        finally:
            with self.coordinator._dictation_lock:
                # Stop keeps this exact slot occupied until the synthesizer
                # returns; a late finally cannot clear a replacement session.
                if lease.operation is operation:
                    if not lease.revoked:
                        lease.state = "completed"
                    self._transport._release(lease, operation)

    def stop(self, owner: DictationOwner, handle: DictationHandle, *,
             validate: Callable[[], None]) -> TalkSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            self._transport._invalidate(lease)
            return self._snapshot(lease)
