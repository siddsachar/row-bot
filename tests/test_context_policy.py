import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint


def test_context_policy_uses_local_cap_for_ollama_ref(monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_cloud_num_ctx", 1_048_576)
    monkeypatch.setattr(models, "get_model_max_context", lambda model_name=None: 32_768)

    policy = models.get_context_policy("model:ollama:qwen3:14b")

    assert policy.provider_id == "ollama"
    assert policy.policy_kind == "local"
    assert policy.user_cap == 65_536
    assert policy.effective_context == 32_768
    assert policy.request_application == "ollama_num_ctx"


def test_context_policy_uses_provider_cap_for_cloud_ref(monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_cloud_num_ctx", 131_072)
    monkeypatch.setattr(models, "get_model_max_context", lambda model_name=None: 1_048_576)

    policy = models.get_context_policy("model:openai:gpt-5.5")

    assert policy.provider_id == "openai"
    assert policy.policy_kind == "provider"
    assert policy.user_cap == 131_072
    assert policy.effective_context == 131_072
    assert policy.request_application == "trim_only"


def test_context_policy_coerces_string_context_caps(monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(models, "_num_ctx", "65536")
    monkeypatch.setattr(models, "_cloud_num_ctx", "262144")
    monkeypatch.setattr(models, "get_model_max_context", lambda model_name=None: "131072")

    local_policy = models.get_context_policy("model:ollama:qwen3:14b")
    cloud_policy = models.get_context_policy("model:openai:gpt-5.5")

    assert local_policy.user_cap == 65_536
    assert local_policy.native_max == 131_072
    assert local_policy.effective_context == 65_536
    assert cloud_policy.user_cap == 262_144
    assert cloud_policy.native_max == 131_072
    assert cloud_policy.effective_context == 131_072


def test_model_info_coerces_string_context_window():
    from row_bot.providers.models import ModelInfo, TransportMode

    info = ModelInfo(
        provider_id="openai",
        model_id="gpt-test",
        display_name="GPT Test",
        context_window="131072",
        transport=TransportMode.OPENAI_CHAT,
    )

    assert info.context_window == 131_072
    assert isinstance(info.context_window, int)


def test_context_setters_coerce_ui_string_values(monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(models, "_llm_instance", object())
    monkeypatch.setattr(models, "_current_model", "model:openai:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda model_name: True)
    monkeypatch.setattr(models, "_get_cloud_llm", lambda model_name: object())
    saved = {}
    monkeypatch.setattr(models, "_save_settings", lambda payload: saved.update(payload))

    models.set_cloud_context_size("262144")
    models.set_context_size("65536")

    assert models.get_cloud_context_size() == 262_144
    assert models.get_user_context_size() == 65_536
    assert saved["cloud_context_size"] == 262_144
    assert saved["context_size"] == 65_536


def test_local_llm_construction_does_not_force_reasoning(monkeypatch):
    import row_bot.models as models

    captured = {}

    class _FakeChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(models, "ChatOllama", _FakeChatOllama)
    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_ollama_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(models, "is_cloud_model", lambda model_name: False)
    monkeypatch.setattr(models, "get_model_max_context", lambda model_name=None: 65_536)
    models.clear_llm_cache()

    model = models.get_llm_for("model:ollama:vendor/non-tool-chat:14b")

    assert model
    assert captured["model"] == "vendor/non-tool-chat:14b"
    assert captured["num_ctx"] == models.get_user_context_size()
    assert "reasoning" not in captured


def test_local_thinking_model_enables_reasoning(monkeypatch):
    import row_bot.models as models

    captured = {}

    class _FakeChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(models, "ChatOllama", _FakeChatOllama)
    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_ollama_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(models, "is_cloud_model", lambda model_name: False)
    monkeypatch.setattr(models, "get_model_max_context", lambda model_name=None: 65_536)
    models.clear_llm_cache()

    model = models.get_llm_for("model:ollama:qwen3.6:27b")

    assert model
    assert captured["model"] == "qwen3.6:27b"
    assert captured["reasoning"] is True


def test_context_policy_uses_local_cap_for_local_custom_endpoint(tmp_path, monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_cloud_num_ctx", 131_072)
    save_custom_endpoint({
        "id": "omlx",
        "name": "oMLX",
        "base_url": "http://127.0.0.1:8000/v1",
        "execution_location": "local",
        "auth_required": False,
        "models": [{
            "id": "qwen-local",
            "model_id": "qwen-local",
            "ctx": 32_768,
            "provider": custom_provider_id("omlx"),
            "capabilities_snapshot": {"tasks": ["chat"]},
        }],
    })
    models._cloud_model_cache.pop("qwen-local", None)

    policy = models.get_context_policy(f"model:{custom_provider_id('omlx')}:qwen-local")

    assert policy.provider_id == custom_provider_id("omlx")
    assert policy.policy_kind == "local"
    assert policy.user_cap == 65_536
    assert policy.effective_context == 32_768
    assert policy.request_application == "trim_only"


def test_context_policy_uses_profile_fallback_for_unknown_local_custom_endpoint(tmp_path, monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_cloud_num_ctx", 131_072)
    save_custom_endpoint({
        "id": "lm-studio",
        "name": "LM Studio",
        "profile": "lmstudio",
        "base_url": "http://127.0.0.1:1234/v1",
        "execution_location": "local",
        "auth_required": False,
        "models": [{
            "id": "qwen-local",
            "model_id": "qwen-local",
            "provider": custom_provider_id("lm-studio"),
            "capabilities_snapshot": {"tasks": ["chat"]},
        }],
    })
    models._cloud_model_cache.pop("qwen-local", None)

    policy = models.get_context_policy(f"model:{custom_provider_id('lm-studio')}:qwen-local")

    assert policy.provider_id == custom_provider_id("lm-studio")
    assert policy.policy_kind == "local"
    assert policy.native_max == 32_768
    assert policy.cap_source == "profile_default"
    assert policy.effective_context == 32_768


def test_context_policy_uses_provider_cap_for_remote_custom_endpoint(tmp_path, monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_cloud_num_ctx", 131_072)
    save_custom_endpoint({
        "id": "proxy",
        "name": "Proxy",
        "base_url": "https://llm.example.test/v1",
        "execution_location": "remote",
        "auth_required": True,
        "models": [{
            "id": "qwen-remote",
            "model_id": "qwen-remote",
            "ctx": 262_144,
            "provider": custom_provider_id("proxy"),
            "capabilities_snapshot": {"tasks": ["chat"]},
        }],
    })
    models._cloud_model_cache.pop("qwen-remote", None)

    policy = models.get_context_policy(f"model:{custom_provider_id('proxy')}:qwen-remote")

    assert policy.provider_id == custom_provider_id("proxy")
    assert policy.policy_kind == "provider"
    assert policy.user_cap == 131_072
    assert policy.effective_context == 131_072


def test_context_policy_marks_custom_runtime_context_param(tmp_path, monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "_num_ctx", 65_536)
    monkeypatch.setattr(models, "_cloud_num_ctx", 131_072)
    save_custom_endpoint({
        "id": "llamacpp",
        "name": "llama.cpp",
        "profile": "llama_cpp",
        "base_url": "http://127.0.0.1:8080/v1",
        "execution_location": "local",
        "auth_required": False,
        "models": [{
            "id": "qwen-local",
            "model_id": "qwen-local",
            "ctx": 32_768,
            "provider": custom_provider_id("llamacpp"),
            "capabilities_snapshot": {"tasks": ["chat"]},
        }],
    })
    models._cloud_model_cache.pop("qwen-local", None)

    policy = models.get_context_policy(f"model:{custom_provider_id('llamacpp')}:qwen-local")

    assert policy.provider_id == custom_provider_id("llamacpp")
    assert policy.policy_kind == "local"
    assert policy.effective_context == 32_768
    assert policy.request_application == "request_param:n_ctx"
