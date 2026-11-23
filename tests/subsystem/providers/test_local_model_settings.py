"""Each local model's own settings (local-models uplift U1, U2): one store, applied by each engine.

Ollama gets the window as ``num_ctx``, the reply limit as ``num_predict`` and Thinking as ``think`` on every
request; a server the person runs gets the reply limit and Thinking per request while its window stays the
server's. The old global local window migrates once and stays the default of Ollama models without their own.
Learning's entries (marked ``owner: "learning"``, or personal-model names saved before the marker) are never
shown or changed, and only they make a model never think. The Cloud reading limit reaches cloud models only.
These drive the transports against fakes on loopback and read what reached them.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage

import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, save_custom_endpoint
from row_bot.providers.runtime import create_chat_model
from tests.helpers.fake_provider_http import Reply, fake_provider_http

pytestmark = pytest.mark.subsystem

MODEL = "qwen3.5:9b"
REF = f"model:ollama:{MODEL}"
ANSWER = Reply(body=json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": "4"},
                                             "finish_reason": "stop"}]}).encode())
CHAT = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}
PROMPT = [HumanMessage(content="What is 2 + 2?")]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An empty settings store in a scratch folder, with the global window on Automatic."""
    import row_bot.models as models

    monkeypatch.setattr(models, "_SETTINGS_PATH", tmp_path / "model_settings.json")
    monkeypatch.setattr(models, "_local_model_settings", {})
    monkeypatch.setattr(models, "_override_llm_cache", {})
    monkeypatch.setattr(models, "_observed_local_context", {})
    monkeypatch.setattr(models, "_local_context_mode", "auto")
    monkeypatch.setattr(models, "_num_ctx", models.LOCAL_AUTO_TARGET_CONTEXT)
    monkeypatch.setattr(models, "_cloud_context_override", None)
    monkeypatch.setattr(models, "_cloud_num_ctx", models.DEFAULT_CLOUD_CONTEXT_SIZE)
    monkeypatch.setattr(models, "_current_model", "model:openai:gpt-5")
    monkeypatch.setattr(models, "_llm_instance", None)
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    return models


@pytest.fixture
def ollama(store, tmp_path, monkeypatch):
    """Ollama on loopback with one Qwen model that thinks and reads 256K, in Row-Bot's catalog."""
    from row_bot.providers import model_catalog_cache as cache
    from row_bot.providers.model_catalog import load_ollama_catalog_rows
    from tests.helpers.fake_ollama import fake_streaming_ollama

    show = {"details": {"format": "gguf", "family": "qwen3.5", "families": ["qwen3.5"]},
            "capabilities": ["completion", "tools", "thinking"], "model_info": {"general.context_length": 262144}}
    with fake_streaming_ollama(tokens=["4"], model=MODEL, show=show) as server:
        monkeypatch.setenv("OLLAMA_HOST", server.url)
        monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
        rows = load_ollama_catalog_rows()
        cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(cache.CACHE_VERSION, 0.0, {}, rows, {}, (), "t"))
        yield server


def _ollama_request(server) -> dict:
    create_chat_model(REF).invoke(PROMPT)
    return server.chat_requests()[-1]


def test_an_ollama_models_own_window_reply_limit_and_thinking_reach_each_request(ollama, store):
    before = _ollama_request(ollama)
    assert before["options"]["num_ctx"] == 65_536  # Automatic: the default every Ollama model has
    assert before["options"]["num_predict"] == 65_536

    store.save_person_local_model_settings("ollama", MODEL, context=32_768, reasoning="off", reply_limit=8_192)
    sent = _ollama_request(ollama)

    assert sent["options"]["num_ctx"] == 32_768
    assert sent["options"]["num_predict"] == 8_192
    assert sent["think"] is False
    assert store.get_context_policy(REF, allow_probe=False).effective_limit_tokens == 32_768


def test_the_persons_thinking_off_is_a_default_a_chat_may_still_turn_on(ollama, store):
    from row_bot.providers.reasoning import (ReasoningRequestPlan, ReasoningSelection,
                                             resolve_reasoning_capabilities)

    store.save_person_local_model_settings("ollama", MODEL, reasoning="off")
    caps = resolve_reasoning_capabilities("ollama", MODEL)
    assert caps is not None and not store.local_model_never_thinks(MODEL)

    create_chat_model(REF, reasoning_plan=ReasoningRequestPlan(REF, ReasoningSelection(kind="on"), caps)).invoke(PROMPT)

    assert ollama.chat_requests()[-1]["think"] is True


def test_the_catalog_marks_the_models_that_have_a_settings_sheet(ollama, store):
    from row_bot.providers.client_status import list_cached_models

    rows = list_cached_models(surface="chat", provider_id=None, query="", cursor=None, limit=80,
                              readiness=False).items

    assert {row.selection_ref: row.local_settings for row in rows}[REF] is True


def test_automatic_again_removes_the_models_entry(store):
    store.save_person_local_model_settings("ollama", MODEL, context=32_768)
    store.save_person_local_model_settings("ollama", MODEL)

    assert store.local_model_settings(MODEL) == {}
    assert "local_model_settings" not in json.loads(store._SETTINGS_PATH.read_text(encoding="utf-8"))


def test_ollama_models_without_their_own_window_keep_the_window_chosen_before(store, monkeypatch):
    monkeypatch.setattr(store, "get_model_max_context", lambda name=None, **kwargs: 262_144)
    store.set_context_size(48_000)  # the old global setting
    store.save_person_local_model_settings("ollama", "qwen3:8b", context=131_072)

    assert store.get_context_policy("model:ollama:llama3.2:3b", allow_probe=False).effective_limit_tokens == 48_000
    assert store.get_context_policy("model:ollama:qwen3:8b", allow_probe=False).effective_limit_tokens == 131_072


@pytest.mark.parametrize("saved, migrated_entry", [
    ({"local_context_mode": "fixed", "context_size": 48_000}, {"context": 48_000}),
    ({"local_context_mode": "auto", "context_size": 65_536}, None),
])
def test_the_old_global_window_moves_once_to_the_ollama_model_in_use(store, saved, migrated_entry):
    old = {"context_policy_version": 3, "model": "model:ollama:qwen3:8b", **saved}

    migrated, changed = store._migrate_context_settings(old)

    assert changed
    assert (migrated.get("local_model_settings") or {}).get("qwen3:8b") == migrated_entry
    assert migrated["local_context_mode"] == saved["local_context_mode"]  # still the other models' default
    # Once: a later change of the setting isn't migrated again.
    again, _ = store._migrate_context_settings({**migrated, "local_model_settings": {}})
    assert again.get("local_model_settings") == {}


def test_a_cloud_model_in_use_migrates_nothing(store):
    migrated, _ = store._migrate_context_settings({"context_policy_version": 3, "model": "model:openai:gpt-5",
                                                   "local_context_mode": "fixed", "context_size": 48_000})

    assert "local_model_settings" not in migrated


# ── Learning's entries ───────────────────────────────────────────────────────

PERSONAL = "row-bot-personal-v2:abcd1234"
MARKED = "my-arm:latest"


def test_learnings_entries_are_invisible_to_the_person_and_never_changed(store):
    store.set_local_model_settings(PERSONAL, context=16_384, reasoning="off")  # saved before the marker
    store.set_local_model_settings(MARKED, context=16_384, reasoning="off", owner="learning")
    saved = json.loads(store._SETTINGS_PATH.read_text(encoding="utf-8"))["local_model_settings"]

    for name in (PERSONAL, MARKED):
        assert store.person_local_model_settings("ollama", name) == {}
        with pytest.raises(ValueError, match="learning_model"):
            store.save_person_local_model_settings("ollama", name, context=65_536)
        assert store.local_model_never_thinks(name)  # served as trained, whatever a chat asks

    assert json.loads(store._SETTINGS_PATH.read_text(encoding="utf-8"))["local_model_settings"] == saved
    assert saved[MARKED] == {"context": 16_384, "reasoning": "off", "owner": "learning"}
    assert store.local_model_settings(MARKED)["context"] == 16_384  # Learning's own reads keep working


def test_a_persons_own_entry_never_makes_a_model_a_learning_one(store):
    store.save_person_local_model_settings("ollama", "qwen3:8b", context=32_768, reasoning="off")

    assert not store.local_model_never_thinks("qwen3:8b")
    assert store.person_local_model_settings("ollama", "qwen3:8b") == {"context": 32_768, "reasoning": "off"}


# ── A server the person runs ─────────────────────────────────────────────────

def _endpoint(endpoint_id: str, base_url: str, *, profile: str = "llama_cpp", location: str = "local",
              model: str = "qwen3.5-9b", **extra) -> str:
    save_custom_endpoint({"id": endpoint_id, "name": endpoint_id, "base_url": base_url, "profile": profile,
                          "execution_location": location, "auth_required": False,
                          "models": [{"id": model, "model_id": model, "provider": custom_provider_id(endpoint_id),
                                      "capabilities_snapshot": CHAT, "context_window": 32_768}], **extra})
    return custom_provider_id(endpoint_id)


def _sent(server, provider: str, model: str = "qwen3.5-9b") -> dict:
    create_chat_model(model, provider).invoke(PROMPT)
    return json.loads(server.seen[-1].body)


def test_a_server_the_person_runs_gets_their_reply_limit_and_thinking_per_request(store):
    with fake_provider_http([ANSWER, ANSWER]) as server:
        provider = _endpoint("mine", f"{server.url}/v1", reasoning_mode="on")
        assert _sent(server, provider)["max_tokens"] == 32_768  # llama.cpp's own limit (#1037)

        store.save_person_local_model_settings(provider, "qwen3.5-9b", reasoning="off", reply_limit=4_096)
        body = _sent(server, provider)

    assert body["max_tokens"] == 4_096
    assert body["chat_template_kwargs"]["enable_thinking"] is False  # the model's own beats the endpoint's


def test_a_remote_endpoint_and_learnings_endpoints_ignore_per_model_settings(store, monkeypatch):
    from row_bot.providers import custom

    with fake_provider_http([ANSWER, ANSWER]) as server:
        monkeypatch.setattr(custom, "managed_connection", lambda endpoint, start=True: (f"{server.url}/v1", "key"))
        for endpoint_id, extra in (("gateway", {"location": "remote"}),
                                   ("row-bot-personal-row-bot", {"managed": "learning", "reasoning_mode": "off"})):
            provider = _endpoint(endpoint_id, f"{server.url}/v1", **extra)
            key = store.local_settings_key(provider, "qwen3.5-9b")
            store._local_model_settings[key] = {"reply_limit": 4_096, "reasoning": "on"}  # however it got there
            body = _sent(server, provider)
            assert body.get("max_tokens") != 4_096
            assert (body.get("chat_template_kwargs") or {}).get("enable_thinking") is not True


def _json(payload: dict) -> Reply:
    return Reply(body=json.dumps(payload).encode())


def _studio(loaded: int) -> dict:
    return {"models": [{"type": "llm", "key": "gemma-4", "max_context_length": 131_072,
                        "loaded_instances": [{"id": "gemma-4", "config": {"context_length": loaded}}]}]}


def test_lm_studio_loads_the_model_again_at_the_window_the_person_asked_for(store):
    from row_bot.application.client_local_model_settings import read_local_model_settings, reload_in_lm_studio

    script = [_json(_studio(4_096)), _json({"instance_id": "gemma-4"}),
              _json({"type": "llm", "instance_id": "gemma-4", "status": "loaded",
                     "load_config": {"context_length": 65_536}}),
              _json({"data": [{"id": "gemma-4", "object": "model"}]}), _json(_studio(65_536))]
    with fake_provider_http(script) as server:
        provider = _endpoint("lmstudio-1234", f"{server.url}/v1", profile="lmstudio", model="gemma-4")
        ref = f"model:{provider}:gemma-4"
        sheet = read_local_model_settings(ref)
        assert not sheet["context"]["editable"] and 65_536 in sheet["context"]["reload_choices"]

        view = reload_in_lm_studio(ref, 65_536, validate=lambda: None)

        calls = [(request.method, request.path) for request in server.seen[:3]]
        bodies = [json.loads(request.body) for request in server.seen[1:3]]
    assert calls == [("GET", "/api/v1/models"), ("POST", "/api/v1/models/unload"), ("POST", "/api/v1/models/load")]
    assert bodies == [{"instance_id": "gemma-4"}, {"model": "gemma-4", "context_length": 65_536,
                                                    "echo_load_config": True}]
    assert view["notice"] == "LM Studio loaded it at 64K."
    assert view["context"]["loaded"] == 65_536


def test_lm_studio_is_never_asked_for_more_than_the_model_reads(store):
    from row_bot.application.client_local_model_settings import reload_in_lm_studio

    with fake_provider_http([_json(_studio(4_096))]) as server:
        provider = _endpoint("lmstudio-1234", f"{server.url}/v1", profile="lmstudio", model="gemma-4")

        view = reload_in_lm_studio(f"model:{provider}:gemma-4", 262_144, validate=lambda: None)

        assert [request.method for request in server.seen] == ["GET"]  # nothing unloaded
    assert view["notice"] == "This model reads at most 131,072 tokens. Choose a smaller size."


# ── Sampling (D2: the model card's preset, which the person may override) ────

SAMPLING = ("temperature", "top_p", "top_k", "min_p", "presence_penalty")
OWN = {"temperature": 0.3, "top_p": 0.9, "top_k": 40, "min_p": 0.05, "presence_penalty": 0.5}


def _sampling(values: dict) -> dict:
    return {key: values[key] for key in SAMPLING if key in values}


def _save_sampling(ref: str, sampling: str, values: dict | None = None) -> dict:
    from row_bot.application.client_local_model_settings import save_local_model_settings

    return save_local_model_settings(ref, context=None, thinking="auto", reply_limit=None, sampling=sampling,
                                     sampling_values=values, validate=lambda: None)


def test_the_sheet_shows_the_model_cards_values_and_the_persons_own_replace_them_on_ollama(ollama, store):
    from row_bot.application.client_local_model_settings import read_local_model_settings

    sheet = read_local_model_settings(REF)["sampling"]
    assert sheet["value"] == "auto" and sheet["thinks"] is True
    assert sheet["card"] == {"thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
                                         "presence_penalty": 1.5},
                             "non_thinking": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
                                              "presence_penalty": 1.5}}
    assert _sampling(_ollama_request(ollama)["options"]) == sheet["card"]["thinking"]  # what was sent

    view = _save_sampling(REF, "custom", {**OWN, "min_p": None})
    assert view["sampling"]["value"] == "custom"
    assert _sampling(_ollama_request(ollama)["options"]) == {key: OWN[key] for key in OWN if key != "min_p"}

    _save_sampling(REF, "engine")
    assert _sampling(_ollama_request(ollama)["options"]) == {}  # Ollama's own sampling

    view = _save_sampling(REF, "auto")
    assert view["sampling"]["value"] == "auto" and store.local_model_settings(MODEL) == {}
    assert _sampling(_ollama_request(ollama)["options"]) == sheet["card"]["thinking"]


def test_a_servers_own_sampling_beats_the_persons_and_lm_studio_is_offered_no_min_p(store):
    from row_bot.application.client_local_model_settings import read_local_model_settings

    with fake_provider_http([ANSWER, ANSWER]) as server:
        provider = _endpoint("mine", f"{server.url}/v1", extra_body={"top_k": 64})
        _save_sampling(f"model:{provider}:qwen3.5-9b", "custom", OWN)
        body = _sent(server, provider)
        studio = _endpoint("lmstudio-1234", f"{server.url}/v1", profile="lmstudio")
        studio_ref = f"model:{studio}:qwen3.5-9b"
        sheet = read_local_model_settings(studio_ref)["sampling"]

    assert _sampling(body) == {**OWN, "top_k": 64}  # the endpoint's extra_body still wins
    assert "min_p" not in sheet["fields"] and "min_p" not in sheet["card"]["thinking"]
    with pytest.raises(ValueError, match="invalid_command"):
        _save_sampling(studio_ref, "custom", {"min_p": 0.1})  # LM Studio isn't sent it
    with pytest.raises(ValueError, match="invalid_command"):
        _save_sampling(studio_ref, "custom", {})  # Custom names a value
    with pytest.raises(ValueError, match="invalid_command"):
        _save_sampling(studio_ref, "custom", {"temperature": 3.0})
    assert store.person_local_model_settings(studio, "qwen3.5-9b") == {}


def test_a_family_without_a_card_gets_nothing_until_the_person_sets_values(store):
    from row_bot.application.client_local_model_settings import read_local_model_settings

    with fake_provider_http([ANSWER, ANSWER]) as server:
        provider = _endpoint("mine", f"{server.url}/v1", model="gemma-4")
        ref = f"model:{provider}:gemma-4"
        assert read_local_model_settings(ref)["sampling"]["card"] is None
        assert _sampling(_sent(server, provider, "gemma-4")) == {}

        _save_sampling(ref, "custom", {"temperature": 1.0, "top_k": 64})
        body = _sent(server, provider, "gemma-4")

    assert _sampling(body) == {"temperature": 1.0, "top_k": 64}


# ── The Cloud reading limit ──────────────────────────────────────────────────

def test_the_cloud_reading_limit_trims_cloud_models_only(store, monkeypatch):
    local = _endpoint("lmstudio-1234", "http://127.0.0.1:1234/v1", profile="lmstudio", model="gemma-4")
    remote = _endpoint("gateway", "http://127.0.0.1:4000/v1", location="remote", model="gemma-4")
    monkeypatch.setattr(store, "get_model_max_context", lambda name=None, **kwargs: 131_072)

    store.set_cloud_context_size(32_768)

    assert store.get_context_policy(f"model:{remote}:gemma-4", allow_probe=False).effective_limit_tokens == 32_768
    assert store.get_context_policy(f"model:{local}:gemma-4", allow_probe=False).effective_limit_tokens == 131_072
    assert store.get_context_policy(REF, allow_probe=False).effective_limit_tokens == 65_536
