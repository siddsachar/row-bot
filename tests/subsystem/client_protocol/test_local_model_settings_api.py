"""A local model's settings sheet over the client API (local-models uplift U1, U2).

The sheet reads one local model and saves its window, Thinking and reply limit; a server the person runs keeps its
own window; Learning's personal models and cloud models have no sheet. The Cloud reading limit is its own route.
"""
from __future__ import annotations

import json

import pytest

import row_bot.models as models_module
import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem

ROUTE = "/api/v1/settings/models/local-model"
# The window as the server reported it (the store fixture fixes every model's at 128K).
READ_WINDOW = models_module.get_model_max_context


@pytest.fixture
def store(tmp_path, monkeypatch):
    import row_bot.models as models

    monkeypatch.setattr(models, "_SETTINGS_PATH", tmp_path / "model_settings.json")
    monkeypatch.setattr(models, "_local_model_settings", {})
    monkeypatch.setattr(models, "_override_llm_cache", {})
    monkeypatch.setattr(models, "_observed_local_context", {})
    monkeypatch.setattr(models, "_local_context_mode", "auto")
    monkeypatch.setattr(models, "_num_ctx", models.LOCAL_AUTO_TARGET_CONTEXT)
    monkeypatch.setattr(models, "_current_model", "model:openai:gpt-5")
    monkeypatch.setattr(models, "_llm_instance", None)
    monkeypatch.setattr(models, "get_model_max_context", lambda name=None, **kwargs: 131_072)
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    return models


def _saved(store) -> dict:
    return json.loads(store._SETTINGS_PATH.read_text(encoding="utf-8")).get("local_model_settings", {})


def test_an_ollama_models_sheet_reads_and_saves_its_own_settings(store):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        sheet = client.get(ROUTE, params={"model_ref": "model:ollama:qwen3:8b"}, headers=headers)
        assert sheet.status_code == 200, sheet.text
        view = sheet.json()
        assert view["engine"] == "ollama" and view["context"]["editable"]
        assert view["context"]["automatic"] == 65_536 and view["context"]["chosen"] is None
        assert [choice["tokens"] for choice in view["context"]["choices"]] == [16_384, 32_768, 65_536, 131_072]
        assert client.post(ROUTE, json={"model_ref": "model:ollama:qwen3:8b", "context": 32_768}).status_code == 403

        saved = client.post(ROUTE, headers=headers, json={
            "model_ref": "model:ollama:qwen3:8b", "context": 32_768, "thinking": "off", "reply_limit": 8_192})

    assert saved.status_code == 200, saved.text
    assert saved.json()["context"]["chosen"] == 32_768 and saved.json()["context"]["current"] == 32_768
    assert saved.json()["thinking"]["value"] == "off" and saved.json()["reply_limit"]["value"] == 8_192
    assert _saved(store) == {"qwen3:8b": {"context": 32_768, "reasoning": "off", "reply_limit": 8_192}}


def test_the_persons_sampling_is_saved_kept_by_other_changes_and_refused_out_of_range(store):
    ref = "model:ollama:qwen3.5:9b"
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        card = client.get(ROUTE, params={"model_ref": ref}, headers=headers).json()["sampling"]
        saved = client.post(ROUTE, headers=headers, json={
            "model_ref": ref, "sampling": "custom", "sampling_values": {"temperature": 0.4, "top_k": 30}})
        # A change that doesn't name sampling (an older client) keeps it.
        kept = client.post(ROUTE, headers=headers, json={"model_ref": ref, "reply_limit": 8_192})
        refused = client.post(ROUTE, headers=headers, json={
            "model_ref": ref, "sampling": "custom", "sampling_values": {"temperature": 2.5}})

    assert card["value"] == "auto" and card["card"]["non_thinking"]["temperature"] == 0.7
    assert saved.status_code == 200, saved.text
    assert saved.json()["sampling"]["custom"] == {"temperature": 0.4, "top_k": 30}
    assert kept.json()["sampling"]["value"] == "custom"
    assert refused.status_code == 422
    assert _saved(store) == {"qwen3.5:9b": {"reply_limit": 8_192, "sampling": {"temperature": 0.4, "top_k": 30}}}


def test_a_size_the_sheet_doesnt_offer_is_refused(store):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        for body in ({"context": 262_144}, {"reply_limit": 65_536}, {"context": 50_000}):
            refused = client.post(ROUTE, headers=headers, json={"model_ref": "model:ollama:qwen3:8b", **body})
            assert refused.status_code == 422, body
    assert not store._SETTINGS_PATH.exists()


@pytest.mark.parametrize("model_ref", [
    "model:ollama:row-bot-personal-v2:abcd1234",  # Learning's, saved before the marker
    "model:ollama:my-arm:latest",  # Learning's, marked
    "model:openai:gpt-5",  # a cloud model
])
def test_learnings_models_and_cloud_models_have_no_sheet(store, model_ref):
    store.set_local_model_settings("row-bot-personal-v2:abcd1234", context=16_384, reasoning="off")
    store.set_local_model_settings("my-arm:latest", context=16_384, reasoning="off", owner="learning")
    before = _saved(store)
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        read = client.get(ROUTE, params={"model_ref": model_ref}, headers=headers)
        saved = client.post(ROUTE, headers=headers, json={"model_ref": model_ref, "context": 65_536})

    assert read.status_code == 409 and read.json()["code"] == "model_configuration_unavailable"
    assert saved.status_code == 409
    assert _saved(store) == before


def test_a_server_the_person_runs_keeps_its_own_window(store, monkeypatch):
    monkeypatch.setattr(store, "get_model_max_context", READ_WINDOW)  # what the server said it loaded
    save_custom_endpoint({"id": "lmstudio-1234", "name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1",
                          "profile": "lmstudio", "execution_location": "local", "auth_required": False,
                          "models": [{"id": "gemma-4", "model_id": "gemma-4", "context_window": 32_768,
                                      "provider": custom_provider_id("lmstudio-1234")}]})
    ref = f"model:{custom_provider_id('lmstudio-1234')}:gemma-4"
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        view = client.get(ROUTE, params={"model_ref": ref}, headers=headers).json()
        refused = client.post(ROUTE, headers=headers, json={"model_ref": ref, "context": 32_768})
        saved = client.post(ROUTE, headers=headers, json={"model_ref": ref, "reply_limit": 4_096})

    assert view["engine"] == "server" and view["engine_name"] == "LM Studio" and view["profile"] == "lmstudio"
    assert not view["context"]["editable"] and view["context"]["choices"] == []
    assert view["context"]["loaded"] == 32_768 and 65_536 in view["context"]["reload_choices"]
    assert refused.status_code == 422
    assert saved.status_code == 200 and saved.json()["reply_limit"]["value"] == 4_096


def test_the_cloud_reading_limit_saves_without_a_policy_kind(store, monkeypatch):
    monkeypatch.setattr(store, "_cloud_context_override", None)
    monkeypatch.setattr(store, "_cloud_num_ctx", store.DEFAULT_CLOUD_CONTEXT_SIZE)
    monkeypatch.setattr(store, "set_context_size", lambda size: pytest.fail("the local window isn't this setting"))
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        saved = client.post("/api/v1/settings/models/context", headers=headers, json={"cap": 65_536})
        stale = client.post("/api/v1/settings/models/context", headers=headers,
                            json={"policy_kind": "local", "cap": 65_536})

    assert saved.status_code == 200, saved.text
    assert saved.json()["context"]["selected_cap"] == 65_536
    assert store.get_cloud_context_override() == 65_536
    assert stale.status_code == 422
