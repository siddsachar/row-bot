"""UX1 source contracts: isolated public API behavior, no live catalogs."""
# ruff: noqa: F811
import copy
import json
import threading
import urllib.error
from dataclasses import replace

import pytest

from row_bot.application import client_integrations as api
from row_bot.application.integration_sources import source_status
from row_bot.mcp_client import marketplace, registry_snapshot
from row_bot.skills_hub import source_registry
from row_bot.skills_hub.models import SkillHubEntry
from tests.subsystem.client_protocol.test_integrations_api import isolated  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.platform


def entry(name="Writing", identity="clawhub:publisher/writing"):
    return SkillHubEntry(identity, name, "Write useful notes", "clawhub", "clawhub", identity, author="publisher")


class Skills:
    id = "clawhub"
    supports_browse = supports_search = True
    calls = 0

    def search(self, query, *, limit):
        self.calls += 1
        return [entry()]

    def browse(self, *, limit):
        return self.search("", limit=limit)


@pytest.fixture
def catalogs(isolated, monkeypatch):
    fake = Skills()
    monkeypatch.setattr(source_registry, "_DEFAULT_REGISTRY", source_registry.SkillSourceRegistry([fake]))
    monkeypatch.setattr(api, "_RESULTS", {})
    monkeypatch.setattr(api, "_SEARCHES", {})
    return fake


def test_type_default_and_passive_http_contract(service, catalogs, monkeypatch):
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("central registry searched"))
    with client_for(service) as client:
        _, headers = bootstrap(client)
        for draft in ("", "writing", "new draft"):
            response = client.post("/api/v1/settings/integrations/search", headers=headers,
                json={"kind": "skill", "query": draft})
            assert response.status_code == 200, response.text
            assert not response.json()["items"]
            assert {s["source"] for s in response.json()["sources"]} <= {"clawhub", "github"}
        live = client.post("/api/v1/settings/integrations/search", headers=headers,
            json={"kind": "skill", "query": "writing", "refresh": True})
        assert live.status_code == 200, live.text
        assert live.json()["items"][0]["compatibility"] == "not_inspected"
        assert live.json()["items"][0]["evidence_stage"] == "listed"
        assert catalogs.calls == 1


@pytest.mark.parametrize("source", ["skills_sh", "browse_sh", "lobehub", "glama", "smithery", "pulsemcp", "clawhub_plugins"])
def test_unavailable_sources_never_fetch(catalogs, monkeypatch, source):
    monkeypatch.setattr(api, "_search_source", lambda *a, **k: pytest.fail("unavailable source fetched"))
    page = api.search_integrations(owner_id="owner", sources=[source], refresh=True)
    assert not page["items"]
    assert page["sources"][0]["access"] == "unavailable"
    assert not page["sources"][0]["enabled"]


@pytest.mark.parametrize(("error", "status"), [
    (urllib.error.HTTPError("https://example.test", 401, "denied", {}, None), "auth_required"),
    (urllib.error.HTTPError("https://example.test", 429, "limited", {}, None), "rate_limited"),
    (ValueError("bad schema"), "malformed"),
    (TimeoutError(), "timeout"),
])
def test_partial_errors_preserve_other_results(catalogs, monkeypatch, error, status):
    def fail(*a, **k):
        raise error
    monkeypatch.setattr(catalogs, "search", fail)
    page = api.search_integrations(owner_id="owner", sources=["clawhub", "recommended"], refresh=True)
    assert {item["name"] for item in page["items"]} >= {"Notion MCP", "Linear MCP"}
    assert next(s for s in page["sources"] if s["source"] == "clawhub")["status"] == status


def test_fair_merge_pagination_and_proven_identity(catalogs, monkeypatch):
    def source(source, **kwargs):
        count = 160 if source == "official" else 3
        rows = [api._item("mcp", f"{source}:{i}", f"Result {i:03}", installed=False,
            status="discover", source=source, compatibility="not_inspected") for i in range(count)]
        # One proven identical deployment, plus same names with distinct identities.
        rows[0]["canonical_identity"] = "mcp:endpoint:proven"
        return rows, [source_status(source, status="cached")], {r["id"]: {"kind": "native", "plugin_id": r["id"]} for r in rows}
    monkeypatch.setattr(api, "_search_source", source)
    args = {"owner_id": "owner", "sources": ["official", "recommended"], "limit": 4}
    first = api.search_integrations(**args)
    assert first["total"] == 162  # no global 96-result truncation
    duplicate = next(r for r in first["items"] if r["canonical_identity"])
    assert {a["source"] for a in duplicate["attributions"]} == {"official", "recommended"}
    assert any(r["source"] == "recommended" for r in first["items"])
    all_ids = [r["id"] for r in first["items"]]
    cursor = first["next_cursor"]
    monkeypatch.setattr(api, "_search_source", lambda *a, **k: pytest.fail("pagination fetched"))
    while cursor:
        page = api.search_integrations(**args, cursor=cursor)
        all_ids.extend(r["id"] for r in page["items"])
        cursor = page["next_cursor"]
    assert len(all_ids) == len(set(all_ids)) == 162
    with pytest.raises(ValueError, match="cursor_expired"):
        api.search_integrations(**{**args, "owner_id": "other"}, cursor=first["next_cursor"])
    with pytest.raises(ValueError, match="cursor_expired"):
        api.search_integrations(**args, query="changed", cursor=first["next_cursor"])


def test_parallel_sources_and_cancellation_suppress_late_results(catalogs, monkeypatch):
    barrier = threading.Barrier(2)
    release, entered, cancelled = threading.Event(), threading.Event(), threading.Event()
    def source(source, **kwargs):
        barrier.wait(timeout=1)
        entered.set()
        release.wait(timeout=1)
        return [api._item("mcp", source, source)], [source_status(source)], {}
    monkeypatch.setattr(api, "_search_source", source)
    outcomes = []
    def search():
        try:
            outcomes.append(api.search_integrations(owner_id="owner", sources=["official", "recommended"], cancelled=cancelled.is_set))
        except ValueError as exc:
            outcomes.append(str(exc))
    thread = threading.Thread(target=search)
    thread.start()
    assert entered.wait(timeout=1)
    cancelled.set()
    thread.join(timeout=1)
    release.set()
    assert outcomes == ["integration_search_cancelled"]
    assert not api._RESULTS and not api._SEARCHES


def test_deadline_keeps_partial_results(catalogs, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    original = api._search_source
    def source(name, **kwargs):
        if name == "clawhub":
            entered.set()
            release.wait(timeout=1)
            return [], [], {}
        assert entered.wait(timeout=1)
        return original(name, **kwargs)
    monkeypatch.setattr(api, "_search_source", source)
    # Coordinator clock advances once the quick source has returned; no sleeps.
    clock = [0.0]
    original_wait = api.concurrent.futures.wait
    def wait(*args, **kwargs):
        result = original_wait(*args, **kwargs)
        if result[0]:
            clock[0] = 100.0
        return result
    monkeypatch.setattr(api.concurrent.futures, "wait", wait)
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    try:
        page = api.search_integrations(owner_id="owner", sources=["clawhub", "recommended"])
    finally:
        release.set()
    assert page["items"]
    assert next(s for s in page["sources"] if s["source"] == "clawhub")["status"] == "timeout"


def test_registry_search_is_local_and_snapshot_has_provenance(catalogs, monkeypatch):
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("search fetched registry"))
    page = api.search_integrations(owner_id="owner", sources=["official"], refresh=True)
    status = page["sources"][0]
    assert status["snapshot_version"] == "v0.1" and len(status["snapshot_digest"]) == 64
    assert status["fetched_at"]
    assert page["items"] and all(r["status"] == "discover" for r in page["items"])


@pytest.mark.parametrize("state", ["deleted", "deprecated", "changed", "missing"])
def test_stale_registry_preview_requires_current_identical_recipe(catalogs, monkeypatch, state):
    original = marketplace.MarketplaceEntry("org.fixture/tool@1.0.0", "Fixture", "", "official",
        install={"transport": "streamable_http", "url": "https://example.test/mcp"},
        metadata={"canonical_name": "org.fixture/tool", "version": "1.0.0", "status": "active"})
    monkeypatch.setattr(registry_snapshot, "read_snapshot", lambda: {"entries": [original], "status": "stale"})
    page = api.search_integrations(owner_id="owner", sources=["official"])
    remote = {"server": {"name": "org.fixture/tool", "version": "1.0.0", "remotes": [{"type": "streamable-http", "url": "https://example.test/changed" if state == "changed" else "https://example.test/mcp"}]},
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": state if state in {"deleted", "deprecated"} else "active"}}}
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: {} if state == "missing" else remote)
    with pytest.raises(ValueError):
        api.preview_integration(owner_id="owner", revision=page["revision"], item_id=page["items"][0]["id"])


def test_hermes_subpath_and_pin_are_distinct_and_removed_hidden(catalogs, monkeypatch):
    from row_bot.plugins import hermes_catalog
    rows = [{"id": f"hermes:{i}", "name": "Same", "description": "", "publisher": "fixture", "url": "https://github.com/example/repo",
        "version": "1", "pin": pin, "source_identity": "https://github.com/example/repo#" + sub,
        "platforms": [], "compatibility": compatibility, "reason": ""}
        for i, (sub, pin, compatibility) in enumerate([("a", "a"*40, "not_inspected"), ("b", "a"*40, "not_inspected"), ("a", "b"*40, "not_inspected"), ("c", "a"*40, "unsupported")])]
    monkeypatch.setattr(hermes_catalog, "read_catalog", lambda **k: {"entries": rows, "status": "cached", "message": "", "fetched_at": 1})
    page = api.search_integrations(owner_id="owner", sources=["hermes"])
    assert page["total"] == 3
    assert api.search_integrations(owner_id="owner", sources=["hermes"], include_incompatible=True)["total"] == 4
    with pytest.raises(ValueError, match="source_unavailable"):
        api.preview_integration(owner_id="owner", revision=page["revision"], item_id=page["items"][0]["id"])


def test_cancelled_skill_search_does_not_publish_source_cache(catalogs, isolated, monkeypatch):
    cancelled = [False]
    def search(*a, **k):
        cancelled[0] = True
        return [entry()]
    monkeypatch.setattr(catalogs, "search", search)
    with pytest.raises(ValueError, match="cancelled"):
        api.search_integrations(owner_id="owner", kind="skill", query="writing", refresh=True, cancelled=lambda: cancelled[0])
    assert not list(isolated.rglob("clawhub_search_*.json"))


def test_clawhub_install_rejects_moderation_after_preview(catalogs, monkeypatch):
    from uuid import uuid4
    from row_bot.application import client_skill_hub
    from row_bot.skills_hub import clawhub_source
    from tests.subsystem.skills.test_skills_hub_sources import _bundle
    bundle = _bundle("writing", source="clawhub", install_ref="clawhub:publisher/writing@1.0.0")
    monkeypatch.setattr(catalogs, "inspect", lambda _: bundle, raising=False)
    page = api.search_integrations(owner_id="owner", sources=["clawhub"], query="writing", refresh=True)
    preview = api.preview_integration(owner_id="owner", revision=page["revision"], item_id=page["items"][0]["id"])["skill"]
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda *a: {"owner": {"handle": "publisher"}, "skill": {"deletedAt": 1}})
    monkeypatch.setattr(client_skill_hub.installer, "install_bundle", lambda *a, **k: pytest.fail("removed skill installed"))
    with pytest.raises(ValueError, match="removed"):
        client_skill_hub.install_previewed_skill(owner_id="owner", command_id=str(uuid4()),
            preview_id=preview["preview_id"], content_hash=preview["content_hash"], make_available=False)


@pytest.mark.parametrize("change", ["unchanged", "unchanged_package", "deleted", "headers", "auth", "env", "runtime", "registry", "transport"])
def test_registry_setup_revalidated_from_envelope_at_configuration_publication(service, catalogs, monkeypatch, change):
    from row_bot.mcp_client import config
    from tests.subsystem.client_protocol.test_mcp_configuration_api import review, send
    envelope = {"server": {"name": "org.fixture/tool", "version": "1.0.0", "remotes": [{"type": "streamable-http", "url": "https://example.test/mcp"}]},
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}
    if change in {"env", "runtime", "registry", "unchanged_package"}:
        envelope["server"].pop("remotes")
        envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-tool", "version": "1.0.0", "transport": {"type": "stdio"}}]
    original = marketplace.registry_entries({"servers": [envelope]})[0]
    monkeypatch.setattr(registry_snapshot, "read_snapshot", lambda: {"entries": [original], "status": "cached"})
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: envelope)
    with client_for(service) as client:
        _, headers = bootstrap(client)
        page = client.post("/api/v1/settings/integrations/search", headers=headers, json={"sources": ["official"]}).json()
        preview = client.post("/api/v1/settings/integrations/preview", headers=headers,
            json={"revision": page["revision"], "item_id": page["items"][0]["id"]})
        assert preview.status_code == 200, preview.text
        assert preview.json()["mcp"]["auth_requirement"] == "unknown"
        imported = json.loads(preview.json()["mcp"]["import_json"])["mcpServers"]
        assert next(iter(imported.values()))["source"]["registry_setup_digest"]
        command = review(client, headers, {"operation": "import", "import_json": preview.json()["mcp"]["import_json"]})
        before = config.CONFIG_PATH.read_bytes()
        if change == "deleted": envelope["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
        elif change == "headers": envelope["server"]["remotes"][0]["headers"] = [{"name": "X-Tenant", "value": "alpha", "isRequired": True}]
        elif change == "auth": envelope["server"]["auth"] = {"type": "oauth2", "scopes": ["write"]}
        elif change == "env": envelope["server"]["packages"][0]["environmentVariables"] = [{"name": "TOKEN", "isSecret": True, "isRequired": True}]
        elif change == "runtime": envelope["server"]["packages"][0]["runtimeHint"] = "custom-runtime"
        elif change == "registry": envelope["server"]["packages"][0]["registryBaseUrl"] = "https://packages.example.test"
        elif change == "transport": envelope["server"]["remotes"][0]["type"] = "sse"
        if change not in {"unchanged", "unchanged_package"}:
            stale_preview = client.post("/api/v1/settings/integrations/preview", headers=headers,
                json={"revision": page["revision"], "item_id": page["items"][0]["id"]})
            assert stale_preview.status_code >= 400
        response = send(client, headers, command)
        if change in {"unchanged", "unchanged_package"}:
            assert response.status_code == 200, response.text
            saved = config.read_saved_configuration().document["servers"][preview.json()["mcp"]["name"]]
            assert saved["source"]["registry_setup_digest"] == original.metadata["setup_digest"]
            assert not saved["enabled"]
        else:
            assert response.status_code >= 400
            assert config.CONFIG_PATH.read_bytes() == before
