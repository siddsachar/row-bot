"""U08: voice callbacks must retain their session, conversation and run owner."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.voice.client_realtime import ClientRealtimeTransport
from row_bot.voice.client_transport import ClientDictationTransport, DictationOwner
from row_bot.voice.coordinator import VoiceSessionCoordinator


def test_coordinator_identity_and_inactive_output_guard():
    coordinator = VoiceSessionCoordinator()
    provider = SimpleNamespace(
        create_client_secret=lambda **_: {"value": "synthetic-ephemeral", "expires_at": 1060},
        transport="openai_realtime", status=lambda: SimpleNamespace(ready=True))
    dictation = ClientDictationTransport(
        coordinator=coordinator, clock=lambda: 0.0,
        browser_service=lambda: pytest.fail("realtime must not load local speech models"))
    adapter = ClientRealtimeTransport(
        dictation=dictation, provider_factory=lambda option: provider,
        submit=lambda *args: pytest.fail("no turn is submitted here"),
        active_generation=lambda owner: None,
        control_generation=lambda *args: pytest.fail("no turn is controlled here"),
        wall_clock=lambda: 1000.0)
    owner = DictationOwner(str(uuid4()), "local:synthetic", "A", "epoch")
    handle = adapter.start(owner, request_id=str(uuid4()), validate=lambda: None).snapshot.handle
    session = handle.voice_session_id
    # The browser session is bound to its conversation; another conversation can't take its callbacks.
    identity = coordinator.capture_callback(session, thread_id="A", generation_id="run")
    assert identity is not None
    assert coordinator.capture_callback(session, thread_id="B") is None
    assert coordinator.accepts_callback(identity, thread_id="A", generation_id="run")
    assert not coordinator.accepts_callback(identity, thread_id="A", generation_id="old")
    adapter.stop(owner, handle, validate=lambda: None)
    before = coordinator.diagnostic_snapshot()
    coordinator.record_realtime_output_started(session_id=session, response_id="stale")
    coordinator.record_realtime_output_done(session_id=session, clear_generation=True)
    coordinator.record_assistant_output("stale", session_id=session)
    assert not coordinator.record_barge_in(reason="late", session_id=session)
    assert coordinator.diagnostic_snapshot() == before
    assert not coordinator.output_activity.should_drop_echo("stale")
