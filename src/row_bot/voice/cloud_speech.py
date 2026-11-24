"""Cloud speech parts for Row-Bot Voice: who can listen and speak, what it costs, whose key.

Every option here is optional and off until the person picks it in Settings › Voice. Its
audio (listening) or text (speaking) goes from the host to that company with the person's
own key; the key is never sent to the browser, never logged and never returned. Readiness
is whether a key is saved, read from the key store with no network call.

A chosen cloud part whose key is missing (or was removed) is replaced by the local one, and
the voice bar and Settings say so; it never moves to a different company.

Prices are approximate, from each company's published pricing on the date shown; the
person's provider bills them, never Row-Bot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from row_bot.voice.runtime import VoiceRuntimeSettings

PRICES_CHECKED = "23 Nov 2026"
# Where the person adds an OpenAI or Google key (Settings › Models › Providers).
PROVIDERS_LINK = "/settings/models#providers-api"

Part = Literal["listen", "speak"]
Describe = Literal["", "style", "design"]


@dataclass(frozen=True)
class CloudSpeechOption:
    id: str
    part: Part
    label: str
    company: str
    price: str
    # The key: a Voice setting of its own (key_env in Row-Bot's key store, entered as key_field),
    # or the provider's key in Providers (provider_key).
    key_env: str = ""
    key_field: str = ""
    provider_key: str = ""
    # Speaking: preset voices (id, label), the default, and how a voice can be described.
    voices: tuple[tuple[str, str], ...] = ()
    describe: Describe = ""
    # Speaking: the languages its model speaks (codes), as the company lists them.
    languages: frozenset[str] = frozenset()

    @property
    def speaks(self) -> frozenset[str]:
        """The languages of Settings › Voice › Language this voice speaks."""
        from row_bot.voice.languages import LANGUAGES

        return self.languages & frozenset(LANGUAGES)

    @property
    def voice_field(self) -> str:
        return f"{self.id}_voice" if self.voices else ""

    @property
    def description_field(self) -> str:
        return f"{self.id}_voice_description" if self.describe else ""


LISTEN_OPTIONS: tuple[CloudSpeechOption, ...] = (
    # Nova-3 streaming: $0.0048 a minute (promotional; $0.0058 multilingual).
    CloudSpeechOption("deepgram", "listen", "Deepgram Nova-3", "Deepgram", "about $0.005 a minute",
                      key_env="DEEPGRAM_API_KEY", key_field="deepgram.credential"),
    # AssemblyAI-STT-3.7-Realtime: $0.30 an hour of session; a session lasts one turn here.
    CloudSpeechOption("assemblyai", "listen", "AssemblyAI Realtime", "AssemblyAI", "about $0.005 a minute",
                      key_env="ASSEMBLYAI_API_KEY", key_field="assemblyai.credential"),
    # gpt-live-transcribe: $0.017 a minute of audio.
    CloudSpeechOption("openai_transcribe", "listen", "OpenAI Live Transcribe", "OpenAI", "about $0.017 a minute",
                      provider_key="openai"),
    # gemini-3.5-transcribe-live: about $0.005 a minute in and $0.004 out.
    CloudSpeechOption("gemini_transcribe", "listen", "Gemini Transcribe Live", "Google", "about $0.009 a minute",
                      provider_key="google"),
)

# Flash v2.5's 32 languages; Sonic 3.6's 44; Gemini 3.8 Flash TTS lists over 130 (Urdu is not among them).
ELEVENLABS_LANGUAGES = frozenset(
    "en ja zh de hi fr ko pt it es id nl tr fil pl sv bg ro ar cs el fi hr ms sk da ta uk ru hu no vi".split())
CARTESIA_LANGUAGES = frozenset(
    "en fr de es pt zh ja hi it ko nl pl ru sv tr tl bg ro ar cs el fi hr ms sk da ta uk hu no vi bn th he ka id "
    "te gu kn ml mr pa or ur".split())
GEMINI_TTS_LANGUAGES = frozenset(
    "en es fr de it pt nl pl sv da no fi cs ro hu el tr ru uk ar he fa hi bn ta zh ja ko vi th id ms sw".split())

ELEVENLABS_VOICES = (
    ("EXAVITQu4vr4xnSDxMaL", "Sarah"), ("JBFqnCBsd6RMkjVDRZzb", "George"), ("pFZP5JQG7iQjIQuC4Bku", "Lily"),
    ("nPczCjzI2devNBz1zQrb", "Brian"), ("Xb7hH8MSUJpSbSDYk0k2", "Alice"), ("onwK4e9ZLuTAKqWW03F9", "Daniel"),
)
CARTESIA_VOICES = (
    ("db6b0ed5-d5d3-463d-ae85-518a07d3c2b4", "Skylar"), ("47c38ca4-5f35-497b-b1a3-415245fb35e1", "Daniel"),
    ("9626c31c-bec5-4cca-baa8-f8ba9e84c8bc", "Jacqueline"), ("62ae83ad-4f6a-430b-af41-a9bede9286ca", "Gemma"),
    ("ef191366-f52f-447a-a398-ed8c0f2943a1", "Archie"),
)
GEMINI_VOICES = (
    ("Kore", "Kore (firm)"), ("Puck", "Puck (upbeat)"), ("Charon", "Charon (informative)"),
    ("Aoede", "Aoede (breezy)"), ("Sulafat", "Sulafat (warm)"), ("Achird", "Achird (friendly)"),
)
# ElevenLabs' picker entry for the voice made from the person's description.
DESIGNED_VOICE = "designed"

SPEAK_OPTIONS: tuple[CloudSpeechOption, ...] = (
    # Flash v2.5: $0.04 per 1,000 characters, about a minute of speech.
    CloudSpeechOption("elevenlabs", "speak", "ElevenLabs Flash", "ElevenLabs", "about $0.04 a minute of speech",
                      key_env="ELEVENLABS_API_KEY", key_field="elevenlabs.credential", voices=ELEVENLABS_VOICES,
                      describe="design", languages=ELEVENLABS_LANGUAGES),
    # Sonic 3.6: 1 credit a character, about $0.05 per 1,000 characters on the cheapest plan.
    CloudSpeechOption("cartesia", "speak", "Cartesia Sonic", "Cartesia", "about $0.04 a minute of speech",
                      key_env="CARTESIA_API_KEY", key_field="cartesia.credential", voices=CARTESIA_VOICES,
                      languages=CARTESIA_LANGUAGES),
    # gemini-3.8-flash-tts: $9 per 1M audio tokens out (25 a second) to 31 Dec 2026, $18 from 1 Jan 2027.
    CloudSpeechOption("gemini_tts", "speak", "Gemini Flash TTS", "Google",
                      "about $0.014 a minute of speech ($0.027 from 1 Jan 2027)",
                      provider_key="google", voices=GEMINI_VOICES, describe="style", languages=GEMINI_TTS_LANGUAGES),
)

OPTIONS = LISTEN_OPTIONS + SPEAK_OPTIONS
LISTEN_IDS = tuple(option.id for option in LISTEN_OPTIONS)
SPEAK_IDS = tuple(option.id for option in SPEAK_OPTIONS)
# The Voice settings that hold a key, and the key's name in Row-Bot's key store.
KEY_FIELDS = {option.key_field: option.key_env for option in OPTIONS if option.key_field}
# Descriptions are short: a sentence or two about how the voice sounds.
MAX_DESCRIPTION = 300
MIN_DESIGN_DESCRIPTION = 20


def option(option_id: str, part: Part | None = None) -> CloudSpeechOption | None:
    for found in OPTIONS:
        if found.id == option_id and (part is None or found.part == part):
            return found
    return None


def api_key(found: CloudSpeechOption) -> str:
    """The person's saved key for *found* ("" when none or unreadable). Never logged or returned."""
    if found.provider_key:
        from row_bot.voice.provider_base import provider_api_key

        return provider_api_key(found.provider_key)
    from row_bot import api_keys

    try:
        return str(api_keys.get_key(found.key_env) or "")
    except Exception:
        return ""


def key_saved(found: CloudSpeechOption) -> bool:
    return bool(api_key(found))


def chosen_voice(found: CloudSpeechOption, settings: VoiceRuntimeSettings) -> str:
    """The preset voice chosen for *found*, or ElevenLabs' designed voice when it was made and chosen."""
    picked = str(settings.cloud_voices.get(found.id) or "")
    if found.describe == "design" and picked == DESIGNED_VOICE:
        return str(settings.designed_voices.get(found.id) or "") or found.voices[0][0]
    return picked if picked in {voice for voice, _ in found.voices} else found.voices[0][0]


def voice_choices(found: CloudSpeechOption, settings: VoiceRuntimeSettings) -> list[str]:
    choices = [voice for voice, _ in found.voices]
    if found.describe == "design" and settings.designed_voices.get(found.id):
        choices.append(DESIGNED_VOICE)
    return choices


def fallbacks(settings: VoiceRuntimeSettings) -> list[dict[str, str]]:
    """The chosen cloud parts that run locally for now because their key is missing."""
    missing = []
    for part, chosen in (("listen", settings.talk_provider), ("speak", settings.speech_output_provider)):
        found = option(chosen, part)  # type: ignore[arg-type]
        if found is not None and not key_saved(found):
            missing.append({"part": part, "id": found.id, "company": found.company})
    return missing


def listen_provider(found: CloudSpeechOption, *, language: str | None = None) -> Any:
    from row_bot.voice.cloud_stt import CloudSpeechToText

    return CloudSpeechToText(found, language=language)


def speak_provider(found: CloudSpeechOption, settings: VoiceRuntimeSettings) -> Any:
    from row_bot.voice.cloud_tts import CloudSpeechOutput

    return CloudSpeechOutput(found, voice=chosen_voice(found, settings),
                             style=str(settings.voice_descriptions.get(found.id) or "")
                             if found.describe == "style" else "")


def rows(settings: VoiceRuntimeSettings, credential: Any) -> list[dict[str, Any]]:
    """One row per cloud option for Settings: price, company, whether a key is saved (never the key)."""
    out = []
    for found in OPTIONS:
        row: dict[str, Any] = {
            "id": found.id, "part": found.part, "label": found.label, "company": found.company,
            "price": found.price, "price_checked": PRICES_CHECKED, "key_saved": key_saved(found),
            "key_field": found.key_field or None,
            "key": credential(found.key_env) if found.key_env else None,
            "describe": found.describe,
        }
        if found.voices:
            labels = dict(found.voices)
            row["voices"] = [{"value": voice, "label": labels.get(voice, "Your described voice")}
                             for voice in voice_choices(found, settings)]
            picked = str(settings.cloud_voices.get(found.id) or "")
            row["voice"] = picked if picked in voice_choices(found, settings) else found.voices[0][0]
        if found.describe:
            row["description"] = str(settings.voice_descriptions.get(found.id) or "")
            row["designed"] = bool(settings.designed_voices.get(found.id))
        out.append(row)
    return out
