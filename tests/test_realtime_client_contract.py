from __future__ import annotations

from pathlib import Path

from row_bot.migration.row_bot_legacy_rebrand import LEGACY_SERVICE_PREFIX
from row_bot.voice.realtime_client import (
    send_realtime_function_output_js,
    send_realtime_run_event_js,
    start_realtime_client_js,
    stop_realtime_client_js,
)


ROOT = Path(__file__).resolve().parents[1]


def test_realtime_client_js_uses_voice_agent_webrtc_lifecycle():
    source = start_realtime_client_js(sink_id=123, session_id=7)

    assert "/api/voice/realtime/client-secret" in source
    assert "https://api.openai.com/v1/realtime/calls" in source
    assert "navigator.mediaDevices.getUserMedia" in source
    assert "createDataChannel('oai-events')" in source
    assert "transcription: {model: 'gpt-realtime-whisper'}" not in source
    assert "create_response: false" not in source
    assert "response.function_call_arguments.delta" in source
    assert "function_call_ready" in source
    assert "conversation.item.create" in source
    assert "function_call_output" in source
    assert "response.output_audio.done" in source
    assert "response.output_audio_transcript.delta" in source
    assert "consult_fallback_needed" in source
    assert "output_audio_buffer.clear" in source
    assert "barge_in_cancelled" in source
    assert "barge_in_ignored" in source
    assert "navigator.permissions.query({name: 'microphone'})" in source
    assert "microphone_permission" in source
    assert "server_error" in source
    assert "fatal_error" in source
    assert "sk_" not in source


def test_realtime_client_has_function_output_and_run_event_helpers():
    assert "RowBotRealtimeVoice.stop" in stop_realtime_client_js()

    output_source = send_realtime_function_output_js(
        call_id="call_1",
        output={"status": "completed", "speakable": "Done."},
        thread_id="thread-1",
        generation_id="gen-1",
    )
    run_event_source = send_realtime_run_event_js(
        "I'm using the browser.",
        origin="tool_start",
        thread_id="thread-1",
        generation_id="gen-1",
    )

    assert "sendFunctionOutput" in output_source
    assert "call_1" in output_source
    assert '"thread_id": "thread-1"' in output_source
    assert '"generation_id": "gen-1"' in output_source
    assert '"silent": false' in output_source
    assert "sendRunEvent" in run_event_source
    assert "tool_start" in run_event_source

    silent_output_source = send_realtime_function_output_js(
        call_id="call_2",
        output={"status": "completed", "speakable": ""},
        thread_id="thread-1",
        generation_id="gen-1",
        silent=True,
    )
    assert '"silent": true' in silent_output_source
    client_source = start_realtime_client_js(sink_id=2, session_id=2)
    assert "function_call_response" in client_source
    assert "localControlMeta" in client_source
    assert "responseMetadata" in client_source
    assert "if (key === 'silent') return;" in client_source
    assert "out[key] = String(value);" in client_source
    assert "!localMeta.silent" in client_source
    assert "metadata: Object.assign({row_bot_origin" not in client_source
    assert "metadata: responseMetadata(meta" in client_source


def test_realtime_client_serializes_response_create_with_arbiter():
    source = start_realtime_client_js(sink_id=3, session_id=3)

    assert "responseState: 'idle'" in source
    assert "pendingResponseCreate: null" in source
    assert "createResponse(response, label, priority)" in source
    assert "_sendResponseCreate(event, label)" in source
    assert "settleResponseLifecycle(reason, delayMs)" in source
    assert "flushResponseQueue()" in source
    assert "handleProviderError(message, payload)" in source
    assert "cancelUnauthorizedResponse(responseId, payload)" not in source
    assert "provider_response_without_app_authorization" not in source
    assert "unauthorized_response_cancelled" not in source
    assert "if (runtime.responseState !== 'creating')" not in source
    assert "provider_response_create_sent" in source
    assert "provider_response_queued" in source
    assert "provider_response_requeued" in source
    assert "active_response_error" in source
    assert "this.settleResponseLifecycle('barge_in_cancelled', 450)" in source
    assert "runtime.settleResponseLifecycle('response_done', 180)" in source
    assert "runtime.settleResponseLifecycle('response_cancelled', 300)" in source
    assert "type: 'response.create'" in source
    assert "this.createResponse({" in source
    assert "this.queueOrSendOutput({{\n        type: 'response.create'" not in source


def test_realtime_events_let_auto_response_decide_before_fallback():
    source = (ROOT / "src" / "row_bot" / "ui" / "voice_realtime_events.py").read_text(encoding="utf-8")
    transcript_branch = source.split('if event_type == "transcript_final":', 1)[1].split('if event_type == "server_error":', 1)[0]
    fallback_branch = source.split('if event_type == "consult_fallback_needed":', 1)[1].split('if event_type == "transcript_final":', 1)[0]
    function_branch = source.split("async def _handle_function_call", 1)[1].split("async def _handle_forced_fallback", 1)[0]

    assert "_handle_forced_fallback" not in transcript_branch
    assert "_handle_forced_fallback" in fallback_branch
    assert "silent = bool(result.get(\"silent\"))" in function_branch
    assert "silent=silent" in function_branch
    assert "next_state = \"listening\" if silent else \"speaking\"" in function_branch


def test_realtime_run_event_distinguishes_answers_from_status_cues():
    source = start_realtime_client_js(sink_id=4, session_id=4)

    assert "const answerOrigin = origin === 'final' || origin === 'stream_chunk' || origin.includes('result');" in source
    assert "Speak this Row-Bot response naturally and faithfully" in source
    assert "Do not add framing, summarize, or ask follow-up questions" in source
    assert "Speak exactly this brief Row-Bot status" in source
    assert "Do not add details or ask follow-up questions" in source
    assert f"Say this concise {LEGACY_SERVICE_PREFIX} status/result" not in source
    assert "origin === 'tool_progress'" in source
    assert "origin === 'long_running'" in source
    assert "answerOrigin || origin === 'tool_start'" in source


def test_realtime_client_removed_old_hybrid_speech_bridge():
    source = start_realtime_client_js(sink_id=1, session_id=1)
    streaming_src = (ROOT / "src" / "row_bot" / "ui" / "streaming.py").read_text(encoding="utf-8")

    assert "speech_request_" not in source
    assert "scheduleSpeechFinished" not in source
    assert "playbackFinishDelayMs" not in source
    assert "assistant_audio_drain_started" not in source
    assert f"Read this {LEGACY_SERVICE_PREFIX} response aloud exactly" not in source
    assert "speak_realtime_js" not in streaming_src


def test_app_exposes_ephemeral_secret_endpoint_without_long_lived_key():
    source = (ROOT / "src" / "row_bot" / "app.py").read_text(encoding="utf-8")
    endpoint_source = source.split('@app.post("/api/voice/realtime/client-secret")', 1)[1].split("@", 1)[0]

    assert '@app.post("/api/voice/realtime/client-secret")' in source
    assert "create_client_secret" in source
    assert "load_voice_runtime_settings" in endpoint_source
    assert "model=voice_settings.talk_model" in endpoint_source
    assert "voice=voice_settings.realtime_voice" in endpoint_source
    assert "speaking_rate" not in endpoint_source
    assert "realtime_speaking_rate" not in endpoint_source
    assert "OPENAI_API_KEY" not in endpoint_source
