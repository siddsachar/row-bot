"""Browser dictation over the existing coordinator and local speech owner.

The coordinator owns the single lease and operation slot. This adapter holds
only dependencies; microphone bytes and results never enter durable storage.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import re
import time
from typing import Literal, TYPE_CHECKING
from uuid import UUID, uuid4

from row_bot.voice.browser_local import (
    ALLOWED_AUDIO_TYPES, MAX_INPUT_BYTES, MAX_TEXT_CHARS,
    MAX_UTTERANCE_SECONDS, BrowserLocalVoiceService,
)
from row_bot.voice.coordinator import OutputActivityTracker, VoiceSessionCoordinator

if TYPE_CHECKING:
    from row_bot.voice.client_realtime import _RealtimeState

LEASE_SECONDS = 120.0
MAX_RECEIVE_SECONDS = 30.0
_CONVERSATION = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")


class DictationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DictationOwner:
    client_session_id: str
    access_binding: str
    conversation_id: str
    server_epoch: str


@dataclass(frozen=True)
class DictationHandle:
    lease_id: str
    voice_session_id: int
    conversation_id: str
    server_epoch: str


@dataclass(frozen=True)
class DictationCapability:
    schema_version: int = 1
    browser_dictation_available: bool = True
    native_capture_available: bool = False
    reason: Literal["available", "host_unavailable"] = "available"


@dataclass(frozen=True)
class DictationSnapshot:
    schema_version: int
    handle: DictationHandle
    state: Literal["capturing", "receiving", "transcribing", "completed", "stopping", "stopped"]
    quiesced: bool
    expires_in_ms: int
    max_audio_bytes: int = MAX_INPUT_BYTES
    max_utterance_ms: int = MAX_UTTERANCE_SECONDS * 1000


@dataclass(frozen=True)
class DictationResult:
    snapshot: DictationSnapshot
    utterance_id: str
    outcome: Literal["transcribed", "no_speech"]
    text: str


@dataclass(frozen=True)
class DictationOperation:
    token: str
    owner: DictationOwner
    handle: DictationHandle
    utterance_id: str
    content_type: str
    replay: bool


@dataclass
class _Lease:
    owner: DictationOwner
    request_id: str
    handle: DictationHandle
    expires: float
    state: str = "capturing"
    revoked: bool = False
    operation: DictationOperation | None = None
    phase: str = ""
    receive_deadline: float = 0.0
    digest: str = ""
    utterance_id: str = ""
    text: str = ""
    ready: bool = False
    starting: bool = True
    mode: str = "dictate"
    utterance_ids: set[str] = field(default_factory=set)
    output_ids: set[str] = field(default_factory=set)
    run_id: str = ""
    started_at: float = 0.0
    last_activity: float = 0.0
    transport: str = "browser"
    realtime: _RealtimeState | None = None
    chat_context: dict | None = field(default=None, repr=False)
    admitted_run: object | None = field(default=None, repr=False)


def _uuid(value: str) -> None:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise DictationError("invalid_command") from None


def _owner(owner: DictationOwner) -> None:
    if not isinstance(owner, DictationOwner):
        raise DictationError("action_denied")
    _uuid(owner.client_session_id)
    if (not isinstance(owner.access_binding, str) or not 1 <= len(owner.access_binding) <= 2048
            or not isinstance(owner.conversation_id, str) or not _CONVERSATION.fullmatch(owner.conversation_id)
            or not isinstance(owner.server_epoch, str) or not 1 <= len(owner.server_epoch) <= 128):
        raise DictationError("invalid_command")


class ClientDictationTransport:
    def __init__(self, *, coordinator: VoiceSessionCoordinator,
                 browser_service: Callable[[], BrowserLocalVoiceService],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.coordinator = coordinator
        self._browser_service = browser_service
        self._clock = clock
        with coordinator._dictation_lock:
            if coordinator._dictation_adapter is not None:
                raise DictationError("voice_session_busy")
            coordinator._dictation_adapter = self
            coordinator._dictation_clock = clock

    def capability(self) -> DictationCapability:
        return DictationCapability()

    def _invalidate(self, lease: _Lease) -> None:
        lease.revoked = True
        lease.chat_context = None
        lease.admitted_run = None
        lease.text = ""
        lease.digest = ""
        lease.run_id = ""
        if lease.realtime is not None:
            lease.realtime.clear_private()
        lease.state = "stopping" if lease.starting or lease.operation is not None else "stopped"
        if self.coordinator.session_id == lease.handle.voice_session_id:
            self.coordinator._active = False
            if lease.transport == "realtime":
                self.coordinator.realtime_state = "stopped"
                self.coordinator._reset_realtime_runtime_state()

    def _expire(self, lease: _Lease) -> None:
        if (self._clock() >= lease.expires or
                (lease.mode == "talk" and self._clock() >= lease.started_at + 600.0)):
            self._invalidate(lease)

    def _lease(self, owner: DictationOwner, handle: DictationHandle, *, active: bool = True) -> _Lease:
        lease = self.coordinator._dictation_lease
        if lease is None or lease.handle != handle:
            raise DictationError("voice_session_expired")
        if lease.owner != owner:
            raise DictationError("action_denied")
        self._expire(lease)
        if active and lease.revoked:
            raise DictationError("voice_session_expired")
        return lease

    def _snapshot(self, lease: _Lease) -> DictationSnapshot:
        return DictationSnapshot(1, lease.handle, lease.state, not lease.starting and lease.operation is None,
                                 max(0, int((lease.expires - self._clock()) * 1000)))

    def start(self, owner: DictationOwner, *, request_id: str,
              validate: Callable[[], None]) -> DictationSnapshot:
        return self._start(owner, request_id=request_id, validate=validate, mode="dictate")

    def _start(self, owner: DictationOwner, *, request_id: str,
               validate: Callable[[], None], mode: Literal["dictate", "talk"],
               _transport: Literal["browser", "realtime"] = "browser",
               _prepare: Callable[[Callable[[], None]], None] | None = None,
               _chat_context: dict | None = None) -> DictationSnapshot:
        _owner(owner)
        _uuid(request_id)
        validate()
        with self.coordinator._dictation_lock:
            if self.coordinator._dictation_closed:
                raise DictationError("voice_session_expired")
            old = self.coordinator._dictation_lease
            if old is not None:
                self._expire(old)
                if old.request_id == request_id:
                    if old.owner != owner or old.mode != mode or old.transport != _transport:
                        raise DictationError("idempotency_mismatch")
                    if old.revoked:
                        raise DictationError("voice_session_expired")
                    if old.chat_context != _chat_context:
                        raise DictationError("idempotency_mismatch")
                    if not old.ready:
                        raise DictationError("voice_session_busy")
                    return self._snapshot(old)
                if old.starting or old.operation is not None or (not old.revoked and
                        (old.mode == "talk" or old.state != "completed")):
                    raise DictationError("voice_session_busy")
            if self.coordinator._active or self.coordinator._legacy_transition_owner is not None:
                raise DictationError("voice_session_busy")
            self.coordinator._session_id += 1
            handle = DictationHandle(str(uuid4()), self.coordinator.session_id,
                                      owner.conversation_id, owner.server_epoch)
            lease = _Lease(owner, request_id, handle, self._clock() + LEASE_SECONDS)
            lease.mode = mode
            from copy import deepcopy
            lease.chat_context = deepcopy(_chat_context)
            lease.transport = _transport
            lease.started_at = lease.last_activity = self._clock()
            self.coordinator._dictation_lease = lease
            self.coordinator.mode = mode
            self.coordinator.transport = _transport
            self.coordinator._active = True
            if mode == "talk":
                self.coordinator.output_activity = OutputActivityTracker()
            if _transport == "realtime":
                self.coordinator._reset_realtime_runtime_state()
                self.coordinator._callback_thread_id = owner.conversation_id
                self.coordinator.realtime_state = "connecting"
        try:
            validate()
            if _prepare is not None:
                def admitted_validation() -> None:
                    validate()
                    with self.coordinator._dictation_lock:
                        self._lease(owner, handle)
                _prepare(admitted_validation)
            else:
                if mode == "talk":
                    self.coordinator._apply_selected_local_stt_model(mode)
                service = self._browser_service()
                if not service.voice_service.whisper_model_available():
                    raise DictationError("whisper_model_missing")
            validate()
            with self.coordinator._dictation_lock:
                self._lease(owner, handle).ready = True
                lease.starting = False
                return self._snapshot(lease)
        except BaseException:
            with self.coordinator._dictation_lock:
                if self.coordinator._dictation_lease is lease:
                    self._invalidate(lease)
            raise
        finally:
            with self.coordinator._dictation_lock:
                lease.starting = False
                if lease.revoked and lease.operation is None:
                    lease.state = "stopped"

    def snapshot(self, owner: DictationOwner, handle: DictationHandle, *,
                 validate: Callable[[], None]) -> DictationSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            return self._snapshot(self._lease(owner, handle, active=False))

    def begin_receive(self, owner: DictationOwner, handle: DictationHandle, *,
                      utterance_id: str, content_type: str,
                      declared_size: int | None = None) -> DictationOperation:
        _owner(owner)
        _uuid(utterance_id)
        if not isinstance(content_type, str) or len(content_type) > 256:
            raise DictationError("unsupported_audio_type")
        mime = content_type.partition(";")[0].strip().lower()
        if mime not in ALLOWED_AUDIO_TYPES:
            raise DictationError("unsupported_audio_type")
        if declared_size is not None:
            if type(declared_size) is not int or declared_size < 0:
                raise DictationError("invalid_command")
            if declared_size > MAX_INPUT_BYTES:
                raise DictationError("payload_too_large")
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle)
            if lease.transport != "browser":
                raise DictationError("action_denied")
            if not lease.ready:
                raise DictationError("voice_session_busy")
            if lease.operation is not None:
                raise DictationError("voice_session_busy")
            if lease.state not in {"capturing", "completed"}:
                raise DictationError("voice_session_expired")
            replay = lease.state == "completed"
            if lease.mode == "talk":
                if utterance_id in lease.utterance_ids and utterance_id != lease.utterance_id:
                    raise DictationError("utterance_expired")
                replay = utterance_id == lease.utterance_id and bool(lease.digest)
                if not replay and len(lease.utterance_ids) >= 200:
                    raise DictationError("voice_session_limit")
            operation = DictationOperation(str(uuid4()), owner, handle, utterance_id,
                                           mime, replay)
            lease.operation = operation
            lease.phase = "receive"
            lease.receive_deadline = min(lease.expires, self._clock() + MAX_RECEIVE_SECONDS)
            lease.state = "receiving"
            return operation

    def _operation(self, operation: DictationOperation, *, active: bool = True) -> _Lease:
        lease = self._lease(operation.owner, operation.handle, active=active)
        if lease.operation is not operation:
            raise DictationError("voice_session_expired")
        return lease

    def check_receive(self, operation: DictationOperation) -> None:
        with self.coordinator._dictation_lock:
            lease = self._operation(operation)
            if lease.phase != "receive":
                raise DictationError("voice_session_expired")
            if self._clock() >= lease.receive_deadline:
                self._invalidate(lease)
                raise DictationError("audio_receive_timeout")

    def _release(self, lease: _Lease, operation: DictationOperation) -> None:
        if lease.operation is not operation:
            return
        lease.operation = None
        lease.phase = ""
        if lease.revoked:
            lease.state = "stopped"
        elif lease.mode == "talk" and (lease.state == "completed" or (operation.replay and lease.digest)):
            lease.state = "capturing"
        elif operation.replay and lease.digest:
            lease.state = "completed"
        elif lease.state != "completed":
            self._invalidate(lease)

    def abort_receive(self, operation: DictationOperation) -> None:
        with self.coordinator._dictation_lock:
            lease = self.coordinator._dictation_lease
            if lease is not None and lease.operation is operation and lease.phase == "receive":
                self._expire(lease)
                self._release(lease, operation)

    def cancel_operation(self, operation: DictationOperation) -> None:
        with self.coordinator._dictation_lock:
            lease = self.coordinator._dictation_lease
            if (lease is not None and lease.owner == operation.owner and lease.handle == operation.handle
                    and (lease.operation is operation or lease.operation is None)):
                self._invalidate(lease)

    def complete_receive(self, operation: DictationOperation, *, audio: bytes,
                         validate: Callable[[], None],
                         _accept: Callable[[str, Callable[[], None]], str] | None = None) -> DictationResult:
        lease = None
        try:
            with self.coordinator._dictation_lock:
                # Even cancelled queued workers must acknowledge their exact slot.
                lease = self._operation(operation, active=False)
                if lease.phase != "receive":
                    lease = None
                    raise DictationError("voice_session_busy")
                self.check_receive(operation)
                if lease.mode == "talk" and _accept is None:
                    raise DictationError("action_denied")
                lease.phase = "worker"
                lease.state = "transcribing"
            if not isinstance(audio, bytes) or not audio:
                raise DictationError("empty_audio")
            if len(audio) > MAX_INPUT_BYTES:
                raise DictationError("payload_too_large")
            validate()
            digest = hashlib.sha256(operation.content_type.encode() + b"\0" + audio).hexdigest()
            with self.coordinator._dictation_lock:
                self._operation(operation)
                if operation.replay:
                    if lease.digest != digest or lease.utterance_id != operation.utterance_id:
                        raise DictationError("idempotency_mismatch")
                    text = lease.text
                else:
                    text = None
            if text is None:
                def admitted_validation() -> None:
                    validate()
                    with self.coordinator._dictation_lock:
                        self._operation(operation)
                admitted_validation()
                text = self._browser_service().transcribe(operation.owner.client_session_id, audio,
                                                         operation.content_type, validate=admitted_validation)
                if not isinstance(text, str):
                    raise DictationError("voice_operation_failed")
                if len(text) > MAX_TEXT_CHARS:
                    raise DictationError("transcript_too_large")
                text = text.strip()
                if lease.mode == "talk":
                    with self.coordinator._dictation_lock:
                        self._operation(operation)
                        if self.coordinator._should_drop_transcript(text):
                            text = ""
            validate()
            if lease.mode == "talk" and not operation.replay:
                def accept_validation() -> None:
                    validate()
                    with self.coordinator._dictation_lock:
                        self._operation(operation)
                accept_validation()
                # Reserve the identity before normal chat admission. A callback
                # failure after admission must never lead to automatic resubmission.
                with self.coordinator._dictation_lock:
                    lease.utterance_ids.add(operation.utterance_id)
                    lease.phase = "submit"
                run_id = _accept(text, accept_validation) if text else ""
                accept_validation()
                if not isinstance(run_id, str) or (text and not _CONVERSATION.fullmatch(run_id)):
                    raise DictationError("voice_admission_failed")
                with self.coordinator._dictation_lock:
                    self._operation(operation)
                    lease.run_id = run_id
                    if text:
                        lease.last_activity = self._clock()
            with self.coordinator._dictation_lock:
                self._operation(operation)
                lease.text = text
                lease.digest = digest
                lease.utterance_id = operation.utterance_id
                if not operation.replay:
                    lease.expires = self._clock() + LEASE_SECONDS
                lease.state = "completed"
                self.coordinator._active = lease.mode == "talk"
                self._release(lease, operation)
                return DictationResult(self._snapshot(lease), operation.utterance_id,
                                        "transcribed" if text else "no_speech", text)
        except BaseException as exc:
            # Only a mismatched replay retains the previously authorized result.
            if lease is not None and not (isinstance(exc, DictationError)
                                           and exc.code == "idempotency_mismatch"):
                with self.coordinator._dictation_lock:
                    if self.coordinator._dictation_lease is lease:
                        self._invalidate(lease)
            raise
        finally:
            if lease is not None:
                with self.coordinator._dictation_lock:
                    if self.coordinator._dictation_lease is lease:
                        self._release(lease, operation)

    def stop(self, owner: DictationOwner, handle: DictationHandle, *,
             validate: Callable[[], None]) -> DictationSnapshot:
        validate()
        with self.coordinator._dictation_lock:
            lease = self._lease(owner, handle, active=False)
            self._invalidate(lease)
            return self._snapshot(lease)

    def close(self) -> bool:
        with self.coordinator._dictation_lock:
            self.coordinator._dictation_closed = True
            lease = self.coordinator._dictation_lease
            if lease is not None:
                self._invalidate(lease)
            return lease is None or (not lease.starting and lease.operation is None)
