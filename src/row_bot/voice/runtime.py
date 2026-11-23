from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.voice import languages
from row_bot.voice.openai_realtime import (
    DEFAULT_REALTIME_VOICE,
    REALTIME_VOICE_OPTIONS,
)


def _settings_path():
    return get_row_bot_data_dir() / "voice_runtime_settings.json"


@dataclass
class VoiceRuntimeSettings:
    talk_provider: str = "local"
    talk_model: str = "local-whisper"
    dictation_provider: str = "local"
    dictation_model: str = "local-whisper"
    speech_output_provider: str = "local"
    speech_output_model: str = "local-kokoro"
    realtime_voice: str = DEFAULT_REALTIME_VOICE
    captions_enabled: bool = True
    # Row-Bot Voice: how readily a pause ends the person's turn, and push to talk.
    turn_eagerness: str = "normal"
    push_to_talk: bool = False
    # What the person speaks (Talk, Dictate, chat-app voice notes): a language code, or "auto" to detect it.
    speech_language: str = "auto"
    # Cloud speech (V5): each speaking option's chosen voice, its described voice (a style, or what
    # ElevenLabs made from the description), all by option id. Saved flat as "<id>_voice",
    # "<id>_voice_description" and "<id>_designed_voice".
    cloud_voices: dict[str, str] = field(default_factory=dict)
    voice_descriptions: dict[str, str] = field(default_factory=dict)
    designed_voices: dict[str, str] = field(default_factory=dict)


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _coerce_realtime_voice(value: Any) -> str:
    voice = str(value or DEFAULT_REALTIME_VOICE)
    if voice in REALTIME_VOICE_OPTIONS:
        return voice
    return DEFAULT_REALTIME_VOICE


TURN_EAGERNESS = ("relaxed", "normal", "eager")


def _coerce_eagerness(value: Any) -> str:
    return value if value in TURN_EAGERNESS else "normal"


def cloud_voice_settings(data: dict[str, Any]) -> dict[str, dict[str, str]]:
    from row_bot.voice.cloud_speech import SPEAK_IDS

    def saved(suffix: str) -> dict[str, str]:
        found = {option_id: data.get(f"{option_id}_{suffix}") for option_id in SPEAK_IDS}
        return {option_id: value for option_id, value in found.items() if isinstance(value, str) and value}

    return {"cloud_voices": saved("voice"), "voice_descriptions": saved("voice_description"),
            "designed_voices": saved("designed_voice")}


def save_voice_settings(values: dict[str, Any]) -> None:
    """Save some Voice runtime settings, keeping the rest, as Settings would."""
    from row_bot.integrations.safe import write_atomic

    settings_path = _settings_path()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.update(values)
    write_atomic(settings_path, json.dumps(data, indent=2, ensure_ascii=False))


def save_talk_model(engine_id: str) -> None:
    """Choose what Talk listens with (a downloaded speech model, or Whisper), as Settings would."""
    save_voice_settings({"talk_model": str(engine_id)})


def load_voice_runtime_settings() -> VoiceRuntimeSettings:
    settings_path = _settings_path()
    try:
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return VoiceRuntimeSettings(
                    talk_provider=str(data.get("talk_provider") or "local"),
                    talk_model=str(data.get("talk_model") or "local-whisper"),
                    dictation_provider=str(data.get("dictation_provider") or "local"),
                    dictation_model=str(data.get("dictation_model") or "local-whisper"),
                    speech_output_provider=str(data.get("speech_output_provider") or "local"),
                    speech_output_model=str(data.get("speech_output_model") or "local-kokoro"),
                    realtime_voice=_coerce_realtime_voice(data.get("realtime_voice")),
                    captions_enabled=_coerce_bool(data.get("captions_enabled"), True),
                    turn_eagerness=_coerce_eagerness(data.get("turn_eagerness")),
                    push_to_talk=_coerce_bool(data.get("push_to_talk"), False),
                    speech_language=languages.setting(data.get("speech_language")),
                    **cloud_voice_settings(data),
                )
    except Exception:
        pass
    return VoiceRuntimeSettings()
