from __future__ import annotations

from types import SimpleNamespace

import pytest

import row_bot.api_keys as api_keys
import row_bot.providers.config as provider_config
import row_bot.providers.runtime as runtime
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint


pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def isolated_provider_config(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})


def test_provider_status_handles_ollama_custom_and_secret_backed_providers(monkeypatch) -> None:
    monkeypatch.setattr("row_bot.models._ollama_reachable", lambda: True)
    monkeypatch.setattr("row_bot.models.list_local_models", lambda: ["qwen3:14b", "llama3.2"])
    monkeypatch.setattr(
        runtime,
        "provider_secret_status",
        lambda provider_id, _field: {
            "configured": provider_id == "openai",
            "source": "keyring" if provider_id == "openai" else "",
            "fingerprint": "fp-openai" if provider_id == "openai" else "",
        },
    )

    save_custom_endpoint(
        {
            "id": "lab",
            "name": "Local Lab",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth_required": False,
        }
    )

    assert runtime.provider_status("ollama") == {
        "provider_id": "ollama",
        "configured": True,
        "source": "local_daemon",
        "fingerprint": "",
        "model_count": 2,
    }
    custom_status = runtime.provider_status(custom_provider_id("lab"))
    assert custom_status["configured"] is True
    assert custom_status["source"] == "no_auth"
    assert custom_status["base_url"] == "http://127.0.0.1:8000/v1"
    assert runtime.provider_status("openai")["fingerprint"] == "fp-openai"


def test_list_configured_provider_ids_is_ordered_tolerant_and_includes_enabled_custom(monkeypatch) -> None:
    available = {"openai", "atlascloud", "minimax"}

    def fake_status(provider_id: str, *, refresh_tokens: bool = True) -> dict[str, object]:
        if provider_id == "claude_subscription":
            raise RuntimeError("probe unavailable")
        return {"configured": provider_id in {"codex", "xai_oauth"}}

    monkeypatch.setattr(runtime, "is_provider_available", lambda provider_id: provider_id in available)
    monkeypatch.setattr(runtime, "provider_status", fake_status)
    save_custom_endpoint(
        {
            "id": "enabled",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth_required": False,
            "enabled": True,
        }
    )
    save_custom_endpoint(
        {
            "id": "disabled",
            "base_url": "http://127.0.0.1:9000/v1",
            "auth_required": False,
            "enabled": False,
        }
    )

    assert runtime.list_configured_provider_ids() == [
        "openai",
        "atlascloud",
        "minimax",
        "codex",
        "xai_oauth",
        custom_provider_id("enabled"),
    ]


def test_custom_endpoint_chat_compatibility_uses_declared_model_snapshots() -> None:
    provider_id = custom_provider_id("lab")
    save_custom_endpoint(
        {
            "id": "lab",
            "base_url": "http://127.0.0.1:8000/v1",
            "auth_required": False,
            "models": [
                "ignored",
                {"id": "embedding-only", "capabilities_snapshot": {"tasks": ["embedding"]}},
                {
                    "model_id": "chatty",
                    "capabilities_snapshot": {
                        "tasks": ["chat"],
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                },
            ],
        }
    )

    assert runtime._capability_snapshot_for_selection("chatty", provider_id)["tasks"] == ["chat"]
    with pytest.raises(ValueError, match="not compatible with chat"):
        runtime.ensure_chat_model_compatible("embedding-only", provider_id)
    runtime.ensure_chat_model_compatible("unknown-model", provider_id)


def test_provider_snapshot_helpers_defer_to_resolution_modules(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "row_bot.providers.capability_resolution.resolve_capability_snapshot",
        lambda provider_id, model_name: calls.append((provider_id, model_name)) or {"tasks": ["chat"]},
    )
    monkeypatch.setattr(
        "row_bot.providers.capability_resolution.cached_provider_capability_snapshot",
        lambda provider_id, model_name: {"provider": provider_id, "model": model_name},
    )
    monkeypatch.setattr(
        "row_bot.providers.resolution.resolve_provider_config",
        lambda model_name, **_kwargs: SimpleNamespace(provider_id=f"inferred:{model_name}"),
    )

    assert runtime._capability_snapshot_for_selection("gpt-4o", "openai") == {"tasks": ["chat"]}
    assert calls == [("openai", "gpt-4o")]
    assert runtime._cached_provider_capability_snapshot("openrouter", "vendor/model") == {
        "provider": "openrouter",
        "model": "vendor/model",
    }
    assert runtime._infer_provider("bare-model") == "inferred:bare-model"
    assert runtime.openai_model_uses_responses_api("openai/gpt-5-mini") is True
    assert runtime.openai_model_uses_responses_api("gpt-4o") is False
