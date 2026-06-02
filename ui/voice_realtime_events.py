from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

from ui.state import AppState, P, _active_generations

logger = logging.getLogger(__name__)

_LOG_RECEIVED_EVENTS = {
    "function_call_ready",
    "consult_fallback_needed",
    "transcript_final",
    "server_error",
    "fatal_error",
    "client_event_failed",
}
_QUIET_OBSERVED_EVENTS = {
    "server_event",
    "transcript_delta",
    "assistant_transcript_delta",
    "function_call_delta",
    "session_lifecycle",
    "provider_response_create_sent",
    "provider_response_queued",
    "provider_response_settling",
}


def make_realtime_event_handler(
    *,
    state: AppState,
    p: P,
    send_message: Callable[..., Any],
):
    async def _on_realtime_event(e):
        payload = e.args if isinstance(e.args, dict) else {}
        await handle_realtime_event(payload, state=state, p=p, send_message=send_message)

    return _on_realtime_event


async def handle_realtime_event(
    payload: dict[str, Any],
    *,
    state: AppState,
    p: P,
    send_message: Callable[..., Any],
) -> None:
    event_type = str(payload.get("type") or "")
    session_id = _session_id(payload)

    def _realtime_diag(stage: str, **extra: Any) -> None:
        active_gen = _active_generations.get(state.thread_id)
        snapshot = state.voice_coordinator.diagnostic_snapshot()
        snapshot.update({
            "stage": stage,
            "event_type": event_type,
            "event_session_id": session_id,
            "voice_enabled": state.voice_enabled,
            "voice_input_mode": state.voice_input_mode,
            "thread_id": state.thread_id,
            "active_generation_status": getattr(active_gen, "status", None),
            "response_arbiter_state": str(payload.get("response_state") or ""),
            "queued_response": bool(payload.get("queued_response") or False),
            **extra,
        })
        logger.info("voice.realtime.pipeline %s", snapshot)

    if event_type in _LOG_RECEIVED_EVENTS:
        _realtime_diag("browser_event_received")

    if event_type == "function_call_ready":
        state.voice_coordinator.mark_realtime_latency("function_call_ready")
        await _handle_function_call(payload, state=state, p=p, send_message=send_message, session_id=session_id)
        return

    if event_type == "consult_fallback_needed":
        state.voice_coordinator.mark_realtime_latency("consult_fallback_needed")
        await _handle_forced_fallback(payload, state=state, send_message=send_message, session_id=session_id)
        return

    if event_type == "transcript_final":
        text = state.voice_coordinator.record_realtime_transcript(
            str(payload.get("text") or ""),
            session_id=session_id,
            item_id=str(payload.get("item_id") or ""),
        )
        _realtime_diag("transcript_final_accepted" if text else "transcript_final_dropped", text_chars=len(text or ""))
        if text:
            state.voice_coordinator.mark_realtime_latency("transcript_final")
        return

    if event_type == "server_error":
        detail = str(payload.get("message") or "Realtime server error")
        if "Cancellation failed: no active response found" in detail:
            _realtime_diag("server_error_suppressed", detail=detail)
            return
        logger.warning("OpenAI Realtime server event error: %s", detail)
        state.voice_coordinator.set_realtime_state("connected", detail=detail, session_id=session_id)
        ui.notify(f"Realtime warning: {detail}", type="warning", close_button=True, timeout=8000)
        return

    if event_type == "microphone_permission":
        permission_state = str(payload.get("state") or "")
        state.voice_coordinator.mark_realtime_latency("mic_permission")
        _realtime_diag(
            "microphone_permission",
            permission_state=permission_state,
            origin=str(payload.get("origin") or ""),
            host=str(payload.get("host") or ""),
        )
        if permission_state == "denied":
            ui.notify("Microphone permission is denied for this Thoth window.", type="negative", close_button=True, timeout=10000)
        return

    if event_type in {"output_started", "output_item_started"}:
        state.voice_coordinator.record_realtime_output_started(
            response_id=str(payload.get("response_id") or ""),
            output_item_id=str(payload.get("output_item_id") or ""),
            session_id=session_id,
        )
        _realtime_diag("output_lifecycle_started")
        return

    if event_type == "assistant_transcript_final":
        state.voice_coordinator.record_assistant_output(str(payload.get("text") or ""))

    if event_type in {"response_done", "response_cancelled", "output_audio_done", "assistant_transcript_final"}:
        active_gen = _active_generations.get(state.thread_id)
        active_status = str(getattr(active_gen, "status", "") or "")
        clear_generation = event_type in {"response_done", "assistant_transcript_final"} and active_status not in {
            "running",
            "streaming",
        }
        state.voice_coordinator.record_realtime_output_done(
            session_id=session_id,
            clear_generation=clear_generation,
        )
        _realtime_diag("output_lifecycle_done")
        return

    if event_type == "barge_in_cancelled":
        accepted = state.voice_coordinator.record_barge_in(
            reason=str(payload.get("reason") or "user_speech_started"),
            session_id=session_id,
        )
        _realtime_diag(
            "barge_in_cancelled" if accepted else "barge_in_ignored",
            response_id=str(payload.get("response_id") or ""),
            output_item_id=str(payload.get("output_item_id") or ""),
            output_elapsed_ms=payload.get("output_elapsed_ms"),
        )
        return

    if event_type == "barge_in_ignored":
        _realtime_diag("barge_in_ignored", reason=str(payload.get("reason") or ""))
        return

    latency_events = {
        "connecting": "connecting",
        "connected": "connected",
        "provider_response_create_sent": "provider_response_create",
        "disconnected": "disconnected",
    }
    if event_type in latency_events:
        state.voice_coordinator.mark_realtime_latency(latency_events[event_type])
        if event_type == "provider_response_create_sent":
            _realtime_diag("provider_response_create_sent")
            return

    state_map = {
        "connecting": "connecting",
        "connected": "listening",
        "listening": "listening",
        "disconnected": "stopped",
        "stopped": "stopped",
        "fatal_error": "error",
        "client_event_failed": "error",
    }
    if event_type in state_map:
        detail = str(payload.get("message") or payload.get("state") or payload.get("reason") or "")
        if event_type == "fatal_error":
            _handle_realtime_fatal_error(detail, state=state, p=p, session_id=session_id)
            _realtime_diag("realtime_fatal_error_handled", detail=detail[:500])
            return
        state.voice_coordinator.set_realtime_state(state_map[event_type], detail=detail, session_id=session_id)
        _realtime_diag("coordinator_event_state_applied", mapped_state=state_map[event_type], detail=detail)
        return

    if event_type == "speech_started":
        state.voice_coordinator.record_realtime_speech_started(session_id=session_id)
        _realtime_diag("speech_window_started")
        return

    if event_type == "speech_stopped":
        state.voice_coordinator.record_realtime_speech_stopped(session_id=session_id)
        _realtime_diag("speech_window_stopped")
        return

    if event_type not in _QUIET_OBSERVED_EVENTS:
        _realtime_diag("browser_event_observed")
    else:
        logger.debug("voice.realtime.browser_event %s", {"event_type": event_type})


async def _handle_function_call(
    payload: dict[str, Any],
    *,
    state: AppState,
    p: P,
    send_message: Callable[..., Any],
    session_id: int | None,
) -> None:
    from voice.agent_bridge import VoiceAgentBridge
    from voice.realtime_client import send_realtime_function_output_js
    from ui.streaming import run_realtime_client_js

    bridge = VoiceAgentBridge(
        send_message=send_message,
        active_generation=lambda: _active_generations.get(state.thread_id),
        surface=lambda: _active_surface(state),
        thread_id=lambda: state.thread_id,
    )

    name = str(payload.get("name") or "")
    call_id = str(payload.get("call_id") or "")
    state.voice_coordinator.set_realtime_state(
        "consulting_thoth" if name == "thoth_agent_consult" else "listening",
        detail=name,
        session_id=session_id,
    )
    result = await bridge.handle_realtime_function_call(
        name=name,
        call_id=call_id,
        arguments=payload.get("arguments") or payload.get("parsed_arguments"),
        queue_consult=state.voice_coordinator.queue_realtime_tool_call,
    )
    if result.get("deferred"):
        return
    output = str(result.get("output") or "{}")
    silent = bool(result.get("silent"))
    delivered = run_realtime_client_js(
        p,
        send_realtime_function_output_js(
            call_id=call_id,
            output=output,
            thread_id=state.thread_id,
            generation_id=f"{state.thread_id}:control",
            silent=silent,
        ),
        context="realtime_control_function_output",
    )
    if delivered:
        state.voice_coordinator.mark_realtime_latency("function_output_sent")
        next_state = "listening" if silent else "speaking"
        state.voice_coordinator.set_realtime_state(next_state, detail=f"function_output:{name}", session_id=session_id)
        _realtime_diag(
            "function_output_sent",
            function_name=name,
            silent=silent,
        )


async def _handle_forced_fallback(
    payload: dict[str, Any],
    *,
    state: AppState,
    send_message: Callable[..., Any],
    session_id: int | None,
) -> None:
    from voice.agent_bridge import VoiceAgentBridge

    bridge = VoiceAgentBridge(
        send_message=send_message,
        active_generation=lambda: _active_generations.get(state.thread_id),
        surface=lambda: _active_surface(state),
        thread_id=lambda: state.thread_id,
    )
    text = str(payload.get("text") or "")
    state.voice_coordinator.set_realtime_state("consulting_thoth", detail="forced_fallback", session_id=session_id)
    state.voice_coordinator.mark_realtime_latency("forced_consult_started")
    result = await bridge.force_consult_if_substantive(
        text,
        queue_consult=state.voice_coordinator.queue_realtime_tool_call,
    )
    if not result.get("handled"):
        state.voice_coordinator.set_realtime_state("listening", detail="fallback_skipped", session_id=session_id)


def _session_id(payload: dict[str, Any]) -> int | None:
    raw = payload.get("session_id")
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


def _handle_realtime_fatal_error(
    detail: str,
    *,
    state: AppState,
    p: P,
    session_id: int | None,
) -> None:
    from voice.realtime_client import stop_realtime_client_js
    from ui.streaming import run_realtime_client_js

    message = _friendly_realtime_error(detail)
    fallback = bool(getattr(state.voice_runtime_settings, "realtime_fallback_to_local", False))
    if fallback:
        run_realtime_client_js(p, stop_realtime_client_js(), context="realtime_fatal_fallback_stop")
        state.voice_enabled = True
        state.voice_input_mode = "talk"
        state.voice_coordinator.start_talk()
        if p.voice_switch:
            p.voice_switch.value = True
            p.voice_switch.update()
        if p.dictate_btn:
            p.dictate_btn.props("color=grey")
        ui.notify(
            f"{message} Falling back to local Talk.",
            type="warning",
            close_button=True,
            timeout=10000,
        )
        return

    state.voice_coordinator.set_realtime_state("error", detail=detail, session_id=session_id)
    state.voice_enabled = False
    if p.voice_switch:
        p.voice_switch.value = False
        p.voice_switch.update()
    ui.notify(message, type="negative", close_button=True, timeout=12000)


def _friendly_realtime_error(detail: str) -> str:
    clean = str(detail or "").strip()
    if "insufficient_quota" in clean or "exceeded your current quota" in clean:
        return "OpenAI Realtime cannot start because the API key has insufficient quota."
    if not clean:
        return "OpenAI Realtime could not start."
    return f"OpenAI Realtime could not start: {clean[:500]}"


def _active_surface(state: AppState) -> str:
    if getattr(state, "active_developer_workspace_id", None):
        return "developer"
    if getattr(state, "active_designer_project", None):
        return "designer"
    return "normal_chat"
