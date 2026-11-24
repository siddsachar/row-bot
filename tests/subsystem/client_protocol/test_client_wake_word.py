"""The wake word over HTTP and the voice socket: desktop only, off by default, single-use tickets.

The model is the loudness stand-in from ``tests/fixtures/wake_standin.py``; no
microphone, network or real model is used.
"""
# ruff: noqa: F811 -- shared isolated fixtures.
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from row_bot.voice.coordinator import VoiceSessionCoordinator
from row_bot.voice.wake import WakeOptions
from tests.fixtures.wake_standin import write_standin
from tests.helpers.voice_fakes import fake_browser_speech
from tests.subsystem.client_protocol.test_client_voice_stream import STREAM, _conversation, _start
from tests.subsystem.client_protocol.test_protocol_application import (  # noqa: F401
    _client, _remote_client, service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.voice.test_dictation_lifecycle import Voice
from tests.helpers.voice_fakes import FakeClock, FakeSpeechToText, fake_engine_parts

pytestmark = pytest.mark.subsystem
LOUD = np.array([9830, -9830] * 160, np.int16).tobytes()  # 20 ms at amplitude 0.3


def _bind(service, tmp_path, *, enabled: dict, model: bool = True):
    clock = FakeClock()
    stt = FakeSpeechToText(clock, "Synthetic spoken request")
    engine = SimpleNamespace(readiness=lambda: None, build=lambda: fake_engine_parts(clock, stt))
    path = write_standin(tmp_path) if model else tmp_path / "absent.onnx"
    voice = Voice()
    service.bind_voice(VoiceSessionCoordinator(), browser_service=lambda: fake_browser_speech(voice),
                       voice_engine=engine,
                       wake_options=WakeOptions(enabled=lambda: enabled["value"], model_path=path))
    service.readiness_factory = lambda controls: True


@pytest.fixture
def wake(service, tmp_path):
    enabled = {"value": True}
    _bind(service, tmp_path, enabled=enabled)
    with _client(service) as client:
        _, headers = bootstrap(client)
        yield SimpleNamespace(client=client, headers=headers, enabled=enabled, service=service)
    service.close_voice()


def _ticket(wake):
    response = wake.client.post("/api/v1/voice/wake", headers=wake.headers)
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _hello(socket, ticket, mode="wake"):
    socket.send_text(json.dumps({"type": "hello", "mode": mode, "ticket": ticket}))


def test_the_desktop_app_can_arm_it_and_hears_the_wake_word(wake):
    capability = wake.client.get("/api/v1/voice/dictation/capability", headers=wake.headers).json()
    assert capability["wake_reason"] == "available"
    with wake.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        _hello(socket, _ticket(wake))
        assert json.loads(socket.receive_text()) == {"type": "ready", "input_sample_rate": 16000}
        for _ in range(4):
            socket.send_bytes(LOUD)
        assert json.loads(socket.receive_text()) == {"type": "wake"}
        socket.send_text(json.dumps({"type": "end"}))


def test_a_wake_ticket_is_single_use_and_never_opens_a_voice_session(wake):
    ticket = _ticket(wake)
    with wake.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        _hello(socket, ticket)
        socket.receive_text()
        socket.send_text(json.dumps({"type": "end"}))
    for mode in ("wake", None):
        with pytest.raises(WebSocketDisconnect) as closed:
            with wake.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
                _hello(socket, ticket, mode=mode)
                socket.receive_text()
        assert closed.value.code == 1008


def test_a_voice_session_ticket_does_not_open_the_wake_mode(wake):
    conversation, body = _conversation(wake.client, wake.headers)
    started = _start(wake.client, wake.headers, conversation, body)
    with pytest.raises(WebSocketDisconnect) as closed:
        with wake.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
            _hello(socket, started["ticket"])
            socket.receive_text()
    assert closed.value.code == 1008


def test_it_hears_nothing_while_a_voice_session_holds_the_microphone(wake):
    conversation, body = _conversation(wake.client, wake.headers)
    _start(wake.client, wake.headers, conversation, body)
    with wake.client.websocket_connect(STREAM, headers={"Origin": "http://localhost"}) as socket:
        _hello(socket, _ticket(wake))
        socket.receive_text()
        for _ in range(40):
            socket.send_bytes(LOUD)
        socket.send_text(json.dumps({"type": "end"}))
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_text()
        assert closed.value.code == 1000


def test_it_is_off_until_the_person_turns_it_on(wake):
    wake.enabled["value"] = False
    refused = wake.client.post("/api/v1/voice/wake", headers=wake.headers)
    assert refused.status_code == 409 and refused.json()["code"] == "wake_word_off"


def test_it_listens_on_this_computer_only(service, tmp_path):
    _bind(service, tmp_path, enabled={"value": True})
    client, _ = _remote_client(service)
    with client:
        _, headers = bootstrap(client)
        capability = client.get("/api/v1/voice/dictation/capability", headers=headers).json()
        assert capability["wake_reason"] == "not_this_computer"
        refused = client.post("/api/v1/voice/wake", headers=headers)
        assert refused.status_code == 409 and refused.json()["code"] == "desktop_only"
    service.close_voice()


def test_without_the_model_it_says_so(service, tmp_path):
    _bind(service, tmp_path, enabled={"value": True}, model=False)
    with _client(service) as client:
        _, headers = bootstrap(client)
        capability = client.get("/api/v1/voice/dictation/capability", headers=headers).json()
        assert capability["wake_reason"] == "wake_model_missing"
        refused = client.post("/api/v1/voice/wake", headers=headers)
        assert refused.status_code == 409 and refused.json()["code"] == "wake_model_missing"
    service.close_voice()
