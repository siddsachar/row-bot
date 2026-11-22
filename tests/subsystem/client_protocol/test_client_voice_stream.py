"""The Row-Bot Voice socket: ticket, origin and session binding, and a spoken turn end to end.

The engine runs with fake speech and turn models; the agent is the scripted
normal-chat stream. No audio device, model or network is used.
"""
# ruff: noqa: F811 -- shared isolated fixtures.
from __future__ import annotations

import json
import struct
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from starlette.websockets import WebSocketDisconnect

from row_bot.voice.coordinator import VoiceSessionCoordinator
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream
from tests.helpers.voice_fakes import (
    SILENT, VOICED, FakeClock, FakeSpeechToText, fake_browser_speech, fake_engine_parts,
)
from tests.subsystem.client_protocol.test_protocol_application import (  # noqa: F401
    _client, _command, _remote_client, service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.voice.test_dictation_lifecycle import Voice

pytestmark = pytest.mark.subsystem
STREAM = "ws://localhost/api/v1/voice/stream"


def _bind(service, *finals: str):
    clock = FakeClock()
    stt = FakeSpeechToText(clock, *finals)
    engine = SimpleNamespace(readiness=lambda: None, build=lambda: fake_engine_parts(clock, stt))
    voice = Voice()
    service.bind_voice(VoiceSessionCoordinator(), browser_service=lambda: fake_browser_speech(voice),
                       voice_engine=engine)
    service.talk.tick_seconds = 0.05
    service.readiness_factory = lambda controls: True
    return stt


def _conversation(client, headers):
    result = _command(client, headers, "conversation.create", {"title": "Synthetic voice"})
    assert result.status_code == 200
    conversation = result.json()["conversation_id"]
    body = {"request_id": str(uuid4()), "conversation_revision": result.json()["revision"],
            "model_selection": {"provider_id": "fixture", "model_ref": "model:fixture:model"}, "write_targets": []}
    return conversation, body


def _start(client, headers, conversation, body):
    response = client.post(f"/api/v1/conversations/{conversation}/voice/talk", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def voice(service):
    stt = _bind(service, "Synthetic spoken request")
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation, body = _conversation(client, headers)
        yield SimpleNamespace(client=client, headers=headers, conversation=conversation, body=body,
                              service=service, stt=stt)
    service.close_voice()


def _until(socket, kind: str, *, limit: int = 400) -> dict:
    for _ in range(limit):
        message = socket.receive()
        if message.get("text"):
            event = json.loads(message["text"])
            if event["type"] == kind:
                return event
    raise AssertionError(f"no {kind} event")


def _refused(client, ticket, *, origin="http://localhost"):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(STREAM, headers={"Origin": origin}) as socket:
            socket.send_text(json.dumps({"type": "hello", "ticket": ticket}))
            socket.receive_text()
    return closed.value.code


def test_the_socket_needs_a_live_single_use_ticket(voice):
    started = _start(voice.client, voice.headers, voice.conversation, voice.body)
    assert _refused(voice.client, "not-a-ticket-but-long-enough") == 1008
    with voice.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
        ready = _until(socket, "ready")
        assert ready["input_sample_rate"] == 16000 and ready["captions"] is True
        socket.send_text(json.dumps({"type": "end"}))
    # Used once: the same ticket never opens a second socket.
    assert _refused(voice.client, started["ticket"]) == 1008
    # Ending from the bar stops the lease, and its ticket route refuses afterwards.
    handle = started["snapshot"]["handle"]
    url = f"/api/v1/conversations/{voice.conversation}/voice/talk/{handle['lease_id']}/ticket"
    identity = {"voice_session_id": handle["voice_session_id"], "server_epoch": handle["server_epoch"]}
    assert voice.client.post(url, headers=voice.headers, json=identity).status_code == 410


def test_another_origin_or_a_stopped_lease_is_refused(voice):
    started = _start(voice.client, voice.headers, voice.conversation, voice.body)
    assert _refused(voice.client, started["ticket"], origin="http://evil.example") == 1008
    with pytest.raises(WebSocketDisconnect):
        with voice.client.websocket_connect(STREAM) as socket:  # No Origin at all.
            socket.receive_text()
    handle = started["snapshot"]["handle"]
    ticket = voice.client.post(
        f"/api/v1/conversations/{voice.conversation}/voice/talk/{handle['lease_id']}/ticket",
        headers=voice.headers,
        json={"voice_session_id": handle["voice_session_id"], "server_epoch": handle["server_epoch"]}).json()["ticket"]
    stop = voice.client.post(
        f"/api/v1/conversations/{voice.conversation}/voice/talk/{handle['lease_id']}/stop", headers=voice.headers,
        json={"voice_session_id": handle["voice_session_id"], "server_epoch": handle["server_epoch"]})
    assert stop.status_code == 200
    assert _refused(voice.client, ticket) == 1008


def test_a_signed_out_device_loses_its_open_voice_socket(service):
    _bind(service)
    client, active = _remote_client(service)
    with client:
        _, headers = bootstrap(client)
        conversation, body = _conversation(client, headers)
        started = _start(client, headers, conversation, body)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
                socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
                _until(socket, "ready")
                active["value"] = False
                socket.send_bytes(SILENT)
                while True:  # Nothing more arrives: the server closes the socket.
                    socket.receive_text()
    service.close_voice()


@pytest.mark.slow
def test_a_spoken_turn_runs_the_normal_agent_and_its_answer_comes_back_as_speech(voice):
    heard = []
    scripted = ScriptedAgentStream((("token", "Hello there. "), ("token", "How can I help?"),
                                    CheckpointCommit((AIMessage(id="spoken-answer",
                                                                content="Hello there. How can I help?"),),
                                                     "spoken-answer"), ("done", None)))

    def stream(text, *args, **kwargs):
        heard.append(text)
        yield from scripted.stream(text, *args, **kwargs)

    voice.service.stream_factory = stream
    started = _start(voice.client, voice.headers, voice.conversation, voice.body)
    captions, audio = [], 0
    with voice.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
        _until(socket, "ready")
        for frame in [VOICED] * 30 + [SILENT] * 20:
            socket.send_bytes(frame)
        for _ in range(400):
            message = socket.receive()
            if message.get("bytes"):
                kind, segment = struct.unpack("<BI", message["bytes"][:5])
                assert kind == 1 and segment >= 1
                audio += len(message["bytes"]) - 5
            elif message.get("text"):
                event = json.loads(message["text"])
                if event["type"] == "caption" and event["final"] and event["text"]:
                    captions.append((event["role"], event["text"]))
                if ("assistant", "How can I help?") in captions:
                    break
        socket.send_text(json.dumps({"type": "end"}))
    assert ("user", "Synthetic spoken request") in captions
    assert [text for role, text in captions if role == "assistant"] == ["Hello there.", "How can I help?"]
    assert audio > 0
    assert any("Synthetic spoken request" in text for text in heard)
    assert voice.service.dictation.coordinator._dictation_lease.revoked


def _call(call_id, name):
    return {"id": call_id, "name": name, "args": {}, "type": "tool_call"}


def _spoken_turn(voice, *steps, last: str):
    """One spoken turn over the socket, run by *steps*; returns what was said aloud and the run's configs."""
    configs = []
    scripted = ScriptedAgentStream(tuple(steps))

    def stream(text, tools, config, *, stop_event=None):
        configs.append(config["configurable"])
        yield from scripted.stream(text, tools, config, stop_event=stop_event)

    voice.service.stream_factory = stream
    started = _start(voice.client, voice.headers, voice.conversation, voice.body)
    said = []
    with voice.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
        _until(socket, "ready")
        for frame in [VOICED] * 30 + [SILENT] * 20:
            socket.send_bytes(frame)
        for _ in range(2000):
            message = socket.receive()
            if message.get("text"):
                event = json.loads(message["text"])
                if event["type"] == "caption" and event["role"] == "assistant":
                    said.append(event["text"])
                    if event["text"] == last:
                        break
        socket.send_text(json.dumps({"type": "end"}))
    return said, configs


@pytest.mark.slow
def test_a_multi_agent_turn_speaks_its_whole_answer_never_its_tool_results(voice):
    final = " ".join(f"Helper finding {index} is worth hearing." for index in range(1, 9))
    said, configs = _spoken_turn(voice, ("token", "Let me ask two helpers. "), ("token", final), CheckpointCommit((
        AIMessage(id="plan", content="Let me ask two helpers.", tool_calls=[_call("call-delegate", "delegate_work")]),
        ToolMessage(content="Started helpers A and B.", tool_call_id="call-delegate", name="delegate_work"),
        AIMessage(id="wait", content="", tool_calls=[_call("call-wait", "agent_wait")]),
        ToolMessage(content="Helper A: private tool output.", tool_call_id="call-wait", name="agent_wait"),
        AIMessage(id="final", content=final)), "final"), ("done", None),
        last="Helper finding 8 is worth hearing.")
    # A spoken turn runs in voice mode, and the whole answer is heard, with no cap.
    assert configs and all(config.get("voice_mode") is True for config in configs)
    assert said == ["Let me ask two helpers."] + [f"Helper finding {index} is worth hearing." for index in range(1, 9)]
    assert not any("private tool output" in text or "Started helpers" in text for text in said)


@pytest.mark.slow
def test_a_final_answer_written_with_a_tool_call_is_spoken_without_the_code(voice):
    answer = 'I saved that to your notes. Here is the gist: buy milk.\n```json\n{"note": "buy milk"}\n```'
    said, _ = _spoken_turn(voice, ("token", answer), CheckpointCommit((
        AIMessage(id="final", content=answer, tool_calls=[_call("call-save", "save_note")]),
        ToolMessage(content='{"saved": true}', tool_call_id="call-save", name="save_note")), "final"),
        ("done", None), last="Here is the gist: buy milk.")
    assert said == ["I saved that to your notes.", "Here is the gist: buy milk."]


def _typed(voice, text: str) -> str:
    response = _command(voice.client, voice.headers, "conversation.submit", {
        "submission_id": str(uuid4()), "text": text, "attachment_refs": [],
        "model_selection": voice.body["model_selection"], "write_targets": []},
        target=voice.conversation, revision=voice.body["conversation_revision"])
    assert response.status_code == 202, response.text
    return response.json()["generation_id"]


def _recording(voice):
    configs = []

    def stream(text, tools, config, *, stop_event=None):
        configs.append(config["configurable"])
        yield from ScriptedAgentStream((("token", "Typed answer, heard aloud. "), ("done", None))).stream(
            text, tools, config, stop_event=stop_event)

    voice.service.stream_factory = stream
    return configs


@pytest.mark.slow
def test_typed_turns_while_voice_listens_run_in_voice_mode_and_are_spoken(voice):
    configs = _recording(voice)
    started = _start(voice.client, voice.headers, voice.conversation, voice.body)
    with voice.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
        _until(socket, "ready")
        first = _typed(voice, "Typed while voice is on")
        caption = _until(socket, "caption", limit=2000)
        while caption.get("role") != "assistant":
            caption = _until(socket, "caption", limit=2000)
        assert caption["text"] == "Typed answer, heard aloud."
        socket.send_text(json.dumps({"type": "end"}))
    assert voice.service.registry.conversation_generation(voice.conversation, first).producer_done.wait(5)
    assert configs[-1].get("voice_mode") is True
    # With voice ended, a typed turn is text only again.
    run = _typed(voice, "Typed after voice")
    assert voice.service.registry.conversation_generation(voice.conversation, run).producer_done.wait(5)
    assert not configs[-1].get("voice_mode")


@pytest.mark.parametrize("listening", [True, False])
def test_a_typed_turn_runs_in_voice_mode_only_while_voice_listens(voice, listening):
    configs = _recording(voice)
    listener = lambda event, handle: None  # noqa: E731 -- a voice session's listener
    listener.live = lambda: listening  # Paused (or ended) sessions do not listen.
    voice.service.voice_listeners[voice.conversation] = listener
    try:
        run = _typed(voice, "Typed with voice open")
        assert voice.service.registry.conversation_generation(voice.conversation, run).producer_done.wait(5)
    finally:
        voice.service.voice_listeners.pop(voice.conversation, None)
    assert configs and bool(configs[-1].get("voice_mode")) is listening


def _caption(socket, **match) -> dict:
    while True:
        caption = _until(socket, "caption", limit=2000)
        if all(caption.get(key) == value for key, value in match.items()):
            return caption


@pytest.mark.slow
def test_a_spoken_yes_after_the_summary_approves_an_undoable_action_through_the_card(service):
    stt = _bind(service, "Tidy my files", "Yes, go ahead.")
    answers = []

    def stream(_text, _enabled, _config, *, stop_event=None):
        yield "interrupt", [{"__interrupt_id": "card-1", "tool": "workspace_move_file", "label": "Move / rename file",
                             "description": "Move / rename file: 'a.md' to 'notes/a.md'",
                             "args": {"source_path": "a.md", "destination_path": "notes/a.md"}}]

    def resume(_enabled, _config, approved, *, interrupt_ids=None, stop_event=None):
        answers.append(approved)
        yield "token", "Moved it."
        yield "done", None

    service.stream_factory, service.resume_factory = stream, resume
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation, body = _conversation(client, headers)
        started = _start(client, headers, conversation, body)
        with client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
            socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
            _until(socket, "ready")
            for frame in [VOICED] * 30 + [SILENT] * 20:
                socket.send_bytes(frame)
            asked = _until(socket, "approval", limit=2000)
            assert asked["voice"] is True and asked["action"] == "Move / rename file?"
            summary = _caption(socket, kind="approval")
            assert summary["text"] == "I need your OK. Move / rename file? Say yes, or tap Approve."
            socket.send_text(json.dumps({"type": "playback", "state": "idle"}))
            for frame in [VOICED] * 30 + [SILENT] * 20:
                socket.send_bytes(frame)
            assert _until(socket, "approval", limit=2000)["approval_id"] == ""
            assert _caption(socket, role="assistant")["text"] == "Moved it."
            socket.send_text(json.dumps({"type": "end"}))
    assert answers == [True]
    assert [text for text, _ in stt.finished] == ["Tidy my files", "Yes, go ahead."]
    service.close_voice()
