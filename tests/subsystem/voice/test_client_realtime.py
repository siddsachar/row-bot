"""Fake realtime credentials/events over the one existing voice coordinator."""
import asyncio
from dataclasses import replace
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.voice.client_realtime import ClientRealtimeTransport, RealtimeEvent
from row_bot.voice.client_transport import ClientDictationTransport, DictationError, DictationOwner
from row_bot.voice.coordinator import VoiceSessionCoordinator


@pytest.fixture
def runtime():
    clock, generation, calls, pending = [0.0], [None], [], []

    def secret(**kwargs):
        calls.append(("secret", kwargs))
        return {"value": "synthetic-ephemeral", "expires_at": 1060}

    provider = SimpleNamespace(create_client_secret=secret)
    coordinator = VoiceSessionCoordinator(SimpleNamespace(is_running=False))

    def no_local():
        pytest.fail("realtime must not initialize local audio models")

    transport = ClientDictationTransport(coordinator=coordinator, browser_service=no_local,
                                         clock=lambda: clock[0])

    def submit(owner, text, event_id, validate):
        validate()
        calls.append(("submit", owner.conversation_id, text, event_id))
        generation[0] = SimpleNamespace(generation_id="run-1", status="running", stop_event=threading.Event())
        return "run-1"

    def control(owner, expected, action, text, event_id, validate):
        validate()
        assert expected is generation[0]
        calls.append(("control", action, event_id))
        if action == "cancel":
            expected.stop_event.set()
            return {"control": "cancel", "status": "cancelled"}
        if action == "input":
            pending.append((owner.conversation_id, expected.generation_id, text, event_id))
        return {"control": action, "status": "queued" if action == "input" else "running"}

    adapter = ClientRealtimeTransport(dictation=transport, provider_factory=lambda: provider,
        submit=submit, active_generation=lambda owner: generation[0], control_generation=control,
        wall_clock=lambda: 1000 + clock[0])
    owner = DictationOwner(str(uuid4()), "local:synthetic", "A", "epoch")
    return SimpleNamespace(adapter=adapter, transport=transport, coordinator=coordinator,
                           owner=owner, generation=generation, provider=provider, clock=clock, calls=calls, pending=pending)


def start(r, request_id=None):
    return r.adapter.start(r.owner, request_id=request_id or str(uuid4()), validate=lambda: None)


def event(r, handle, kind, **kwargs):
    payload = RealtimeEvent(str(uuid4()), kind, **kwargs)
    return asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None))


def test_passive_adapter_and_exact_secret_replay_no_local_models(runtime):
    r = runtime
    assert not r.calls
    request = str(uuid4())
    first = start(r, request)
    assert first.client_secret == "synthetic-ephemeral" and first.secret_expires_in_ms == 60_000
    assert first.snapshot.state == "connecting" and first.snapshot.quiesced
    assert start(r, request) == first
    assert r.calls == [("secret", {"expires_after_seconds": 60})]
    with pytest.raises(DictationError, match="idempotency_mismatch"):
        r.transport.start(r.owner, request_id=request, validate=lambda: None)
    with pytest.raises(DictationError, match="voice_session_busy"):
        r.coordinator.start_browser()
    event(r, first.snapshot.handle, "connected")
    assert start(r, request).client_secret is None
    assert r.coordinator.state == "listening"


def test_secret_expiry_discards_credential_without_silent_provider_retry(runtime):
    r = runtime
    request = str(uuid4())
    first = start(r, request)
    r.clock[0] = 61
    assert start(r, request).client_secret is None
    assert len(r.calls) == 1
    r.adapter.stop(r.owner, first.snapshot.handle, validate=lambda: None)
    assert r.coordinator._dictation_lease.realtime.secret == ""


def test_provider_failure_is_sanitized_and_releases_start_ownership(runtime):
    r = runtime

    def fail(**kwargs):
        raise RuntimeError("synthetic secret body not for wire")

    r.provider.create_client_secret = fail
    with pytest.raises(DictationError, match="realtime_provider_unavailable") as caught:
        start(r)
    assert "secret body" not in str(caught.value)
    assert not r.coordinator._active
    assert not r.coordinator._dictation_lease.starting


@pytest.mark.parametrize("payload", [{"value": "x", "expires_at": float("inf")},
                                     {"value": "x", "expires_at": 999},
                                     {"value": "x" * 4097, "expires_at": 1060}])
def test_invalid_bootstrap_never_publishes_a_ready_session(runtime, payload):
    r = runtime
    r.provider.create_client_secret = lambda **kwargs: payload
    with pytest.raises(DictationError, match="realtime_credential"):
        start(r)
    assert not r.coordinator.is_running


def test_stop_during_provider_call_retains_slot_then_discards_late_secret(runtime):
    r = runtime
    entered, release, outcomes = threading.Event(), threading.Event(), []

    def secret(**kwargs):
        entered.set()
        assert release.wait(3)
        return {"value": "synthetic-late-secret", "expires_at": 1060}

    r.provider.create_client_secret = secret

    def worker():
        try:
            outcomes.append(start(r))
        except DictationError as exc:
            outcomes.append(exc.code)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        handle = r.coordinator._dictation_lease.handle
        snapshot = r.adapter.stop(r.owner, handle, validate=lambda: None)
        assert not snapshot.quiesced and snapshot.state == "stopping"
        with pytest.raises(DictationError, match="voice_session_busy"):
            start(r)
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and outcomes == ["voice_session_expired"]
    assert r.coordinator._dictation_lease.realtime is None


def test_transcript_speech_window_and_phantom_gates_preserved(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    assert not event(r, handle, "transcript_final", item_id="early", text="Please inspect my work").accepted
    event(r, handle, "speech_started")
    event(r, handle, "speech_stopped")
    result = event(r, handle, "transcript_final", item_id="one", text="Please inspect my work")
    assert result.caption == "Please inspect my work"
    assert not event(r, handle, "transcript_final", item_id="one", text="Please inspect my work").accepted
    event(r, handle, "speech_started")
    event(r, handle, "speech_stopped")
    assert not event(r, handle, "transcript_final", item_id="two", text="Thanks for watching").accepted
    assert all(call[0] != "submit" for call in r.calls)


def test_restricted_bridge_consults_once_and_old_run_events_do_not_control_new_run(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    payload = RealtimeEvent(str(uuid4()), "function_call_ready", call_id="call-1", name="row_bot_agent_consult",
                            arguments=json.dumps({"request": "Please inspect my work"}))
    result = asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None))
    assert result.accepted and result.snapshot.run_id == "run-1"
    assert r.coordinator.queued_realtime_tool_call["call_id"] == "call-1"
    assert asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None)) == result
    assert len([call for call in r.calls if call[0] == "submit"]) == 1
    stale = event(r, handle, "function_call_ready", generation_id="", call_id="old", name="row_bot_agent_control",
                  arguments='{"action":"cancel"}')
    assert not stale.accepted and not r.generation[0].stop_event.is_set()
    event(r, handle, "function_call_ready", generation_id="run-1", call_id="new", name="row_bot_agent_control",
          arguments='{"action":"cancel"}')
    assert r.generation[0].stop_event.is_set()


def test_direct_tools_blocked_wait_silent_and_steer_uses_existing_queue(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    blocked = event(r, handle, "function_call_ready", call_id="bad", name="delete_files", arguments='{"path":"private"}')
    assert json.loads(blocked.function_output)["status"] == "blocked"
    wait = event(r, handle, "function_call_ready", call_id="wait", name="wait_for_user")
    assert wait.silent and not any(call[0] == "submit" for call in r.calls)
    event(r, handle, "function_call_ready", call_id="start", name="row_bot_agent_consult",
          arguments='{"request":"Inspect the document"}')
    follow = event(r, handle, "function_call_ready", generation_id="run-1", call_id="follow",
                   name="row_bot_agent_control", arguments='{"action":"steer","text":"also include tests"}')
    assert follow.function_output
    assert r.pending[0][:3] == ("A", "run-1", "also include tests")
    assert not hasattr(r.generation[0], "voice_control_queue")
    assert len([call for call in r.calls if call[0] == "submit"]) == 1


def test_exact_fallback_transcript_required(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    event(r, handle, "speech_started")
    event(r, handle, "speech_stopped")
    event(r, handle, "transcript_final", item_id="item", text="Please inspect my work")
    with pytest.raises(DictationError, match="voice_transcript_changed"):
        event(r, handle, "consult_fallback_needed", item_id="item", text="Different request")
    assert all(call[0] != "submit" for call in r.calls)


def test_output_barge_in_and_exact_stop_do_not_cancel_other_work(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    event(r, handle, "output_started", response_id="response", output_item_id="output")
    assert r.coordinator.playback_active
    assert event(r, handle, "barge_in_cancelled").accepted
    assert not r.coordinator.playback_active
    r.adapter.stop(r.owner, handle, validate=lambda: None)
    replacement = start(r).snapshot.handle
    with pytest.raises(DictationError, match="voice_session_expired"):
        r.adapter.stop(r.owner, handle, validate=lambda: None)
    assert r.adapter.snapshot(r.owner, replacement, validate=lambda: None).state == "connecting"
    assert all(call[0] != "cancel" for call in r.calls)


def test_revoked_event_and_oversized_payload_cannot_admit_work(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    payload = RealtimeEvent(str(uuid4()), "function_call_ready", call_id="call", name="row_bot_agent_consult",
                            arguments='{"request":"Inspect my work"}')

    def deny():
        raise DictationError("revoked")

    with pytest.raises(DictationError, match="revoked"):
        asyncio.run(r.adapter.event(r.owner, handle, payload, validate=deny))
    with pytest.raises(DictationError, match="invalid_voice_event"):
        asyncio.run(r.adapter.event(r.owner, handle, replace(payload, arguments="x" * 8193), validate=lambda: None))
    assert all(call[0] != "submit" for call in r.calls)


def test_local_audio_route_cannot_reuse_realtime_lease(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    with pytest.raises(DictationError, match="action_denied"):
        r.transport.begin_receive(r.owner, handle, utterance_id=str(uuid4()), content_type="audio/webm")


def test_authority_exception_after_factory_is_preserved_and_no_provider_call(runtime):
    r = runtime
    revoked = [False]
    sentinel = PermissionError("synthetic revoked authority")

    def factory():
        revoked[0] = True
        return r.provider

    def validate():
        if revoked[0]:
            raise sentinel

    r.adapter._provider = factory
    with pytest.raises(PermissionError) as caught:
        r.adapter.start(r.owner, request_id=str(uuid4()), validate=validate)
    assert caught.value is sentinel and not r.calls


def test_replay_refreshes_expiry_and_cannot_replay_old_control_after_run_replacement(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    payload = RealtimeEvent(str(uuid4()), "function_call_ready", call_id="wait", name="wait_for_user")
    result = asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None))
    r.clock[0] = 30
    replay = asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None))
    assert replay.snapshot.expires_in_ms == 90_000 and replay.function_output == result.function_output
    r.generation[0] = SimpleNamespace(generation_id="replacement")
    rejected = asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None))
    assert not rejected.accepted and not rejected.function_output


def test_stop_during_normal_chat_admission_keeps_slot_and_discards_late_event_result(runtime):
    r = runtime
    entered, release, outcomes = threading.Event(), threading.Event(), []
    handle = start(r).snapshot.handle
    payload = RealtimeEvent(str(uuid4()), "function_call_ready", call_id="call", name="row_bot_agent_consult",
                            arguments='{"request":"Please inspect my work"}')

    def submit(owner, text, event_id, validate):
        validate()
        r.generation[0] = SimpleNamespace(generation_id="run-1")
        entered.set()
        assert release.wait(3)
        return "run-1"

    def worker():
        try:
            outcomes.append(asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None)))
        except DictationError as exc:
            outcomes.append(exc.code)

    r.adapter._submit = submit
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        assert not r.adapter.stop(r.owner, handle, validate=lambda: None).quiesced
        with pytest.raises(DictationError, match="voice_session_busy"):
            start(r)
        assert r.coordinator._dictation_lock.acquire(blocking=False)
        r.coordinator._dictation_lock.release()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and outcomes == ["voice_session_expired"]
    assert r.adapter.snapshot(r.owner, handle, validate=lambda: None).quiesced
    assert r.coordinator._dictation_lease.realtime.last_result is None


@pytest.mark.parametrize("race", ["auth", "revision", "replacement", "run_aba", "stop"])
def test_active_control_revalidates_at_canonical_admission_before_queue_effect(runtime, race):
    r = runtime
    original = SimpleNamespace(generation_id="run-1")
    r.generation[0] = original
    handle = start(r).snapshot.handle
    changed = [False]
    entered, release, outcomes = threading.Event(), threading.Event(), []

    def validate():
        if changed[0]:
            raise DictationError("authority_changed")

    def control(owner, expected, action, text, event_id, admitted):
        assert expected is original
        entered.set()
        assert release.wait(3)
        admitted()
        r.pending.append(text)
        return {"status": "queued"}

    payload = RealtimeEvent(str(uuid4()), "function_call_ready", generation_id="run-1",
                            call_id="control", name="row_bot_agent_control",
                            arguments='{"action":"steer","text":"more tests"}')

    def worker():
        try:
            asyncio.run(r.adapter.event(r.owner, handle, payload, validate=validate))
        except DictationError as exc:
            outcomes.append(exc.code)

    r.adapter._control = control
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(3)
        if race in {"auth", "revision", "run_aba"}:
            if race == "run_aba":
                r.generation[0] = SimpleNamespace(generation_id="run-2")
                r.generation[0] = original
            changed[0] = True
        elif race == "replacement":
            r.generation[0] = SimpleNamespace(generation_id="run-1")
        else:
            assert not r.adapter.stop(r.owner, handle, validate=lambda: None).quiesced
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and len(outcomes) == 1 and not r.pending
    assert not hasattr(original, "voice_control_queue")
    assert r.coordinator._dictation_lease.realtime.last_result is None


def test_active_consult_uses_durable_event_identity_and_dedup_ack_without_legacy_queue(runtime):
    r = runtime
    r.generation[0] = SimpleNamespace(generation_id="run-1")
    handle = start(r).snapshot.handle
    payload = RealtimeEvent(str(uuid4()), "function_call_ready", generation_id="run-1",
                            call_id="consult", name="row_bot_agent_consult",
                            arguments='{"request":"Inspect the document"}')
    controls = []

    def control(owner, expected, action, text, event_id, validate):
        validate()
        controls.append((expected, action, text, event_id))
        return {"status": "already_running"}

    r.adapter._control = control
    response = asyncio.run(r.adapter.event(r.owner, handle, payload, validate=lambda: None))
    assert controls == [(r.generation[0], "input", "Inspect the document", payload.event_id)]
    assert response.silent and json.loads(response.function_output)["status"] == "already_running"
    assert not r.coordinator.queued_realtime_tool_call
    assert not any(call[0] == "submit" for call in r.calls)


@pytest.mark.parametrize("status,expected", [(401, "realtime_auth_unavailable"),
    (403, "realtime_auth_unavailable"), (429, "realtime_quota_or_rate_limit"),
    (500, "realtime_provider_unavailable")])
def test_provider_status_disposition_never_exposes_private_response(runtime, status, expected):
    r = runtime

    def fail(**kwargs):
        error = RuntimeError("synthetic private body and credential")
        error.response = SimpleNamespace(status_code=status)
        raise RuntimeError("private wrapper") from error

    r.provider.create_client_secret = fail
    with pytest.raises(DictationError) as caught:
        start(r)
    assert caught.value.code == expected and "private" not in str(caught.value)
    assert not r.coordinator.is_running
