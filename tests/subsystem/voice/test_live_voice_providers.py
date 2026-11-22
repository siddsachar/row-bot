"""The three Live voice fronts with fake HTTP and fake provider sockets: session shape, relay mapping, readiness."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
import requests

from row_bot.voice.agent_bridge import REALTIME_ALLOWED_TOOLS
from row_bot.voice.gemini_live import GEMINI_LIVE_MODEL, GEMINI_LIVE_URL, GeminiLiveProvider
from row_bot.voice.grok_voice import GROK_VOICE_URL, GrokVoiceProvider
from row_bot.voice.live_front import RUN_ANSWER, RUN_STATUS, front_instructions, live_outputs
from row_bot.voice.live_relay import LiveOutput
from row_bot.voice.live_voice import live_voice_rows
from row_bot.voice.openai_realtime import CLIENT_SECRETS_URL, DEFAULT_REALTIME_MODEL, OpenAIRealtimeProvider


class _Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.text = json.dumps(body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self.body


class FakeSocket:
    """A provider's WebSocket: what the host sent, and frames it will read."""

    def __init__(self, *incoming):
        self.sent: list[dict] = []
        self.incoming = list(incoming)
        self.closed = False

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        return self.incoming.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)

    async def close(self):
        self.closed = True


def connector(socket):
    opened = []

    async def connect(url, *, headers):
        opened.append((url, headers))
        return socket

    return connect, opened


@pytest.fixture
def posts(monkeypatch):
    calls, replies = [], []

    def post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        return replies.pop(0)

    monkeypatch.setattr("row_bot.voice.openai_realtime.requests.post", post)
    return calls, replies


def test_openai_secret_carries_the_session_instructions_and_only_bridge_tools(posts):
    calls, replies = posts
    replies.append(_Response({"value": "ek_synthetic", "expires_at": 1060}))
    data = OpenAIRealtimeProvider(api_key="sk_synthetic", voice="cedar").create_client_secret(
        instructions="Synthetic front instructions", expires_after_seconds=60)
    assert data == {"value": "ek_synthetic", "expires_at": 1060}
    sent = calls[0]
    assert sent["url"] == CLIENT_SECRETS_URL and sent["headers"]["Authorization"] == "Bearer sk_synthetic"
    session = sent["json"]["session"]
    assert sent["json"]["expires_after"] == {"anchor": "created_at", "seconds": 60}
    assert session["model"] == DEFAULT_REALTIME_MODEL and session["audio"]["output"]["voice"] == "cedar"
    assert session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert session["instructions"] == "Synthetic front instructions"
    assert [tool["name"] for tool in session["tools"]] == list(REALTIME_ALLOWED_TOOLS)


def test_openai_defaults_to_the_front_instructions_and_sanitizes_stale_values():
    config = OpenAIRealtimeProvider(api_key="sk", model="local-whisper-base", voice="nope").session_config()
    assert config["instructions"] == front_instructions()
    assert config["model"] == DEFAULT_REALTIME_MODEL and config["audio"]["output"]["voice"] == "marin"


def test_provider_failure_never_carries_the_response_body(posts):
    calls, replies = posts
    replies.append(_Response({"error": {"message": "echo of Synthetic private chat"}}, status=400))
    with pytest.raises(requests.HTTPError) as caught:
        OpenAIRealtimeProvider(api_key="sk").create_client_secret(instructions="Synthetic private chat")
    assert "Synthetic private chat" not in str(caught.value)
    assert caught.value.response.status_code == 400


def test_gemini_relay_opens_from_the_host_with_the_key_sends_the_setup_and_waits_for_it():
    socket = FakeSocket(json.dumps({"setupComplete": {}}).encode())
    connect, opened = connector(socket)
    relay = asyncio.run(GeminiLiveProvider(api_key="g_synthetic").open_relay("Synthetic front", connect=connect))
    # The key goes in the host's own request headers, never in a page or a URL.
    assert opened == [(GEMINI_LIVE_URL, {"x-goog-api-key": "g_synthetic"})]
    setup = socket.sent[0]["setup"]
    assert setup["model"] == f"models/{GEMINI_LIVE_MODEL}"
    assert setup["systemInstruction"] == {"parts": [{"text": "Synthetic front"}]}
    declarations = setup["tools"][0]["functionDeclarations"]
    assert [item["name"] for item in declarations] == list(REALTIME_ALLOWED_TOOLS)
    assert declarations[0]["behavior"] == "NON_BLOCKING" and declarations[0]["parameters"]["type"] == "OBJECT"
    assert "additionalProperties" not in json.dumps(declarations)
    asyncio.run(relay.send_audio(b"\x01\x00\x02\x00"))
    assert socket.sent[1] == {"realtimeInput": {"audio": {"data": base64.b64encode(b"\x01\x00\x02\x00").decode(),
                                                         "mimeType": "audio/pcm;rate=16000"}}}


def test_gemini_messages_become_row_bot_events_and_its_consult_is_answered_with_a_tool_response():
    socket = FakeSocket()
    from row_bot.voice.gemini_live import GeminiRelay
    relay = GeminiRelay(socket)
    pcm = base64.b64encode(b"\x00\x00\xff\x7f").decode()
    actions = []
    for message in (
        {"serverContent": {"inputTranscription": {"text": "Good "}}},
        {"serverContent": {"inputTranscription": {"text": "morning"}}},
        {"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": pcm}}]},
                           "outputTranscription": {"text": "Morning!"}}},
        {"serverContent": {"turnComplete": True}},
        {"toolCall": {"functionCalls": [{"id": "call-1", "name": "row_bot_agent_consult",
                                         "args": {"request": "weather"}}]}},
    ):
        actions += relay.map_message(message)
    kinds = [action[1]["type"] if action[0] == "event" else action[0] for action in actions]
    assert kinds == ["speech_started", "speech_stopped", "transcript_final", "output_started", "audio",
                     "assistant_transcript_final", "response_done", "function_call_ready"]
    assert actions[2][1]["text"] == "Good morning"
    assert actions[4] == ("audio", actions[4][1], b"\x00\x00\xff\x7f")
    assert actions[5][1]["text"] == "Morning!" and actions[5][1]["origin"] == "front"
    assert actions[7][1] == {"type": "function_call_ready", "call_id": "call-1", "name": "row_bot_agent_consult",
                             "arguments": '{"request": "weather"}', "response_id": ""}
    asked = asyncio.run(relay.send_output(LiveOutput("a", "function_output", '{"status":"completed","answer":"Sunny."}',
                                                     "call-1")))
    assert asked and socket.sent[-1] == {"toolResponse": {"functionResponses": [{
        "id": "call-1", "name": "row_bot_agent_consult",
        "response": {"status": "completed", "answer": "Sunny.", "scheduling": "INTERRUPT"}}]}}
    # The next turn is Row-Bot's answer, not the front's own line.
    actions = relay.map_message({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": pcm}}]}}})
    assert actions[0][1]["origin"] == "row_bot"
    # A status is dropped while the front speaks; an interruption stops the turn.
    assert not asyncio.run(relay.send_output(LiveOutput("c", "status", "Working on: web search.")))
    actions = relay.map_message({"serverContent": {"interrupted": True}})
    assert actions[0] == ("flush",) and [a[1]["type"] for a in actions[1:]] == ["barge_in_cancelled",
                                                                              "response_cancelled"]
    assert asyncio.run(relay.send_output(LiveOutput("d", "status", "Working on: web search.")))
    assert socket.sent[-1] == {"clientContent": {"turns": [{"role": "user", "parts": [
        {"text": RUN_STATUS + "Working on: web search."}]}], "turnComplete": True}}


def test_grok_relay_opens_from_the_host_with_the_key_and_sends_its_session_first():
    socket = FakeSocket()
    connect, opened = connector(socket)
    asyncio.run(GrokVoiceProvider(api_key="xai_synthetic").open_relay("Synthetic front", connect=connect))
    assert opened == [(GROK_VOICE_URL, {"Authorization": "Bearer xai_synthetic"})]
    session = socket.sent[0]["session"]
    assert socket.sent[0]["type"] == "session.update" and session["instructions"] == "Synthetic front"
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 16000}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert [tool["name"] for tool in session["tools"]] == list(REALTIME_ALLOWED_TOOLS)


def test_grok_events_become_row_bot_events_barge_in_cancels_and_answers_go_back_on_its_schema():
    from row_bot.voice.grok_voice import GrokRelay
    socket = FakeSocket()
    relay = GrokRelay(socket)
    delta = base64.b64encode(b"\x10\x00").decode()
    actions = []
    for message in (
        {"type": "input_audio_buffer.speech_started"},
        {"type": "input_audio_buffer.speech_stopped"},
        {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Hi"},
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "response.output_audio.delta", "response_id": "r1", "delta": delta},
        {"type": "response.output_audio_transcript.done", "response_id": "r1", "transcript": "Hello!"},
        {"type": "response.function_call_arguments.done", "response_id": "r1", "call_id": "c1",
         "name": "row_bot_agent_consult", "arguments": '{"request":"x"}'},
        {"type": "response.output_item.done", "response_id": "r1",
         "item": {"type": "function_call", "call_id": "c1", "name": "row_bot_agent_consult"}},
    ):
        actions += relay.map_message(message)
    kinds = [action[1]["type"] if action[0] == "event" else action[0] for action in actions]
    assert kinds == ["speech_started", "speech_stopped", "transcript_final", "output_started", "audio",
                     "assistant_transcript_final", "function_call_ready"]
    assert actions[4] == ("audio", 1, b"\x10\x00")
    # Talking over the reply: playback flushes, the response is cancelled, the barge-in recorded.
    barge = relay.map_message({"type": "input_audio_buffer.speech_started"})
    assert barge[:2] == [("flush",), ("cancel",)] and barge[2][1]["type"] == "barge_in_cancelled"
    relay.map_message({"type": "response.done", "response": {"id": "r1"}})
    assert asyncio.run(relay.send_output(LiveOutput("a", "function_output", '{"answer":"Done."}', "c1")))
    assert socket.sent[-2:] == [
        {"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": "c1",
                                                      "output": '{"answer":"Done."}'}},
        {"type": "response.create", "response": {}}]
    assert relay.map_message({"type": "response.created", "response": {"id": "r2"}})[0][1]["origin"] == "row_bot"
    assert not asyncio.run(relay.send_output(LiveOutput("b", "status", "Working on: files.")))
    relay.map_message({"type": "response.done", "response": {"id": "r2"}})
    asyncio.run(relay.send_output(LiveOutput("c", "final", "All set.")))
    assert socket.sent[-1] == {"type": "response.create", "response": {"instructions": RUN_ANSWER + "All set."}}


def test_what_the_front_is_asked_to_say_from_the_runs_view():
    answered: dict[str, str] = {}
    working = {"run_id": "run", "state": "running", "call_id": "call", "function_output": None,
               "cues": [{"id": "cue-0", "text": "Working on: web search."}]}
    assert live_outputs(working, answered) == [{"id": "run:cue-0", "kind": "status", "text": "Working on: web search."}]
    approval = {"run_id": "run", "state": "waiting_approval", "call_id": "call",
                "function_output": '{"status":"needs_approval"}', "cues": []}
    assert live_outputs(approval, answered)[0]["kind"] == "function_output"
    answered["call"] = "run:waiting_approval"
    done = {"run_id": "run", "state": "completed", "call_id": "call", "text": "Done.",
            "function_output": '{"status":"completed","answer":"Done."}', "cues": []}
    # The call was already answered: the later answer is said as Row-Bot's.
    assert live_outputs(done, answered) == [{"id": "run:completed", "kind": "final", "text": "Done."}]


@pytest.mark.parametrize("provider", [OpenAIRealtimeProvider, GeminiLiveProvider, GrokVoiceProvider])
def test_readiness_follows_the_saved_key_without_a_provider_call(provider, posts):
    calls, _ = posts
    missing = provider(api_key="").status()
    assert not missing.ready and missing.unavailable_code == "realtime_key_missing" and not missing.local
    assert provider(api_key="synthetic-key").status().ready
    assert not calls


def test_rows_say_who_hears_the_audio_what_it_costs_and_why_unavailable():
    rows = {row["id"]: row for row in live_voice_rows({"gemini": True})}
    assert list(rows) == ["openai", "gemini", "grok"]
    assert rows["gemini"]["available"] and rows["gemini"]["reason"] == "available"
    assert not rows["openai"]["available"] and rows["openai"]["reason"] == "realtime_key_missing"
    assert [rows[key]["company"] for key in rows] == ["OpenAI", "Google", "xAI"]
    assert all(row["price"].endswith("a minute") and row["price_checked"] for row in rows.values())
