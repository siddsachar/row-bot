from __future__ import annotations

import json


def test_voice_runtime_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    from row_bot.voice import runtime

    settings = runtime.load_voice_runtime_settings()
    assert settings.talk_provider == "local"
    assert settings.dictation_provider == "local"
    assert settings.speech_output_provider == "local"
    assert settings.realtime_voice == "marin"
    assert settings.realtime_model == "gpt-realtime-2"
    assert settings.captions_enabled is True

    saved = {
        "talk_provider": "deepgram",
        "captions_enabled": False,
        "dictation_model": "local-whisper-base",
        "realtime_voice": "cedar",
    }
    (tmp_path / "voice_runtime_settings.json").write_text(json.dumps(saved), encoding="utf-8")

    loaded = runtime.load_voice_runtime_settings()
    assert loaded.talk_provider == "deepgram"
    assert loaded.captions_enabled is False
    assert loaded.dictation_model == "local-whisper-base"
    assert loaded.realtime_voice == "cedar"

    (tmp_path / "voice_runtime_settings.json").write_text(
        json.dumps({**saved, "realtime_voice": "not-a-voice"}), encoding="utf-8"
    )

    assert runtime.load_voice_runtime_settings().realtime_voice == "marin"


def test_an_old_realtime_listen_with_reads_as_local_and_keeps_live_voices_model_and_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    from row_bot.voice import live_voice, runtime

    path = tmp_path / "voice_runtime_settings.json"
    path.write_text(json.dumps({"talk_provider": "openai_realtime", "talk_model": "gpt-realtime-2-mini",
                                "realtime_voice": "cedar"}), encoding="utf-8")

    loaded = runtime.load_voice_runtime_settings()
    assert (loaded.talk_provider, loaded.talk_model) == ("local", "local-whisper")
    assert (loaded.realtime_model, loaded.realtime_voice) == ("gpt-realtime-2-mini", "cedar")
    front = live_voice.live_voice_provider("openai")
    assert (front.model, front.voice) == ("gpt-realtime-2-mini", "cedar")

    # The next save rewrites the file, so the realtime model outlives a new talk model.
    runtime.save_talk_model("local-moonshine-base-en")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "talk_provider": "local", "talk_model": "local-moonshine-base-en",
        "realtime_model": "gpt-realtime-2-mini", "realtime_voice": "cedar"}


def test_a_saved_realtime_model_wins_and_a_name_that_is_not_one_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    from row_bot.voice import runtime

    path = tmp_path / "voice_runtime_settings.json"
    path.write_text(json.dumps({"talk_model": "gpt-realtime-old", "realtime_model": "gpt-realtime-2"}),
                    encoding="utf-8")
    assert runtime.load_voice_runtime_settings().realtime_model == "gpt-realtime-2"

    path.write_text(json.dumps({"realtime_model": "whisper-1"}), encoding="utf-8")
    assert runtime.load_voice_runtime_settings().realtime_model == "gpt-realtime-2"
