import sys
from types import SimpleNamespace

import pytest


def _is_local_model(*installed):
    installed_set = set(installed)
    return lambda model: model in installed_set


@pytest.fixture(autouse=True)
def _isolated_thoth_data(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / "data"))


def test_thoth_status_normalizes_dynamic_image_model_label(monkeypatch):
    import tools.image_gen_tool as image_gen_tool
    from tools.thoth_status_tool import _normalize_provider_model_value

    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {
        "openai/gpt-image-2": "⬡  GPT Image 2  (OpenAI)",
        "google/gemini-3-pro-image-preview": "💎  Nano Banana Pro  (Google)",
    })

    assert _normalize_provider_model_value("image_gen_model", "GPT Image 2") == "openai/gpt-image-2"
    assert _normalize_provider_model_value("image_gen_model", "gpt-image-2") == "openai/gpt-image-2"
    assert _normalize_provider_model_value("image_gen_model", "Nano Banana Pro") == "google/gemini-3-pro-image-preview"


def test_thoth_status_normalizes_dynamic_video_model_label(monkeypatch):
    import tools.video_gen_tool as video_gen_tool
    from tools.thoth_status_tool import _normalize_provider_model_value

    monkeypatch.setattr(video_gen_tool, "get_available_video_models", lambda: {
        "google/veo-3.1-generate-preview": "💎  Veo 3.1  (Google)",
        "xai/grok-imagine-video": "𝕏  Grok Imagine Video  (xAI)",
    })

    assert _normalize_provider_model_value("video_gen_model", "Veo 3.1") == "google/veo-3.1-generate-preview"
    assert _normalize_provider_model_value("video_gen_model", "grok-imagine-video") == "xai/grok-imagine-video"


def test_thoth_status_leaves_ambiguous_media_bare_id_unchanged(monkeypatch):
    import tools.image_gen_tool as image_gen_tool
    from tools.thoth_status_tool import _normalize_provider_model_value

    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {
        "openai/shared-image": "⬡  Shared Image  (OpenAI)",
        "custom_openai_lab/shared-image": "↔  Shared Image  (Lab)",
    })

    assert _normalize_provider_model_value("image_gen_model", "shared-image") == "shared-image"


def test_thoth_status_voice_reports_runtime_and_realtime(monkeypatch):
    from tools.thoth_status_tool import _query_voice

    monkeypatch.setattr("voice.openai_realtime.get_key", lambda name: "")

    output = _query_voice()

    assert "Talk provider:" in output
    assert "Talk model:" in output
    assert "Dictation provider:" in output
    assert "Speech output provider:" in output
    assert "Realtime fallback:" in output
    assert "OpenAI Realtime:" in output
    assert "User-facing modes: Talk, Dictate" in output
    assert "Dictate policy: STT-only" in output
    assert "Realtime brain strategy: thoth-consult" in output
    assert "Realtime direct normal-tool access: blocked" in output
    assert "thoth_agent_consult" in output
    assert "thoth_agent_control" in output
    assert "Realtime quiet idle tool: wait_for_user" in output
    assert "Active Thoth run:" in output
    assert "sk_" not in output
    assert "ek_" not in output


def test_thoth_status_voice_reports_active_run_controls(monkeypatch):
    import threading
    from types import SimpleNamespace

    from tools.thoth_status_tool import _query_voice
    from ui.state import _active_generations

    monkeypatch.setattr("voice.openai_realtime.get_key", lambda name: "")
    _active_generations.clear()
    _active_generations["thread123"] = SimpleNamespace(
        status="streaming",
        pending_tools={"call1": {"name": "browser_open"}},
        interrupt_data=None,
        stop_event=threading.Event(),
        voice_control_queue=[{"kind": "steer", "text": "also check logs"}],
    )
    try:
        output = _query_voice()
    finally:
        _active_generations.clear()

    assert "Active Thoth runs: 1" in output
    assert "browser_open" in output
    assert "cancel=yes" in output
    assert "follow-up/steer=yes" in output
    assert "queued_controls=1" in output


def test_thoth_status_media_update_seeds_quick_choices(monkeypatch):
    import langgraph.types
    import providers.selection as provider_selection
    import tools.image_gen_tool as image_gen_tool
    import tools.registry as tool_registry
    from tools.thoth_status_tool import _update_setting

    calls = []
    monkeypatch.setattr(langgraph.types, "interrupt", lambda payload: True)
    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {
        "openai/gpt-image-2": "⬡  GPT Image 2  (OpenAI)",
    })
    monkeypatch.setattr(tool_registry, "set_tool_config", lambda tool, key, value: calls.append((tool, key, value)))
    monkeypatch.setattr(provider_selection, "seed_configured_media_quick_choices", lambda: calls.append(("seed", "media", "quick_choices")))

    result = _update_setting("image_gen_model", "GPT Image 2")

    assert result == "Image generation model set to: openai/gpt-image-2"
    assert ("image_gen", "model", "openai/gpt-image-2") in calls
    assert ("seed", "media", "quick_choices") in calls


def test_thoth_status_rejects_unknown_provider_chat_model(tmp_path, monkeypatch):
    import api_keys
    import models
    import providers.config as provider_config
    from tools.thoth_status_tool import _resolve_model_update_value

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(models, "is_model_local", _is_local_model())
    monkeypatch.setattr(models, "list_cloud_models", lambda provider=None: [])
    monkeypatch.setattr(models, "list_cloud_vision_models", lambda: [])

    model_value, error = _resolve_model_update_value("gpt-99-fictional", surface="chat")

    assert model_value is None
    assert "not in the current catalog" in str(error)


def test_thoth_status_allows_installed_unknown_local_chat_model(tmp_path, monkeypatch):
    import api_keys
    import models
    import providers.config as provider_config
    from tools.thoth_status_tool import _resolve_model_update_value

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(models, "is_model_local", _is_local_model("gemma4:e4b"))
    monkeypatch.setattr(models, "list_cloud_models", lambda provider=None: [])
    monkeypatch.setattr(models, "list_cloud_vision_models", lambda: [])

    model_value, error = _resolve_model_update_value("gemma4:e4b", surface="chat")

    assert error is None
    assert model_value == "gemma4:e4b"


def test_thoth_status_rejects_local_model_without_vision_metadata(tmp_path, monkeypatch):
    import api_keys
    import models
    import providers.config as provider_config
    from tools.thoth_status_tool import _resolve_model_update_value

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(models, "is_model_local", _is_local_model("gemma4:e4b"))
    monkeypatch.setattr(models, "list_cloud_models", lambda provider=None: [])
    monkeypatch.setattr(models, "list_cloud_vision_models", lambda: [])

    model_value, error = _resolve_model_update_value("gemma4:e4b", surface="vision")

    assert model_value is None
    assert "does not have Vision capability metadata" in str(error)


def test_thoth_status_vision_model_update_persists_valid_model(tmp_path, monkeypatch):
    import api_keys
    import langgraph.types
    import models
    import providers.config as provider_config
    import tools.vision_tool as vision_tool
    from tools.thoth_status_tool import _update_setting

    calls = []
    vision_service = SimpleNamespace(model="moondream:latest")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(langgraph.types, "interrupt", lambda payload: True)
    monkeypatch.setattr(models, "is_model_local", _is_local_model("gemma3:4b"))
    monkeypatch.setattr(models, "list_cloud_models", lambda provider=None: [])
    monkeypatch.setattr(models, "list_cloud_vision_models", lambda: [])
    monkeypatch.setattr(vision_tool, "_get_vision_service", lambda: vision_service)
    monkeypatch.setitem(sys.modules, "agent", SimpleNamespace(clear_agent_cache=lambda: calls.append("clear")))

    result = _update_setting("vision_model", "gemma3:4b")

    assert result == "Vision model changed to: gemma3:4b"
    assert vision_service.model == "gemma3:4b"
    assert calls == ["clear"]


def test_thoth_status_allows_codex_vision_quick_choice(tmp_path, monkeypatch):
    import api_keys
    import models
    import providers.config as provider_config
    import providers.runtime as provider_runtime
    from providers.codex import fallback_codex_model_infos
    from providers.selection import add_quick_choice_for_model
    from tools.thoth_status_tool import _resolve_model_update_value

    model_info = next(info for info in fallback_codex_model_infos() if info.model_id == "gpt-5.5")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(models, "is_model_local", _is_local_model())
    monkeypatch.setattr(provider_runtime, "provider_status", lambda provider_id: {"runtime_enabled": True})
    add_quick_choice_for_model(
        "gpt-5.5",
        provider_id="codex",
        display_name="GPT-5.5",
        capabilities_snapshot=model_info.capability_snapshot(),
        surface="vision",
    )

    model_value, error = _resolve_model_update_value("model:codex:gpt-5.5", surface="vision")

    assert error is None
    assert model_value == "gpt-5.5"


def test_thoth_status_vision_reports_custom_provider_probe(tmp_path, monkeypatch):
    import providers.config as provider_config
    import vision
    from providers.custom import custom_provider_id, save_custom_endpoint
    from tools.thoth_status_tool import _query_vision

    provider_id = custom_provider_id("lm-studio")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(vision, "_load_settings", lambda: {
        "model": f"model:{provider_id}:qwen/qwen3.5-vl",
        "enabled": True,
        "camera_index": 1,
    })
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "last_probe": {
            "vision_ok": True,
            "vision_model": "qwen/qwen3.5-vl",
            "vision_content_format": "openai_image_url",
        },
    })

    output = _query_vision()

    assert "- Provider: LM Studio" in output
    assert "- Vision readiness: vision verified" in output
    assert "- Vision probe model: qwen/qwen3.5-vl" in output
    assert "- Vision content format: openai_image_url" in output
    assert "Ollama" not in output


def test_thoth_status_vision_reports_custom_provider_failure_without_ollama_wording(tmp_path, monkeypatch):
    import providers.config as provider_config
    import vision
    from providers.custom import custom_provider_id, save_custom_endpoint
    from tools.thoth_status_tool import _query_vision

    provider_id = custom_provider_id("lab")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(vision, "_load_settings", lambda: {
        "model": f"model:{provider_id}:local-vl",
        "enabled": True,
        "camera_index": 0,
    })
    save_custom_endpoint({
        "id": "lab",
        "name": "Lab Endpoint",
        "base_url": "http://127.0.0.1:8000/v1",
        "auth_required": False,
        "last_probe": {
            "vision_ok": False,
            "vision_error": "image input unsupported",
            "vision_model": "local-vl",
        },
    })

    output = _query_vision()

    assert "- Provider: Lab Endpoint" in output
    assert "- Vision readiness: vision failed" in output
    assert "image input unsupported" in output
    assert "not exposed by Ollama" not in output


def test_thoth_status_vision_treats_stale_empty_probe_as_unverified(tmp_path, monkeypatch):
    import providers.config as provider_config
    import vision
    from providers.custom import custom_provider_id, save_custom_endpoint
    from tools.thoth_status_tool import _query_vision

    provider_id = custom_provider_id("lm-studio")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(vision, "_load_settings", lambda: {
        "model": f"model:{provider_id}:qwen/qwen3.5-vl",
        "enabled": True,
        "camera_index": 0,
    })
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "last_probe": {
            "vision_ok": False,
            "vision_error": "unexpected response: <empty>",
            "vision_model": "qwen/qwen3.5-vl",
            "vision_content_format": "openai_image_url",
        },
    })

    output = _query_vision()

    assert "- Vision readiness: vision unverified" in output
    assert "- Vision probe note: unexpected response: <empty>" in output
    assert "vision failed" not in output


def test_thoth_status_vision_reports_manual_disabled_custom_endpoint(tmp_path, monkeypatch):
    import providers.config as provider_config
    import vision
    from providers.custom import custom_provider_id, save_custom_endpoint
    from tools.thoth_status_tool import _query_vision

    provider_id = custom_provider_id("lm-studio")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(vision, "_load_settings", lambda: {
        "model": f"model:{provider_id}:qwen/qwen3.5-9b",
        "enabled": True,
        "camera_index": 0,
    })
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "manual_capabilities": {"vision": False},
        "models": [{
            "id": "qwen/qwen3.5-9b",
            "model_id": "qwen/qwen3.5-9b",
            "capabilities_snapshot": {
                "tasks": ["chat"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        }],
        "last_probe": {
            "vision_ok": None,
            "vision_probed": False,
            "vision_probe_skip_reason": "manual vision capability disabled",
            "vision_model": "qwen/qwen3.5-9b",
        },
    })

    output = _query_vision()

    assert "- Provider: LM Studio" in output
    assert "- Vision readiness: vision disabled for endpoint" in output
    assert "- Vision probe skipped: manual vision capability disabled" in output
    assert "- Vision compatibility: manual vision capability disabled" in output


def test_thoth_status_update_setting_description_mentions_vision_model():
    from tools.thoth_status_tool import ThothStatusTool

    tools = {tool.name: tool for tool in ThothStatusTool().as_langchain_tools()}

    assert "vision_model" in tools["thoth_update_setting"].description


def test_thoth_status_guide_mentions_custom_vision_override_states():
    import pathlib

    guide = pathlib.Path("tool_guides/thoth_status_guide/SKILL.md").read_text(encoding="utf-8").lower()

    assert "vision_model" in guide
    assert "custom-endpoint" in guide or "custom endpoint" in guide
    assert "manual override" in guide
    assert "skipped" in guide


def test_thoth_status_guide_mentions_voice_realtime_contract():
    import pathlib

    guide = pathlib.Path("tool_guides/thoth_status_guide/SKILL.md").read_text(encoding="utf-8").lower()

    assert "talk and dictate" in guide
    assert "stt-only" in guide
    assert "realtime talk is a voice transport/backchannel" in guide
    assert "thoth_agent_consult" in guide
    assert "thoth_agent_control" in guide
    assert "wait_for_user" in guide
    assert "follow-up/steer" in guide
    assert "client_event_failed" in guide
    assert "function_call_ready" in guide


def test_provider_status_summarizes_custom_probe_without_reprobe(tmp_path, monkeypatch):
    import providers.config as provider_config
    from providers.custom import save_custom_endpoint
    from providers.status import summarize_providers

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "auth_required": False,
        "models": [{"id": "qwen/qwen3.5-9b", "model_id": "qwen/qwen3.5-9b"}],
        "last_probe": {
            "classification": "agent_ready",
            "tool_round_trip": True,
            "streaming_tool_calling": True,
            "vision_probed": False,
            "vision_probe_skip_reason": "manual vision capability disabled",
        },
    })

    output = summarize_providers()

    assert "LM Studio" in output
    assert "probe agent ready" in output
    assert "round-trip ok" in output
    assert "stream tools ok" in output
    assert "vision not run (manual vision capability disabled)" in output
