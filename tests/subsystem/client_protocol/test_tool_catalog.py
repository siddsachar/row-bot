"""Passive registered-tool metadata through the authenticated client protocol."""
from __future__ import annotations

import pytest

from row_bot import agent_tool_catalog as catalog
from tests.subsystem.agents.test_client_tool_catalog import owners, _core as seed_core, _plugin, _mcp  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem
ENDPOINT = "/api/v1/settings/tools"


def _core(owner, identity="lookup", **kwargs):
    seed_core(owner, identity, **kwargs)
    # The authenticated policy owner reads the normal registered tool name.
    owner.core._tools[identity].name = identity


@pytest.fixture
def api_owners(owners, monkeypatch):  # noqa: F811
    from row_bot.providers import runtime
    monkeypatch.setattr(runtime, "provider_status", lambda *a, **k: {"configured": False})
    return owners


def read(client, headers, **params):
    response = client.get(ENDPOINT, headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def test_catalog_auth_origin_and_revocation(api_owners, monkeypatch):
    client, service, active = client_app(remote=True)
    with client:
        _core(api_owners)
        assert client.get(ENDPOINT).status_code == 401
        _, headers = bootstrap(client)
        assert client.get(ENDPOINT, headers={"X-Client-Session": headers["X-Client-Session"]}).status_code == 403
        assert client.get(ENDPOINT, headers={**headers, "Origin": "http://foreign.invalid"}).status_code == 403
        assert read(client, headers, source="core")["items"]
        active["value"] = False
        monkeypatch.setattr(catalog, "list_cached_tools", lambda **k: pytest.fail("revoked request reached owner"))
        assert client.get(ENDPOINT, headers=headers).status_code == 401
        assert service.commands == []


def test_catalog_revalidates_before_delivery(api_owners, monkeypatch):
    client, _, active = client_app(remote=True)
    original = catalog.list_cached_tools
    def revoke(**kwargs):
        result = original(**kwargs)
        active["value"] = False
        return result
    with client:
        _core(api_owners, label="PRIVATE_TOOL_LABEL")
        _, headers = bootstrap(client)
        monkeypatch.setattr(catalog, "list_cached_tools", revoke)
        response = client.get(ENDPOINT, headers=headers)
        assert response.status_code == 401
        assert "PRIVATE_TOOL_LABEL" not in response.text


def test_catalog_pages_filters_identities_and_secret_boundary(api_owners):
    client, service, _ = client_app()
    with client:
        api_owners.core._tools.clear()
        for i in range(205):
            _core(api_owners, f"tool-{i:03}", label=f"Saved tool {i:03}")
        _plugin(api_owners, "tool-000")
        _mcp(api_owners)
        _, headers = bootstrap(client)
        page = read(client, headers, source="core", limit=100)
        assert page["total"] == 205 and len(page["items"]) == 100
        revision = page["revision"]
        items = page["items"]
        while page["next_cursor"]:
            page = read(client, headers, source="core", limit=100, cursor=page["next_cursor"])
            assert page["revision"] == revision
            items.extend(page["items"])
        assert len({row["id"] for row in items}) == 205
        page = read(client, headers, query="tool-000")
        assert {(row["source"], row["id"]) for row in page["items"]} == {("core", "tool-000"), ("plugin", "tool-000")}
        assert page["generated_at"] is None and page["freshness"] == "cached"
        assert all(row["runtime_state"] == "unknown" for row in page["items"])
        response = client.get(ENDPOINT, headers=headers)
        assert "DO_NOT_RETURN" not in response.text
        assert "input_schema" not in response.text
        assert service.commands == []


def test_catalog_cursor_expires_when_current_enablement_changes(api_owners):
    client, _, _ = client_app()
    with client:
        _core(api_owners, "one")
        _core(api_owners, "two")
        _, headers = bootstrap(client)
        page = read(client, headers, limit=1)
        api_owners.core._enabled["one"] = False
        response = client.get(ENDPOINT, headers=headers, params={"limit": 1, "cursor": page["next_cursor"]})
        assert response.status_code == 410
        assert response.json()["code"] == "cursor_expired"
        assert read(client, headers)["revision"] != page["revision"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"source": "other"}, {"query": "x" * 257}, {"cursor": "bad"}])
def test_catalog_invalid_query_fails_without_effect(api_owners, params):
    client, service, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        response = client.get(ENDPOINT, headers=headers, params=params)
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_catalog_query"
        assert service.commands == []


def test_catalog_get_does_not_warm_or_execute_owners(api_owners, monkeypatch):
    client, _, _ = client_app()
    with client:
        _core(api_owners)
        _mcp(api_owners)
        _, headers = bootstrap(client)
        def forbidden(*a, **k):
            pytest.fail("GET warmed or executed a tool owner")
        for owner, names in ((api_owners.core, ("_ensure_config_scope", "_apply_saved_config")),
                             (api_owners.config, ("get_config", "load_config")),
                             (api_owners.runtime, ("_sync_catalog_from_config", "_get_effective_config", "get_catalog_snapshot")),
                             (api_owners.plugins, ("get_enabled_plugin_tool_records",))):
            for name in names:
                monkeypatch.setattr(owner, name, forbidden)
        first = read(client, headers)
        assert first == read(client, headers)
