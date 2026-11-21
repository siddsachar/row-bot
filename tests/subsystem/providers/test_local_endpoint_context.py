"""Custom endpoints' context, told truthfully (local-models uplift L2, L5).

What a server reports wins (LM Studio's loaded context over its trained maximum, each llama.cpp
router model's own n_ctx); a server that says nothing gets the profile's stated 32K, never 128K; and a
local endpoint counts as on this computer for the prompt tiers, except Learning's personal models.
"""

from __future__ import annotations

import pytest

import row_bot.models as models
import row_bot.providers.config as provider_config
from row_bot.providers.custom import custom_provider_id, get_custom_endpoint, refresh_custom_endpoint_models, save_custom_endpoint
from row_bot.providers.readiness import evaluate_agent_readiness

pytestmark = pytest.mark.subsystem


class _Response:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        return self._payload


@pytest.fixture
def providers(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "_cloud_context_override", None)


def _serve(monkeypatch, routes: dict[str, object]) -> list[str]:
    """Fake HTTP: each path (with its query) answers its payload, any other a 404; returns the URLs asked."""
    import httpx

    asked: list[str] = []

    def _get(url, *args, **kwargs):
        asked.append(str(url))
        path = "/" + str(url).split("//", 1)[1].split("/", 1)[1]
        if path in routes:
            return _Response(routes[path])
        return _Response({"error": "not found"}, status=404)

    monkeypatch.setattr(httpx, "get", _get)
    return asked


def _endpoint(endpoint_id: str, *, location: str = "local", ctx: int | None = None, **extra) -> str:
    model = {"id": "qwen", "model_id": "qwen", "provider": custom_provider_id(endpoint_id),
             "capabilities_snapshot": {"tasks": ["chat"]}}
    if ctx:
        model["ctx"] = ctx
    save_custom_endpoint({"id": endpoint_id, "name": endpoint_id, "base_url": "http://127.0.0.1:8000/v1",
                          "execution_location": location, "auth_required": False, "models": [model], **extra})
    return f"model:{custom_provider_id(endpoint_id)}:qwen"


# ── The unknown window ─────────────────────────────────────────────────────────


def test_a_server_that_says_nothing_gets_the_profile_fallback_not_128k(providers):
    ref = _endpoint("lm-studio", profile="lmstudio")

    policy = models.get_context_policy(ref)

    assert policy.native_max is None
    assert policy.effective_limit_tokens == 32_768
    assert models.get_context_size(ref) == 32_768
    assert policy.capacity_source == "profile_default"
    assert policy.capacity_state == "ready"
    # The context setting still treats the endpoint as a provider (its window is the server's).
    assert policy.policy_kind == "provider"


def test_the_fallback_is_ready_for_agent_mode_and_says_it_is_assumed(providers, monkeypatch):
    import row_bot.providers.readiness as readiness

    monkeypatch.setattr(readiness, "provider_status", lambda provider_id: {"configured": True})
    ref = _endpoint("lab", last_probe={"ok": True, "tool_calling": True, "tool_round_trip": True})

    result = evaluate_agent_readiness(ref)

    assert result.ready is True
    assert result.context_window == 32_768
    assert any("context is inferred" in warning for warning in result.warnings)


def test_a_native_limit_and_server_metadata_win_over_the_fallback(providers):
    manual = _endpoint("manual", manual_capabilities={"context_window": 98_304})
    reported = _endpoint("reported", ctx=131_072)

    assert models.get_context_policy(manual).effective_limit_tokens == 98_304
    assert models.get_context_policy(manual).capacity_source == "manual_declaration"
    assert models.get_context_policy(reported).effective_limit_tokens == 131_072
    assert models.get_context_policy(reported).capacity_source == "server_metadata"


def test_a_reading_cap_still_trims_below_the_fallback_but_never_raises_it(providers, monkeypatch):
    ref = _endpoint("capped", location="remote")

    monkeypatch.setattr(models, "_cloud_context_override", 16_384)
    assert models.get_context_policy(ref).effective_limit_tokens == 16_384
    assert models.get_context_policy(ref).capacity_source == "advanced_override"

    monkeypatch.setattr(models, "_cloud_context_override", 262_144)
    assert models.get_context_policy(ref).effective_limit_tokens == 32_768
    assert models.get_context_policy(ref).capacity_source == "profile_default"


def test_the_cloud_reading_limit_never_trims_a_server_on_this_computer(providers, monkeypatch):
    ref = _endpoint("mine")

    monkeypatch.setattr(models, "_cloud_context_override", 16_384)

    assert models.get_context_policy(ref).effective_limit_tokens == 32_768
    assert models.get_context_policy(ref).capacity_source == "profile_default"


# ── Which endpoints are on this computer ──────────────────────────────────────


def test_local_endpoints_count_as_on_this_computer_except_learnings(providers):
    hand_added = _endpoint("my-llama", profile="llama_cpp")
    remote = _endpoint("proxy", location="remote")
    personal = _endpoint("row-bot-personal-row-bot", profile="llama_cpp", managed="learning")
    personal_lm_studio = _endpoint("row-bot-personal-lmstudio", profile="lmstudio")

    assert models.is_local_custom_endpoint(hand_added) is True
    assert models.is_local_custom_endpoint(remote) is False
    assert models.is_local_custom_endpoint(personal) is False
    assert models.is_local_custom_endpoint(personal_lm_studio) is False
    # The transport is unchanged: a custom endpoint is still reached over the OpenAI-compatible API.
    assert models.is_cloud_model(hand_added) is True


# ── LM Studio: the loaded context ─────────────────────────────────────────────


def _lm_studio(providers) -> None:
    save_custom_endpoint({"id": "lm-studio", "base_url": "http://127.0.0.1:1234/v1", "profile": "lmstudio",
                          "execution_location": "local", "auth_required": False})


def test_lm_studio_gives_the_context_the_model_was_loaded_with(providers, monkeypatch):
    _lm_studio(providers)
    _serve(monkeypatch, {
        "/v1/models": {"data": [{"id": "qwen/qwen3.5-9b"}, {"id": "idle-model"}]},
        "/api/v1/models": {"models": [
            {"key": "qwen/qwen3.5-9b", "max_context_length": 262_144,
             "loaded_instances": [{"id": "qwen/qwen3.5-9b", "config": {"context_length": 16_384}}]},
            {"key": "idle-model", "max_context_length": 131_072, "loaded_instances": []},
        ]},
    })

    infos = {info.model_id: info for info in refresh_custom_endpoint_models("lm-studio")}

    assert infos["qwen/qwen3.5-9b"].context_window == 16_384
    # A model not loaded yet: its maximum is all LM Studio says.
    assert infos["idle-model"].context_window == 131_072
    assert models.get_context_size(f"model:{custom_provider_id('lm-studio')}:qwen/qwen3.5-9b") == 16_384


def test_lm_studio_v0_loaded_context_length_wins_over_its_maximum(providers, monkeypatch):
    _lm_studio(providers)
    _serve(monkeypatch, {
        "/v1/models": {"data": [{"id": "qwen/qwen3.5-9b"}]},
        "/api/v0/models": {"data": [{"id": "qwen/qwen3.5-9b", "state": "loaded",
                                     "max_context_length": 262_144, "loaded_context_length": 40_960}]},
    })

    infos = refresh_custom_endpoint_models("lm-studio")

    assert infos[0].context_window == 40_960


# ── llama.cpp router mode: each model's own n_ctx ─────────────────────────────


def test_llamacpp_router_reads_each_models_own_props_without_loading_it(providers, monkeypatch):
    save_custom_endpoint({"id": "router", "base_url": "http://127.0.0.1:8000/v1", "profile": "llama_cpp",
                          "execution_location": "local", "auth_required": False})
    asked = _serve(monkeypatch, {
        "/v1/models": {"data": [{"id": "qwen3.5-9b"}, {"id": "qwen3.8-27b"}, {"id": "not-loaded"}]},
        # The router's own /props names no model.
        "/props": {"role": "router"},
        "/props?model=qwen3.5-9b&autoload=false": {
            "model_alias": "qwen3.5-9b", "default_generation_settings": {"n_ctx": 65_536}},
        "/props?model=qwen3.8-27b&autoload=false": {
            "model_alias": "qwen3.8-27b", "default_generation_settings": {"n_ctx": 131_072}},
    })

    infos = {info.model_id: info for info in refresh_custom_endpoint_models("router")}

    assert infos["qwen3.5-9b"].context_window == 65_536
    assert infos["qwen3.8-27b"].context_window == 131_072
    assert infos["not-loaded"].context_window == 0
    assert "http://127.0.0.1:8000/props?model=not-loaded&autoload=false" in asked
    saved = {item["model_id"]: item for item in get_custom_endpoint("router")["models"]}
    assert saved["qwen3.8-27b"]["context_window"] == 131_072
    # The model it couldn't ask gets the stated fallback.
    assert models.get_context_size(f"model:{custom_provider_id('router')}:not-loaded") == 32_768


def test_llamacpp_single_model_server_is_read_from_plain_props(providers, monkeypatch):
    save_custom_endpoint({"id": "single", "base_url": "http://127.0.0.1:8000/v1", "profile": "llama_cpp",
                          "execution_location": "local", "auth_required": False})
    asked = _serve(monkeypatch, {
        "/v1/models": {"data": [{"id": "qwen3.5-9b"}]},
        "/props": {"model_alias": "qwen3.5-9b", "default_generation_settings": {"n_ctx": 49_152}},
    })

    infos = refresh_custom_endpoint_models("single")

    assert infos[0].context_window == 49_152
    assert not any("?model=" in url for url in asked)


# ── A manual window always wins (Learning saves each endpoint at its serve context) ──


_LM_STUDIO_LOADED = {
    "v1": {"/api/v1/models": {"models": [{
        "key": "qwen/qwen3.5-9b", "max_context_length": 262_144,
        "loaded_instances": [{"id": "qwen/qwen3.5-9b", "config": {"context_length": 16_384}}]}]}},
    "v0": {"/api/v0/models": {"data": [{
        "id": "qwen/qwen3.5-9b", "state": "loaded", "max_context_length": 262_144,
        "loaded_context_length": 16_384}]}},
}


@pytest.mark.parametrize("api", sorted(_LM_STUDIO_LOADED))
def test_a_manual_window_beats_lm_studios_loaded_context(providers, monkeypatch, api):
    save_custom_endpoint({"id": "lm-studio", "base_url": "http://127.0.0.1:1234/v1", "profile": "lmstudio",
                          "execution_location": "local", "auth_required": False,
                          "manual_capabilities": {"context_window": 65_536}})
    _serve(monkeypatch, {"/v1/models": {"data": [{"id": "qwen/qwen3.5-9b"}]}, **_LM_STUDIO_LOADED[api]})

    infos = refresh_custom_endpoint_models("lm-studio")

    assert infos[0].context_window == 65_536
    assert get_custom_endpoint("lm-studio")["models"][0]["context_window"] == 65_536
    assert models.get_context_size(f"model:{custom_provider_id('lm-studio')}:qwen/qwen3.5-9b") == 65_536


def test_a_manual_window_beats_a_llamacpp_router_models_own_n_ctx(providers, monkeypatch):
    save_custom_endpoint({"id": "router", "base_url": "http://127.0.0.1:8000/v1", "profile": "llama_cpp",
                          "execution_location": "local", "auth_required": False,
                          "manual_capabilities": {"context_window": 32_768}})
    _serve(monkeypatch, {
        "/v1/models": {"data": [{"id": "qwen3.5-9b"}]},
        "/props": {"role": "router"},
        "/props?model=qwen3.5-9b&autoload=false": {
            "model_alias": "qwen3.5-9b", "context_length": 131_072,
            "default_generation_settings": {"n_ctx": 131_072}},
    })

    infos = refresh_custom_endpoint_models("router")

    assert infos[0].context_window == 32_768
    assert get_custom_endpoint("router")["models"][0]["context_window"] == 32_768
    assert models.get_context_size(f"model:{custom_provider_id('router')}:qwen3.5-9b") == 32_768
