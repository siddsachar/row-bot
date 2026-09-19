"""Single-owner receive/STT admission with isolated fake voice dependencies."""
from dataclasses import replace
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.voice.client_transport import (
    ClientDictationTransport, DictationError, DictationOwner, MAX_INPUT_BYTES,
)
from row_bot.voice.coordinator import VoiceSessionCoordinator


class Voice:
    is_running = False
    state = "stopped"

    def __init__(self):
        self.effects = []
        self.ready = True

    def whisper_model_available(self):
        return self.ready

    def start(self):
        self.effects.append("start")
        self.is_running = True
        self.state = "listening"

    def stop(self):
        self.effects.append("stop")
        self.is_running = False
        self.state = "stopped"

    def mute(self):
        self.effects.append("mute")

    def unmute(self):
        self.effects.append("unmute")

    def get_transcription(self):
        self.effects.append("transcript")
        return "legacy text"


@pytest.fixture
def runtime():
    clock = [0.0]
    voice = Voice()
    coordinator = VoiceSessionCoordinator(voice)
    calls, factories = [], []

    def transcribe(key, audio, mime, *, validate):
        validate()
        calls.append((key, audio, mime))
        return "  dictation result  "

    service = SimpleNamespace(voice_service=voice, transcribe=transcribe)

    def factory():
        factories.append(True)
        return service

    adapter = ClientDictationTransport(coordinator=coordinator, browser_service=factory,
                                       clock=lambda: clock[0])
    owner = DictationOwner(str(uuid4()), "local:https://isolated.test", "conversation-A", "epoch")
    return SimpleNamespace(adapter=adapter, coordinator=coordinator, voice=voice, service=service,
                           calls=calls, factories=factories, clock=clock, owner=owner)


def start(runtime):
    return runtime.adapter.start(runtime.owner, request_id=str(uuid4()), validate=lambda: None)


def receive(runtime, snapshot, **kwargs):
    return runtime.adapter.begin_receive(runtime.owner, snapshot.handle,
                                          utterance_id=kwargs.pop("utterance_id", str(uuid4())),
                                          content_type=kwargs.pop("content_type", "audio/webm"), **kwargs)


def finish(runtime, operation, **kwargs):
    return runtime.adapter.complete_receive(operation, audio=kwargs.pop("audio", b"synthetic audio"),
                                             validate=kwargs.pop("validate", lambda: None), **kwargs)


def test_capability_construction_does_not_initialize_or_start(runtime):
    assert runtime.adapter.capability().browser_dictation_available
    assert not runtime.factories and not runtime.voice.effects
    snapshot = start(runtime)
    assert snapshot.state == "capturing" and snapshot.quiesced
    assert runtime.coordinator.transport == "browser"
    assert not runtime.voice.effects and not runtime.voice.is_running


def test_exact_coordinator_cannot_acquire_a_competing_adapter(runtime):
    with pytest.raises(DictationError, match="voice_session_busy"):
        ClientDictationTransport(coordinator=runtime.coordinator, browser_service=lambda: runtime.service)


def test_start_replay_same_identity_and_no_extra_initialization(runtime):
    request = str(uuid4())
    first = runtime.adapter.start(runtime.owner, request_id=request, validate=lambda: None)
    assert runtime.adapter.start(runtime.owner, request_id=request, validate=lambda: None) == first
    assert len(runtime.factories) == 1
    with pytest.raises(DictationError, match="idempotency_mismatch"):
        runtime.adapter.start(replace(runtime.owner, conversation_id="other"), request_id=request,
                              validate=lambda: None)


@pytest.mark.parametrize("method", ["start_talk", "start_dictation", "start_realtime_talk", "start_browser"])
def test_legacy_start_cannot_take_api_owner(runtime, method):
    first = start(runtime)
    with pytest.raises(DictationError, match="voice_session_busy"):
        getattr(runtime.coordinator, method)()
    assert runtime.adapter.snapshot(runtime.owner, first.handle, validate=lambda: None) == first
    assert not runtime.voice.effects


def test_legacy_controls_do_not_stop_or_drain_api_voice(runtime):
    first = start(runtime)
    runtime.coordinator.stop()
    runtime.coordinator.mute()
    runtime.coordinator.unmute()
    assert runtime.coordinator.get_transcription() is None
    assert runtime.adapter.snapshot(runtime.owner, first.handle, validate=lambda: None).state == "capturing"
    assert not runtime.voice.effects


def test_unleased_legacy_semantics_and_api_busy(runtime):
    runtime.coordinator.start_talk()
    with pytest.raises(DictationError, match="voice_session_busy"):
        start(runtime)
    runtime.coordinator.start_dictation()
    runtime.coordinator.stop()
    assert runtime.voice.effects == ["start", "stop", "start", "stop"]
    assert start(runtime).state == "capturing"


def test_receive_reserves_before_any_audio_and_stop_waits_for_reader(runtime):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)
    view = runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None)
    assert view.state == "receiving" and not view.quiesced
    with pytest.raises(DictationError, match="voice_session_busy"):
        receive(runtime, snapshot)
    stopped = runtime.adapter.stop(runtime.owner, snapshot.handle, validate=lambda: None)
    assert stopped.state == "stopping" and not stopped.quiesced
    with pytest.raises(DictationError, match="voice_session_busy"):
        start(runtime)
    runtime.adapter.abort_receive(operation)
    assert runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced
    assert start(runtime).state == "capturing"
    assert not runtime.calls


@pytest.mark.parametrize("field,value", [("client_session_id", str(uuid4())), ("access_binding", "another"),
                                        ("conversation_id", "B"), ("server_epoch", "other-epoch")])
def test_private_owner_mismatch_denied_before_work(runtime, field, value):
    snapshot = start(runtime)
    with pytest.raises(DictationError, match="action_denied"):
        runtime.adapter.begin_receive(replace(runtime.owner, **{field: value}), snapshot.handle,
                                        utterance_id=str(uuid4()), content_type="audio/webm")
    assert not runtime.calls


@pytest.mark.parametrize("size,error", [(-1, "invalid_command"), (True, "invalid_command"),
                                        (MAX_INPUT_BYTES + 1, "payload_too_large")])
def test_header_validation_before_receive_slot(runtime, size, error):
    snapshot = start(runtime)
    with pytest.raises(DictationError, match=error):
        receive(runtime, snapshot, declared_size=size)
    assert runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced


@pytest.mark.parametrize("mime", ["audio/unknown", "text/html", "", "x" * 257])
def test_unsupported_mime_before_admission(runtime, mime):
    snapshot = start(runtime)
    with pytest.raises(DictationError, match="unsupported_audio_type"):
        receive(runtime, snapshot, content_type=mime)


def test_receive_deadline_retains_slot_until_acknowledged(runtime):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)
    runtime.clock[0] = 30
    with pytest.raises(DictationError, match="audio_receive_timeout"):
        runtime.adapter.check_receive(operation)
    assert not runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced
    runtime.adapter.abort_receive(operation)
    assert start(runtime).quiesced


def test_expiry_does_not_release_receiving_operation(runtime):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)
    runtime.clock[0] = 121
    with pytest.raises(DictationError, match="voice_session_busy"):
        start(runtime)
    runtime.adapter.abort_receive(operation)
    assert start(runtime).quiesced


def test_exact_result_replay_does_not_repeat_stt(runtime):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot, content_type="Audio/WebM;codecs=opus")
    result = finish(runtime, operation)
    assert result.text == "dictation result" and result.snapshot.quiesced
    assert result.snapshot.state == "completed"
    replay = receive(runtime, snapshot, utterance_id=operation.utterance_id)
    assert replay.replay
    with pytest.raises(DictationError, match="voice_session_busy"):
        receive(runtime, snapshot)
    assert finish(runtime, replay).text == result.text
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("change", ["bytes", "mime", "utterance"])
def test_mismatched_replay_preserves_original_result(runtime, change):
    snapshot = start(runtime)
    first = receive(runtime, snapshot)
    finish(runtime, first)
    replay = receive(runtime, snapshot, utterance_id=str(uuid4()) if change == "utterance" else first.utterance_id,
                      content_type="audio/ogg" if change == "mime" else "audio/webm")
    with pytest.raises(DictationError, match="idempotency_mismatch"):
        finish(runtime, replay, audio=b"changed" if change == "bytes" else b"synthetic audio")
    retry = receive(runtime, snapshot, utterance_id=first.utterance_id)
    assert finish(runtime, retry).text == "dictation result"
    assert len(runtime.calls) == 1


def test_aborted_replay_preserves_result_but_cancel_discards_it(runtime):
    snapshot = start(runtime)
    first = receive(runtime, snapshot)
    finish(runtime, first)
    replay = receive(runtime, snapshot, utterance_id=first.utterance_id)
    runtime.adapter.abort_receive(replay)
    replay = receive(runtime, snapshot, utterance_id=first.utterance_id)
    runtime.adapter.cancel_operation(replay)
    runtime.adapter.abort_receive(replay)
    with pytest.raises(DictationError, match="voice_session_expired"):
        receive(runtime, snapshot)


def test_old_completion_token_cannot_cancel_new_lease(runtime):
    first = start(runtime)
    operation = receive(runtime, first)
    finish(runtime, operation)
    second = start(runtime)
    runtime.adapter.cancel_operation(operation)
    runtime.adapter.abort_receive(operation)
    assert runtime.adapter.snapshot(runtime.owner, second.handle, validate=lambda: None).state == "capturing"


def test_cancelled_queued_worker_acknowledges_without_stt(runtime):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)
    runtime.adapter.cancel_operation(operation)
    with pytest.raises(DictationError, match="voice_session_expired"):
        finish(runtime, operation)
    assert runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced
    assert not runtime.calls


def test_stt_stop_keeps_real_worker_slot_until_return(runtime):
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def transcribe(*args, validate):
        validate()
        entered.set()
        assert release.wait(3)
        return "late private result"

    runtime.service.transcribe = transcribe
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)

    def worker():
        try:
            outcomes.append(finish(runtime, operation))
        except DictationError as exc:
            outcomes.append(exc.code)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        runtime.adapter.cancel_operation(operation)
        runtime.adapter.abort_receive(operation)  # Reader cleanup cannot release a worker.
        assert not runtime.adapter.close()
        with pytest.raises(DictationError, match="voice_session_busy"):
            runtime.coordinator.start_talk()
        assert not runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert outcomes == ["voice_session_expired"]
    assert runtime.adapter.close()
    assert runtime.coordinator._dictation_lease.text == ""


@pytest.mark.parametrize("failure", ["validate", "service", "oversize", "empty"])
def test_worker_failure_acknowledges_without_persisting_audio(runtime, failure):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)

    def fail():
        raise DictationError("capability_revoked")

    if failure == "service":
        runtime.service.transcribe = lambda *a, **k: fail()
    with pytest.raises(DictationError):
        finish(runtime, operation, audio=b"" if failure == "empty" else
               b"x" * (MAX_INPUT_BYTES + 1) if failure == "oversize" else b"synthetic audio",
               validate=fail if failure == "validate" else lambda: None)
    assert runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced
    assert runtime.coordinator._dictation_lease.text == ""
    assert not any(isinstance(value, bytes) for value in vars(runtime.coordinator._dictation_lease).values())


def test_transcript_size_rejected_without_truncation(runtime):
    runtime.service.transcribe = lambda *args, **kwargs: "x" * 4001
    snapshot = start(runtime)
    with pytest.raises(DictationError, match="transcript_too_large"):
        finish(runtime, receive(runtime, snapshot))


def test_missing_model_releases_initial_lease_without_device_start(runtime):
    runtime.voice.ready = False
    with pytest.raises(DictationError, match="whisper_model_missing"):
        start(runtime)
    runtime.voice.ready = True
    assert start(runtime).quiesced
    assert not runtime.voice.effects


def test_legacy_start_expires_completed_api_replay(runtime):
    first = start(runtime)
    operation = receive(runtime, first)
    finish(runtime, operation)
    runtime.coordinator.start_browser("talk")
    with pytest.raises(DictationError, match="voice_session_expired"):
        receive(runtime, first, utterance_id=operation.utterance_id)
    assert runtime.coordinator.is_running and runtime.coordinator.mode == "talk"


def test_start_replay_cannot_publish_handle_during_readiness(runtime):
    entered, release = threading.Event(), threading.Event()
    outcomes = []
    request = str(uuid4())

    def ready():
        entered.set()
        assert release.wait(3)
        return True

    runtime.voice.whisper_model_available = ready
    thread = threading.Thread(target=lambda: outcomes.append(runtime.adapter.start(
        runtime.owner, request_id=request, validate=lambda: None)))
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(DictationError, match="voice_session_busy"):
            runtime.adapter.start(runtime.owner, request_id=request, validate=lambda: None)
        with pytest.raises(DictationError, match="voice_session_busy"):
            runtime.coordinator.start_talk()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and len(outcomes) == 1


@pytest.mark.parametrize("termination", ["expiry", "stop", "close"])
def test_readiness_retains_ownership_until_actual_return(runtime, termination):
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def ready():
        entered.set()
        assert release.wait(3)
        return True

    def run():
        try:
            start(runtime)
        except DictationError as exc:
            outcomes.append(exc.code)

    runtime.voice.whisper_model_available = ready
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        handle = runtime.coordinator._dictation_lease.handle
        if termination == "expiry":
            runtime.clock[0] += 121
        elif termination == "stop":
            runtime.adapter.stop(runtime.owner, handle, validate=lambda: None)
        else:
            assert not runtime.adapter.close()
        snapshot = runtime.adapter.snapshot(runtime.owner, handle, validate=lambda: None)
        assert snapshot.state == "stopping" and not snapshot.quiesced
        with pytest.raises(DictationError, match="voice_session_busy"):
            runtime.coordinator.start_talk()
        expected = "voice_session_expired" if termination == "close" else "voice_session_busy"
        with pytest.raises(DictationError, match=expected):
            start(runtime)
        # Admission stays responsive while the readiness dependency is blocked.
        assert runtime.coordinator._dictation_lock.acquire(blocking=False)
        runtime.coordinator._dictation_lock.release()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and outcomes == ["voice_session_expired"]
    snapshot = runtime.adapter.snapshot(runtime.owner, handle, validate=lambda: None)
    assert snapshot.state == "stopped" and snapshot.quiesced
    assert runtime.adapter.close()
    runtime.coordinator.start_talk()
    assert runtime.voice.effects == ["start"]


def test_failed_readiness_releases_start_reservation(runtime):
    def fail():
        raise RuntimeError("synthetic readiness failure")

    runtime.voice.whisper_model_available = fail
    with pytest.raises(RuntimeError, match="synthetic readiness failure"):
        start(runtime)
    lease = runtime.coordinator._dictation_lease
    snapshot = runtime.adapter.snapshot(runtime.owner, lease.handle, validate=lambda: None)
    assert snapshot.state == "stopped" and snapshot.quiesced
    runtime.voice.whisper_model_available = lambda: True
    replacement = start(runtime)
    assert replacement.state == "capturing" and replacement.handle != lease.handle


def test_replay_revocation_discards_previously_cached_text(runtime):
    snapshot = start(runtime)
    operation = receive(runtime, snapshot)
    finish(runtime, operation)
    replay = receive(runtime, snapshot, utterance_id=operation.utterance_id)

    def revoked():
        raise DictationError("capability_revoked")

    with pytest.raises(DictationError, match="capability_revoked"):
        finish(runtime, replay, validate=revoked)
    assert runtime.coordinator._dictation_lease.text == ""
    assert runtime.coordinator._dictation_lease.digest == ""
    assert runtime.adapter.snapshot(runtime.owner, snapshot.handle, validate=lambda: None).quiesced


def test_blocked_legacy_shutdown_reserves_owner_without_holding_admission_lock(runtime):
    entered, release = threading.Event(), threading.Event()
    runtime.coordinator.start_talk()
    original_stop = runtime.voice.stop

    def stop_voice():
        entered.set()
        assert release.wait(3)
        original_stop()

    runtime.voice.stop = stop_voice
    thread = threading.Thread(target=runtime.coordinator.stop)
    thread.start()
    try:
        assert entered.wait(3)
        assert runtime.coordinator._dictation_lock.acquire(blocking=False)
        runtime.coordinator._dictation_lock.release()
        with pytest.raises(DictationError, match="voice_session_busy"):
            start(runtime)
        with pytest.raises(DictationError, match="voice_session_busy"):
            runtime.coordinator.start_browser()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert start(runtime).state == "capturing"


def test_legacy_exception_releases_transition_reservation(runtime):
    def fail_start():
        raise RuntimeError("device unavailable")

    runtime.voice.start = fail_start
    with pytest.raises(RuntimeError, match="device unavailable"):
        runtime.coordinator.start_talk()
    assert runtime.coordinator._legacy_transition_owner is None
    runtime.coordinator.stop()
    assert start(runtime).state == "capturing"


@pytest.mark.parametrize("revoke_at", ["admission", "decoded"])
def test_real_browser_service_revalidates_before_decode_and_stt(runtime, revoke_at):
    from row_bot.voice.browser_local import BrowserLocalVoiceService

    effects = []
    revoked = [False]

    def runner(*args, **kwargs):
        effects.append("decode")
        revoked[0] = revoke_at == "decoded"
        return SimpleNamespace(returncode=0, stdout=b"\0" * 3200, stderr=b"")

    def recognize(pcm, *, allow_download):
        assert allow_download is False
        effects.append("stt")
        return "recognized"

    runtime.voice.transcribe_pcm16 = recognize
    service = BrowserLocalVoiceService(voice_service=runtime.voice, tts_service=object(),
                                       runner=runner, ffmpeg_path="synthetic-ffmpeg")
    original_job = service._job

    class Admission:
        def __enter__(self):
            self.job = original_job("isolated")
            self.job.__enter__()
            revoked[0] = revoke_at == "admission"

        def __exit__(self, *args):
            return self.job.__exit__(*args)

    service._job = lambda key: Admission()

    def validate():
        if revoked[0]:
            raise DictationError("capability_revoked")

    with pytest.raises(DictationError, match="capability_revoked"):
        service.transcribe("isolated", b"encoded", "audio/webm", validate=validate)
    assert effects == ([] if revoke_at == "admission" else ["decode"])
    # Both the existing session lock and global concurrency slot were released.
    assert service._lock_for("isolated").acquire(blocking=False)
    service._lock_for("isolated").release()
    assert service._global_slots.acquire(blocking=False)
    assert service._global_slots.acquire(blocking=False)
    service._global_slots.release()
    service._global_slots.release()
