from __future__ import annotations

from types import SimpleNamespace

from row_bot.voice.openai_realtime import DEFAULT_REALTIME_MODEL
from row_bot.voice.provider_catalog import (
    build_voice_provider_catalog,
    model_options_for_capability,
    provider_options_for_capability,
    selected_or_default_model,
)


class FakeTTS:
    voice = "af_heart"

    def __init__(self, installed: bool) -> None:
        self._installed = installed

    def is_installed(self) -> bool:
        return self._installed


def test_voice_catalog_filters_models_by_runtime_capability(monkeypatch):
    monkeypatch.setattr("voice.openai_realtime.get_key", lambda name: "sk-test")

    catalog = build_voice_provider_catalog(
        voice_service=SimpleNamespace(whisper_size="base"),
        tts_service=FakeTTS(installed=True),
    )

    assert provider_options_for_capability(catalog, "talk") == {
        "local": "Local",
        "openai_realtime": "OpenAI Realtime",
    }
    assert provider_options_for_capability(catalog, "dictation") == {"local": "Local"}
    assert provider_options_for_capability(catalog, "speech_output") == {"local": "Local"}
    assert model_options_for_capability(catalog, "talk", "openai_realtime") == {
        DEFAULT_REALTIME_MODEL: f"{DEFAULT_REALTIME_MODEL} (default)",
    }
    assert model_options_for_capability(catalog, "dictation", "openai_realtime") == {}


def test_voice_catalog_reports_openai_realtime_default_ready_with_key(monkeypatch):
    monkeypatch.setattr("voice.openai_realtime.get_key", lambda name: "sk-test")

    catalog = build_voice_provider_catalog(
        voice_service=SimpleNamespace(whisper_size="small"),
        tts_service=FakeTTS(installed=False),
    )
    openai = next(provider for provider in catalog if provider.provider_id == "openai_realtime")

    assert openai.ready is True
    assert openai.default_talk_model == DEFAULT_REALTIME_MODEL
    assert openai.talk_models[0].ready is True
    assert selected_or_default_model(catalog, "talk", "openai_realtime", "missing") == DEFAULT_REALTIME_MODEL


def test_voice_catalog_marks_openai_realtime_setup_when_key_missing(monkeypatch):
    monkeypatch.setattr("voice.openai_realtime.get_key", lambda name: "")

    catalog = build_voice_provider_catalog(
        voice_service=SimpleNamespace(whisper_size="base"),
        tts_service=FakeTTS(installed=True),
    )
    openai = next(provider for provider in catalog if provider.provider_id == "openai_realtime")

    assert openai.ready is False
    assert openai.talk_models[0].ready is False
    assert "Providers" in openai.reason
