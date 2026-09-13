from __future__ import annotations

import json


def start_realtime_client_js(
    *, sink_id: int, session_id: int, thread_id: str = "", generation_id: str = ""
) -> str:
    """Retained NiceGUI wrapper around the same packaged browser runtime."""
    from pathlib import Path

    source = Path(__file__).with_name("realtime_runtime.js").read_text(encoding="utf-8")
    source = source.replace("export async function startRealtimeRuntime", "async function startRealtimeRuntime", 1)
    return "(async function() {\n" + source + f"""
  return startRealtimeRuntime({{
    sessionId: {json.dumps(session_id)},
    threadId: {json.dumps(thread_id)},
    generationId: {json.dumps(generation_id)},
    emit(detail) {{
      const sinkId = {json.dumps(sink_id)};
      const el = typeof getElement === 'function' ? getElement(sinkId) :
        document.querySelector('[data-nicegui-id="' + sinkId + '"]');
      if (el) el.dispatchEvent(new CustomEvent('row-bot-realtime-event', {{detail}}));
    }},
    async bootstrap() {{
      const response = await fetch('/api/voice/realtime/client-secret', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}}
      }});
      if (!response.ok) throw new Error(await response.text() || 'Realtime token request failed');
      return response.json();
    }},
    async exchange(sdp, key) {{
      const response = await fetch('https://api.openai.com/v1/realtime/calls', {{
        method: 'POST', body: sdp,
        headers: {{Authorization: 'Bearer ' + key, 'Content-Type': 'application/sdp'}}
      }});
      if (!response.ok) throw new Error(await response.text() || 'Realtime SDP exchange failed');
      return response.text();
    }}
  }});
}})();
"""


def stop_realtime_client_js() -> str:
    return """
(async function() {
  window.RowBotRealtimeActivation = null;
  if (window.RowBotRealtimeVoice && window.RowBotRealtimeVoice.stop) {
    await window.RowBotRealtimeVoice.stop();
  }
})();
"""


def clear_realtime_generation_js(*, session_id: int, thread_id: str, generation_id: str) -> str:
    """Release only the completed run; a newer session or run keeps its identity."""
    return f"""
(function() {{
  const runtime = window.RowBotRealtimeVoice;
  if (runtime && runtime.clearGeneration) runtime.clearGeneration(
    {json.dumps(session_id)}, {json.dumps(thread_id)}, {json.dumps(generation_id)});
}})();
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
  if (window.RowBotRealtimeVoice && window.RowBotRealtimeVoice.sendFunctionOutput) {{
    window.RowBotRealtimeVoice.sendFunctionOutput({json.dumps(call_id)}, {json.dumps(output_text)}, {json.dumps(meta)});
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
  if (window.RowBotRealtimeVoice && window.RowBotRealtimeVoice.sendRunEvent) {{
    window.RowBotRealtimeVoice.sendRunEvent({json.dumps(text)}, {json.dumps(meta)});
  }}
}})();
"""
