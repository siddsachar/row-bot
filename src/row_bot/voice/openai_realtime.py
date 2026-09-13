from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable
import time

import requests
from requests import HTTPError

from row_bot.api_keys import get_key
from row_bot.voice.agent_bridge import REALTIME_WAIT_TOOL, realtime_bridge_tool_declarations
from row_bot.voice.provider_base import VoiceProviderStatus


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


ROW_BOT_REALTIME_INSTRUCTIONS = """You are Row-Bot in realtime voice mode.
Speak as Row-Bot directly, in the same helpful style and level of substance as
normal chat. For any substantive request, current information, memory, file or
workspace question, browser or computer control, tool use, approval-sensitive
action, or status about work in progress, call row_bot_agent_consult or
row_bot_agent_control. Do not claim that you used tools, changed files, browsed,
remembered something, or received approval unless the tool output says so. You
may use brief natural backchannel speech while waiting, but when the bridge
returns a result, speak it naturally and faithfully without framing it as
another assistant's work.

If the latest audio is silence, background noise, hold music, TV audio, side
conversation, assistant echo, or speech not addressed to Row-Bot, call
wait_for_user. Do not respond conversationally after calling wait_for_user. Do
not say filler like "I'm here", "I didn't catch that", "take your time", or
"let me know when you're ready". Resume normal responses only when the user
clearly addresses Row-Bot or asks for help."""


@dataclass(frozen=True)
class RealtimeProviderEvent:
    type: str
    text: str = ""
    raw: dict[str, Any] | None = None


class OpenAIRealtimeProvider:
    provider_id = "openai_realtime"
    display_name = "OpenAI Realtime"

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
        if self.api_key is not None:
            return str(self.api_key or "")
        return str(get_key("OPENAI_API_KEY") or "")

    def status(self) -> VoiceProviderStatus:
        configured = bool(self._api_key())
        return VoiceProviderStatus(
            provider_id=self.provider_id,
            display_name=self.display_name,
            ready=configured,
            reason="OpenAI API key configured." if configured else "Connect OpenAI in Providers.",
            local=False,
        )

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
        config["instructions"] = instructions or ROW_BOT_REALTIME_INSTRUCTIONS
        if REALTIME_WAIT_TOOL not in config["instructions"]:
            config["instructions"] = f"{config['instructions']}\n\nUse {REALTIME_WAIT_TOOL} for non-addressed or idle audio."
        return config

    def transcription_session_config(self, *, language: str = "en") -> dict[str, Any]:
        return {
            "type": "transcription",
            "audio": {
                "input": {
                    "transcription": {
                        "model": "gpt-realtime-whisper",
                        "language": language,
                    },
                },
            },
        }

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
        try:
            response.raise_for_status()
        except HTTPError as exc:
            detail = response.text.strip()
            message = f"OpenAI Realtime client secret request failed: {exc}"
            if detail:
                message = f"{message}; response: {detail[:1000]}"
            raise RuntimeError(message) from exc
        return response.json()

    def map_server_event(self, event: dict[str, Any]) -> RealtimeProviderEvent | None:
        event_type = str(event.get("type") or "")
        if event_type in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}:
            return RealtimeProviderEvent(type=event_type, raw=event)
        if event_type in {
            "conversation.item.input_audio_transcription.completed",
            "response.output_audio_transcript.done",
        }:
            text = str(event.get("transcript") or event.get("text") or "")
            return RealtimeProviderEvent(type="transcript_final", text=text, raw=event)
        if event_type in {
            "response.output_audio.delta",
            "response.output_audio.done",
        }:
            return RealtimeProviderEvent(type=event_type, raw=event)
        if event_type in {
            "response.function_call_arguments.delta",
            "response.output_item.done",
            "response.done",
        }:
            return RealtimeProviderEvent(type=event_type, raw=event)
        if event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            return RealtimeProviderEvent(type="error", text=str(error.get("message") or event.get("message") or "Realtime error"), raw=event)
        return None

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
