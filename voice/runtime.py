from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from data_paths import get_thoth_data_dir
from voice.openai_realtime import (
    DEFAULT_REALTIME_VOICE,
    REALTIME_VOICE_OPTIONS,
)


def _settings_path():
    return get_thoth_data_dir() / "voice_runtime_settings.json"


@dataclass
class VoiceRuntimeSettings:
    talk_provider: str = "local"
    talk_model: str = "local-whisper"
    dictation_provider: str = "local"
    dictation_model: str = "local-whisper"
    speech_output_provider: str = "local"
    speech_output_model: str = "local-kokoro"
    speech_output_voice: str = "af_heart"
    realtime_voice: str = DEFAULT_REALTIME_VOICE
    captions_enabled: bool = True
    talk_auto_start: bool = False
    realtime_fallback_to_local: bool = True


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _coerce_realtime_voice(value: Any) -> str:
    voice = str(value or DEFAULT_REALTIME_VOICE)
    if voice in REALTIME_VOICE_OPTIONS:
        return voice
    return DEFAULT_REALTIME_VOICE


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
                    speech_output_voice=str(data.get("speech_output_voice") or "af_heart"),
                    realtime_voice=_coerce_realtime_voice(data.get("realtime_voice")),
                    captions_enabled=_coerce_bool(data.get("captions_enabled"), True),
                    talk_auto_start=_coerce_bool(data.get("talk_auto_start"), False),
                    realtime_fallback_to_local=_coerce_bool(data.get("realtime_fallback_to_local"), True),
                )
    except Exception:
        pass
    return VoiceRuntimeSettings()


def save_voice_runtime_settings(settings: VoiceRuntimeSettings) -> None:
    settings_path = _settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def update_voice_runtime_settings(**updates: Any) -> VoiceRuntimeSettings:
    settings = load_voice_runtime_settings()
    for key, value in updates.items():
        if hasattr(settings, key):
            if key == "realtime_voice":
                value = _coerce_realtime_voice(value)
            setattr(settings, key, value)
    save_voice_runtime_settings(settings)
    return settings
