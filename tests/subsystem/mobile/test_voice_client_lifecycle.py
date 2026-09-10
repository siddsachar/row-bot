"""U08: transport events must retain their session, conversation and run owner."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from row_bot.ui import voice_realtime_events as events
from row_bot.voice.actions import ActiveVoiceSurfaceBinding
from row_bot.voice.coordinator import VoiceSessionCoordinator


@pytest.fixture
def client(monkeypatch):
    coordinator = VoiceSessionCoordinator(SimpleNamespace(is_running=False))
    session = coordinator.start_realtime_talk()
    state = SimpleNamespace(thread_id="A", voice_coordinator=coordinator,
                            voice_enabled=True, voice_input_mode="talk")
    p = SimpleNamespace(active_voice_binding=None)
    sent, delivered, cancelled, notices = [], [], [], []
    monkeypatch.setattr(events, "_active_generations", {})
    monkeypatch.setattr(events.ui, "notify", lambda *a, **k: notices.append(a))
    import row_bot.ui.streaming as streaming
    monkeypatch.setattr(streaming, "run_realtime_client_js", lambda *a, **k: delivered.append(a) or True)
    monkeypatch.setattr(streaming, "request_generation_stop", lambda thread, **k: cancelled.append(thread) or True)

    async def send(text, **kwargs):
        sent.append((state.thread_id, text, kwargs))

    async def dispatch(kind="function_call_ready", **payload):
        await events.handle_realtime_event(
            {"type": kind, "session_id": session, "thread_id": "A", "generation_id": "",
             "name": "row_bot_agent_consult", "call_id": "call", "arguments": {"request": "inspect files"},
             **payload}, state=state, p=p, send_message=send)

    return SimpleNamespace(state=state, p=p, coordinator=coordinator, session=session,
                           dispatch=dispatch, send=send, sent=sent, delivered=delivered,
                           cancelled=cancelled, notices=notices)


@pytest.mark.parametrize("kind", ["function_call_ready", "consult_fallback_needed", "output_started",
                                    "assistant_transcript_final", "response_done", "fatal_error", "server_error"])
@pytest.mark.parametrize("identity", ["session", "thread", "run", "missing", "stopped", "dictate"])
def test_stale_event_never_dispatches_or_mutates(client, kind, identity):
    payload = {}
    if identity == "session":
        payload["session_id"] = client.session + 1
    elif identity == "thread":
        payload["thread_id"] = "B"
    elif identity == "run":
        payload["generation_id"] = "old"
    elif identity == "missing":
        payload["session_id"] = None
    elif identity == "stopped":
        client.coordinator.stop()
    else:
        client.coordinator.start_browser("dictate")
    before = client.coordinator.diagnostic_snapshot()
    asyncio.run(client.dispatch(kind, text="inspect the repo", response_id="old-output", **payload))
    assert client.coordinator.diagnostic_snapshot() == before
    assert not client.sent and not client.delivered and not client.cancelled and not client.notices
    assert client.coordinator.queued_realtime_tool_call is None


@pytest.mark.parametrize("kind", ["function_call_ready", "consult_fallback_needed"])
def test_current_consult_reaches_only_normal_sender(client, kind):
    asyncio.run(client.dispatch(kind, text="inspect the repo"))
    assert len(client.sent) == 1
    assert client.sent[0][0] == "A"
    assert client.sent[0][2] == {"voice_mode": True}
    assert client.coordinator.queued_realtime_tool_call is not None
    assert not client.delivered


@pytest.mark.parametrize("switch", ["thread", "run", "session", "binding"])
def test_awaited_result_cannot_mutate_new_owner(client, monkeypatch, switch):
    from row_bot.voice.agent_bridge import VoiceAgentBridge

    async def delayed(self, **kwargs):
        if switch == "thread":
            client.state.thread_id = "B"
        elif switch == "run":
            events._active_generations["A"] = SimpleNamespace(generation_id="new-run")
        elif switch == "session":
            client.coordinator.start_realtime_talk()
        else:
            client.p.active_voice_binding = SimpleNamespace(is_current=lambda _: True)
        await self._send_message("late request")
        kwargs["queue_consult"]({"name": "late"})
        return {"output": "late output", "deferred": False}

    monkeypatch.setattr(VoiceAgentBridge, "handle_realtime_function_call", delayed)
    asyncio.run(client.dispatch(name="row_bot_agent_control"))
    assert not client.sent and not client.delivered and not client.cancelled
    assert client.coordinator.queued_realtime_tool_call is None


def test_current_control_stops_captured_run_and_reports_approval(client):
    events._active_generations["A"] = SimpleNamespace(
        generation_id="A:run", status="streaming", interrupt_data={"approval": True}, pending_tools={})
    asyncio.run(client.dispatch(name="row_bot_agent_control", generation_id="A:run",
                               arguments={"action": "status"}))
    assert "approval" in client.delivered[0][1]
    asyncio.run(client.dispatch(name="row_bot_agent_control", generation_id="A:run",
                               arguments={"action": "cancel"}))
    assert client.cancelled == ["A"]
    assert not client.sent


def test_handler_survives_activation_binding_but_rejects_replaced_surface(client):
    handler = events.make_realtime_event_handler(state=client.state, p=client.p, send_message=client.send)
    binding = SimpleNamespace(is_current=lambda thread: thread == "A")
    client.p.active_voice_binding = binding
    payload = SimpleNamespace(args={"type": "output_started", "session_id": client.session, "response_id": "one"})
    asyncio.run(handler(payload))
    assert client.coordinator.active_realtime_response_id == "one"
    client.state.thread_id = "B"
    asyncio.run(handler(SimpleNamespace(args={**payload.args, "response_id": "two"})))
    client.state.thread_id = "A"
    client.p.active_voice_binding = SimpleNamespace(is_current=lambda _: True)
    asyncio.run(handler(SimpleNamespace(args={**payload.args, "response_id": "three"})))
    assert client.coordinator.active_realtime_response_id == "one"


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


def test_final_spoken_output_can_finish_after_run_leaves_active_map(client):
    client.coordinator.set_active_row_bot_generation("A:finished")
    client.coordinator.record_realtime_output_started(response_id="final", session_id=client.session)
    asyncio.run(client.dispatch("response_done", generation_id="A:finished", response_id="final"))
    assert client.coordinator.state == "listening"
    assert client.coordinator.active_row_bot_generation_id == ""
    assert "clearGeneration" in client.delivered[0][1]
    asyncio.run(client.dispatch("consult_fallback_needed", text="new request"))
    assert len(client.sent) == 1


def test_cleared_binding_cannot_send_or_read_or_write_composer():
    actions = []
    binding = ActiveVoiceSurfaceBinding("chat", "A", lambda: actions.append("read") or "draft",
                                        lambda text: actions.append(text), lambda text: actions.append(text))
    binding.clear()
    asyncio.run(binding.send_talk("late talk"))
    assert binding.append_dictation("late dictation") == ""
    assert actions == []


def test_generated_fake_webrtc_producer_preserves_run_identity_and_cleans_late_capture(client):
    from row_bot.voice.realtime_client import start_realtime_client_js, stop_realtime_client_js

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the generated transport with fake WebRTC")
    scripts = json.dumps({"start": start_realtime_client_js(sink_id=7, session_id=client.session,
                                                            thread_id="A", generation_id="run-1"),
                          "stop": stop_realtime_client_js()})
    source = r"""
const scripts = SCRIPTS;
globalThis.window = globalThis;
globalThis.location = {origin:'https://voice-fixture.invalid', host:'voice-fixture.invalid'};
const emitted = [], fetches = [], tracks = [], peers = [];
globalThis.getElement = () => ({dispatchEvent: event => emitted.push(event.detail)});
globalThis.CustomEvent = class { constructor(type, options) { this.detail = options.detail; } };
globalThis.document = {body: {appendChild() {}}, createElement: () => ({style:{}, remove() {}})};
let holdMicrophone = false, releaseMicrophone;
const stream = () => { const track = {stopped:false, stop() {this.stopped = true;}}; tracks.push(track); return {getTracks: () => [track]}; };
Object.defineProperty(globalThis, 'navigator', {value:{mediaDevices:{getUserMedia: async () => {
  if (holdMicrophone) return new Promise(resolve => { releaseMicrophone = () => resolve(stream()); });
  return stream();
}}}, configurable:true});
globalThis.fetch = async (url) => { fetches.push(url); return {ok:true, json: async () => ({value:'fake-token'}), text: async () => 'fake-sdp'}; };
globalThis.setTimeout = () => 1;
globalThis.clearTimeout = () => {};
globalThis.RTCPeerConnection = class {
  constructor() {peers.push(this); this.closed = false;}
  close() {this.closed = true;}
  addTrack() {}
  createDataChannel() {this.dc = {readyState:'open', listeners:{}, addEventListener(kind, fn) {this.listeners[kind] = fn;}, send() {}, close() {}}; return this.dc;}
  async createOffer() {return {sdp:'fake-offer'};}
  async setLocalDescription() {}
  async setRemoteDescription() {}
};
await eval(scripts.start);
const runtime = window.RowBotRealtimeVoice, dc = peers[0].dc;
const deliver = payload => dc.listeners.message({data:JSON.stringify(payload)});
deliver({type:'response.created', response:{id:'old', metadata:{thread_id:'A', generation_id:'run-1'}}});
runtime.sendRunEvent('synthetic status', {thread_id:'A', generation_id:'run-2'});
deliver({type:'response.output_item.done', response_id:'old', item:{type:'function_call', call_id:'old-call', name:'row_bot_agent_control', arguments:'{"action":"status"}'}});
deliver({type:'response.created', response:{id:'new', metadata:{thread_id:'A', generation_id:'run-2'}}});
deliver({type:'response.output_item.done', response_id:'new', item:{type:'function_call', call_id:'new-call', name:'row_bot_agent_control', arguments:'{"action":"status"}'}});
const calls = emitted.filter(event => event.type === 'function_call_ready');
if (runtime.clearGeneration(1, 'A', 'run-1')) throw Error('stale clear released newer run');
if (!runtime.clearGeneration(1, 'A', 'run-2')) throw Error('current clear failed');
runtime.emit('speech_started');
if (emitted.at(-1).generation_id !== '') throw Error('next turn retained completed run');
await eval(scripts.stop);
const stoppedCount = emitted.length;
deliver({type:'response.created', response:{id:'late'}});
if (emitted.length !== stoppedCount || runtime.sendEvent({type:'late'})) throw Error('stopped callbacks resumed');
holdMicrophone = true;
const pending = eval(scripts.start);
for (let i=0; i<50 && !releaseMicrophone; i++) await Promise.resolve();
if (!releaseMicrophone) throw Error('capture not pending');
await eval(scripts.stop);
const before = fetches.length;
releaseMicrophone();
await pending;
if (fetches.length !== before || tracks.some(track => !track.stopped) || peers.some(peer => !peer.closed)) throw Error('stopped capture leaked');
console.log(JSON.stringify({calls, stopped:tracks.length, fetches:fetches.length}));
""".replace("SCRIPTS", scripts)
    result = subprocess.run([node, "--input-type=module"], input=source, text=True,
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    produced = json.loads(result.stdout)
    assert produced["stopped"] == 2
    assert [call["generation_id"] for call in produced["calls"]] == ["run-1", "run-2"]
    events._active_generations["A"] = SimpleNamespace(generation_id="run-2", status="streaming")
    for payload in produced["calls"]:
        asyncio.run(events.handle_realtime_event(payload, state=client.state, p=client.p, send_message=client.send))
    assert len(client.delivered) == 1
    assert "new-call" in client.delivered[0][1]
    assert not client.sent and not client.cancelled
