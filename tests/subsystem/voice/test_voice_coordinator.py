from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.voice.client_realtime import ClientRealtimeTransport
from row_bot.voice.client_transport import ClientDictationTransport, DictationOwner
from row_bot.voice.coordinator import VoiceSessionCoordinator


@pytest.fixture
def realtime():
    """A coordinator whose realtime sessions start and stop the way the browser starts them."""
    coordinator = VoiceSessionCoordinator()
    provider = SimpleNamespace(
        create_client_secret=lambda **_: {"value": "synthetic-ephemeral", "expires_at": 1060})
    dictation = ClientDictationTransport(
        coordinator=coordinator, clock=lambda: 0.0,
        browser_service=lambda: pytest.fail("realtime must not load local speech models"))
    adapter = ClientRealtimeTransport(
        dictation=dictation, provider_factory=lambda: provider,
        submit=lambda *args: pytest.fail("no turn is submitted here"),
        active_generation=lambda owner: None,
        control_generation=lambda *args: pytest.fail("no turn is controlled here"),
        wall_clock=lambda: 1000.0)
    owner = DictationOwner(str(uuid4()), "local:synthetic", "A", "epoch")
    handles = []

    def start() -> int:
        handle = adapter.start(owner, request_id=str(uuid4()), validate=lambda: None).snapshot.handle
        handles.append(handle)
        return handle.voice_session_id

    def stop() -> None:
        adapter.stop(owner, handles[-1], validate=lambda: None)

    return SimpleNamespace(coordinator=coordinator, start=start, stop=stop)


def test_coordinator_drops_phantom_realtime_transcripts(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)

    assert coordinator.record_realtime_transcript("Thanks for watching", session_id=session_id,
                                                  item_id="item-1") is None


def test_coordinator_no_speech_timeout_only_while_listening(realtime):
    coordinator = realtime.coordinator
    coordinator.no_speech_timeout_seconds = 10
    session_id = realtime.start()
    coordinator.set_realtime_state("listening", session_id=session_id)
    assert coordinator.no_speech_timed_out(now=coordinator._last_activity_at + 11) is True

    coordinator.record_realtime_output_started(response_id="resp", output_item_id="out", session_id=session_id)
    assert coordinator.no_speech_timed_out(now=coordinator._last_activity_at + 99) is False


def test_coordinator_realtime_lifecycle_and_stale_events(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()

    assert coordinator.transport == "realtime"
    assert coordinator.state == "connecting"

    coordinator.set_realtime_state("listening", session_id=session_id)
    assert coordinator.state == "listening"

    coordinator.set_realtime_state("error", detail="old", session_id=session_id - 1)
    assert coordinator.state == "listening"

    assert coordinator.record_realtime_transcript("hello", session_id=session_id, item_id="early") is None
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)
    assert coordinator.state == "listening"
    assert coordinator.record_realtime_transcript("hello there", session_id=session_id, item_id="item-1") == "hello there"
    assert coordinator.record_realtime_transcript("stale", session_id=session_id - 1) is None

    realtime.stop()
    assert coordinator.state == "stopped"


def test_coordinator_exposes_realtime_diagnostic_snapshot(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.set_realtime_state("thinking", session_id=session_id)

    snapshot = coordinator.diagnostic_snapshot()

    assert snapshot["session_id"] == session_id
    assert snapshot["active"] is True
    assert snapshot["mode"] == "talk"
    assert snapshot["transport"] == "realtime"
    assert snapshot["state"] == "thinking"
    assert snapshot["realtime_state"] == "thinking"
    assert snapshot["playback_active"] is False


def test_coordinator_tracks_realtime_tool_output_and_barge_in_lifecycle(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.queue_realtime_tool_call({"call_id": "call_1", "name": "row_bot_agent_consult", "request": "do work"})
    assert coordinator.consume_realtime_tool_call() == {
        "call_id": "call_1",
        "name": "row_bot_agent_consult",
        "request": "do work",
    }
    assert coordinator.consume_realtime_tool_call() is None

    coordinator.record_realtime_output_started(response_id="resp_1", output_item_id="item_1", session_id=session_id)
    assert coordinator.state == "speaking"
    assert coordinator.diagnostic_snapshot()["playback_active"] is True
    assert coordinator.diagnostic_snapshot()["active_realtime_response_id"] == "resp_1"

    coordinator.record_barge_in(reason="user_speech_started", session_id=session_id)
    assert coordinator.state == "interrupted"
    assert coordinator.diagnostic_snapshot()["barge_in_reason"] == "user_speech_started"

    coordinator.record_realtime_output_done(session_id=session_id)
    assert coordinator.state == "listening"
    assert coordinator.diagnostic_snapshot()["playback_active"] is False
    assert coordinator.diagnostic_snapshot()["barge_in_reason"] == ""


def test_coordinator_resets_realtime_runtime_state_between_sessions(realtime):
    coordinator = realtime.coordinator
    first_session = realtime.start()
    coordinator.queue_realtime_tool_call({"call_id": "call_1", "name": "row_bot_agent_consult", "request": "do work"})
    coordinator.set_active_row_bot_generation("gen_1")
    coordinator.record_realtime_output_started(response_id="resp_1", output_item_id="item_1", session_id=first_session)
    coordinator.record_barge_in(reason="user_speech_started", session_id=first_session)
    realtime.stop()

    second_session = realtime.start()
    snapshot = coordinator.diagnostic_snapshot()

    assert second_session != first_session
    assert snapshot["state"] == "connecting"
    assert snapshot["active_realtime_response_id"] == ""
    assert snapshot["active_realtime_output_item_id"] == ""
    assert snapshot["active_row_bot_generation_id"] == ""
    assert snapshot["playback_active"] is False
    assert snapshot["barge_in_reason"] == ""
    assert coordinator.consume_realtime_tool_call() is None


def test_coordinator_ignores_realtime_barge_in_without_active_output(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.set_realtime_state("listening", session_id=session_id)

    accepted = coordinator.record_barge_in(reason="connect_time_cancel", session_id=session_id)

    assert accepted is False
    assert coordinator.state == "listening"
    assert coordinator.diagnostic_snapshot()["barge_in_reason"] == ""
    assert any(event.name == "realtime_barge_in_ignored" for event in coordinator.drain_events())


def test_coordinator_clears_active_generation_when_final_response_completes(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.set_active_row_bot_generation("gen_1")
    coordinator.record_realtime_output_started(response_id="resp_1", output_item_id="item_1", session_id=session_id)

    coordinator.record_realtime_output_done(session_id=session_id, clear_generation=True)

    snapshot = coordinator.diagnostic_snapshot()
    assert snapshot["active_row_bot_generation_id"] == ""
    assert snapshot["active_realtime_response_id"] == ""
    assert snapshot["active_realtime_output_item_id"] == ""
    assert snapshot["state"] == "listening"


def test_coordinator_records_realtime_latency_per_session(realtime):
    coordinator = realtime.coordinator
    coordinator.mark_realtime_latency("mic_permission", now=101.0)
    coordinator.mark_realtime_latency("connected", now=102.0)

    assert coordinator.diagnostic_snapshot()["realtime_latency_ms"] == {"mic_permission": 0, "connected": 1000}

    # A new session starts its own marks.
    realtime.start()
    breakdown = coordinator.diagnostic_snapshot()["realtime_latency_ms"]
    assert "mic_permission" not in breakdown and "connected" not in breakdown


def test_coordinator_summarizes_realtime_turn_latency(realtime):
    coordinator = realtime.coordinator
    coordinator.mark_realtime_latency("speech_stopped", now=100.0)
    coordinator.mark_realtime_latency("thinking_ui", now=100.01)
    coordinator.mark_realtime_latency("transcript_final", now=100.25)
    coordinator.mark_realtime_latency("function_call_ready", now=100.3)
    coordinator.mark_realtime_latency("forced_consult_started", now=100.4)
    coordinator.mark_realtime_latency("row_bot_consult_started", now=100.5)
    coordinator.mark_realtime_latency("first_token", now=101.0)
    coordinator.mark_realtime_latency("first_speakable_chunk", now=101.25)
    coordinator.mark_realtime_latency("first_substantive_audio_requested", now=101.3)
    coordinator.mark_realtime_latency("provider_response_create", now=101.5)
    coordinator.mark_realtime_latency("first_assistant_audio", now=102.0)

    summary = coordinator.realtime_latency_summary()

    assert summary == {
        "speech_stop_to_thinking_ui": 10,
        "speech_stop_to_transcript_final": 250,
        "speech_stop_to_function_call_ready": 299,
        "speech_stop_to_forced_consult": 400,
        "speech_stop_to_row_bot_start": 500,
        "speech_stop_to_first_token": 1000,
        "speech_stop_to_first_speakable_chunk": 1250,
        "speech_stop_to_first_spoken_row_bot": 1299,
        "transcript_final_to_forced_consult": 150,
        "consult_to_first_token": 500,
        "first_token_to_first_speakable_chunk": 250,
        "first_speakable_chunk_to_provider_response_create": 250,
        "provider_response_create_to_first_audio": 500,
    }
    assert coordinator.diagnostic_snapshot()["realtime_latency_summary_ms"] == summary


def test_coordinator_drops_realtime_transcript_without_speech_window(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()

    assert coordinator.record_realtime_transcript("please inspect the repo", session_id=session_id, item_id="item-1") is None
    assert any(
        event.name == "realtime_transcript_without_speech_window_ignored"
        for event in coordinator.drain_events()
    )


def test_coordinator_keeps_realtime_listening_after_idle_speech_stop(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.set_realtime_state("listening", session_id=session_id)
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)

    assert coordinator.state == "listening"
    assert any(
        event.name == "realtime_speech_window_stopped"
        for event in coordinator.drain_events()
    )


def test_coordinator_drops_duplicate_realtime_transcript_item_ids(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)

    assert coordinator.record_realtime_transcript("please inspect the repo", session_id=session_id, item_id="item-1")
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)
    assert coordinator.record_realtime_transcript("please inspect the repo", session_id=session_id, item_id="item-1") is None


def test_coordinator_drops_realtime_assistant_echo_and_short_fragments(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.record_assistant_output("The answer is visible in the app.")
    coordinator.record_realtime_output_started(response_id="resp", output_item_id="out", session_id=session_id)
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)

    assert coordinator.record_realtime_transcript("answer is visible", session_id=session_id, item_id="item-1") is None

    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)
    assert coordinator.record_realtime_transcript("ok", session_id=session_id, item_id="item-2") is None


def test_coordinator_accepts_realtime_question_fragment_after_speech_window(realtime):
    coordinator = realtime.coordinator
    session_id = realtime.start()
    coordinator.record_realtime_speech_started(session_id=session_id)
    coordinator.record_realtime_speech_stopped(session_id=session_id)

    assert coordinator.record_realtime_transcript("why?", session_id=session_id, item_id="item-1") == "why?"
