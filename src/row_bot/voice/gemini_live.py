"""Gemini Live (Google) as a Live voice front.

The host mints a single-use ephemeral token with the person's Google key and
locks the session to the model, instructions and bridge tools built here
(``liveConnectConstraints``). The browser connects to the constrained Live
WebSocket with the token and sends the same ``setup`` message first.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from row_bot.voice.agent_bridge import realtime_bridge_tool_declarations
from row_bot.voice.live_front import front_instructions
from row_bot.voice.provider_base import VoiceProviderStatus, live_voice_status, provider_api_key

AUTH_TOKENS_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"
GEMINI_LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
                   "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained")
GEMINI_LIVE_MODEL = "gemini-3.8-live"
# A session may run this long on one token (the voice lease ends sooner).
SESSION_SECONDS = 11 * 60
# The bridge's consult runs while the front keeps talking (asynchronous function calling).
_NON_BLOCKING = {"row_bot_agent_consult"}


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


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GeminiLiveProvider:
    provider_id = "gemini_live"
    display_name = "Gemini Live"
    credential = "google"
    transport = "gemini_live"

    def __init__(self, *, api_key: str | None = None, clock: Callable[[], float] = time.time) -> None:
        self.api_key = api_key
        self._clock = clock

    def _api_key(self) -> str:
        return self.api_key if self.api_key is not None else provider_api_key(self.credential)

    def status(self) -> VoiceProviderStatus:
        return live_voice_status(self, bool(self._api_key()))

    def live_config(self, *, instructions: str = "") -> dict[str, Any]:
        return {
            "responseModalities": ["AUDIO"],
            "systemInstruction": {"parts": [{"text": instructions or front_instructions()}]},
            "tools": [{"functionDeclarations": function_declarations()}],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }

    def setup_message(self, *, instructions: str = "") -> dict[str, Any]:
        config = self.live_config(instructions=instructions)
        return {"setup": {
            "model": f"models/{GEMINI_LIVE_MODEL}",
            "generationConfig": {"responseModalities": config.pop("responseModalities")},
            **config,
        }}

    def create_client_secret(self, *, instructions: str = "", expires_after_seconds: int = 60) -> dict[str, Any]:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("Google API key is not configured.")
        now = self._clock()
        start_by = now + expires_after_seconds
        response = requests.post(
            AUTH_TOKENS_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "uses": 1,
                "expireTime": _iso(now + SESSION_SECONDS),
                "newSessionExpireTime": _iso(start_by),
                "liveConnectConstraints": {"model": f"models/{GEMINI_LIVE_MODEL}",
                                           "config": self.live_config(instructions=instructions)},
            },
            timeout=30,
        )
        # Only the status leaves, never the body.
        response.raise_for_status()
        return {
            "value": response.json().get("name"),
            "expires_at": start_by,
            "url": GEMINI_LIVE_URL,
            "setup": self.setup_message(instructions=instructions),
        }
