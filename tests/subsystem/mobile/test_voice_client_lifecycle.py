"""U08: voice callbacks must retain their session, conversation and run owner."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from row_bot.voice.actions import ActiveVoiceSurfaceBinding
from row_bot.voice.coordinator import VoiceSessionCoordinator


def test_coordinator_identity_and_inactive_output_guard():
    coordinator = VoiceSessionCoordinator(SimpleNamespace(is_running=False))
    session = coordinator.start_realtime_talk()
    identity = coordinator.capture_callback(session, thread_id="A", generation_id="run")
    assert identity is not None
    assert coordinator.capture_callback(session, thread_id="B") is None
    assert coordinator.accepts_callback(identity, thread_id="A", generation_id="run")
    assert not coordinator.accepts_callback(identity, thread_id="A", generation_id="old")
    coordinator.stop()
    before = coordinator.diagnostic_snapshot()
    coordinator.record_realtime_output_started(session_id=session, response_id="stale")
    coordinator.record_realtime_output_done(session_id=session, clear_generation=True)
    coordinator.record_assistant_output("stale", session_id=session)
    assert not coordinator.record_barge_in(reason="late", session_id=session)
    assert coordinator.diagnostic_snapshot() == before
    assert not coordinator.output_activity.should_drop_echo("stale")


def test_cleared_binding_cannot_send_or_read_or_write_composer():
    actions = []
    binding = ActiveVoiceSurfaceBinding("chat", "A", lambda: actions.append("read") or "draft",
                                        lambda text: actions.append(text), lambda text: actions.append(text))
    binding.clear()
    asyncio.run(binding.send_talk("late talk"))
    assert binding.append_dictation("late dictation") == ""
    assert actions == []
