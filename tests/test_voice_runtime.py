from __future__ import annotations

import importlib


def test_voice_runtime_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    import row_bot.voice.runtime
    from row_bot import voice

    voice.runtime = importlib.reload(voice.runtime)

    settings = voice.runtime.load_voice_runtime_settings()
    assert settings.talk_provider == "local"
    assert settings.dictation_provider == "local"
    assert settings.speech_output_provider == "local"
    assert settings.speech_output_voice == "af_heart"
    assert settings.realtime_voice == "marin"
    assert settings.captions_enabled is True

    updated = voice.runtime.update_voice_runtime_settings(
        talk_provider="openai_realtime",
        captions_enabled=False,
        dictation_model="local-whisper-base",
        speech_output_voice="marin",
        realtime_voice="cedar",
    )

    assert updated.talk_provider == "openai_realtime"
    assert updated.captions_enabled is False
    assert updated.dictation_model == "local-whisper-base"
    assert updated.speech_output_voice == "marin"
    assert updated.realtime_voice == "cedar"

    loaded = voice.runtime.load_voice_runtime_settings()
    assert loaded == updated

    clamped = voice.runtime.update_voice_runtime_settings(
        realtime_voice="not-a-voice",
    )

    assert clamped.realtime_voice == "marin"
