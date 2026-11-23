from __future__ import annotations

from typing import Any
from collections.abc import Callable
import time

import requests

from row_bot.voice.agent_bridge import realtime_bridge_tool_declarations
from row_bot.voice.live_front import front_instructions
from row_bot.voice.provider_base import VoiceProviderStatus, live_voice_status, provider_api_key


CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
CALLS_URL = "https://api.openai.com/v1/realtime/calls"
DEFAULT_REALTIME_MODEL = "gpt-realtime-2"
DEFAULT_REALTIME_VOICE = "marin"
REALTIME_VOICE_OPTIONS: dict[str, str] = {
    "alloy": "Alloy",
    "ash": "Ash",
    "ballad": "Ballad",
    "coral": "Coral",
    "echo": "Echo",
    "sage": "Sage",
    "shimmer": "Shimmer",
    "verse": "Verse",
    "marin": "Marin",
    "cedar": "Cedar",
}


class OpenAIRealtimeProvider:
    provider_id = "openai_realtime"
    display_name = "OpenAI Realtime"
    credential = "openai"
    # WebRTC: the host exchanges the SDP with the ephemeral secret; the browser never holds one.
    transport = "openai_realtime"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_REALTIME_MODEL,
        voice: str = DEFAULT_REALTIME_VOICE,
    ) -> None:
        self.api_key = api_key
        self.model = model if str(model or "").startswith("gpt-realtime") else DEFAULT_REALTIME_MODEL
        self.voice = voice if voice in REALTIME_VOICE_OPTIONS else DEFAULT_REALTIME_VOICE

    def _api_key(self) -> str:
        return self.api_key if self.api_key is not None else provider_api_key(self.credential)

    def status(self) -> VoiceProviderStatus:
        return live_voice_status(self, bool(self._api_key()))

    def session_config(self, *, instructions: str = "") -> dict[str, Any]:
        config: dict[str, Any] = {
            "type": "realtime",
            "model": self.model,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "transcription": {
                        "model": "gpt-realtime-whisper",
                    },
                    "turn_detection": {
                        "type": "semantic_vad",
                        "eagerness": "high",
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {"voice": self.voice},
            },
            "tools": realtime_bridge_tool_declarations(),
            "tool_choice": "auto",
        }
        config["instructions"] = instructions or front_instructions()
        return config

    def create_client_secret(
        self,
        *,
        instructions: str = "",
        expires_after_seconds: int = 600,
    ) -> dict[str, Any]:
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("OpenAI API key is not configured.")
        payload = {
            "expires_after": {"anchor": "created_at", "seconds": expires_after_seconds},
            "session": self.session_config(instructions=instructions),
        }
        response = requests.post(
            CLIENT_SECRETS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        # Only the status leaves: a provider body can echo the session's chat context.
        response.raise_for_status()
        return response.json()

    def exchange_sdp(self, sdp: bytes, *, client_secret: str, validate: Callable[[], None]) -> bytes:
        """One bounded SDP exchange using only the already admitted ephemeral key."""
        from row_bot.voice.client_transport import DictationError
        if (not isinstance(sdp, bytes) or not sdp.startswith(b"v=0") or len(sdp) > 1024 * 1024
                or not isinstance(client_secret, str) or not 1 <= len(client_secret) <= 4096
                or any(character in client_secret for character in "\r\n")):
            raise DictationError("invalid_voice_sdp")
        started = time.monotonic()
        def current() -> None:
            validate()
            if time.monotonic() - started >= 25:
                raise DictationError("realtime_exchange_timeout")
        current()
        # A read can remain blocked for at most five seconds beyond the last
        # deadline check. Redirects and requests-level retries are not admitted.
        response = requests.post(CALLS_URL, data=sdp, headers={"Authorization": "Bearer " + client_secret,
            "Content-Type": "application/sdp", "Accept": "application/sdp"},
            timeout=(5, 5), stream=True, allow_redirects=False)
        try:
            current()
            if not 200 <= response.status_code < 300:
                code = "realtime_auth_unavailable" if response.status_code in {401, 403} else \
                    "realtime_quota_or_rate_limit" if response.status_code == 429 else "realtime_provider_unavailable"
                raise DictationError(code)
            if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/sdp":
                raise DictationError("invalid_voice_sdp")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                current()
                if len(data) + len(chunk) > 1024 * 1024:
                    raise DictationError("voice_sdp_too_large")
                data.extend(chunk)
            current()
            if not data.startswith(b"v=0"):
                raise DictationError("invalid_voice_sdp")
            return bytes(data)
        finally:
            response.close()
