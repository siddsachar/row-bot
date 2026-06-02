from __future__ import annotations

import json


def start_realtime_client_js(*, sink_id: int, session_id: int) -> str:
    sink = json.dumps(sink_id)
    session = json.dumps(session_id)
    return f"""
(async function() {{
  const sinkId = {sink};
  const sessionId = {session};

  function getSink() {{
    if (typeof getElement === 'function') return getElement(sinkId);
    return document.querySelector('[data-nicegui-id="' + sinkId + '"]');
  }}

  function emit(type, detail) {{
    const el = getSink();
    if (!el) return;
    el.dispatchEvent(new CustomEvent('thoth-realtime-event', {{
      detail: Object.assign({{type, session_id: sessionId}}, detail || {{}})
    }}));
  }}

  function safeJson(value) {{
    try {{
      if (!value) return {{}};
      if (typeof value === 'object') return value;
      return JSON.parse(String(value));
    }} catch (_) {{
      return {{request: String(value || '')}};
    }}
  }}

  function responseMetadata(meta, base) {{
    const out = {{}};
    const source = Object.assign({{}}, base || {{}}, meta || {{}});
    Object.keys(source).forEach((key) => {{
      if (key === 'silent') return;
      const value = source[key];
      if (value === undefined || value === null) return;
      out[key] = String(value);
    }});
    return out;
  }}

  function localControlMeta(meta) {{
    return {{
      silent: Boolean(meta && meta.silent)
    }};
  }}

  async function cleanup(existing) {{
    if (!existing) return;
    try {{ existing.dc && existing.dc.close(); }} catch (_) {{}}
    try {{ existing.pc && existing.pc.close(); }} catch (_) {{}}
    try {{
      (existing.stream && existing.stream.getTracks() || []).forEach((track) => track.stop());
    }} catch (_) {{}}
    try {{ existing.audio && existing.audio.remove(); }} catch (_) {{}}
  }}

  const previousRuntime = window.ThothRealtimeVoice;
  await cleanup(previousRuntime && previousRuntime.session);

  const runtime = {{
    session: null,
    activeResponseId: '',
    activeOutputItemId: '',
    outputStartedAt: 0,
    playbackActive: false,
    responseState: 'idle',
    pendingResponseCreate: null,
    responseSettleTimer: null,
    lastResponseCreate: null,
    pendingTranscript: '',
    pendingTranscriptItemId: '',
    consultStartedForItemId: '',
    functionArgumentDeltas: {{}},
    handledCallIds: new Set(),
    outputQueue: [],
    emit,
    dcState() {{
      const dc = this.session && this.session.dc;
      return dc ? dc.readyState : 'missing';
    }},
    sendEvent(event) {{
      const dc = this.session && this.session.dc;
      if (!dc || dc.readyState !== 'open') {{
        emit('client_event_failed', {{
          reason: 'data_channel_not_open',
          data_channel_state: this.dcState(),
          event_type: event && event.type || ''
        }});
        return false;
      }}
      dc.send(JSON.stringify(event));
      return true;
    }},
    async stop() {{
      this.outputQueue = [];
      this.playbackActive = false;
      this.responseState = 'idle';
      this.pendingResponseCreate = null;
      this.lastResponseCreate = null;
      if (this.responseSettleTimer) clearTimeout(this.responseSettleTimer);
      this.responseSettleTimer = null;
      this.activeResponseId = '';
      this.activeOutputItemId = '';
      await cleanup(this.session);
      this.session = null;
      emit('stopped');
    }},
    queueOrSendOutput(event, label) {{
      if (this.playbackActive) {{
        this.outputQueue.push({{event, label}});
        emit('provider_output_queued', {{
          reason: 'playback_active',
          queue_length: this.outputQueue.length,
          output_label: label || ''
        }});
        return true;
      }}
      return this.sendEvent(event);
    }},
    createResponse(response, label, priority) {{
      const event = {{type: 'response.create', response}};
      const queuedLabel = label || 'response_create';
      const queuedPriority = priority || 'normal';
      if (this.responseState !== 'idle' || this.playbackActive) {{
        if (queuedPriority === 'low') {{
          emit('provider_output_dropped', {{
            reason: 'response_active_low_priority',
            response_state: this.responseState,
            output_label: queuedLabel
          }});
          return true;
        }}
        this.pendingResponseCreate = {{event, label: queuedLabel, priority: queuedPriority}};
        emit('provider_response_queued', {{
          reason: 'response_not_idle',
          response_state: this.responseState,
          output_label: queuedLabel,
          queue_length: this.pendingResponseCreate ? 1 : 0
        }});
        return true;
      }}
      return this._sendResponseCreate(event, queuedLabel);
    }},
    _sendResponseCreate(event, label) {{
      this.responseState = 'creating';
      this.lastResponseCreate = {{event, label: label || 'response_create'}};
      const sent = this.sendEvent(event);
      if (!sent) {{
        this.responseState = 'idle';
        this.lastResponseCreate = null;
      }} else {{
        emit('provider_response_create_sent', {{
          response_state: this.responseState,
          output_label: label || ''
        }});
      }}
      return sent;
    }},
    settleResponseLifecycle(reason, delayMs) {{
      if (this.responseSettleTimer) clearTimeout(this.responseSettleTimer);
      this.responseState = 'draining';
      this.responseSettleTimer = setTimeout(() => {{
        this.responseSettleTimer = null;
        if (this.responseState === 'draining' || this.responseState === 'cancelling') {{
          this.responseState = 'idle';
        }}
        this.flushResponseQueue();
      }}, Math.max(0, Number(delayMs || 0)));
      emit('provider_response_settling', {{
        reason: reason || '',
        response_state: this.responseState,
        settle_ms: Math.max(0, Number(delayMs || 0)),
        queued_response: Boolean(this.pendingResponseCreate)
      }});
    }},
    flushResponseQueue() {{
      if (this.responseState !== 'idle' || this.playbackActive || !this.pendingResponseCreate) return false;
      const next = this.pendingResponseCreate;
      this.pendingResponseCreate = null;
      return this._sendResponseCreate(next.event, next.label);
    }},
    handleProviderError(message, payload) {{
      const detail = String(message || '');
      const activeConflict = detail.toLowerCase().includes('active response');
      if (activeConflict && this.lastResponseCreate) {{
        this.pendingResponseCreate = this.lastResponseCreate;
        this.responseState = 'draining';
        emit('provider_response_requeued', {{
          reason: 'active_response_error',
          response_state: this.responseState,
          output_label: this.lastResponseCreate.label || '',
          raw: payload || {{}}
        }});
        this.settleResponseLifecycle('active_response_error', 600);
        return true;
      }}
      return false;
    }},
    flushOutputQueue() {{
      while (!this.playbackActive && this.outputQueue.length) {{
        const next = this.outputQueue.shift();
        if (next) this.sendEvent(next.event);
      }}
    }},
    sendFunctionOutput(callId, output, meta) {{
      const cleanCallId = String(callId || '').trim();
      const cleanOutput = typeof output === 'string' ? output : JSON.stringify(output || {{}});
      const localMeta = localControlMeta(meta);
      if (!cleanCallId) return this.sendRunEvent(cleanOutput, Object.assign({{}}, meta || {{}}, {{origin: 'forced_consult_result'}}));
      const sent = this.queueOrSendOutput({{
        type: 'conversation.item.create',
        item: {{
          type: 'function_call_output',
          call_id: cleanCallId,
          output: cleanOutput
        }}
      }}, 'function_call_output');
      if (sent && !localMeta.silent) {{
        this.createResponse({{
            output_modalities: ['audio'],
            metadata: responseMetadata(meta, {{thoth_origin: 'function_call_output', call_id: cleanCallId}})
        }}, 'function_call_response', 'normal');
      }}
      return sent;
    }},
    sendRunEvent(text, meta) {{
      const clean = String(text || '').trim();
      if (!clean) return false;
      const origin = String(meta && meta.origin || '');
      const answerOrigin = origin === 'final' || origin === 'stream_chunk' || origin.includes('result');
      const priority = (answerOrigin || origin === 'tool_start' || origin === 'tool_progress' || origin === 'long_running') ? 'normal' : 'low';
      const instructions = answerOrigin
        ? 'Speak this Thoth response naturally and faithfully. Do not add framing, summarize, or ask follow-up questions: ' + clean
        : 'Speak exactly this brief Thoth status. Do not add details or ask follow-up questions: ' + clean;
      return this.createResponse({{
          output_modalities: ['audio'],
          metadata: responseMetadata(meta, {{thoth_origin: 'run_event'}}),
          instructions
      }}, 'run_event', priority);
    }},
    cancelActiveOutput(reason) {{
      const elapsedMs = this.outputStartedAt ? Math.max(0, Math.round(performance.now() - this.outputStartedAt)) : 0;
      if (elapsedMs > 0 && elapsedMs < 250) {{
        emit('barge_in_ignored', {{
          reason: 'very_early_input',
          active_response_id: this.activeResponseId,
          active_output_item_id: this.activeOutputItemId,
          output_elapsed_ms: elapsedMs
        }});
        return false;
      }}
      let sentCancel = false;
      if (this.activeResponseId) sentCancel = this.sendEvent({{type: 'response.cancel'}});
      this.sendEvent({{type: 'output_audio_buffer.clear'}});
      emit('barge_in_cancelled', {{
        reason: reason || 'user_speech_started',
        response_id: this.activeResponseId,
        output_item_id: this.activeOutputItemId,
        output_elapsed_ms: elapsedMs,
        playback_active: this.playbackActive,
        provider_cancel_sent: sentCancel
      }});
      this.playbackActive = false;
      this.activeResponseId = '';
      this.activeOutputItemId = '';
      this.responseState = 'cancelling';
      if (this.pendingResponseCreate && this.pendingResponseCreate.priority === 'low') {{
        this.pendingResponseCreate = null;
      }}
      this.settleResponseLifecycle('barge_in_cancelled', 450);
      return true;
    }},
    emitFunctionCall(item, sourceType) {{
      if (!item || item.type !== 'function_call') return false;
      const callId = String(item.call_id || item.id || '');
      if (!callId || this.handledCallIds.has(callId)) return false;
      this.handledCallIds.add(callId);
      const argumentsText = String(item.arguments || this.functionArgumentDeltas[callId] || this.functionArgumentDeltas[item.id] || '');
      if (item.name === 'thoth_agent_consult') {{
        this.consultStartedForItemId = this.pendingTranscriptItemId || 'unknown';
      }}
      emit('function_call_ready', {{
        name: String(item.name || ''),
        call_id: callId,
        arguments: argumentsText,
        parsed_arguments: safeJson(argumentsText),
        source_event_type: sourceType || '',
        response_id: this.activeResponseId,
        item_id: String(item.id || '')
      }});
      return true;
    }},
    handleResponseOutputItem(item, sourceType) {{
      if (!item) return;
      if (item.type === 'function_call') this.emitFunctionCall(item, sourceType);
    }}
  }};
  window.ThothRealtimeVoice = runtime;

  try {{
    emit('connecting');
    const tokenResponse = await fetch('/api/voice/realtime/client-secret', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}}
    }});
    if (!tokenResponse.ok) {{
      const detail = await tokenResponse.text();
      throw new Error(detail || ('Realtime token request failed: ' + tokenResponse.status));
    }}
    const tokenData = await tokenResponse.json();
    const ephemeralKey = tokenData.value;
    if (!ephemeralKey) throw new Error('Realtime client secret did not include a value.');

    const pc = new RTCPeerConnection();
    const audio = document.createElement('audio');
    audio.autoplay = true;
    audio.style.display = 'none';
    document.body.appendChild(audio);
    pc.ontrack = (event) => {{
      audio.srcObject = event.streams[0];
      emit('remote_audio_track', {{streams: event.streams.length}});
    }};
    pc.onconnectionstatechange = () => emit('connection_state', {{state: pc.connectionState}});

    try {{
      if (navigator.permissions && navigator.permissions.query) {{
        const permission = await navigator.permissions.query({{name: 'microphone'}});
        emit('microphone_permission', {{
          state: permission.state || '',
          origin: window.location.origin || '',
          host: window.location.host || ''
        }});
      }} else {{
        emit('microphone_permission', {{
          state: 'unsupported',
          origin: window.location.origin || '',
          host: window.location.host || ''
        }});
      }}
    }} catch (error) {{
      emit('microphone_permission', {{
        state: 'query_failed',
        origin: window.location.origin || '',
        host: window.location.host || '',
        message: String(error && error.message || error)
      }});
    }}

    const stream = await navigator.mediaDevices.getUserMedia({{audio: true}});
    stream.getTracks().forEach((track) => pc.addTrack(track, stream));

    const dc = pc.createDataChannel('oai-events');
    runtime.session = {{pc, dc, stream, audio}};

    dc.addEventListener('open', () => emit('connected'));
    dc.addEventListener('message', (event) => {{
      let payload = null;
      try {{ payload = JSON.parse(event.data); }} catch (_) {{ return; }}
      const type = String(payload.type || '');
      if (type === 'session.created' || type === 'session.updated') {{
        emit('session_lifecycle', {{event_type: type, raw: payload}});
      }} else if (type === 'input_audio_buffer.speech_started') {{
        runtime.cancelActiveOutput('user_speech_started');
        emit('speech_started', {{raw: payload}});
      }} else if (type === 'input_audio_buffer.speech_stopped') {{
        emit('speech_stopped', {{raw: payload}});
      }} else if (type === 'conversation.item.input_audio_transcription.delta') {{
        emit('transcript_delta', {{text: payload.delta || '', item_id: payload.item_id || '', raw: payload}});
      }} else if (type === 'conversation.item.input_audio_transcription.completed') {{
        runtime.pendingTranscript = String(payload.transcript || payload.text || '');
        runtime.pendingTranscriptItemId = String(payload.item_id || '');
        runtime.consultStartedForItemId = '';
        emit('transcript_final', {{
          text: runtime.pendingTranscript,
          item_id: runtime.pendingTranscriptItemId,
          raw: payload
        }});
      }} else if (type === 'response.created') {{
        const responseId = String(payload.response && payload.response.id || payload.response_id || '');
        runtime.activeResponseId = responseId;
        runtime.playbackActive = true;
        runtime.responseState = 'active';
        runtime.outputStartedAt = performance.now();
        emit('output_started', {{
          response_id: runtime.activeResponseId,
          response_state: runtime.responseState,
          raw: payload
        }});
      }} else if (type === 'response.output_item.added' || type === 'response.output_item.created') {{
        const item = payload.item || {{}};
        runtime.activeOutputItemId = String(item.id || payload.item_id || runtime.activeOutputItemId || '');
        emit('output_item_started', {{
          response_id: String(payload.response_id || runtime.activeResponseId || ''),
          output_item_id: runtime.activeOutputItemId,
          item_type: String(item.type || ''),
          raw: payload
        }});
      }} else if (type === 'response.function_call_arguments.delta') {{
        const callId = String(payload.call_id || payload.item_id || '');
        runtime.functionArgumentDeltas[callId] = String(runtime.functionArgumentDeltas[callId] || '') + String(payload.delta || '');
        emit('function_call_delta', {{
          call_id: callId,
          delta: String(payload.delta || ''),
          raw: payload
        }});
      }} else if (type === 'response.output_item.done') {{
        const item = payload.item || {{}};
        runtime.handleResponseOutputItem(item, type);
        emit('output_item_done', {{
          response_id: String(payload.response_id || runtime.activeResponseId || ''),
          output_item_id: String(item.id || payload.item_id || runtime.activeOutputItemId || ''),
          item_type: String(item.type || ''),
          raw: payload
        }});
      }} else if (type === 'response.output_audio_transcript.delta') {{
        emit('assistant_transcript_delta', {{
          text: payload.delta || '',
          response_id: String(payload.response_id || runtime.activeResponseId || ''),
          output_item_id: String(payload.item_id || runtime.activeOutputItemId || ''),
          raw: payload
        }});
      }} else if (type === 'response.output_audio_transcript.done') {{
        emit('assistant_transcript_final', {{
          text: payload.transcript || payload.text || '',
          response_id: String(payload.response_id || runtime.activeResponseId || ''),
          output_item_id: String(payload.item_id || runtime.activeOutputItemId || ''),
          raw: payload
        }});
      }} else if (type === 'response.output_audio.done') {{
        emit('output_audio_done', {{
          response_id: String(payload.response_id || runtime.activeResponseId || ''),
          output_item_id: String(payload.item_id || runtime.activeOutputItemId || ''),
          response_state: runtime.responseState,
          raw: payload
        }});
        if (runtime.responseState === 'draining') {{
          runtime.responseState = 'idle';
          runtime.flushResponseQueue();
        }}
      }} else if (type === 'response.done') {{
        const response = payload.response || {{}};
        const outputs = Array.isArray(response.output) ? response.output : [];
        let hadFunctionCall = false;
        outputs.forEach((item) => {{
          if (item && item.type === 'function_call') hadFunctionCall = runtime.emitFunctionCall(item, type) || hadFunctionCall;
        }});
        const responseId = String(response.id || runtime.activeResponseId || '');
        const outputItemId = runtime.activeOutputItemId;
        const elapsedMs = runtime.outputStartedAt ? Math.max(0, Math.round(performance.now() - runtime.outputStartedAt)) : 0;
        runtime.playbackActive = false;
        runtime.activeResponseId = '';
        runtime.activeOutputItemId = '';
        runtime.outputStartedAt = 0;
        emit('response_done', {{
          response_id: responseId,
          output_item_id: outputItemId,
          output_elapsed_ms: elapsedMs,
          response_state: runtime.responseState,
          had_function_call: hadFunctionCall,
          consult_started: Boolean(runtime.consultStartedForItemId),
          pending_transcript: runtime.pendingTranscript,
          pending_transcript_item_id: runtime.pendingTranscriptItemId,
          raw: payload
        }});
        if (runtime.pendingTranscript && !runtime.consultStartedForItemId && !hadFunctionCall) {{
          emit('consult_fallback_needed', {{
            text: runtime.pendingTranscript,
            item_id: runtime.pendingTranscriptItemId,
            response_id: responseId,
            raw: payload
          }});
        }}
        runtime.pendingTranscript = '';
        runtime.pendingTranscriptItemId = '';
        runtime.consultStartedForItemId = '';
        runtime.settleResponseLifecycle('response_done', 180);
        runtime.flushOutputQueue();
      }} else if (type === 'response.cancelled') {{
        runtime.playbackActive = false;
        runtime.responseState = 'cancelling';
        emit('response_cancelled', {{
          response_id: String(payload.response_id || runtime.activeResponseId || ''),
          output_item_id: runtime.activeOutputItemId,
          response_state: runtime.responseState,
          raw: payload
        }});
        runtime.activeResponseId = '';
        runtime.activeOutputItemId = '';
        runtime.settleResponseLifecycle('response_cancelled', 300);
      }} else if (type === 'error') {{
        const message = (payload.error && payload.error.message) || payload.message || 'Realtime error';
        const requeued = runtime.handleProviderError(message, payload);
        emit('server_error', {{message, requeued, response_state: runtime.responseState, raw: payload}});
      }} else {{
        emit('server_event', {{event_type: type, raw: payload}});
      }}
    }});
    dc.addEventListener('close', () => emit('disconnected'));
    dc.addEventListener('error', () => emit('fatal_error', {{message: 'Realtime data channel error'}}));

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdpResponse = await fetch('https://api.openai.com/v1/realtime/calls', {{
      method: 'POST',
      body: offer.sdp,
      headers: {{
        Authorization: 'Bearer ' + ephemeralKey,
        'Content-Type': 'application/sdp'
      }}
    }});
    if (!sdpResponse.ok) {{
      const detail = await sdpResponse.text();
      throw new Error(detail || ('Realtime SDP exchange failed: ' + sdpResponse.status));
    }}
    await pc.setRemoteDescription({{type: 'answer', sdp: await sdpResponse.text()}});
    emit('listening');
  }} catch (error) {{
    await cleanup(runtime.session);
    runtime.session = null;
    emit('fatal_error', {{message: String(error && error.message || error)}});
  }}
}})();
"""


def stop_realtime_client_js() -> str:
    return """
(async function() {
  if (window.ThothRealtimeVoice && window.ThothRealtimeVoice.stop) {
    await window.ThothRealtimeVoice.stop();
  }
})();
"""


def send_realtime_function_output_js(
    *,
    call_id: str,
    output: str | dict,
    thread_id: str | None = None,
    generation_id: str | None = None,
    silent: bool = False,
) -> str:
    if not isinstance(output, str):
        output_text = json.dumps(output, ensure_ascii=False)
    else:
        output_text = output
    meta = {
        "thread_id": thread_id or "",
        "generation_id": generation_id or "",
        "silent": bool(silent),
    }
    return f"""
(function() {{
  if (window.ThothRealtimeVoice && window.ThothRealtimeVoice.sendFunctionOutput) {{
    window.ThothRealtimeVoice.sendFunctionOutput({json.dumps(call_id)}, {json.dumps(output_text)}, {json.dumps(meta)});
  }}
}})();
"""


def send_realtime_run_event_js(
    text: str,
    *,
    origin: str = "status",
    thread_id: str | None = None,
    generation_id: str | None = None,
) -> str:
    meta = {
        "origin": origin,
        "thread_id": thread_id or "",
        "generation_id": generation_id or "",
    }
    return f"""
(function() {{
  if (window.ThothRealtimeVoice && window.ThothRealtimeVoice.sendRunEvent) {{
    window.ThothRealtimeVoice.sendRunEvent({json.dumps(text)}, {json.dumps(meta)});
  }}
}})();
"""
