"""Models owner reuses saved catalog readiness and NiceGUI's local settings owners."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from row_bot.application import client_models_settings as owner


pytestmark = pytest.mark.subsystem


def _row(ref: str, *, category: str, pinned: bool, ready: bool):
    provider, model = ref.split(":", 2)[1:]
    return SimpleNamespace(
        selection_ref=ref, provider_id=provider, model_id=model,
        display_name=model, source="saved_catalog", context_window=32000,
        configured=ready, runtime_ready=ready, installed=True,
        categories=(category,), pinned_surfaces=(category,) if pinned else (),
        status_reason="Connect the provider" if not ready else "",
    )


def test_picker_keeps_unavailable_current_and_only_pinned_available_choices():
    rows = (
        _row("model:openai:current", category="image", pinned=False, ready=False),
        _row("model:openai:ready", category="image", pinned=True, ready=True),
        _row("model:openai:unselected", category="image", pinned=False, ready=True),
    )
    picker = owner._picker("image", "model:openai:current", rows, enabled=True)
    assert picker["current_ref"] == "model:openai:current"
    assert picker["enabled"] is True
    assert {option["selection_ref"] for option in picker["options"]} == {
        "model:openai:current", "model:openai:ready",
    }
    assert picker["options"][0]["available"] is False
    assert "unavailable" in picker["warning"]


def test_legacy_media_defaults_remain_provider_qualified():
    assert owner._media_ref("gpt-image-1.5", default_provider="openai") == "model:openai:gpt-image-1.5"
    assert owner._media_ref("google/veo-3.1", default_provider="google") == "model:google:veo-3.1"


def test_surface_default_writes_real_owner_and_rejects_unavailable(monkeypatch):
    from row_bot import agent
    from row_bot.providers import selection
    from row_bot.tools import registry
    from row_bot import vision_runtime

    @dataclass
    class Tool:
        value: str = ""

        def set_config(self, key, value):
            assert key == "model"
            self.value = value

    tool = Tool()
    vision = SimpleNamespace(model="model:openai:old", enabled=True, camera_index=0)
    calls = []
    monkeypatch.setattr(registry, "get_tool", lambda name: tool)
    monkeypatch.setattr(vision_runtime, "get_vision_service", lambda: vision)
    monkeypatch.setattr(selection, "seed_configured_media_quick_choices", lambda: calls.append("seed"))
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: calls.append("clear"))
    monkeypatch.setattr(owner, "read_models_settings", lambda **kwargs: {
        "vision": {"options": [{"selection_ref": "model:openai:vision", "available": True}]},
        "image": {"options": [{"selection_ref": "model:openai:image", "available": True}]},
        "video": {"options": [{"selection_ref": "model:google:video", "available": False}]},
    })
    owner.update_model_surface("image", "default", selection_ref="model:openai:image", validate=lambda: None)
    assert tool.value == "openai/image" and calls == ["seed"]
    owner.update_model_surface("vision", "default", selection_ref="model:openai:vision", validate=lambda: None)
    assert vision.model == "model:openai:vision" and calls == ["seed", "clear"]
    with pytest.raises(ValueError, match="model_configuration_unavailable"):
        owner.update_model_surface("video", "default", selection_ref="model:google:video", validate=lambda: None)
    assert tool.value == "openai/image"


def test_context_write_requires_current_policy_and_uses_existing_setters(monkeypatch):
    from row_bot import models

    calls = []
    monkeypatch.setattr(owner, "read_models_settings", lambda **kwargs: {"context": {"policy_kind": "provider"}})
    monkeypatch.setattr(models, "validate_context_size", lambda cap: calls.append(("validate", cap)))
    monkeypatch.setattr(models, "set_cloud_context_size", lambda cap: calls.append(("set", cap)))
    monkeypatch.setattr(models, "clear_cloud_context_override", lambda: calls.append(("auto", None)))
    owner.update_model_context("provider", 65536, validate=lambda: None)
    owner.update_model_context("provider", None, validate=lambda: None)
    assert calls == [("validate", 65536), ("set", 65536), ("auto", None)]
    with pytest.raises(ValueError, match="context_policy_changed"):
        owner.update_model_context("local", 65536, validate=lambda: None)


def test_models_view_prefers_actual_brain_runtime_and_never_probes_context(monkeypatch):
    from row_bot import models, vision_runtime
    from row_bot.providers import client_status
    from row_bot.tools import registry
    from row_bot.tools import image_gen_tool, video_gen_tool

    context_calls = []
    monkeypatch.setattr(owner, "read_default_model",
                        lambda **kwargs: SimpleNamespace(selection_ref="model:openai:saved"))
    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:active")
    monkeypatch.setattr(models, "get_context_policy",
                        lambda ref, **kwargs: context_calls.append((ref, kwargs)) or SimpleNamespace(
                            policy_kind="local", native_max=131072,
                            effective_context=131072, warning=""))
    monkeypatch.setattr(models, "get_local_context_mode", lambda: "auto")
    monkeypatch.setattr(models, "get_cloud_context_override", lambda: None)
    monkeypatch.setattr(models, "get_user_context_size", lambda: 131072)
    monkeypatch.setattr(vision_runtime, "get_vision_service",
                        lambda: SimpleNamespace(model="model:ollama:vision",
                                                enabled=True, camera_index=0))
    monkeypatch.setattr(registry, "get_tool", lambda name: None)
    monkeypatch.setattr(registry, "get_tool_config", lambda name, key, fallback: fallback)
    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {})
    monkeypatch.setattr(video_gen_tool, "get_available_video_models", lambda: {})
    monkeypatch.setattr(client_status, "_read", lambda now, **kwargs: (
        SimpleNamespace(freshness="unavailable", generated_at=None, refresh_running=False),
        (_row("model:ollama:active", category="chat", pinned=False, ready=True),)))
    state = owner.read_models_settings()
    assert state["brain"]["current_ref"] == "model:ollama:active"
    assert state["brain"]["options"][0]["selection_ref"] == "model:ollama:active"
    assert context_calls == [("model:ollama:active", {"allow_probe": False})]
