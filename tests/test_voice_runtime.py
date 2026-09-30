from __future__ import annotations

import json


def test_voice_runtime_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    from row_bot.voice import runtime

    settings = runtime.load_voice_runtime_settings()
    assert settings.talk_provider == "local"
    assert settings.dictation_provider == "local"
    assert settings.speech_output_provider == "local"
    assert settings.speech_output_voice == "af_heart"
    assert settings.realtime_voice == "marin"
    assert settings.captions_enabled is True

    saved = {
        "talk_provider": "openai_realtime",
        "captions_enabled": False,
        "dictation_model": "local-whisper-base",
        "speech_output_voice": "marin",
        "realtime_voice": "cedar",
    }
    (tmp_path / "voice_runtime_settings.json").write_text(json.dumps(saved), encoding="utf-8")

    loaded = runtime.load_voice_runtime_settings()
    assert loaded.talk_provider == "openai_realtime"
    assert loaded.captions_enabled is False
    assert loaded.dictation_model == "local-whisper-base"
    assert loaded.speech_output_voice == "marin"
    assert loaded.realtime_voice == "cedar"

    (tmp_path / "voice_runtime_settings.json").write_text(
        json.dumps({**saved, "realtime_voice": "not-a-voice"}), encoding="utf-8"
    )

    assert runtime.load_voice_runtime_settings().realtime_voice == "marin"
