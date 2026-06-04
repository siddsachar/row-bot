import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint
from row_bot.providers.models import TransportMode
from row_bot.providers.resolution import resolve_provider_config


def test_resolve_provider_config_preserves_ollama_identity():
    resolved = resolve_provider_config("model:ollama:qwen3:14b")

    assert resolved.selection_ref == "model:ollama:qwen3:14b"
    assert resolved.provider_id == "ollama"
    assert resolved.runtime_model == "qwen3:14b"
    assert resolved.transport == TransportMode.OLLAMA_CHAT
    assert resolved.execution_location == "local"


def test_resolve_provider_config_normalizes_legacy_local_ref():
    resolved = resolve_provider_config("model:local:qwen3:14b")

    assert resolved.selection_ref == "model:ollama:qwen3:14b"
    assert resolved.provider_id == "ollama"
    assert resolved.runtime_model == "qwen3:14b"


def test_resolve_provider_config_requires_provider_for_unknown_bare_model():
    try:
        resolve_provider_config("qwen3:14b", allow_legacy_local=False)
    except ValueError as exc:
        assert "Provider is required" in str(exc)
    else:
        raise AssertionError("Expected unknown bare model to require a provider")


def test_resolve_provider_config_uses_custom_endpoint_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    save_custom_endpoint({
        "id": "local-vllm",
        "name": "Local vLLM",
        "base_url": "http://127.0.0.1:8000/v1/",
        "auth_required": False,
        "execution_location": "local",
    })
    provider_id = custom_provider_id("local-vllm")

    resolved = resolve_provider_config(f"model:{provider_id}:meta-llama/Llama-3.1-8B-Instruct")

    assert resolved.provider_id == provider_id
    assert resolved.runtime_model == "meta-llama/Llama-3.1-8B-Instruct"
    assert resolved.base_url == "http://127.0.0.1:8000/v1"
    assert resolved.execution_location == "local"
    assert resolved.transport == TransportMode.OPENAI_CHAT
