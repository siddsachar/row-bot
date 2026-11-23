"""The Live voice options: which cloud fronts exist, what they cost, who hears the audio.

Prices are approximate, from each provider's published pricing on the date
shown; the person's provider bills them, never Row-Bot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRICES_CHECKED = "22 Nov 2026"
# Where the person adds a provider's API key (Settings › Models › API providers).
PROVIDERS_LINK = "/settings/models#providers-api"


@dataclass(frozen=True)
class LiveVoiceOption:
    id: str
    label: str
    company: str
    credential: str
    transport: str
    price: str
    # The front sees what the person shares on camera or screen.
    shares: bool = False


LIVE_VOICE_OPTIONS: tuple[LiveVoiceOption, ...] = (
    # gpt-realtime-2: $32 / $64 per 1M audio tokens in / out.
    LiveVoiceOption("openai", "OpenAI Realtime", "OpenAI", "openai", "openai_realtime", "about $0.10 a minute"),
    # gemini-3.8-live: $0.005 a minute in, $0.018 a minute out.
    LiveVoiceOption("gemini", "Gemini Live", "Google", "google", "gemini_live", "about $0.02 a minute",
                    shares=True),
    # grok-voice-3.0: $0.08 a minute.
    LiveVoiceOption("grok", "Grok Voice", "xAI", "xai", "grok_voice", "about $0.08 a minute"),
)
LIVE_VOICE_IDS = tuple(option.id for option in LIVE_VOICE_OPTIONS)


def live_voice_option(option_id: str) -> LiveVoiceOption:
    for option in LIVE_VOICE_OPTIONS:
        if option.id == option_id:
            return option
    from row_bot.voice.client_transport import DictationError
    raise DictationError("voice_provider_unavailable")


def live_voice_provider(option_id: str) -> Any:
    """The front for one option, with the person's saved Voice settings."""
    option = live_voice_option(option_id)
    if option.id == "gemini":
        from row_bot.voice.gemini_live import GeminiLiveProvider
        return GeminiLiveProvider()
    if option.id == "grok":
        from row_bot.voice.grok_voice import GrokVoiceProvider
        return GrokVoiceProvider()
    from row_bot.voice.openai_realtime import OpenAIRealtimeProvider
    from row_bot.voice.runtime import load_voice_runtime_settings
    settings = load_voice_runtime_settings()
    return OpenAIRealtimeProvider(model=settings.talk_model, voice=settings.realtime_voice)


def live_voice_rows(configured: dict[str, bool]) -> list[dict[str, Any]]:
    """One row per option for the client: availability is the saved key (no provider call)."""
    return [{"id": option.id, "label": option.label, "company": option.company,
             "price": option.price, "price_checked": PRICES_CHECKED, "shares": option.shares,
             "available": bool(configured.get(option.id)),
             "reason": "available" if configured.get(option.id) else "realtime_key_missing"}
            for option in LIVE_VOICE_OPTIONS]


def live_voice_readiness() -> list[dict[str, Any]]:
    from row_bot.voice.provider_base import provider_api_key
    return live_voice_rows({option.id: bool(provider_api_key(option.credential)) for option in LIVE_VOICE_OPTIONS})
