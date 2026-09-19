"""Saved provider/model reads through the authenticated public protocol."""
from __future__ import annotations

import json

import pytest

from row_bot.providers import client_status as status, config, model_catalog_cache as cache
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app
from tests.subsystem.providers.test_client_status import saved  # noqa: F401 - shared isolated fixture

pytestmark = pytest.mark.subsystem
ENDPOINTS = ("/api/v1/settings/providers", "/api/v1/settings/models")


@pytest.fixture
def api_saved(saved, monkeypatch):  # noqa: F811 - shared isolated fixture
    from row_bot.providers import runtime

    # Existing tool registration checks provider availability during app import.
    # Keep that separate startup owner fake while exercising real saved GETs.
    monkeypatch.setattr(runtime, "provider_status", lambda *args, **kwargs: {"configured": False})
    clock = [1001.0]
    read = status._read
    monkeypatch.setattr(status, "_read", lambda _, **kwargs: read(clock[0], **kwargs))
    return saved, clock


def _read(client, headers, endpoint, **params):
    response = client.get(endpoint, headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_saved_reads_require_session_origin_and_current_authentication(api_saved, endpoint, monkeypatch):
    write, _ = api_saved
    write(cloud={"fixture": {"provider": "openai", "label": "Saved"}})
    client, _, active = client_app(remote=True)
    with client:
        assert client.get(endpoint).status_code == 401
        _, headers = bootstrap(client)
        assert client.get(endpoint, headers={"X-Client-Session": headers["X-Client-Session"]}).status_code == 403
        assert client.get(endpoint, headers={**headers, "Origin": "http://foreign.invalid"}).status_code == 403
        _read(client, headers, endpoint)
        active["value"] = False
        monkeypatch.setattr(status, "_read", lambda _: pytest.fail("revoked request reached saved owner"))
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"
        assert "Saved" not in response.text


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_auth_revoked_during_saved_read_never_delivers_snapshot(api_saved, endpoint, monkeypatch):
    write, _ = api_saved
    write(cloud={"fixture": {"provider": "openai", "label": "PRIVATE_SAVED_LABEL"}})
    client, _, active = client_app(remote=True)
    read = status.load_provider_config

    def revoke_during_store_read():
        result = read()
        active["value"] = False
        return result

    with client:
        _, headers = bootstrap(client)
        monkeypatch.setattr(status, "load_provider_config", revoke_during_store_read)
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 401
        assert "PRIVATE_SAVED_LABEL" not in response.text
        assert "items" not in response.json() and "providers" not in response.json()


@pytest.mark.parametrize("condition", ["missing", "corrupt", "unsupported"])
def test_unavailable_saved_catalog_remains_recoverable_without_bootstrap(api_saved, condition, monkeypatch):
    if condition == "corrupt":
        cache.CATALOG_CACHE_PATH.write_text("PRIVATE_INVALID_CACHE")
    elif condition == "unsupported":
        cache.CATALOG_CACHE_PATH.write_text(json.dumps({"version": 999}))
    monkeypatch.setattr(cache, "_bootstrap_snapshot_from_runtime", lambda: pytest.fail("implicit runtime bootstrap"))
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        for endpoint in ENDPOINTS:
            body = _read(client, headers, endpoint)
            assert body["schema_version"] == 1
            assert body["freshness"] == "unavailable" and body["generated_at"] is None
            assert "PRIVATE_INVALID_CACHE" not in json.dumps(body)
        assert _read(client, headers, ENDPOINTS[1])["items"] == []


def test_fresh_stale_empty_unknown_and_last_good_contracts(api_saved):
    write, clock = api_saved
    write(cloud={"fixture": {"provider": "openai"}}, providers={
        "openai": {"status": "error", "message": "PRIVATE_FAILURE"},
        "openrouter": {"status": "ok", "count": 0},
        "codex": {"status": "error", "count": 0},
    })
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        fresh = _read(client, headers, ENDPOINTS[0])
        assert fresh["freshness"] == "fresh" and fresh["generated_at"] == 1000
        rows = {row["provider_id"]: row for row in fresh["providers"]}
        assert rows["openai"]["catalog_state"] == "cached" and rows["openai"]["model_count"] == 1
        assert rows["openrouter"]["catalog_state"] == "verified_empty" and rows["openrouter"]["model_count"] == 0
        assert rows["codex"]["catalog_state"] == "error" and rows["codex"]["model_count"] is None
        assert rows["google"]["catalog_state"] == "unavailable" and rows["google"]["model_count"] is None
        assert all(row["runtime_state"] == "unknown" for row in rows.values())
        clock[0] = 99999
        stale = _read(client, headers, ENDPOINTS[0])
        assert stale["freshness"] == "stale" and stale["revision"] == fresh["revision"]
        models = _read(client, headers, ENDPOINTS[1])
        assert models["freshness"] == "stale" and models["revision"] == stale["revision"]
        assert models["items"][0]["context_window"] is None
        assert models["items"][0]["installed"] is None
        assert models["items"][0]["tool_calling"] is None


def test_config_only_custom_and_subscription_models_survive_missing_central_cache(api_saved):
    config.save_provider_config({"custom_endpoints": [{"id": "private", "enabled": False, "models": [{"id": "same"}]}],
        "providers": {"codex": {"catalog_cache": {"models": [{"id": "same"}]}}}})
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        models = _read(client, headers, ENDPOINTS[1])
        assert models["total"] == 2 and models["freshness"] == "unavailable"
        assert {row["selection_ref"] for row in models["items"]} == {"model:codex:same", "model:custom_openai_private:same"}
        providers = _read(client, headers, ENDPOINTS[0])
        endpoint = next(row for row in providers["providers"] if row["provider_id"] == "custom_openai_private")
        assert endpoint["enabled"] is False and endpoint["model_count"] == 1


def test_bounded_pagination_whole_catalog_search_and_cursor_recovery(api_saved):
    write, _ = api_saved
    write(cloud={**{f"model:openai:model-{i:03}": {"provider": "openai", "label": "Same label"} for i in range(205)},
                 "model:anthropic:model-204": {"provider": "anthropic", "label": "Same label"}})
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        page = _read(client, headers, ENDPOINTS[1], limit=100)
        assert page["total"] == 206 and len(page["items"]) == 100
        cursor = page["next_cursor"]
        seen = {row["selection_ref"] for row in page["items"]}
        while page["next_cursor"]:
            page = _read(client, headers, ENDPOINTS[1], limit=100, cursor=page["next_cursor"])
            seen.update(row["selection_ref"] for row in page["items"])
        assert len(seen) == 206 and len(page["items"]) == 6
        searched = _read(client, headers, ENDPOINTS[1], provider_id="openai", query="model-204", limit=1)
        assert searched["total"] == 1 and searched["items"][0]["selection_ref"] == "model:openai:model-204"
        mismatch = client.get(ENDPOINTS[1], headers=headers, params={"cursor": cursor, "provider_id": "openai"})
        assert mismatch.status_code == 410 and mismatch.json()["code"] == "cursor_expired"
        write(cloud={"changed": {"provider": "openai"}})
        expired = client.get(ENDPOINTS[1], headers=headers, params={"cursor": cursor})
        assert expired.status_code == 410 and expired.json()["code"] == "cursor_expired"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"query": "x" * 257}, {"provider_id": "x" * 161}, {"provider_id": ""}, {"cursor": "x" * 2049}])
def test_invalid_query_uses_safe_typed_error(api_saved, params):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINTS[1], headers=headers, params=params)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "invalid_catalog_query"


def test_bad_cursor_uses_safe_typed_error(api_saved):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINTS[1], headers=headers, params={"cursor": "PRIVATE_INVALID_CURSOR"})
        assert response.status_code == 410 and response.json()["code"] == "cursor_expired"
        assert "PRIVATE_INVALID_CURSOR" not in response.text


@pytest.mark.parametrize("provider,identity", [("openai", "model-" + "x" * 300), ("p" * 128, "m" * 512)])
def test_domain_supported_long_model_identity_round_trips(api_saved, provider, identity):
    write, _ = api_saved
    write(cloud={identity: {"provider": provider}})
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        body = _read(client, headers, ENDPOINTS[1])
        assert body["items"][0]["model_id"] == identity
        assert body["items"][0]["selection_ref"] == f"model:{provider}:{identity}"


def test_public_redaction_no_implicit_runtime_io_or_saved_mutation(api_saved, monkeypatch):
    from row_bot.providers import auth_store, custom, model_catalog, runtime, selection

    write, _ = api_saved
    secret = {"api_key": "PRIVATE_SENTINEL", "fingerprint": "PRIVATE_SENTINEL", "last_error": "PRIVATE_SENTINEL", "path": "PRIVATE_SENTINEL"}
    write(cloud={"fixture": {"provider": "openai", **secret, "capabilities_snapshot": {
        "tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"], "tool_calling": True,
        "reasoning": {"supported_efforts": ["high", "PRIVATE_SENTINEL"], "thinking_mode": "toggle", **secret}, **secret,
    }}}, settings={"providers": {"openai": secret}, "custom_endpoints": [{"id": "private", "base_url": "PRIVATE_SENTINEL", "headers": secret}]})
    files = {path: path.read_bytes() for path in (config.CONFIG_PATH, cache.CATALOG_CACHE_PATH)}
    calls = []

    stage = ["startup"]

    def forbidden(owner):
        calls.append((stage[0], owner))
        raise AssertionError("provider/credential/mutation IO during saved API read")

    for module, names in [
        (cache, ["_bootstrap_snapshot_from_runtime", "refresh_model_catalog_cache", "write_model_catalog_cache"]),
        (config, ["save_provider_config"]), (auth_store, ["get_provider_secret", "provider_secret_status"]),
        (runtime, ["provider_status"]), (selection, ["prune_stale_custom_quick_choices", "list_quick_choices"]),
        (custom, ["custom_probe_for_model", "refresh_custom_endpoint_models"]),
        (model_catalog, ["_provider_status_by_id", "_codex_model_infos", "_claude_subscription_model_infos", "_xai_oauth_model_infos", "_probe_ollama_show_metadata"]),
    ]:
        for name in names:
            monkeypatch.setattr(module, name, lambda *args, owner=f"{module.__name__}.{name}", **kwargs: forbidden(owner))
    client, service, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        stage[0] = "read"
        for endpoint in ENDPOINTS:
            body = _read(client, headers, endpoint)
            assert "PRIVATE_SENTINEL" not in json.dumps(body)
        model = _read(client, headers, ENDPOINTS[1])["items"][0]
        assert model["reasoning"]["supported_efforts"] == ["high"]
        assert model["tool_calling"] is True
        assert not service.commands
        stage[0] = "shutdown"
    assert not [call for call in calls if call[0] != "startup"], repr(calls)
    assert all(path.read_bytes() == value for path, value in files.items())
