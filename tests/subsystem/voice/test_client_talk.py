"""Browser Talk uses the existing coordinator without host devices/providers."""
from dataclasses import replace
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.voice.client_talk import ClientTalkTransport, TalkSpeechSource
from row_bot.voice.client_transport import ClientDictationTransport, DictationError, DictationOwner
from row_bot.voice.coordinator import VoiceSessionCoordinator


@pytest.fixture
def runtime():
    clock = [0.0]
    calls = []
    voice = SimpleNamespace(is_running=False, whisper_model_available=lambda: True)

    def transcribe(*args, validate):
        validate()
        calls.append("stt")
        return "Hello from Talk"

    def synthesize(key, text, *, validate):
        validate()
        calls.append(("speech", text))
        return b"RIFF-synthetic"

    def submit(owner, text, utterance, validate):
        validate()
        calls.append(("submit", owner.conversation_id, text, utterance))
        return "run-" + utterance

    coordinator = VoiceSessionCoordinator(voice)
    service = SimpleNamespace(voice_service=voice, transcribe=transcribe, synthesize=synthesize)
    dictation = ClientDictationTransport(coordinator=coordinator, browser_service=lambda: service,
                                         clock=lambda: clock[0])
    talk = ClientTalkTransport(dictation=dictation, submit=submit,
                               resolve_output=lambda *args: TalkSpeechSource("**Saved answer.**"))
    owner = DictationOwner(str(uuid4()), "synthetic-owner", "conversation-A", "epoch")
    return SimpleNamespace(talk=talk, dictation=dictation, coordinator=coordinator,
                           owner=owner, service=service, calls=calls, clock=clock)


def start(r):
    return r.talk.start(r.owner, request_id=str(uuid4()), validate=lambda: None)


def receive(r, snapshot, utterance=None):
    return r.talk.begin_receive(r.owner, snapshot.handle, utterance_id=utterance or str(uuid4()),
                                content_type="audio/webm")


def finish(r, op, **kwargs):
    return r.talk.complete_receive(op, audio=kwargs.pop("audio", b"synthetic"),
                                   validate=kwargs.pop("validate", lambda: None), **kwargs)


def test_passive_construction_and_one_shared_owner(runtime):
    r = runtime
    assert not r.calls and r.coordinator._dictation_lease is None
    first = start(r)
    assert first.state == "listening" and first.quiesced
    assert r.coordinator.is_running and not r.coordinator.voice_service.is_running
    assert r.coordinator.mode == "talk"
    with pytest.raises(DictationError, match="voice_session_busy"):
        r.dictation.start(r.owner, request_id=str(uuid4()), validate=lambda: None)
    for method in (r.coordinator.start_talk, r.coordinator.start_realtime_talk,
                   r.coordinator.start_dictation, r.coordinator.start_browser):
        with pytest.raises(DictationError, match="voice_session_busy"):
            method()
    r.coordinator.stop()
    assert r.talk.snapshot(r.owner, first.handle, validate=lambda: None).state == "listening"


def test_each_utterance_submitted_once_and_previous_identity_cannot_reappear(runtime):
    r = runtime
    snapshot = start(r)
    first = receive(r, snapshot)
    result = finish(r, first)
    assert result.outcome == "submitted" and result.text == "Hello from Talk"
    assert result.snapshot.state == "listening" and r.coordinator.is_running
    retry = finish(r, receive(r, snapshot, first.utterance_id))
    assert retry.run_id == result.run_id
    assert len(r.calls) == 2
    second = finish(r, receive(r, snapshot))
    assert second.run_id != result.run_id
    with pytest.raises(DictationError, match="utterance_expired"):
        receive(r, snapshot, first.utterance_id)
    assert len(r.calls) == 4


def test_replayed_audio_conflict_preserves_admitted_result(runtime):
    r = runtime
    snapshot = start(r)
    first = receive(r, snapshot)
    original = finish(r, first)
    with pytest.raises(DictationError, match="idempotency_mismatch"):
        finish(r, receive(r, snapshot, first.utterance_id), audio=b"different")
    assert finish(r, receive(r, snapshot, first.utterance_id)).run_id == original.run_id
    assert len(r.calls) == 2


def test_dictation_handle_cannot_be_used_as_talk(runtime):
    r = runtime
    snapshot = r.dictation.start(r.owner, request_id=str(uuid4()), validate=lambda: None)
    with pytest.raises(DictationError, match="action_denied"):
        receive(r, snapshot)
    with pytest.raises(DictationError, match="action_denied"):
        r.talk.stop(r.owner, snapshot.handle, validate=lambda: None)
    assert r.dictation.snapshot(r.owner, snapshot.handle, validate=lambda: None).state == "capturing"


@pytest.mark.parametrize("field,value", [("client_session_id", str(uuid4())), ("conversation_id", "B"),
                                        ("access_binding", "foreign"), ("server_epoch", "new")])
def test_wrong_owner_cannot_stop_or_receive(runtime, field, value):
    r = runtime
    snapshot = start(r)
    owner = replace(r.owner, **{field: value})
    with pytest.raises(DictationError, match="action_denied"):
        r.talk.stop(owner, snapshot.handle, validate=lambda: None)
    with pytest.raises(DictationError, match="action_denied"):
        r.talk.begin_receive(owner, snapshot.handle, utterance_id=str(uuid4()), content_type="audio/webm")
    assert not r.calls


def test_stop_during_admission_retains_slot_and_never_replays_uncertain_submit(runtime):
    r = runtime
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def submit(owner, text, utterance, validate):
        validate()
        r.calls.append("admitted")
        entered.set()
        assert release.wait(3)
        return "saved-run"

    r.talk._submit = submit
    snapshot = start(r)
    op = receive(r, snapshot)

    def worker():
        try:
            finish(r, op)
        except DictationError as exc:
            outcomes.append(exc.code)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        view = r.talk.snapshot(r.owner, snapshot.handle, validate=lambda: None)
        assert view.state == "thinking" and not view.quiesced
        assert r.coordinator.state == "thinking"
        stopped = r.talk.stop(r.owner, snapshot.handle, validate=lambda: None)
        assert stopped.state == "stopping" and not stopped.quiesced
        with pytest.raises(DictationError, match="voice_session_busy"):
            start(r)
        assert r.coordinator._dictation_lock.acquire(blocking=False)
        r.coordinator._dictation_lock.release()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and outcomes == ["voice_session_expired"]
    assert r.calls.count("admitted") == 1
    assert r.talk.snapshot(r.owner, snapshot.handle, validate=lambda: None).quiesced
    replacement = start(r)
    r.talk.cancel_operation(op)
    assert r.talk.snapshot(r.owner, replacement.handle, validate=lambda: None).state == "listening"


def test_authority_rechecked_inside_submit_and_failed_attempt_does_not_repeat(runtime):
    r = runtime
    snapshot = start(r)
    op = receive(r, snapshot)
    revoked = [False]

    def validate():
        if revoked[0]:
            raise DictationError("authority_revoked")

    def submit(owner, text, utterance, check):
        revoked[0] = True
        check()
        pytest.fail("revoked admission")

    r.talk._submit = submit
    with pytest.raises(DictationError, match="authority_revoked"):
        finish(r, op, validate=validate)
    with pytest.raises(DictationError, match="voice_session_expired"):
        receive(r, snapshot, op.utterance_id)


def test_no_speech_has_no_chat_effect_and_timeout_stops(runtime):
    r = runtime
    r.service.transcribe = lambda *args, **kwargs: "  "
    snapshot = start(r)
    first = finish(r, receive(r, snapshot))
    assert first.outcome == "no_speech" and first.run_id is None and not r.calls
    r.clock[0] = 46
    second = finish(r, receive(r, snapshot))
    assert second.snapshot.state == "stopped" and second.snapshot.quiesced


def test_heartbeat_bounded_by_session_and_cannot_revive_expiry(runtime):
    r = runtime
    snapshot = start(r)
    for now in (100, 200, 300, 400, 500):
        r.clock[0] = now
        current = r.talk.snapshot(r.owner, snapshot.handle, validate=lambda: None, heartbeat=True)
        assert current.expires_in_ms == min(120_000, (600 - now) * 1000)
    r.clock[0] = 601
    assert r.talk.snapshot(r.owner, snapshot.handle, validate=lambda: None, heartbeat=True).state == "stopped"
    with pytest.raises(DictationError, match="voice_session_expired"):
        receive(r, snapshot)


def test_completed_identity_budget_never_evicts_and_resubmits(runtime):
    r = runtime
    snapshot = start(r)
    lease = r.coordinator._dictation_lease
    lease.utterance_ids = {str(uuid4()) for _ in range(200)}
    with pytest.raises(DictationError, match="voice_session_limit"):
        receive(r, snapshot)
    assert not r.calls and lease.operation is None


def test_only_current_saved_run_can_speak_once_with_existing_speech_policy(runtime):
    r = runtime
    snapshot = start(r)
    result = finish(r, receive(r, snapshot))
    with pytest.raises(DictationError, match="voice_run_changed"):
        r.talk.output(r.owner, snapshot.handle, run_id="other-run", output_id="message", validate=lambda: None)
    output = r.talk.output(r.owner, snapshot.handle, run_id=result.run_id, output_id="message", validate=lambda: None)
    assert output.audio == b"RIFF-synthetic" and output.content_type == "audio/wav"
    assert r.calls[-1] == ("speech", "Saved answer.")
    with pytest.raises(DictationError, match="voice_output_consumed"):
        r.talk.output(r.owner, snapshot.handle, run_id=result.run_id, output_id="different", validate=lambda: None)
    assert output.snapshot.quiesced and output.snapshot.state == "listening"


def test_stop_during_synthesis_withholds_bytes_and_waits_for_worker(runtime):
    r = runtime
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    def synthesize(*args, validate):
        validate()
        entered.set()
        assert release.wait(3)
        return b"private synthetic speech"

    r.service.synthesize = synthesize
    snapshot = start(r)
    result = finish(r, receive(r, snapshot))

    def worker():
        try:
            outcomes.append(r.talk.output(r.owner, snapshot.handle, run_id=result.run_id,
                                          output_id="message", validate=lambda: None))
        except DictationError as exc:
            outcomes.append(exc.code)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        assert r.talk.snapshot(r.owner, snapshot.handle, validate=lambda: None).state == "speaking"
        assert r.coordinator.state == "speaking"
        assert not r.talk.stop(r.owner, snapshot.handle, validate=lambda: None).quiesced
        assert not r.dictation.close()
    finally:
        release.set()
        thread.join(3)
    assert outcomes == ["voice_session_expired"] and r.dictation.close()
    assert not any(isinstance(value, bytes) for value in vars(r.coordinator._dictation_lease).values())


def test_speech_failure_is_not_automatically_repeated_and_input_still_works(runtime):
    r = runtime
    snapshot = start(r)
    result = finish(r, receive(r, snapshot))

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic unavailable output")

    r.service.synthesize = fail
    with pytest.raises(RuntimeError, match="synthetic unavailable"):
        r.talk.output(r.owner, snapshot.handle, run_id=result.run_id, output_id="message", validate=lambda: None)
    with pytest.raises(DictationError, match="voice_output_consumed"):
        r.talk.output(r.owner, snapshot.handle, run_id=result.run_id, output_id="message", validate=lambda: None)
    assert finish(r, receive(r, snapshot)).outcome == "submitted"


@pytest.mark.parametrize("text", ["Thanks for watching", "Saved answer."])
def test_talk_reuses_phantom_and_spoken_echo_filter_but_new_session_resets_echo(runtime, text):
    r = runtime
    snapshot = start(r)
    result = finish(r, receive(r, snapshot))
    r.talk.output(r.owner, snapshot.handle, run_id=result.run_id, output_id="message", validate=lambda: None)
    r.service.transcribe = lambda *args, **kwargs: text
    assert finish(r, receive(r, snapshot)).outcome == "no_speech"
    assert len([call for call in r.calls if isinstance(call, tuple) and call[0] == "submit"]) == 1
    r.talk.stop(r.owner, snapshot.handle, validate=lambda: None)
    replacement = start(r)
    r.service.transcribe = lambda *args, **kwargs: "Saved answer."
    assert finish(r, receive(r, replacement)).outcome == "submitted"


def test_legacy_can_take_over_after_hard_session_expiry_even_after_utterance_refresh(runtime):
    r = runtime
    snapshot = start(r)
    for now in (100, 200, 300, 400, 500):
        r.clock[0] = now
        r.talk.snapshot(r.owner, snapshot.handle, validate=lambda: None, heartbeat=True)
    r.clock[0] = 590
    finish(r, receive(r, snapshot))
    r.clock[0] = 601
    r.coordinator.start_browser()
    with pytest.raises(DictationError, match="voice_session_expired"):
        receive(r, snapshot)


def test_synthesis_owner_validates_before_model_and_after_worker(runtime):
    from row_bot.voice.browser_local import BrowserLocalVoiceService

    effects = []
    revoked = [False]

    def speak(text):
        effects.append(text)
        revoked[0] = True
        return b"synthetic bytes"

    def validate():
        if revoked[0]:
            raise DictationError("revoked")

    service = BrowserLocalVoiceService(voice_service=object(), tts_service=SimpleNamespace(
        is_installed=lambda: True, synthesize_wav_bytes=speak))
    with pytest.raises(DictationError, match="revoked"):
        service.synthesize("isolated", "Saved response", validate=validate)
    with pytest.raises(DictationError, match="revoked"):
        service.synthesize("isolated", "Never spoken", validate=validate)
    assert effects == ["Saved response"]
