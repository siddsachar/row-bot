import ast
from pathlib import Path
import time

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


def test_inline_model_picker_tracks_current_value_for_default_switch():
    source = (ROOT / "src" / "row_bot" / "ui" / "chat_components.py").read_text(encoding="utf-8")
    picker_section = source.split("def _build_inline_model_picker", 1)[1]

    assert "_current_picker_value = [_picker_val]" in picker_section
    assert "if val == _current_picker_value[0]:" in picker_section
    assert "_current_picker_value[0] = val" in picker_section
    assert "e.sender.set_value(_current_picker_value[0])" in picker_section


def test_inline_model_picker_allows_chat_only_in_normal_chat_and_blocks_agent_only_surfaces():
    source = (ROOT / "src" / "row_bot" / "ui" / "chat_components.py").read_text(encoding="utf-8")
    picker_section = source.split("def _build_inline_model_picker", 1)[1]

    assert "check_tool_support(runtime_model)" not in picker_section
    assert "evaluate_agent_readiness(val)" in picker_section
    assert "active_developer_workspace_id" in picker_section
    assert "active_designer_project" in picker_section
    assert "state.thread_model_override = val" in picker_section


def test_inline_model_picker_uses_stale_while_refresh_cache():
    source = (ROOT / "src" / "row_bot" / "ui" / "chat_components.py").read_text(encoding="utf-8")
    picker_section = source.split("def _build_inline_model_picker", 1)[1]

    assert "_get_cached_model_picker_options()" in picker_section
    assert "chat.model_picker.options.cache" in picker_section
    assert "cached_options is None or _cached_picker_stale" in picker_section
    assert "_merge_picker_options(_cached_options)" in picker_section
    assert "_refresh_model_picker_options()" in picker_section
    assert "_cur_mo_value" in picker_section


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


def test_model_picker_cache_invalidates_when_provider_config_changes(tmp_path, monkeypatch):
    import row_bot.ui.chat_components as chat_components

    config_path = tmp_path / "providers.json"
    config_path.write_text('{"quick_choices":[]}', encoding="utf-8")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(chat_components, "_MODEL_PICKER_CACHE_TTL_SECONDS", 60.0, raising=False)
    monkeypatch.setattr(
        chat_components,
        "_model_picker_options_cache",
        {
            "signature": chat_components._provider_config_signature(),
            "loaded_at": time.monotonic(),
            "options": [{"value": "model:openrouter:test/model", "label": "Test Model"}],
        },
        raising=False,
    )

    cached = chat_components._get_cached_model_picker_options()
    assert cached is not None
    assert cached[0][0]["value"] == "model:openrouter:test/model"
    assert cached[1] is False

    config_path.write_text('{"quick_choices":[],"custom_endpoints":[]}', encoding="utf-8")

    assert chat_components._get_cached_model_picker_options() is None


def test_chat_voice_status_literals_have_no_mojibake():
    bad_sentinels = ("Ã", "Â", "â", "ð", "�")
    paths = [
        ROOT / "src" / "row_bot" / "ui" / "streaming.py",
        ROOT / "src" / "row_bot" / "ui" / "chat.py",
        ROOT / "src" / "row_bot" / "ui" / "chat_components.py",
        ROOT / "src" / "row_bot" / "ui" / "sidebar.py",
    ]
    offenders: list[str] = []

    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(sentinel in node.value for sentinel in bad_sentinels):
                    offenders.append(f"{path.name}:{getattr(node, 'lineno', '?')}: {node.value!r}")

    assert offenders == []


def test_model_picker_cache_returns_stale_options_for_background_refresh(tmp_path, monkeypatch):
    import row_bot.ui.chat_components as chat_components

    config_path = tmp_path / "providers.json"
    config_path.write_text('{"quick_choices":[]}', encoding="utf-8")
    monkeypatch.setattr(provider_config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(chat_components, "_MODEL_PICKER_CACHE_TTL_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(
        chat_components,
        "_model_picker_options_cache",
        {
            "signature": chat_components._provider_config_signature(),
            "loaded_at": time.monotonic() - 5.0,
            "options": [{"value": "model:openrouter:test/model", "label": "Test Model"}],
        },
        raising=False,
    )

    cached = chat_components._get_cached_model_picker_options()

    assert cached is not None
    assert cached[0][0]["label"] == "Test Model"
    assert cached[1] is True


def test_chat_banner_uses_provider_resolution_for_local_custom_models():
    source = (ROOT / "src" / "row_bot" / "ui" / "chat.py").read_text(encoding="utf-8")
    surface_section = source.split("def _model_surface", 1)[1].split("def _render_model_banner", 1)[0]

    assert "resolve_provider_config(active_model" in surface_section
    assert "evaluate_runtime_readiness(resolved, refresh_provider_status=False)" in surface_section
    assert "Chat Only - tools and actions are off" in surface_section
    assert "local_execution" in surface_section
    assert "local/private" in surface_section


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
    import row_bot.providers.codex as codex
    import row_bot.providers.model_catalog_cache as catalog_cache
    import row_bot.providers.runtime as provider_runtime
    import row_bot.providers.selection as selection

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})

    def _boom(*args, **kwargs):
        raise AssertionError("picker option loading must not refresh live catalogs")

    monkeypatch.setattr(catalog_cache, "refresh_model_catalog_cache", _boom)
    monkeypatch.setattr(codex, "list_codex_model_infos", _boom)

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


def test_brain_badge_uses_agent_readiness_for_provider_qualified_ollama(monkeypatch):
    import row_bot.models as models
    import row_bot.ui.settings as settings_ui

    monkeypatch.setattr(models, "get_context_policy", lambda value: models.ContextPolicy(
        model_ref="model:ollama:qwen3.6:27b",
        provider_id="ollama",
        runtime_model="qwen3.6:27b",
        native_max=65_536,
        user_cap=65_536,
        effective_context=65_536,
        policy_kind="local",
        cap_source="provider_metadata",
        request_application="ollama_num_ctx",
    ))

    badge = settings_ui._agent_mode_badge_state(
        "model:ollama:qwen3.6:27b",
        status={"configured": True, "source": "local_daemon"},
        context_window_override=65_536,
    )

    assert badge["visible"] is False


def test_brain_badge_marks_ollama_unknown_tools_as_unverified(monkeypatch):
    import row_bot.models as models
    import row_bot.ui.settings as settings_ui

    monkeypatch.setattr(models, "get_context_policy", lambda value: models.ContextPolicy(
        model_ref="model:ollama:gemma3:4b",
        provider_id="ollama",
        runtime_model="gemma3:4b",
        native_max=131_072,
        user_cap=65_536,
        effective_context=65_536,
        policy_kind="local",
        cap_source="provider_metadata",
        request_application="ollama_num_ctx",
    ))

    badge = settings_ui._agent_mode_badge_state(
        "model:ollama:gemma3:4b",
        status={"configured": True, "source": "local_daemon"},
        context_window_override=65_536,
    )

    assert badge["visible"] is True
    assert badge["label"] == "tools unverified"


def test_brain_badge_keeps_openrouter_unknown_tools_visible(monkeypatch):
    import row_bot.models as models
    import row_bot.ui.settings as settings_ui

    monkeypatch.setattr(models, "get_context_policy", lambda value: models.ContextPolicy(
        model_ref="model:openrouter:vendor/chat",
        provider_id="openrouter",
        runtime_model="vendor/chat",
        native_max=128_000,
        user_cap=128_000,
        effective_context=128_000,
        policy_kind="provider",
        cap_source="provider_metadata",
        request_application="trim_only",
    ))

    badge = settings_ui._agent_mode_badge_state(
        "model:openrouter:vendor/chat",
        capability_snapshot={
            "tasks": ["chat"],
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "tool_calling": None,
            "transport": "openai_chat",
        },
        status={"configured": True},
        context_window_override=128_000,
    )

    assert badge["visible"] is True
    assert badge["label"] == "chat only"


def test_brain_badge_marks_unprobed_custom_endpoint_probe_required(tmp_path, monkeypatch):
    import row_bot.models as models
    import row_bot.ui.settings as settings_ui

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    provider_id = custom_provider_id("lm-studio")
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "profile": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "execution_location": "local",
        "auth_required": False,
        "models": [{
            "id": "qwen/qwen3.5-9b",
            "model_id": "qwen/qwen3.5-9b",
            "context_window": 65_536,
            "capabilities_snapshot": {
                "tasks": ["chat"],
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "tool_calling": True,
            },
        }],
    })
    monkeypatch.setattr(models, "get_context_policy", lambda value: models.ContextPolicy(
        model_ref=f"model:{provider_id}:qwen/qwen3.5-9b",
        provider_id=provider_id,
        runtime_model="qwen/qwen3.5-9b",
        native_max=65_536,
        user_cap=65_536,
        effective_context=65_536,
        policy_kind="local",
        cap_source="provider_metadata",
        request_application="trim_only",
    ))

    badge = settings_ui._agent_mode_badge_state(
        f"model:{provider_id}:qwen/qwen3.5-9b",
        status={"configured": True},
        context_window_override=65_536,
    )

    assert badge["visible"] is True
    assert badge["label"] == "probe required"


def test_models_tab_rerenders_brain_readiness_badge_on_model_change():
    source = (ROOT / "src" / "row_bot" / "ui" / "settings.py").read_text(encoding="utf-8")
    models_section = source.split("def _render_models_tab_content", 1)[1].split("def _collect_models_tab_data", 1)[0]

    assert "brain_readiness_slot_ref" in models_section
    assert "slot.clear()" in models_section
    assert "_render_brain_readiness_badge(sel)" in models_section
    assert "_render_brain_readiness_badge(current_model)" in models_section
    assert "if not is_tool_compatible(current):" not in models_section
    assert "check_tool_support(runtime_model)" not in models_section
    assert "evaluate_runtime_readiness(sel)" in models_section
    assert "set as Chat Only" in models_section


def test_models_tab_validates_stale_vision_default_before_display():
    source = (ROOT / "src" / "row_bot" / "ui" / "settings.py").read_text(encoding="utf-8")
    models_section = source.split("def _render_models_tab_content", 1)[1].split("def _collect_models_tab_data", 1)[0]

    assert "vision_model_compatibility" in models_section
    assert "Resetting incompatible Vision default" in models_section
    assert "not currently marked as Vision-capable" in models_section
    assert "vision_select_value = vision_value if vision_value in vision_opts else None" in models_section
    assert "value=vision_select_value" in models_section
