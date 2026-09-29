from pathlib import Path

import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint


ROOT = Path(__file__).resolve().parents[1]


def test_agent_tool_error_uses_active_thread_override(tmp_path, monkeypatch):
    data_dir = tmp_path / ".row-bot"
    data_dir.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))

    import row_bot.agent as agent

    token = agent._model_override_var.set("model:ollama:vendor/non-tool-chat:14b")
    try:
        message = agent._friendly_api_error("This model does not support tools")
    finally:
        agent._model_override_var.reset(token)

    assert "vendor/non-tool-chat:14b" in message
    assert "model:codex:gpt-5.5" not in message


def test_model_picker_labels_use_clean_text_without_mojibake(monkeypatch):
    from row_bot.providers.selection import (
        format_model_choice_label,
        list_model_choice_options,
        model_ref,
    )
    import row_bot.providers.selection as selection

    cfg = {
        "quick_choices": [{
            "id": model_ref("openai", "gpt-4o"),
            "kind": "model",
            "provider_id": "openai",
            "model_id": "gpt-4o",
            "display_name": "GPT-4o",
            "visibility": ["chat"],
            "active": True,
            "inactive_reason": "",
            "capabilities_snapshot": {
                "tasks": ["chat"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        }],
    }

    monkeypatch.setattr(selection, "load_provider_config", lambda: cfg)
    labels = [
        format_model_choice_label("openai", "gpt-4o"),
        *[str(option["label"]) for option in list_model_choice_options("chat")],
    ]

    for label in labels:
        assert "GPT" in label or "gpt" in label
        assert not any(sentinel in label for sentinel in ("Ã", "Â", "â", "ð", "�"))
        assert " - " in label


def test_runtime_readiness_reuses_provider_status_snapshot(monkeypatch):
    import row_bot.providers.readiness as readiness
    from row_bot.providers.models import TransportMode
    from row_bot.providers.resolution import ResolvedProviderConfig

    calls: list[str] = []

    def _status(provider_id: str):
        calls.append(provider_id)
        return {"configured": True}

    monkeypatch.setattr(readiness, "provider_status", _status)
    resolved = ResolvedProviderConfig(
        selection_ref="model:openai:gpt-4o",
        provider_id="openai",
        model_id="gpt-4o",
        runtime_model="gpt-4o",
        provider_display_name="OpenAI API",
        transport=TransportMode.OPENAI_CHAT,
    )
    snapshot = {
        "tasks": ["chat"],
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "transport": TransportMode.OPENAI_CHAT.value,
        "tool_calling": True,
        "streaming": True,
    }

    result = readiness.evaluate_runtime_readiness(
        resolved,
        capability_snapshot=snapshot,
        context_window_override=65_536,
    )

    assert calls == ["openai"]
    assert result.timings["provider_status_ms"] >= 0
    assert result.timings["agent_readiness_ms"] >= 0
    assert result.timings["chat_readiness_ms"] >= 0


def test_picker_option_loading_uses_non_refreshing_status_and_no_live_catalog(tmp_path, monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.providers.claude_subscription as claude_subscription
    import row_bot.providers.codex as codex
    import row_bot.providers.model_catalog_cache as catalog_cache
    import row_bot.providers.runtime as provider_runtime
    import row_bot.providers.selection as selection
    import row_bot.providers.xai_oauth as xai_oauth

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    def _boom(*args, **kwargs):
        raise AssertionError("picker option loading must not refresh live catalogs")

    monkeypatch.setattr(catalog_cache, "refresh_model_catalog_cache", _boom)
    monkeypatch.setattr(codex, "list_codex_model_infos", _boom)
    monkeypatch.setattr(claude_subscription, "list_claude_subscription_model_infos", _boom)
    monkeypatch.setattr(xai_oauth, "list_xai_oauth_model_infos", _boom)
    selection._provider_status_picker_cache.clear()

    refresh_flags: list[bool] = []

    def _provider_status(provider_id: str, *, refresh_tokens: bool = True):
        refresh_flags.append(refresh_tokens)
        return {"configured": True, "runtime_enabled": True}

    monkeypatch.setattr(provider_runtime, "provider_status", _provider_status)
    snapshot = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}
    selection.add_quick_choice_for_model(
        "gpt-5.5",
        provider_id="codex",
        display_name="GPT-5.5",
        capabilities_snapshot=snapshot,
    )

    options, diagnostics = selection.list_model_choice_options("chat", return_diagnostics=True)

    assert [option["value"] for option in options] == ["model:codex:gpt-5.5"]
    assert refresh_flags == [False]
    assert diagnostics["quick_choices_provider_status_calls"] == 1
    assert diagnostics["quick_choices_provider_status_refresh_tokens"] is False


def test_capability_resolution_uses_xai_oauth_cache_before_live_catalog(tmp_path, monkeypatch):
    import row_bot.providers.xai_oauth as xai_oauth
    from row_bot.providers.capability_resolution import resolve_capability_snapshot

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    provider_config.save_provider_config({
        "providers": {
            "xai_oauth": {
                "catalog_cache": {
                    "models": [{
                        "id": "grok-4.3",
                        "display_name": "grok-4.3",
                        "context_window": 1_000_000,
                        "capabilities": ["chat", "streaming", "text", "vision"],
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                        "tasks": ["responses"],
                        "tool_calling": True,
                        "streaming": True,
                        "transport": "openai_responses",
                    }],
                },
            },
        },
    })

    def _boom(*args, **kwargs):
        raise AssertionError("capability resolution must not call live xAI OAuth catalog reads")

    monkeypatch.setattr(xai_oauth, "list_xai_oauth_model_infos", _boom)

    snapshot = resolve_capability_snapshot("xai_oauth", "grok-4.3")

    assert set(snapshot["input_modalities"]) == {"text", "image"}
    assert snapshot["tool_calling"] is True


def test_context_and_vision_helpers_use_xai_oauth_status_cache(tmp_path, monkeypatch):
    import row_bot.models as models
    import row_bot.providers.xai_oauth as xai_oauth

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    provider_config.save_provider_config({
        "providers": {
            "xai_oauth": {
                "catalog_cache": {
                    "models": [{
                        "id": "grok-4.3",
                        "display_name": "grok-4.3",
                        "context_window": 1_000_000,
                        "capabilities": ["chat", "streaming", "text", "vision"],
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                        "tasks": ["responses"],
                        "tool_calling": True,
                        "streaming": True,
                        "transport": "openai_responses",
                    }],
                },
            },
        },
    })

    def _boom(*args, **kwargs):
        raise AssertionError("context and vision helpers must not call live xAI OAuth catalog reads")

    monkeypatch.setattr(xai_oauth, "list_xai_oauth_model_infos", _boom)
    old_cache = dict(models._cloud_model_cache)
    try:
        models._cloud_model_cache.clear()

        assert models.get_cloud_model_context("model:xai_oauth:grok-4.3") == 1_000_000
        assert models.is_cloud_vision_model("model:xai_oauth:grok-4.3") is True
        assert "model:xai_oauth:grok-4.3" in models.list_cloud_vision_models()
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


def test_non_tool_custom_endpoint_is_blocked_for_agent_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "profile": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "execution_location": "local",
        "auth_required": False,
    })

    import row_bot.models as models
    import row_bot.providers.readiness as readiness

    provider_id = custom_provider_id("lm-studio")
    model_ref = f"model:{custom_provider_id('lm-studio')}:qwen/qwen3.5-9b"
    monkeypatch.setattr(readiness, "provider_status", lambda provider_id: {"configured": True})
    monkeypatch.setattr(models, "get_context_policy", lambda value: models.ContextPolicy(
        model_ref=model_ref,
        provider_id=provider_id,
        runtime_model="qwen/qwen3.5-9b",
        native_max=65_536,
        user_cap=65_536,
        effective_context=65_536,
        policy_kind="local",
        cap_source="provider_metadata",
        request_application="trim_only",
    ))

    result = readiness.evaluate_agent_readiness(model_ref)

    assert result.ready is False
    assert "structured tool calling" in "; ".join(result.errors)


def test_agent_runtime_no_longer_uses_plain_chat_fallback():
    source = (ROOT / "src" / "row_bot" / "agent.py").read_text(encoding="utf-8")

    assert "get_plain_chat_system_prompt" not in source
    assert "plain_custom" not in source
    assert "_pre_model_trim_plain_chat" not in source
