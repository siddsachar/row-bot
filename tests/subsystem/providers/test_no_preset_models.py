"""Decision 9: nothing is preset. A fresh profile has no chat, vision, image or
video model until the person chooses one, and removing a provider never falls
back to a built-in model. Profiles that already saved their choices keep them.
"""
from __future__ import annotations

import contextvars
import json

import pytest

from row_bot import models, vision
from row_bot.providers import config as provider_config
from row_bot.providers.custom import custom_provider_id, delete_custom_endpoint, save_custom_endpoint
from row_bot import api_keys


@pytest.fixture(autouse=True)
def _no_turn_model_override(monkeypatch):
    """Agent turns set a per-turn model override that other tests may leave behind."""
    monkeypatch.setattr(models, "_active_model_override", contextvars.ContextVar("test_override", default=""))


def test_fresh_profile_has_no_chat_model():
    assert models.initial_model_choice({}) == ""
    # A saved choice is kept exactly (canonical provider-qualified reference).
    assert models.initial_model_choice({"model": "model:codex:gpt-5.6-sol"}) == "model:codex:gpt-5.6-sol"


def test_chat_model_calls_refuse_politely_without_a_model(monkeypatch):
    monkeypatch.setattr(models, "_current_model", "")
    monkeypatch.setattr(models, "_llm_instance", None)
    with pytest.raises(models.NoModelChosenError) as refused:
        models.get_llm()
    assert "Choose a model" in str(refused.value)
    with pytest.raises(models.NoModelChosenError):
        models.get_llm_for("")
    assert models.require_model_choice("  ") is None
    assert models.require_model_choice("model:ollama:qwen3.8:27b") == "model:ollama:qwen3.8:27b"


def test_provider_removal_keeps_the_saved_default(tmp_path, monkeypatch):
    """No fallback: the default becomes unavailable and people choose another model."""
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    settings_path = tmp_path / "model_settings.json"
    monkeypatch.setattr(models, "_SETTINGS_PATH", settings_path)
    removed_ref = f"model:{custom_provider_id('old')}:old-model"
    monkeypatch.setattr(models, "_current_model", removed_ref)
    save_custom_endpoint({
        "id": "old",
        "base_url": "http://127.0.0.1:8000/v1",
        "auth_required": False,
        "models": [{"id": "old-model", "model_id": "old-model"}],
    })

    delete_custom_endpoint("old")

    assert models.get_current_model() == removed_ref
    assert not settings_path.exists() or "ollama" not in settings_path.read_text(encoding="utf-8")
    assert models.reset_current_model_if_removed(custom_provider_id("old")) is False
    assert models.get_current_model() == removed_ref


def test_vision_follows_the_chat_model_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "_SETTINGS_PATH", tmp_path / "vision_settings.json")
    monkeypatch.setattr(vision, "_DATA_DIR", tmp_path)
    service = vision.VisionService()
    assert service.model == ""
    assert service.enabled is True
    assert service.effective_model("model:openai:gpt-5.6") == "model:openai:gpt-5.6"
    # Changing another Vision setting never writes a preset model.
    service.camera_index = 1
    saved = json.loads((tmp_path / "vision_settings.json").read_text())
    assert saved["model"] == ""
    # An explicit Vision model wins over the chat model.
    service.model = "model:ollama:gemma3:12b"
    assert service.effective_model("model:openai:gpt-5.6") == "model:ollama:gemma3:12b"
    assert vision.vision_provider_disclosure("")["model"] == "model:ollama:gemma3:12b"


def test_vision_without_any_model_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(vision, "_SETTINGS_PATH", tmp_path / "vision_settings.json")
    monkeypatch.setattr(vision, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(models, "_current_model", "")
    service = vision.VisionService()
    assert service.effective_model("") == ""
    answer = service.analyze(b"\xff\xd8\xff\xe0fake", "What is this?")
    assert "Choose a model" in answer


def test_image_and_video_are_off_and_unset_until_chosen(monkeypatch):
    from row_bot.tools import image_gen_tool, registry, video_gen_tool

    monkeypatch.setattr(registry, "get_tool_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(registry, "get_tool", lambda _name: None)
    assert image_gen_tool._get_configured_selection() == ""
    assert video_gen_tool._get_configured_selection() == ""
    assert image_gen_tool.ImageGenTool().enabled_by_default is False
    assert video_gen_tool.VideoGenTool().enabled_by_default is False


def test_media_generation_refuses_without_a_chosen_model(monkeypatch):
    from row_bot.tools import image_gen_tool, registry, video_gen_tool

    monkeypatch.setattr(registry, "get_tool_config", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(registry, "get_tool", lambda _name: None)
    assert "Choose an image model" in image_gen_tool._generate_image("a lighthouse at dusk")
    assert "Choose a video model" in video_gen_tool._generate_video("waves on rocks")


def _write(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_legacy_finished_profile_keeps_what_it_was_running(tmp_path, monkeypatch):
    from row_bot.application import model_choice_migration as migration

    monkeypatch.setattr(migration, "_migrate_media", lambda _name: False)
    _write(tmp_path / "app_config.json", {"setup_complete": True, "onboarding_version": 3})
    _write(tmp_path / "vision_settings.json", {"camera_index": 2, "enabled": False})

    assert migration.migrate_legacy_presets(tmp_path) == ["chat", "vision"]

    saved = json.loads((tmp_path / "model_settings.json").read_text(encoding="utf-8"))
    assert saved["model"] == "model:ollama:qwen3:14b"
    assert json.loads((tmp_path / "vision_settings.json").read_text(encoding="utf-8")) == {
        "model": "gemma3:4b", "camera_index": 2, "enabled": False,
    }
    # Idempotent: a second start writes nothing.
    assert migration.migrate_legacy_presets(tmp_path) == []


def test_saved_choices_survive_byte_for_byte(tmp_path, monkeypatch):
    from row_bot.application import model_choice_migration as migration

    monkeypatch.setattr(migration, "_migrate_media", lambda _name: False)
    _write(tmp_path / "app_config.json", {"setup_complete": True, "onboarding_version": 3})
    _write(tmp_path / "model_settings.json", {"model": "model:codex:gpt-5.6-sol", "context_policy_version": 3})
    _write(tmp_path / "vision_settings.json", {"model": "model:ollama:qwen3.8:27b", "camera_index": 0, "enabled": True})
    before = {name: (tmp_path / name).read_bytes() for name in ("app_config.json", "model_settings.json", "vision_settings.json")}

    assert migration.migrate_legacy_presets(tmp_path) == []

    assert {name: (tmp_path / name).read_bytes() for name in before} == before


def test_empty_saved_choices_are_kept_empty(tmp_path, monkeypatch):
    """A person who chose "Same as chat model" (or cleared a default) keeps that."""
    from row_bot.application import model_choice_migration as migration

    monkeypatch.setattr(migration, "_migrate_media", lambda _name: False)
    _write(tmp_path / "app_config.json", {"setup_complete": True})
    _write(tmp_path / "model_settings.json", {"model": ""})
    _write(tmp_path / "vision_settings.json", {"model": "", "camera_index": 0, "enabled": True})

    assert migration.migrate_legacy_presets(tmp_path) == []


@pytest.mark.parametrize("config", [
    {},
    {"setup_complete": False, "onboarding_version": 3},
    {"setup_complete": True, "onboarding_version": 4},
])
def test_fresh_and_new_profiles_get_no_preset(tmp_path, config):
    from row_bot.application import model_choice_migration as migration

    if config:
        _write(tmp_path / "app_config.json", config)
    assert migration.migrate_legacy_presets(tmp_path) == []
    assert not (tmp_path / "model_settings.json").exists()
    assert not (tmp_path / "vision_settings.json").exists()


def test_legacy_media_tools_keep_their_old_models_only_when_they_were_on(monkeypatch):
    from row_bot.application import model_choice_migration as migration
    from row_bot.tools import registry

    written: dict[str, object] = {}
    monkeypatch.setattr(registry, "get_tool", lambda name: object())
    monkeypatch.setattr(migration, "_read_json", lambda _path: {
        "tools": {"image_gen": True, "video_gen": False},
        "tool_configs": {},
    })
    monkeypatch.setattr(registry, "set_tool_config", lambda name, key, value: written.__setitem__(name, value))
    monkeypatch.setattr(registry, "set_enabled", lambda name, value: written.__setitem__(name + ":enabled", value))

    assert migration._migrate_media("image_gen") is True
    assert migration._migrate_media("video_gen") is False
    assert written == {"image_gen": "openai/gpt-image-1.5"}
