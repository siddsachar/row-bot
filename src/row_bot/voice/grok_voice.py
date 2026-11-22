"""Grok Voice (xAI) as a Live voice front, relayed by the host.

Grok's Voice Agent API speaks the OpenAI Realtime event schema over a
WebSocket. The host opens it with the person's xAI key (never sent to the
browser), sends the ``session.update`` (the front's instructions, the bridge
tools, 16 kHz audio in and 24 kHz out), relays audio both ways and maps Grok's
events onto Row-Bot's Live voice events.
"""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from row_bot.voice.agent_bridge import realtime_bridge_tool_declarations
from row_bot.voice.live_front import RUN_ANSWER, RUN_STATUS, front_instructions
from row_bot.voice.live_relay import LiveOutput, connect_socket
from row_bot.voice.provider_base import VoiceProviderStatus, live_voice_status, provider_api_key

logger = logging.getLogger(__name__)

GROK_VOICE_MODEL = "grok-voice-latest"
GROK_VOICE_URL = f"wss://api.x.ai/v1/realtime?model={GROK_VOICE_MODEL}"
GROK_VOICE = "eve"
INPUT_RATE = 16000
OUTPUT_RATE = 24000


class GrokVoiceProvider:
    provider_id = "grok_voice"
    display_name = "Grok Voice"
    credential = "xai"
    transport = "grok_voice"

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key

    def _api_key(self) -> str:
        return self.api_key if self.api_key is not None else provider_api_key(self.credential)

    def status(self) -> VoiceProviderStatus:
        return live_voice_status(self, bool(self._api_key()))

    def session_update(self, *, instructions: str = "") -> dict[str, Any]:
        return {"type": "session.update", "session": {
            "instructions": instructions or front_instructions(),
            "voice": GROK_VOICE,
            "turn_detection": {"type": "server_vad"},
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": INPUT_RATE}},
                      "output": {"format": {"type": "audio/pcm", "rate": OUTPUT_RATE}}},
            "tools": realtime_bridge_tool_declarations(),
        }}

    async def open_relay(self, instructions: str, *,
                         connect: Callable[..., Awaitable[Any]] = connect_socket) -> GrokRelay:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("xAI API key is not configured.")
        socket = await connect(GROK_VOICE_URL, headers={"Authorization": f"Bearer {api_key}"})
        relay = GrokRelay(socket)
        await relay.send(self.session_update(instructions=instructions))
        return relay


class GrokRelay:
    """One Grok Voice session's socket: Row-Bot's events out of Grok's (OpenAI-schema) events."""

    def __init__(self, socket: Any) -> None:
        self._socket = socket
        self._active: dict[str, Any] | None = None
        self._awaiting_row_bot = False
        self._segment = 0
        self._origins: dict[str, str] = {}
        self._calls: set[str] = set()

    async def send(self, message: dict) -> None:
        await self._socket.send(json.dumps(message, ensure_ascii=False))

    async def send_audio(self, pcm16: bytes) -> None:
        await self.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm16).decode("ascii")})

    async def _respond(self, response: dict) -> None:
        self._awaiting_row_bot = True
        await self.send({"type": "response.create", "response": response})

    async def send_output(self, output: LiveOutput) -> bool:
        """Say Row-Bot's answer or status; True when Grok was asked to speak."""
        if output.kind == "function_output":
            await self.send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": output.call_id, "output": output.text}})
            if output.silent:
                return False
            await self._respond({})
            return True
        if output.kind == "status" and self._active is not None:
            return False  # A status waits for nothing: dropped while the front is speaking.
        await self._respond({"instructions": (RUN_ANSWER if output.kind == "final" else RUN_STATUS) + output.text})
        return True

    async def stop_speaking(self) -> bool:
        if self._active is None:
            return False
        await self.send({"type": "response.cancel"})
        return True

    async def close(self) -> None:
        await self._socket.close()

    def _origin(self, response_id: str) -> str:
        return self._origins.get(response_id, "front")

    def _call(self, call_id: str, name: str, arguments: str, response_id: str) -> list[tuple]:
        if not call_id or call_id in self._calls or len(call_id) > 128 or len(self._calls) >= 200:
            return []
        self._calls.add(call_id)
        return [("event", {"type": "function_call_ready", "call_id": call_id, "name": name,
                           "arguments": str(arguments or "")[:8192], "response_id": response_id,
                           "origin": self._origin(response_id)})]

    def map_message(self, message: dict) -> list[tuple]:
        kind = str(message.get("type") or "")
        response = message.get("response") if isinstance(message.get("response"), dict) else {}
        response_id = str(response.get("id") or message.get("response_id") or "")[:128]
        if kind == "input_audio_buffer.speech_started":
            actions: list[tuple] = []
            if self._active is not None:
                actions += [("flush",), ("cancel",),
                            ("event", {"type": "barge_in_cancelled", "response_id": self._active["id"]})]
            return [*actions, ("event", {"type": "speech_started"})]
        if kind == "input_audio_buffer.speech_stopped":
            return [("event", {"type": "speech_stopped"})]
        if kind == "conversation.item.input_audio_transcription.completed":
            return [("event", {"type": "transcript_final", "text": str(message.get("transcript") or "")[:4000],
                               "item_id": str(message.get("item_id") or "")[:128]})]
        if kind == "response.created" and response_id:
            origin = "row_bot" if self._awaiting_row_bot else "front"
            self._awaiting_row_bot = False
            self._segment += 1
            if len(self._origins) < 200:
                self._origins[response_id] = origin
            self._active = {"id": response_id, "segment": self._segment}
            return [("event", {"type": "output_started", "response_id": response_id, "origin": origin})]
        if kind in {"response.output_audio.delta", "response.audio.delta"}:
            delta = message.get("delta")
            if self._active is None or not isinstance(delta, str):
                return []
            return [("audio", self._active["segment"], base64.b64decode(delta))]
        if kind in {"response.output_audio_transcript.done", "response.audio_transcript.done"}:
            return [("event", {"type": "assistant_transcript_final", "response_id": response_id,
                               "origin": self._origin(response_id),
                               "text": str(message.get("transcript") or "")[:4000]})]
        if kind == "response.function_call_arguments.done":
            return self._call(str(message.get("call_id") or ""), str(message.get("name") or ""),
                              str(message.get("arguments") or ""), response_id)
        if kind == "response.output_item.done":
            item = message.get("item") if isinstance(message.get("item"), dict) else {}
            if item.get("type") == "function_call":
                return self._call(str(item.get("call_id") or ""), str(item.get("name") or ""),
                                  str(item.get("arguments") or ""), response_id)
            return []
        if kind == "response.done":
            if self._active is not None and self._active["id"] == response_id:
                self._active = None
            return [("event", {"type": "response_done", "response_id": response_id,
                               "origin": self._origin(response_id)})]
        if kind == "error":
            # Grok's own words can echo session context: only the fact is logged.
            logger.warning("Grok Voice reported an error")
        return []

    async def actions(self) -> AsyncIterator[tuple]:
        async for frame in self._socket:
            try:
                message = json.loads(frame)
            except (ValueError, TypeError):
                continue
            if isinstance(message, dict):
                for action in self.map_message(message):
                    if action[0] == "cancel":
                        await self.send({"type": "response.cancel"})
                    else:
                        yield action
