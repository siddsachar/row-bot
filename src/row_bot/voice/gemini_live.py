"""Gemini Live (Google) as a Live voice front, relayed by the host.

The browser speaks only to Row-Bot. The host opens Gemini's Live WebSocket
with the person's Google key (never sent to the browser), sends the setup
(model, the front's instructions and the bridge tools), relays 16 kHz audio up
and 24 kHz audio down, and maps Gemini's messages onto Row-Bot's Live voice
events. The consult is asynchronous (``NON_BLOCKING``), so Gemini keeps talking
while the agent works; its answer comes back as a ``toolResponse``.
"""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from row_bot.voice.agent_bridge import realtime_bridge_tool_declarations
from row_bot.voice.live_front import RUN_ANSWER, RUN_STATUS, front_instructions
from row_bot.voice.live_relay import LiveOutput, connect_socket
from row_bot.voice.provider_base import VoiceProviderStatus, live_voice_status, provider_api_key

GEMINI_LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
                   "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
GEMINI_LIVE_MODEL = "gemini-3.8-live"
INPUT_RATE = 16000
_CONSULT = "row_bot_agent_consult"
# The bridge's consult runs while the front keeps talking (asynchronous function calling).
_NON_BLOCKING = {_CONSULT}


def _schema(value: Any) -> Any:
    """A JSON schema in Gemini's OpenAPI subset: upper-case types, no additionalProperties."""
    if isinstance(value, dict):
        return {key: (str(item).upper() if key == "type" else _schema(item))
                for key, item in value.items() if key != "additionalProperties"}
    if isinstance(value, list):
        return [_schema(item) for item in value]
    return value


def function_declarations() -> list[dict[str, Any]]:
    declarations = []
    for tool in realtime_bridge_tool_declarations():
        declaration = {"name": tool["name"], "description": tool["description"],
                       "parameters": _schema(tool["parameters"])}
        if tool["name"] in _NON_BLOCKING:
            declaration["behavior"] = "NON_BLOCKING"
        declarations.append(declaration)
    return declarations


class GeminiLiveProvider:
    provider_id = "gemini_live"
    display_name = "Gemini Live"
    credential = "google"
    transport = "gemini_live"

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key

    def _api_key(self) -> str:
        return self.api_key if self.api_key is not None else provider_api_key(self.credential)

    def status(self) -> VoiceProviderStatus:
        return live_voice_status(self, bool(self._api_key()))

    def setup_message(self, *, instructions: str = "") -> dict[str, Any]:
        return {"setup": {
            "model": f"models/{GEMINI_LIVE_MODEL}",
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "systemInstruction": {"parts": [{"text": instructions or front_instructions()}]},
            "tools": [{"functionDeclarations": function_declarations()}],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}

    async def open_relay(self, instructions: str, *,
                         connect: Callable[..., Awaitable[Any]] = connect_socket) -> GeminiRelay:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("Google API key is not configured.")
        socket = await connect(GEMINI_LIVE_URL, headers={"x-goog-api-key": api_key})
        relay = GeminiRelay(socket)
        await relay.open(self.setup_message(instructions=instructions))
        return relay


class GeminiRelay:
    """One Gemini Live session's socket: Row-Bot's events out of Gemini's messages."""

    def __init__(self, socket: Any) -> None:
        self._socket = socket
        self._counter = 0
        self._heard = ""
        self._heard_item = ""
        self._turn: dict[str, Any] | None = None
        self._row_bot_next = False
        self._names: dict[str, str] = {}

    async def _send(self, message: dict) -> None:
        await self._socket.send(json.dumps(message, ensure_ascii=False))

    async def open(self, setup: dict) -> None:
        await self._send(setup)
        # Nothing else may be sent until Gemini has taken the setup.
        while True:
            if "setupComplete" in _decode(await self._socket.recv()):
                return

    async def send_audio(self, pcm16: bytes) -> None:
        await self._send({"realtimeInput": {"audio": {
            "data": base64.b64encode(pcm16).decode("ascii"), "mimeType": f"audio/pcm;rate={INPUT_RATE}"}}})

    async def send_output(self, output: LiveOutput) -> bool:
        """Say Row-Bot's answer or status; True when Gemini was asked to speak."""
        if output.kind == "function_output":
            name = self._names.get(output.call_id)
            if not name:
                return False
            try:
                parsed = json.loads(output.text)
            except ValueError:
                parsed = None
            response = parsed if isinstance(parsed, dict) else {"output": output.text}
            if name == _CONSULT:
                # The agent's answer, said now; a silent result is only kept.
                response = {**response, "scheduling": "SILENT" if output.silent else "INTERRUPT"}
                self._row_bot_next = not output.silent
            await self._send({"toolResponse": {"functionResponses": [{"id": output.call_id, "name": name,
                                                                      "response": response}]}})
            return name == _CONSULT and not output.silent
        if output.kind == "status" and self._turn is not None:
            return False  # A status waits for nothing: dropped while the front is speaking.
        self._row_bot_next = True
        prompt = (RUN_ANSWER if output.kind == "final" else RUN_STATUS) + output.text
        await self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": prompt}]}],
                                            "turnComplete": True}})
        return True

    async def stop_speaking(self) -> bool:
        return False  # Gemini stops on its own when the person speaks.

    async def close(self) -> None:
        await self._socket.close()

    def _end_heard(self) -> list[tuple]:
        if not self._heard:
            return []
        heard, self._heard = self._heard, ""
        return [("event", {"type": "speech_stopped"}),
                ("event", {"type": "transcript_final", "text": heard[:4000], "item_id": self._heard_item})]

    def _start_turn(self) -> list[tuple]:
        if self._turn is not None:
            return []
        actions = self._end_heard()
        self._counter += 1
        self._turn = {"id": f"gemini-{self._counter}", "segment": self._counter,
                      "origin": "row_bot" if self._row_bot_next else "front", "text": ""}
        self._row_bot_next = False
        return [*actions, ("event", {"type": "output_started", "response_id": self._turn["id"],
                                     "origin": self._turn["origin"]})]

    def _end_turn(self, kind: str) -> list[tuple]:
        turn, self._turn = self._turn, None
        if turn is None:
            return []
        actions = []
        if turn["text"]:
            actions.append(("event", {"type": "assistant_transcript_final", "response_id": turn["id"],
                                      "origin": turn["origin"], "text": turn["text"][:4000]}))
        return [*actions, ("event", {"type": kind, "response_id": turn["id"], "origin": turn["origin"]})]

    def map_message(self, message: dict) -> list[tuple]:
        actions: list[tuple] = []
        content = message.get("serverContent") or {}
        heard = (content.get("inputTranscription") or {}).get("text")
        if heard:
            if not self._heard:
                self._counter += 1
                self._heard_item = f"heard-{self._counter}"
                actions.append(("event", {"type": "speech_started"}))
            self._heard = (self._heard + heard)[:4000]
        if content.get("interrupted"):
            # The person spoke over the reply: stop what is still queued to play.
            actions.append(("flush",))
            actions.append(("event", {"type": "barge_in_cancelled",
                                      "response_id": self._turn["id"] if self._turn else ""}))
            actions += self._end_turn("response_cancelled")
        for part in (content.get("modelTurn") or {}).get("parts") or []:
            data = (part.get("inlineData") or {}).get("data")
            if data:
                actions += self._start_turn()
                actions.append(("audio", self._turn["segment"], base64.b64decode(data)))
        spoken = (content.get("outputTranscription") or {}).get("text")
        if spoken:
            actions += self._start_turn()
            self._turn["text"] += spoken
        for call in (message.get("toolCall") or {}).get("functionCalls") or []:
            call_id, name = str(call.get("id") or ""), str(call.get("name") or "")
            if not call_id or len(call_id) > 128 or len(self._names) >= 200:
                continue
            self._names[call_id] = name
            actions += self._end_heard()
            actions.append(("event", {"type": "function_call_ready", "call_id": call_id, "name": name,
                                      "arguments": json.dumps(call.get("args") or {})[:8192],
                                      "response_id": self._turn["id"] if self._turn else ""}))
        if content.get("turnComplete"):
            actions += self._end_turn("response_done")
        return actions

    async def actions(self) -> AsyncIterator[tuple]:
        async for frame in self._socket:
            for action in self.map_message(_decode(frame)):
                yield action


def _decode(frame: Any) -> dict:
    """A frame as JSON (Gemini sends text frames as binary); anything else is ignored."""
    try:
        value = json.loads(frame.decode("utf-8") if isinstance(frame, bytes) else frame)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return {}
    return value if isinstance(value, dict) else {}
