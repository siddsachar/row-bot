import threading
import time

from row_bot.providers.model_catalog_cache import CatalogCacheSnapshot


def test_model_catalog_cache_round_trip(tmp_path, monkeypatch):
    import row_bot.providers.model_catalog_cache as cache

    path = tmp_path / "model_catalog_cache.json"
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", path)
    snapshot = CatalogCacheSnapshot(
        version=cache.CACHE_VERSION,
        generated_at=123.0,
        cloud_cache={"gpt-test": {"provider": "openai", "label": "GPT Test"}},
        ollama_rows=[{"model_id": "qwen3:14b", "installed": True}],
        provider_status={"openai": {"status": "ok", "count": 1}},
        warnings=("xAI rate limited",),
        reason="test",
    )

    cache.write_model_catalog_cache(snapshot)
    loaded = cache.read_model_catalog_cache()

    assert loaded.cloud_cache == snapshot.cloud_cache
    assert loaded.ollama_rows == snapshot.ollama_rows
    assert loaded.provider_status == snapshot.provider_status
    assert loaded.warnings == snapshot.warnings
    assert loaded.reason == "test"


def test_model_catalog_refresh_preserves_last_good_when_empty(tmp_path, monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.providers.config as provider_config
    import row_bot.providers.model_catalog_cache as cache

    path = tmp_path / "model_catalog_cache.json"
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", path)
    previous = CatalogCacheSnapshot(
        version=cache.CACHE_VERSION,
        generated_at=time.time() - 1000,
        cloud_cache={"gpt-test": {"provider": "openai", "label": "GPT Test"}},
        ollama_rows=[],
        provider_status={"openai": {"status": "ok", "count": 1}},
        warnings=(),
        reason="previous",
    )
    cache.write_model_catalog_cache(previous)
    monkeypatch.setattr(cache, "_refresh_cloud_cache", lambda provider_id=None: ({}, {"cloud": {"status": "ok", "count": 0}}))
    monkeypatch.setattr(cache, "_refresh_ollama_rows", lambda: [])

    refreshed = cache.refresh_model_catalog_cache(reason="test", force=True)

    assert refreshed.cloud_cache == previous.cloud_cache
    assert cache.read_model_catalog_cache().cloud_cache == previous.cloud_cache


def test_minimax_provider_refresh_does_not_preclear_on_failed_fetch(monkeypatch):
    import row_bot.models as models
    import row_bot.providers.model_catalog_cache as cache

    old_cache = dict(models._cloud_model_cache)
    monkeypatch.setattr(models, "fetch_context_catalog", lambda: 0)
    monkeypatch.setattr(models, "_fetch_cloud_models", lambda provider_id: 0)
    monkeypatch.setattr(models, "_save_cloud_cache", lambda: None)
    try:
        models._cloud_model_cache.clear()
        models._cloud_model_cache["MiniMax-M2.7"] = {
            "provider": "minimax",
            "label": "MiniMax-M2.7",
            "ctx": 204_800,
        }

        cloud_cache, status = cache._refresh_cloud_cache(provider_id="minimax")

        assert "MiniMax-M2.7" in cloud_cache
        assert cloud_cache["MiniMax-M2.7"]["provider"] == "minimax"
        assert status == {
            "minimax": {
                "status": "cached",
                "count": 1,
                "live_count": 0,
                "preserved_count": 1,
                "source": "last_known_good",
                "message": "No live catalog update; kept 1 cached model(s).",
            }
        }
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


def test_background_model_catalog_refresh_coalesces(monkeypatch):
    import row_bot.providers.model_catalog_cache as cache

    started = threading.Event()
    release = threading.Event()

    def _slow_refresh(**kwargs):
        started.set()
        release.wait(timeout=5)
        return cache.empty_catalog_cache()

    monkeypatch.setattr(cache, "refresh_model_catalog_cache", _slow_refresh)
    assert cache.start_model_catalog_refresh_background(reason="test", force=True) is True
    assert started.wait(timeout=2)
    assert cache.start_model_catalog_refresh_background(reason="second", force=True) is False
    release.set()
    deadline = time.time() + 5
    while cache.is_model_catalog_refresh_running() and time.time() < deadline:
        time.sleep(0.05)
    assert cache.is_model_catalog_refresh_running() is False


def test_codex_provider_refresh_discovers_live_models(tmp_path, monkeypatch):
    import row_bot.models as models
    import row_bot.providers.codex as codex
    import row_bot.providers.config as provider_config
    import row_bot.providers.model_catalog_cache as cache

    old_cache = dict(models._cloud_model_cache)
    live_infos = [
        codex._codex_model_info_from_live_item({
            "slug": model_id,
            "display_name": model_id,
            "input_modalities": ["text", "image"],
            "supported_reasoning_efforts": ["medium"],
        })
        for model_id in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
    ]
    assert all(live_infos)
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(models, "fetch_context_catalog", lambda: 0)
    monkeypatch.setattr(models, "_save_cloud_cache", lambda: None)
    monkeypatch.setattr(codex, "list_codex_model_infos", lambda force_refresh=False: list(live_infos))
    try:
        models._cloud_model_cache.clear()

        cloud_cache, status = cache._refresh_cloud_cache(provider_id="codex")

        assert {
            key for key, info in cloud_cache.items()
            if info.get("provider") == "codex"
        } == {
            "model:codex:gpt-5.6-sol",
            "model:codex:gpt-5.6-terra",
            "model:codex:gpt-5.6-luna",
        }
        assert status["codex"]["status"] == "live"
        assert status["codex"]["count"] == 3
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


def test_global_refresh_uses_the_registered_provider_order(monkeypatch):
    import row_bot.models as models

    old_cache = dict(models._cloud_model_cache)
    calls: list[str] = []
    monkeypatch.setattr(models, "fetch_context_catalog", lambda: 0)
    monkeypatch.setattr(models, "_save_cloud_cache", lambda: None)
    monkeypatch.setattr(models, "_fetch_cloud_models", lambda provider_id: calls.append(provider_id) or 0)
    monkeypatch.setattr(models, "_cloud_model_available_after_refresh", lambda _model: True)
    try:
        models.refresh_cloud_models_detailed()
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)

    assert calls == list(models.REFRESHABLE_CLOUD_PROVIDER_IDS)
    assert "codex" in calls


def test_failed_openai_refresh_preserves_last_known_good_rows(monkeypatch):
    import httpx

    import row_bot.models as models
    import row_bot.providers.auth_store as auth_store

    old_cache = dict(models._cloud_model_cache)
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider_id: "test-key" if provider_id == "openai" else "")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    try:
        models._cloud_model_cache.clear()
        models._cloud_model_cache["gpt-existing"] = {
            "provider": "openai",
            "label": "GPT Existing",
        }

        result = models.refresh_cloud_provider_models("openai")

        assert result.status == "cached"
        assert result.effective_count == 1
        assert models._cloud_model_cache["gpt-existing"]["provider"] == "openai"
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


def test_successful_openai_refresh_replaces_only_openai_rows(monkeypatch):
    import httpx

    import row_bot.models as models
    import row_bot.providers.auth_store as auth_store

    old_cache = dict(models._cloud_model_cache)
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider_id: "test-key" if provider_id == "openai" else "")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: _PagedCatalogResponse({"data": [{"id": "gpt-new"}]}),
    )
    try:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update({
            "gpt-retired": {"provider": "openai", "label": "GPT Retired"},
            "claude-existing": {"provider": "anthropic", "label": "Claude Existing"},
        })

        result = models.refresh_cloud_provider_models("openai")

        assert result.status == "live"
        assert result.effective_count == 1
        assert "gpt-new" in models._cloud_model_cache
        assert "gpt-retired" not in models._cloud_model_cache
        assert "claude-existing" in models._cloud_model_cache
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


class _PagedCatalogResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_anthropic_second_page_failure_does_not_commit_partial_catalog(monkeypatch):
    import httpx

    import row_bot.models as models

    old_cache = dict(models._cloud_model_cache)
    responses = iter([
        _PagedCatalogResponse({
            "data": [{"id": "claude-new"}],
            "has_more": True,
            "last_id": "claude-new",
        }),
        RuntimeError("page two failed"),
    ])

    def _get(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", _get)
    try:
        models._cloud_model_cache.clear()
        models._cloud_model_cache["claude-existing"] = {
            "provider": "anthropic",
            "label": "Claude Existing",
        }

        assert models._fetch_anthropic_models("test-key") == 0
        assert "claude-existing" in models._cloud_model_cache
        assert "claude-new" not in models._cloud_model_cache
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


def test_google_second_page_failure_does_not_commit_partial_catalog(monkeypatch):
    import httpx

    import row_bot.models as models

    old_cache = dict(models._cloud_model_cache)
    responses = iter([
        _PagedCatalogResponse({
            "models": [{"name": "models/gemini-new", "supportedGenerationMethods": ["generateContent"]}],
            "nextPageToken": "next-page",
        }),
        RuntimeError("page two failed"),
    ])

    def _get(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "get", _get)
    try:
        models._cloud_model_cache.clear()
        models._cloud_model_cache["gemini-existing"] = {
            "provider": "google",
            "label": "Gemini Existing",
        }

        assert models._fetch_google_models("test-key") == 0
        assert "gemini-existing" in models._cloud_model_cache
        assert "gemini-new" not in models._cloud_model_cache
    finally:
        models._cloud_model_cache.clear()
        models._cloud_model_cache.update(old_cache)


def test_a_refresh_for_claude_alone_still_lists_ollama_when_none_is_saved(tmp_path, monkeypatch):
    """Claude's plan-ready refresh (the first on a computer where Claude Code is signed in) wrote a
    fresh cache without Ollama, so the startup refresh after it skipped and Ollama listed no model.
    Both providers are faked: no Ollama and no Claude Code is reached."""
    import row_bot.providers.model_catalog_cache as cache

    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    claude = {"model:claude_subscription:claude-sonnet-5": {"provider": "claude_subscription"}}
    ollama = [{"model_id": "qwen3:14b", "installed": True}]
    asked: list[str] = []

    def ollama_rows():
        asked.append("ollama")
        return [dict(row) for row in ollama]

    monkeypatch.setattr(cache, "_refresh_cloud_cache",
                        lambda provider_id=None: (dict(claude), {provider_id: {"status": "live", "count": 1}}))
    monkeypatch.setattr(cache, "_refresh_ollama_rows", ollama_rows)

    cache.refresh_model_catalog_cache(reason="claude_plan_ready", force=True, provider_id="claude_subscription")

    assert cache.read_model_catalog_cache().ollama_rows == ollama
    assert asked == ["ollama"]
    # Ollama's rows saved, another provider's refresh leaves them to Ollama's own and the full ones.
    cache.refresh_model_catalog_cache(reason="claude_plan_ready", force=True, provider_id="claude_subscription")
    assert asked == ["ollama"]
    assert cache.read_model_catalog_cache().ollama_rows == ollama


def test_connecting_one_provider_does_not_stop_the_full_refresh_of_the_others(tmp_path, monkeypatch):
    """On 9 Nov each newly connected provider refreshed its own list and stamped the whole catalog fresh, so
    the startup and 6-hourly refreshes skipped all day: Requesty's list stayed two days old, without the
    image models a newer Row-Bot lists. Providers are faked."""
    import row_bot.providers.model_catalog_cache as cache

    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    monkeypatch.setattr(cache, "_refresh_ollama_rows", lambda: [])
    refreshed: list[str] = []

    def cloud(provider_id=None):
        refreshed.append(provider_id or "all")
        return {f"model:{provider_id or 'requesty'}:m": {"provider": provider_id or "requesty"}}, {
            provider_id or "requesty": {"status": "live", "count": 1}}

    monkeypatch.setattr(cache, "_refresh_cloud_cache", cloud)
    cache.refresh_model_catalog_cache(reason="startup", force=True)
    monkeypatch.setattr(cache.time, "time", lambda: cache.read_model_catalog_cache().generated_at
                        + cache.CATALOG_CACHE_TTL_SECONDS + 1)

    cache.refresh_model_catalog_cache(reason="provider_connection", force=True, provider_id="cerebras")
    # The other providers' statuses stay beside the one just refreshed.
    assert {"requesty", "cerebras"} <= set(cache.read_model_catalog_cache().provider_status)
    cache.refresh_model_catalog_cache(reason="scheduled", force=False)

    assert refreshed == ["all", "cerebras", "all"]

def test_the_scheduled_refresh_reads_an_old_catalog_only_while_a_cloud_provider_is_connected(tmp_path, monkeypatch):
    """I23: the start-up and 6-hourly refresh. A local-only install contacts nobody; with a provider
    connected a catalog older than six hours is read again and a fresh one is left alone. Providers are faked."""
    import row_bot.providers.model_catalog_cache as cache
    import row_bot.providers.runtime as runtime

    path = tmp_path / "model_catalog_cache.json"
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", path)
    monkeypatch.setattr(cache, "_refresh_ollama_rows", lambda: [])
    monkeypatch.setattr(cache, "_publish_live", lambda topic: None)
    old = time.time() - cache.CATALOG_CACHE_TTL_SECONDS - 60
    cache.write_model_catalog_cache(CatalogCacheSnapshot(
        version=cache.CACHE_VERSION, generated_at=old, cloud_cache={"model:requesty:m": {"provider": "requesty"}},
        ollama_rows=[], provider_status={}, warnings=(), reason="startup"))
    refreshed: list[str] = []

    def cloud(provider_id=None):
        refreshed.append(provider_id or "all")
        return {"model:requesty:m": {"provider": "requesty"}}, {"requesty": {"status": "live", "count": 1}}

    monkeypatch.setattr(cache, "_refresh_cloud_cache", cloud)

    def scheduled(reason: str) -> bool:
        started = cache.refresh_stale_catalog_if_connected(reason)
        for thread in threading.enumerate():
            if thread.name == "model-catalog-refresh":
                thread.join(timeout=5)
        return started

    monkeypatch.setattr(runtime, "list_configured_provider_ids", lambda: [])
    assert scheduled("startup") is False
    assert refreshed == []
    assert cache.read_model_catalog_cache().generated_at == old

    monkeypatch.setattr(runtime, "list_configured_provider_ids", lambda: ["requesty"])
    assert scheduled("startup") is True
    assert refreshed == ["all"]
    assert cache.read_model_catalog_cache().generated_at > old
    scheduled("scheduled")
    assert refreshed == ["all"]