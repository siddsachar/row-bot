"""Grok Voice (xAI) as a Live voice front.

Grok's Voice Agent API speaks the OpenAI Realtime event schema over a
WebSocket. The host mints a one-session client secret with the person's xAI
key; the browser connects with it (as a WebSocket subprotocol) and sends the
``session.update`` built here first, since xAI secrets carry no session.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests

from row_bot.voice.agent_bridge import realtime_bridge_tool_declarations
from row_bot.voice.live_front import front_instructions
from row_bot.voice.provider_base import VoiceProviderStatus, live_voice_status, provider_api_key

CLIENT_SECRETS_URL = "https://api.x.ai/v1/realtime/client_secrets"
GROK_VOICE_MODEL = "grok-voice-latest"
GROK_VOICE_URL = f"wss://api.x.ai/v1/realtime?model={GROK_VOICE_MODEL}"
GROK_VOICE = "eve"
# The browser sends and plays 24 kHz mono PCM16.
GROK_SAMPLE_RATE = 24000


class GrokVoiceProvider:
    provider_id = "grok_voice"
    display_name = "Grok Voice"
    credential = "xai"
    transport = "grok_voice"

    def __init__(self, *, api_key: str | None = None, clock: Callable[[], float] = time.time) -> None:
        self.api_key = api_key
        self._clock = clock

    def _api_key(self) -> str:
        return self.api_key if self.api_key is not None else provider_api_key(self.credential)

    def status(self) -> VoiceProviderStatus:
        return live_voice_status(self, bool(self._api_key()))

    def session_update(self, *, instructions: str = "") -> dict[str, Any]:
        audio_format = {"type": "audio/pcm", "rate": GROK_SAMPLE_RATE}
        return {"type": "session.update", "session": {
            "instructions": instructions or front_instructions(),
            "voice": GROK_VOICE,
            "turn_detection": {"type": "server_vad"},
            "audio": {"input": {"format": audio_format}, "output": {"format": audio_format}},
            "tools": realtime_bridge_tool_declarations(),
        }}

    def create_client_secret(self, *, instructions: str = "", expires_after_seconds: int = 60) -> dict[str, Any]:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("xAI API key is not configured.")
        response = requests.post(
            CLIENT_SECRETS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"expires_after": {"seconds": int(expires_after_seconds)}},
            timeout=30,
        )
        # Only the status leaves, never the body.
        response.raise_for_status()
        data = response.json()
        # xAI documents the secret at the top level; some responses nest it as OpenAI does.
        secret = data.get("client_secret") if isinstance(data.get("client_secret"), dict) else data
        expires_at = secret.get("expires_at")
        return {
            "value": secret.get("value"),
            "expires_at": expires_at if type(expires_at) in {int, float} else self._clock() + expires_after_seconds,
            "url": GROK_VOICE_URL,
            "setup": self.session_update(instructions=instructions),
        }
