"""Live voice over HTTP and its socket: start, ticket, events, the host's relay, saved lines and answered consults.

The providers are fakes: OpenAI's SDP exchange returns a synthetic answer and a
relayed front (Gemini Live, Grok Voice) is a fake relay the test feeds. The
agent is the scripted normal-chat stream. No provider, audio device or network.
"""
# ruff: noqa: F811 -- shared isolated fixtures.
from __future__ import annotations

import asyncio
import json
import queue
import struct
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from starlette.websockets import WebSocketDisconnect

from row_bot.voice.coordinator import VoiceSessionCoordinator
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier
from tests.helpers.voice_fakes import FakeClock, FakeSpeechToText, fake_browser_speech, fake_engine_parts
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.voice.test_dictation_lifecycle import Voice

pytestmark = pytest.mark.subsystem
LIVE = "ws://localhost/api/v1/voice/live"
ORIGIN = {"Origin": "http://localhost"}
LONG_LIVED = "sk-synthetic-long-lived"


class FakeRelay:
    """A relayed front's provider socket, as the host sees it."""

    def __init__(self, instructions: str) -> None:
        self.instructions = instructions
        self.audio: list[bytes] = []
        self.images: list = []
        self.outputs: list = []
        self.said = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.closed = False

    async def send_audio(self, pcm16: bytes) -> None:
        self.audio.append(pcm16)

    async def send_image(self, still) -> None:
        self.images.append(still)

    async def send_output(self, output) -> bool:
        self.outputs.append(output)
        self.said.set()
        return True

    async def stop_speaking(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True
        self.events.put(None)

    async def actions(self):
        while True:
            item = await asyncio.to_thread(self.events.get)
            if item is None:
                return
            yield item

    def wait_for(self, predicate, limit: int = 50):
        for _ in range(limit):
            self.said.clear()
            match = next((output for output in self.outputs if predicate(output)), None)
            if match is not None:
                return match
            assert self.said.wait(5), "the relay was told nothing"
        raise AssertionError("no matching output")


@pytest.fixture
def live(service):
    effects, relays, instructions = [], [], []

    def credentials(**kwargs):
        effects.append("ephemeral-credential")
        instructions.append(kwargs.get("instructions", ""))
        return {"value": "synthetic-ephemeral-only", "expires_at": 60.0}

    def exchange(offer, *, client_secret, validate):
        validate()
        assert client_secret == "synthetic-ephemeral-only"
        effects.append("exchange")
        return b"v=0\r\nsynthetic-answer"

    async def open_relay(text, *, connect=None):
        relay = FakeRelay(text)
        relays.append(relay)
        return relay

    ready = lambda: SimpleNamespace(ready=True)  # noqa: E731
    openai = SimpleNamespace(create_client_secret=credentials, exchange_sdp=exchange, transport="openai_realtime",
                             status=ready, api_key=LONG_LIVED)
    relayed = SimpleNamespace(transport="gemini_live", status=ready, open_relay=open_relay, api_key=LONG_LIVED)
    clock = FakeClock()
    engine = SimpleNamespace(readiness=lambda: None,
                             build=lambda: fake_engine_parts(clock, FakeSpeechToText(clock, "unused")))
    service.bind_voice(VoiceSessionCoordinator(), browser_service=lambda: fake_browser_speech(Voice()),
                       realtime_provider_factory=lambda option: relayed if option != "openai" else openai,
                       voice_engine=engine)
    service.realtime._wall_clock = lambda: 0.0
    service.realtime.poll_seconds = 0.02
    service.realtime.tick_seconds = 0.05
    service.readiness_factory = lambda controls: True
    with _client(service) as client:
        _, headers = bootstrap(client)
        result = _command(client, headers, "conversation.create", {"title": "Synthetic live voice"})
        assert result.status_code == 200
        conversation = result.json()["conversation_id"]
        body = {"request_id": str(uuid4()), "conversation_revision": result.json()["revision"],
                "model_selection": {"provider_id": "fixture", "model_ref": "model:fixture:model"},
                "write_targets": []}
        yield SimpleNamespace(client=client, headers=headers, conversation=conversation, body=body,
                              base=f"/api/v1/conversations/{conversation}/voice/realtime", service=service,
                              effects=effects, relays=relays, instructions=instructions)
    service.close_voice()


def start(live, provider="openai"):
    response = live.client.post(live.base, headers=live.headers,
                                json={**live.body, "request_id": str(uuid4()), "live_provider": provider})
    assert response.status_code == 200, response.text
    assert LONG_LIVED not in response.text and "synthetic-ephemeral-only" not in response.text
    return response.json()


def identity(started):
    handle = started["snapshot"]["handle"]
    return {"voice_session_id": handle["voice_session_id"], "server_epoch": handle["server_epoch"]}


def lease_url(live, started, action):
    return f"{live.base}/{started['snapshot']['handle']['lease_id']}/{action}"


def until(socket, kind: str, *, limit: int = 400) -> dict:
    for _ in range(limit):
        message = socket.receive()
        assert LONG_LIVED not in str(message)
        if message.get("text"):
            event = json.loads(message["text"])
            if event["type"] == kind:
                return event
    raise AssertionError(f"no {kind} message")


def hello(socket, started):
    socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
    return until(socket, "ready")


def browser_event(socket, kind, **fields):
    event_id = str(uuid4())
    socket.send_text(json.dumps({"type": "event", "event": {"event_id": event_id, "type": kind, **fields}}))
    result = until(socket, "event_result")
    assert result["event_id"] == event_id
    return result


def test_start_returns_only_a_ticket_and_the_socket_takes_it_once_from_this_origin(live):
    started = start(live)
    assert set(started) == {"snapshot", "exchange_available", "ticket", "ticket_expires_in_ms"}
    assert started["exchange_available"] is True and started["snapshot"]["transport"] == "openai_realtime"
    with pytest.raises(WebSocketDisconnect):
        with live.client.websocket_connect(LIVE, headers={"Origin": "http://evil.example"}) as socket:
            socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
            socket.receive_text()
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        assert hello(socket, started)["transport"] == "openai_realtime"
        socket.send_text(json.dumps({"type": "end"}))
    with pytest.raises(WebSocketDisconnect):
        with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
            socket.send_text(json.dumps({"type": "hello", "ticket": started["ticket"]}))
            socket.receive_text()
    # A Row-Bot Voice ticket route never renews a Live voice lease, nor the other way round.
    talk_ticket = f"/api/v1/conversations/{live.conversation}/voice/talk/{started['snapshot']['handle']['lease_id']}/ticket"
    assert live.client.post(talk_ticket, headers=live.headers, json=identity(started)).status_code >= 400


def test_openai_events_go_over_the_socket_and_the_fronts_own_lines_are_saved_marked(live):
    from row_bot import threads
    threads.append_checkpoint_messages(live.conversation, [
        HumanMessage(content="Synthetic earlier question about tea"),
        AIMessage(content="Synthetic earlier answer: green tea.")])
    started = start(live)
    assert "Synthetic earlier question about tea" in live.instructions[0]
    exchange = live.client.post(lease_url(live, started, "exchange"), content=b"v=0\r\nsynthetic-offer",
                                headers={**live.headers, "Content-Type": "application/sdp",
                                         "X-Voice-Session-Id": str(identity(started)["voice_session_id"]),
                                         "X-Server-Epoch": identity(started)["server_epoch"]})
    assert exchange.status_code == 200 and exchange.content == b"v=0\r\nsynthetic-answer"
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        hello(socket, started)
        browser_event(socket, "connected")
        browser_event(socket, "speech_started")
        browser_event(socket, "speech_stopped")
        assert browser_event(socket, "transcript_final", item_id="u1", text="Thanks, that is lovely")["accepted"]
        browser_event(socket, "assistant_transcript_final", response_id="r1", origin="front", text="Glad to help!")
        browser_event(socket, "response_done", response_id="r1", origin="front")
        blocked = browser_event(socket, "function_call_ready", call_id="bad", name="shell",
                                arguments='{"command":"forbidden"}')
        assert "blocked" in blocked["function_output"]
        socket.send_text(json.dumps({"type": "end"}))
    saved = threads.get_latest_checkpoint_messages(live.conversation)
    assert [(message.type, message.content) for message in saved[-2:]] == [
        ("human", "Thanks, that is lovely"), ("ai", "Glad to help!")]
    assert saved[-1].additional_kwargs == {"voice_front": True}
    assert not saved[-2].additional_kwargs.get("voice_front")
    assert live.effects == ["ephemeral-credential", "exchange"]


@pytest.mark.slow
def test_a_relayed_front_hears_through_the_host_and_its_consult_gets_progress_then_the_answer(live):
    barrier = StreamBarrier()
    live.service.stream_factory = ScriptedAgentStream((
        ("tool_call", {"name": "web_search", "raw_name": "web_search", "tool_call_id": "tool-1"}), barrier,
        ("token", "It is sunny."),
        CheckpointCommit((AIMessage(id="live-answer", content="It is sunny."),), "live-answer"),
        ("done", None))).stream
    started = start(live, "gemini")
    assert started["snapshot"]["transport"] == "gemini_live" and started["exchange_available"] is False
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        assert hello(socket, started)["output_sample_rate"] == 24000
        relay = live.relays[0]
        assert "Synthetic" not in relay.instructions or relay.instructions  # built by the host, never sent here
        socket.send_bytes(b"\x01\x00" * 160)
        relay.events.put(("event", {"type": "speech_started"}))
        relay.events.put(("event", {"type": "speech_stopped"}))
        relay.events.put(("event", {"type": "transcript_final", "item_id": "u1", "text": "What is the weather"}))
        assert until(socket, "caption") == {"type": "caption", "role": "user", "text": "What is the weather"}
        relay.events.put(("audio", 7, b"\x02\x00" * 8))
        frame = socket.receive_bytes()
        assert struct.unpack("<BI", frame[:5]) == (1, 7) and frame[5:] == b"\x02\x00" * 8
        relay.events.put(("event", {"type": "function_call_ready", "call_id": "consult-1",
                                    "name": "row_bot_agent_consult", "arguments": '{"request":"What is the weather"}'}))
        try:
            assert barrier.entered.wait(5)
            cue = relay.wait_for(lambda output: output.kind == "status")
            assert cue.text == "Working on: web search."
        finally:
            barrier.release.set()
        answer = relay.wait_for(lambda output: output.kind == "function_output")
        assert answer.call_id == "consult-1"
        assert json.loads(answer.text) == {"status": "completed", "answer": "It is sunny."}
        assert relay.audio and relay.audio[0] == b"\x01\x00" * 160
        socket.send_text(json.dumps({"type": "end"}))
    assert relay.closed


@pytest.mark.slow
def test_a_consult_whose_run_ends_before_its_call_is_answered_still_gets_the_answer(live):
    barrier = StreamBarrier()
    live.service.stream_factory = ScriptedAgentStream((
        barrier, ("token", "It is sunny."),
        CheckpointCommit((AIMessage(id="quick-answer", content="It is sunny."),), "quick-answer"),
        ("done", None))).stream
    # A quick answer on a slow machine: the run the consult started ends before the call's own checks finish.
    realtime = live.service.realtime
    submit, active = realtime._submit, realtime._active
    started = {"thread": None, "run_id": None}

    def submitted(owner, *args):
        run_id = submit(owner, *args)
        started["thread"], started["run_id"] = threading.get_ident(), run_id
        return run_id

    def ended_before_answer(owner):
        lease = live.service.dictation.coordinator._dictation_lease
        if started["thread"] == threading.get_ident() and lease.run_id == started["run_id"]:
            run = active(owner)
            if run is not None:
                barrier.release.set()
                assert run.producer_done.wait(5)
                for _ in range(500):
                    if active(owner) is None:
                        break
                    time.sleep(0.01)
        return active(owner)

    realtime._submit, realtime._active = submitted, ended_before_answer
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        hello(socket, start(live, "gemini"))
        relay = live.relays[0]
        relay.events.put(("event", {"type": "function_call_ready", "call_id": "consult-1",
                                    "name": "row_bot_agent_consult", "arguments": '{"request":"What is the weather"}'}))
        answer = relay.wait_for(lambda output: output.kind == "function_output" and output.call_id == "consult-1"
                                and "sunny" in output.text)
        assert json.loads(answer.text) == {"status": "completed", "answer": "It is sunny."}
        assert barrier.exited.is_set()
        socket.send_text(json.dumps({"type": "end"}))
    assert relay.closed


@pytest.mark.slow
@pytest.mark.parametrize("ends_before_answer", [False, True], ids=["run-still-stopping", "run-ends-mid-answer"])
def test_a_cancelled_consult_answers_with_a_short_cancelled_result(live, ends_before_answer):
    barrier = StreamBarrier(release_on_cancel=not ends_before_answer)
    live.service.stream_factory = ScriptedAgentStream((barrier, ("done", None))).stream
    if ends_before_answer:
        # A slow machine: the cancelled run ends after the cancel is checked, before it is answered.
        realtime = live.service.realtime
        control, active = realtime._control, realtime._active
        after_cancel = {"thread": None, "checks": 0}

        def cancel(owner, expected, *args):
            result = control(owner, expected, *args)
            after_cancel["thread"], after_cancel["run"] = threading.get_ident(), expected
            return result

        def ended_mid_answer(owner):
            # The cancel's own check still sees the run; by the event's next check it has ended.
            if after_cancel["thread"] == threading.get_ident():
                after_cancel["checks"] += 1
                if after_cancel["checks"] == 2:
                    barrier.release.set()
                    assert after_cancel["run"].producer_done.wait(5)
                    for _ in range(500):
                        if active(owner) is None:
                            break
                        time.sleep(0.01)
            return active(owner)

        realtime._control, realtime._active = cancel, ended_mid_answer
    started = start(live, "gemini")
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        hello(socket, started)
        relay = live.relays[0]
        relay.events.put(("event", {"type": "function_call_ready", "call_id": "consult-1",
                                    "name": "row_bot_agent_consult", "arguments": '{"request":"Write a long report"}'}))
        assert barrier.entered.wait(5)
        relay.events.put(("event", {"type": "function_call_ready", "call_id": "control-1",
                                    "name": "row_bot_agent_control", "arguments": '{"action":"cancel"}'}))
        stopped = relay.wait_for(lambda output: output.call_id == "control-1")
        assert "cancel" in stopped.text
        answer = relay.wait_for(lambda output: output.call_id == "consult-1")
        assert json.loads(answer.text) == {"status": "cancelled", "answer": "That was stopped."}
        socket.send_text(json.dumps({"type": "end"}))


def test_live_voice_starts_and_stops_again_and_again_on_one_page(live):
    for round_ in range(4):
        started = start(live, "gemini" if round_ % 2 else "openai")
        with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
            hello(socket, started)
            if round_ % 2:
                socket.send_text(json.dumps({"type": "end"}))
            else:
                # Ended from the bar's HTTP stop while the socket is open: the host closes the socket.
                stop = live.client.post(lease_url(live, started, "stop"), headers=live.headers,
                                        json=identity(started))
                assert stop.status_code == 200
                with pytest.raises(WebSocketDisconnect):
                    while True:
                        socket.receive_text()
        snapshot = live.client.post(lease_url(live, started, "stop"), headers=live.headers, json=identity(started))
        assert snapshot.status_code in {200, 410}


def test_capability_is_unavailable_without_a_key_and_names_each_front(live, monkeypatch):
    keys = {"openai": "", "google": "g-synthetic-long-lived", "xai": ""}
    monkeypatch.setattr("row_bot.voice.provider_base.provider_api_key", lambda credential: keys[credential])
    response = live.client.get("/api/v1/voice/dictation/capability", headers=live.headers)
    assert response.status_code == 200, response.text
    value = response.json()
    rows = {row["id"]: row for row in value["live_providers"]}
    assert rows["gemini"]["available"] and rows["gemini"]["company"] == "Google"
    assert rows["openai"]["reason"] == rows["grok"]["reason"] == "realtime_key_missing"
    assert value["realtime_available"] is True and "g-synthetic-long-lived" not in response.text
    keys["google"] = ""
    value = live.client.get("/api/v1/voice/dictation/capability", headers=live.headers).json()
    assert value["realtime_available"] is False and value["realtime_reason"] == "realtime_key_missing"


def _jpeg() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (80, 60), (200, 80, 40)).save(out, "JPEG")
    return out.getvalue()


def test_a_front_that_sees_images_is_shown_stills_at_a_low_rate_while_sharing(live, monkeypatch):
    from row_bot.voice import live_relay

    monkeypatch.setattr(live_relay, "STILL_INTERVAL_SECONDS", 3600.0)  # A second still comes too soon.
    still = _jpeg()
    started = start(live, "gemini")
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        assert hello(socket, started)["sees_images"] is True
        relay = live.relays[0]

        def show(data):
            socket.send_text(json.dumps({"type": "still", "bytes": len(data)}))
            socket.send_bytes(data)

        show(still)  # Not sharing yet: dropped.
        socket.send_text(json.dumps({"type": "share", "source": "camera"}))
        assert until(socket, "sharing")["source"] == "camera"
        show(b"GIF89a" + b"\0" * 32)
        assert until(socket, "error")["code"] == "voice_still_refused"
        show(still)
        show(still)
        socket.send_text(json.dumps({"type": "share", "source": None}))
        assert until(socket, "sharing")["source"] is None
        show(still)
        socket.send_bytes(b"\x01\x00" * 160)  # Microphone audio still reaches the front.
        socket.send_text(json.dumps({"type": "end"}))
    assert [(image.source, image.data) for image in relay.images] == [("camera", still)]
    assert relay.audio == [b"\x01\x00" * 160]


def test_a_front_that_cannot_take_images_is_never_shown_one(live):
    started = start(live)
    with live.client.websocket_connect(LIVE, headers=ORIGIN) as socket:
        assert hello(socket, started)["sees_images"] is False
        socket.send_text(json.dumps({"type": "share", "source": "screen"}))
        assert until(socket, "error")["code"] == "voice_share_unavailable"
        socket.send_text(json.dumps({"type": "end"}))
