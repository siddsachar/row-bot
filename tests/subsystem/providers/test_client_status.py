from __future__ import annotations

from dataclasses import asdict, replace
import json

import pytest

from row_bot.providers import client_status as status
from row_bot.providers import config, model_catalog, model_catalog_cache as cache


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")

    def write(*, cloud=None, local=None, providers=None, generated=1000, settings=None):
        cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
            1, generated, cloud or {}, local or [], providers or {}, (), "test",
        ))
        config.save_provider_config(settings or {})

    return write


def _model(provider="openai", **kwargs):
    return {"provider": provider, "label": "Example", **kwargs}


def _provider(snapshot, identity):
    return next(row for row in snapshot.providers if row.provider_id == identity)


@pytest.mark.parametrize("condition", ["missing", "corrupt", "unsupported"])
def test_unavailable_catalog_never_bootstraps_runtime(saved, monkeypatch, condition):
    if condition == "corrupt":
        cache.CATALOG_CACHE_PATH.write_text("invalid json")
    elif condition == "unsupported":
        cache.CATALOG_CACHE_PATH.write_text(json.dumps({"version": 999}))

    def forbidden():
        pytest.fail("passive read bootstrapped runtime")

    monkeypatch.setattr(cache, "_bootstrap_snapshot_from_runtime", forbidden)
    before = cache.CATALOG_CACHE_PATH.read_bytes() if cache.CATALOG_CACHE_PATH.exists() else None
    result = status.read_provider_snapshot(now=1001)
    assert result.freshness == "unavailable"
    assert result.generated_at is None
    assert result.total_models == 0
    assert all(row.model_count is None for row in result.providers)
    assert (cache.CATALOG_CACHE_PATH.read_bytes() if cache.CATALOG_CACHE_PATH.exists() else None) == before


def test_legacy_cache_default_still_bootstraps(saved, monkeypatch):
    expected = cache.empty_catalog_cache()
    monkeypatch.setattr(cache, "_bootstrap_snapshot_from_runtime", lambda: expected)
    assert cache.read_model_catalog_cache() is expected


@pytest.mark.parametrize("age,expected", [(0, "fresh"), (21599, "fresh"), (21600, "stale"), (999999, "stale")])
def test_saved_freshness_and_stable_revision(saved, age, expected):
    saved(cloud={"same": _model()})
    first = status.read_provider_snapshot(now=1000)
    later = status.read_provider_snapshot(now=1000 + age)
    assert later.freshness == expected
    assert first.revision == later.revision
    assert later.total_models == 1
    assert _provider(later, "openai").model_count == 1
    assert _provider(later, "openai").runtime_state == "unknown"


def test_known_empty_error_and_last_good_are_distinct(saved):
    saved(cloud={"same": _model()}, providers={
        "openai": {"status": "error", "message": "SENSITIVE"},
        "openrouter": {"status": "ok", "count": 0},
        "codex": {"status": "error", "count": 0},
        "google": {"status": "ok", "count": "0"},
    })
    result = status.read_provider_snapshot(now=1000)
    assert _provider(result, "openai").catalog_state == "cached"
    assert _provider(result, "openrouter").catalog_state == "verified_empty"
    assert _provider(result, "openrouter").model_count == 0
    assert _provider(result, "codex").catalog_state == "error"
    assert _provider(result, "codex").model_count is None
    assert _provider(result, "google").model_count is None


def test_provider_qualified_collisions_custom_disabled_and_subscription_cache(saved):
    saved(cloud={"model:openai:same": _model(), "model:anthropic:same": _model("anthropic")}, settings={
        "custom_endpoints": [{"id": "private", "name": "Private", "enabled": False, "models": [{"id": "same"}]}],
        "providers": {"codex": {"catalog_cache": {"models": [{"id": "same", "display_name": "Saved Codex"}]}}},
    })
    result = status.list_cached_models(now=1000)
    assert result.total == 4
    assert len({row.selection_ref for row in result.items}) == 4
    assert {row.provider_id for row in result.items} == {"openai", "anthropic", "custom_openai_private", "codex"}
    provider = _provider(status.read_provider_snapshot(now=1000), "custom_openai_private")
    assert provider.enabled is False
    assert provider.group == "custom"
    assert provider.model_count == 1
    assert provider.display_name == "Private"


def test_capabilities_unknowns_and_local_install_state(saved):
    saved(cloud={"unknown": _model(), "known": _model(ctx=32000, capabilities_snapshot={
        "tasks": ["chat"], "input_modalities": ["text", "image", "SENSITIVE"], "output_modalities": ["text"],
        "tool_calling": True, "reasoning": {"supported_efforts": ["low", "high", "SENSITIVE"], "default_effort": "high",
            "can_disable": True, "budget_max": 500, "thinking_mode": "manual", "fingerprint": "SENSITIVE"},
    })}, local=[{"model_id": "installed", "installed": True}, {"model_id": "missing", "installed": False}, {"model_id": "unknown"}])
    rows = {row.selection_ref: row for row in status.list_cached_models(now=1000).items}
    unknown = rows["model:openai:unknown"]
    assert unknown.context_window is None
    assert unknown.tool_calling is None
    assert unknown.installed is None
    assert unknown.categories == ()
    assert unknown.reasoning is None
    known = rows["model:openai:known"]
    assert known.context_window == 32000
    assert known.categories == ("chat", "vision")
    assert known.input_modalities == ("text", "image")
    assert known.reasoning.supported_efforts == ("low", "high")
    assert known.reasoning.default_effort == "high"
    assert rows["model:ollama:installed"].installed is True
    assert rows["model:ollama:missing"].installed is False
    assert rows["model:ollama:unknown"].installed is None


def test_pins_are_saved_metadata_without_pruning_or_defaults(saved):
    saved(cloud={"test": _model()}, settings={"quick_choices": [
        {"id": "model:openai:test", "visibility": ["chat", "image"]},
        {"id": "model:custom_openai_deleted:ghost", "visibility": ["chat"]},
    ]})
    before = config.CONFIG_PATH.read_bytes()
    assert status.list_cached_models(now=1000).items[0].pinned_surfaces == ("chat", "image")
    assert config.CONFIG_PATH.read_bytes() == before


def test_search_before_paging_counts_and_revision_invalidation(saved):
    saved(cloud={f"model-{i:03}": _model(label=f"Name {i:03}") for i in range(205)})
    first = status.list_cached_models(limit=100, now=1000)
    second = status.list_cached_models(limit=100, cursor=first.next_cursor, now=99999)
    third = status.list_cached_models(limit=100, cursor=second.next_cursor, now=1000)
    assert [len(page.items) for page in (first, second, third)] == [100, 100, 5]
    assert all(page.total == 205 for page in (first, second, third))
    assert third.next_cursor is None
    assert len({row.selection_ref for page in (first, second, third) for row in page.items}) == 205
    assert status.list_cached_models(query="204", limit=1).items[0].model_id == "model-204"
    with pytest.raises(status.ProviderStatusError, match="cursor_expired"):
        status.list_cached_models(query="name", cursor=first.next_cursor)
    saved(cloud={"changed": _model()})
    with pytest.raises(status.ProviderStatusError, match="cursor_expired"):
        status.list_cached_models(cursor=first.next_cursor)


def test_models_scoped_readiness_and_category_cursor_leave_passive_status_alone(saved, monkeypatch):
    saved(cloud={f"model-{i:03}": _model(label=f"Name {i:03}", capabilities_snapshot={
        "tasks": ["chat"], "output_modalities": ["text"], "tool_calling": True,
    }) for i in range(90)})
    calls = []

    def project(rows):
        calls.append("local-readiness")
        return [replace(row, configured=True, runtime_ready=True, installed=True,
                        runtime_mode="agent") for row in rows]

    monkeypatch.setattr(status, "project_saved_catalog_readiness", project)
    passive = status.list_cached_models(limit=1, now=1000)
    assert calls == []
    assert passive.items[0].runtime_state == "unknown"
    first = status.list_cached_models(surface="chat", readiness=True, limit=80, now=1000)
    second = status.list_cached_models(surface="chat", readiness=True, cursor=first.next_cursor,
                                       limit=80, now=1000)
    assert [len(first.items), len(second.items)] == [80, 10]
    assert first.items[0].runtime_state == "ready"
    assert first.items[0].selection_ref.startswith("model:openai:")
    assert len(calls) == 2
    with pytest.raises(status.ProviderStatusError, match="cursor_expired"):
        status.list_cached_models(surface="vision", readiness=True, cursor=first.next_cursor)


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"query": "x" * 257}, {"query": None}, {"provider_id": ""}])
def test_invalid_query_rejected_before_reads(saved, monkeypatch, kwargs):
    monkeypatch.setattr(status, "_read", lambda _: pytest.fail("invalid request read files"))
    with pytest.raises(status.ProviderStatusError, match="invalid_catalog_query"):
        status.list_cached_models(**kwargs)


@pytest.mark.parametrize("cursor", ["invalid", "[]", "WzEsMiwzLDRd", "x" * 2049, 123])
def test_bad_cursor_rejected(saved, cursor):
    with pytest.raises(status.ProviderStatusError, match="cursor_expired"):
        status.list_cached_models(cursor=cursor)


def test_public_fields_exclude_sensitive_records_and_keep_revision_public(saved):
    sensitive = {"api_key": "SENSITIVE", "fingerprint": "SENSITIVE", "path": "SENSITIVE", "last_error": "SENSITIVE"}
    saved(cloud={"test": _model(**sensitive, capabilities_snapshot={**sensitive})}, providers={"openai": {**sensitive}}, settings={
        "providers": {"openai": {**sensitive}}, "custom_endpoints": [{"id": "private", "name": "Private", "headers": sensitive, "base_url": "SENSITIVE", "last_probe": sensitive}],
    })
    first = status.read_provider_snapshot(now=1000)
    assert "SENSITIVE" not in json.dumps(asdict(first))
    assert "SENSITIVE" not in json.dumps(asdict(status.list_cached_models(now=1000)))
    saved(cloud={"test": _model(api_key="CHANGED")}, settings={"custom_endpoints": [{"id": "private", "name": "Private"}]})
    assert status.read_provider_snapshot(now=1000).revision == first.revision


def test_passive_reads_call_no_runtime_secret_discovery_or_mutation_owner(saved, monkeypatch):
    import row_bot.providers.auth_store as auth
    import row_bot.providers.runtime as runtime
    import row_bot.providers.selection as selection
    import row_bot.providers.custom as custom

    saved(cloud={"test": _model()}, settings={"custom_endpoints": [{"id": "private", "models": [{"id": "test"}]}]})
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("passive status must not invoke side effect owners")

    for module, names in [
        (model_catalog, ["_provider_status_by_id", "_safe_cloud_cache", "_safe_quick_choices", "_custom_model_infos", "_codex_model_infos", "_claude_subscription_model_infos", "_xai_oauth_model_infos", "_curated_media_entries", "_probe_ollama_show_metadata", "_catalog_runtime_summary"]),
        (cache, ["_bootstrap_snapshot_from_runtime", "write_model_catalog_cache", "refresh_model_catalog_cache", "start_model_catalog_refresh_background"]),
        (config, ["save_provider_config"]), (auth, ["get_provider_secret", "provider_secret_status"]),
        (runtime, ["provider_status"]), (selection, ["list_quick_choices", "prune_stale_custom_quick_choices"]),
        (custom, ["list_custom_endpoints", "custom_probe_for_model", "refresh_custom_endpoint_models"]),
    ]:
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert status.read_provider_snapshot(now=1000).total_models == 2
    assert status.list_cached_models(now=1000).total == 2
    assert calls == []


@pytest.mark.parametrize("bad", [None, "bad", 123, [], {"model_id": []}, {"model_id": {"secret": "SENSITIVE"}}])
def test_malformed_saved_rows_preserve_healthy_siblings(saved, bad):
    saved(cloud={"healthy": _model(), "broken": bad}, local=[bad, {"model_id": "healthy"}], settings={
        "providers": {"codex": {"catalog_cache": {"models": [bad, {"id": "healthy"}]}}},
        "custom_endpoints": [bad, {"id": "safe", "models": [bad, {"id": "healthy"}]}],
    })
    result = status.list_cached_models(now=1000)
    assert {"model:openai:healthy", "model:ollama:healthy", "model:codex:healthy", "model:custom_openai_safe:healthy"}.issubset({row.selection_ref for row in result.items})
    assert "SENSITIVE" not in json.dumps(asdict(result))


def test_display_text_is_bounded_and_non_text_metadata_is_never_stringified(saved):
    saved(cloud={
        "long": _model(label="x" * 400 + "\n\x00"),
        "bad-label": _model(label={"secret": "SENSITIVE"}),
    }, settings={"custom_endpoints": [{"id": "private", "name": {"secret": "SENSITIVE"}}]})
    models = {row.model_id: row for row in status.list_cached_models().items}
    assert models["long"].display_name == "x" * 256
    assert models["bad-label"].display_name == "bad-label"
    assert "SENSITIVE" not in json.dumps(asdict(status.read_provider_snapshot()))


def test_each_request_reads_saved_owners_once(saved, monkeypatch):
    saved(cloud={"test": _model()})
    reads = []
    load = status.load_provider_config
    read = status.read_model_catalog_cache

    def config_read():
        reads.append("config")
        return load()

    def cache_read(**kwargs):
        reads.append("cache")
        return read(**kwargs)

    monkeypatch.setattr(status, "load_provider_config", config_read)
    monkeypatch.setattr(status, "read_model_catalog_cache", cache_read)
    status.list_cached_models()
    assert reads == ["cache", "config"]
    reads.clear()
    status.read_provider_snapshot()
    assert reads == ["cache", "config"]
