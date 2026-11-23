from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir
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


def save_talk_model(engine_id: str) -> None:
    """Choose what Talk listens with (a downloaded speech model, or Whisper), as Settings would."""
    from row_bot.integrations.safe import write_atomic

    settings_path = _settings_path()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["talk_model"] = str(engine_id)
    write_atomic(settings_path, json.dumps(data, indent=2, ensure_ascii=False))


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
                )
    except Exception:
        pass
    return VoiceRuntimeSettings()
