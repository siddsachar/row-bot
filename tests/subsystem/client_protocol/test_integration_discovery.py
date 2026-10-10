"""UX1 source contracts: isolated public API behavior, no live catalogs."""
# ruff: noqa: F811
import copy
import json
import threading
import urllib.error
from dataclasses import replace
from uuid import uuid4

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import facts, plans
from row_bot.integrations.safe import TtlCache
from row_bot.integrations.sources import SOURCES
from row_bot.mcp_client import marketplace
from tests.helpers.registry import search_catalog, use_registry
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
    monkeypatch.setattr(api, "_SEARCHES", TtlCache(1200, 64))
    return fake


def context(*, local_owner: bool = False) -> plans.Context:
    return plans.Context("owner", "owner", lambda: None, local_owner=local_owner)


def test_type_default_and_passive_http_contract(service, catalogs, monkeypatch):
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("central registry searched"))
    with client_for(service) as client:
        assert client.post("/api/v1/integrations/items/search", json={"kind": "skill"}).status_code in {401, 403}
        _, headers = bootstrap(client)
        for draft in ("", "writing", "new draft"):
            response = client.post("/api/v1/integrations/items/search", headers=headers,
                json={"kind": "skill", "query": draft})
            assert response.status_code == 200, response.text
            # Passive: only the local featured library answers; live catalogs wait for an explicit search.
            assert all(item["source"] == "featured_skills" for item in response.json()["items"])
            assert response.json()["items"] or draft
            assert {s["source"] for s in response.json()["sources"]} <= {"featured_skills", "clawhub", "github"}
        live = client.post("/api/v1/integrations/items/search", headers=headers,
            json={"kind": "skill", "query": "writing", "refresh": True})
        assert live.status_code == 200, live.text
        assert live.json()["items"][0]["compatibility"] == "not_inspected"
        assert live.json()["items"][0]["evidence"] == "listed"
        assert catalogs.calls == 1


@pytest.mark.parametrize("source", ["skills_sh", "browse_sh", "lobehub", "glama", "smithery", "pulsemcp", "clawhub_plugins"])
def test_unavailable_sources_never_fetch(catalogs, monkeypatch, source):
    monkeypatch.setattr(api, "_search_source", lambda *a, **k: pytest.fail("unavailable source fetched"))
    page = search_catalog(sources=[source], refresh=True)
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
    page = search_catalog(sources=["clawhub", "recommended"], refresh=True)
    assert {item["name"] for item in page["items"]} >= {"Notion MCP", "Linear MCP"}
    assert next(s for s in page["sources"] if s["source"] == "clawhub")["status"] == status


def test_fair_merge_pagination_and_proven_identity(catalogs, monkeypatch):
    def source(source, **kwargs):
        count = 160 if source == "official" else 3
        rows = [facts.finish(facts.entry("mcp", f"{source}:{i}", f"Result {i:03}", installed=False,
            lifecycle="available", source=source, compatibility="not_inspected")) for i in range(count)]
        # One proven identical deployment, plus same names with distinct identities.
        rows[0]["canonical_identity"] = "mcp:endpoint:proven"
        return rows, [SOURCES[source].status(status="cached")], {r["id"]: {"kind": "native", "plugin_id": r["id"]} for r in rows}, 0
    monkeypatch.setattr(api, "_search_source", source)
    args = {"owner_id": "owner", "sources": ["official", "recommended"], "limit": 4}
    first = search_catalog(**args)
    assert first["total"] == 162  # no global 96-result truncation
    duplicate = next(r for r in first["items"] if len(r["attributions"]) > 1)
    assert {a["source"] for a in duplicate["attributions"]} == {"official", "recommended"}
    assert any(r["source"] == "recommended" for r in first["items"])
    all_ids = [r["id"] for r in first["items"]]
    cursor = first["next_cursor"]
    monkeypatch.setattr(api, "_search_source", lambda *a, **k: pytest.fail("pagination fetched"))
    while cursor:
        page = search_catalog(**args, cursor=cursor)
        all_ids.extend(r["id"] for r in page["items"])
        cursor = page["next_cursor"]
    assert len(all_ids) == len(set(all_ids)) == 162
    with pytest.raises(ValueError, match="cursor_expired"):
        search_catalog(**{**args, "owner_id": "other"}, cursor=first["next_cursor"])
    with pytest.raises(ValueError, match="cursor_expired"):
        search_catalog(**args, query="changed", cursor=first["next_cursor"])


def test_parallel_sources_and_cancellation_suppress_late_results(catalogs, monkeypatch):
    barrier = threading.Barrier(2)
    release, entered, cancelled = threading.Event(), threading.Event(), threading.Event()
    def source(source, **kwargs):
        barrier.wait(timeout=1)
        entered.set()
        release.wait(timeout=1)
        return [facts.finish(facts.entry("mcp", source, source))], [SOURCES[source].status()], {}, 0
    monkeypatch.setattr(api, "_search_source", source)
    outcomes = []
    def search():
        try:
            outcomes.append(search_catalog(sources=["official", "recommended"], cancelled=cancelled.is_set))
        except ValueError as exc:
            outcomes.append(str(exc))
    thread = threading.Thread(target=search)
    thread.start()
    assert entered.wait(timeout=1)
    cancelled.set()
    thread.join(timeout=1)
    release.set()
    assert outcomes == ["integration_search_cancelled"]
    assert not len(api._SEARCHES)


def test_deadline_keeps_partial_results(catalogs, monkeypatch):
    release, entered = threading.Event(), threading.Event()
    original = api._search_source
    def source(name, **kwargs):
        if name == "clawhub":
            entered.set()
            release.wait(timeout=1)
            return [], [], {}, 0
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
        page = search_catalog(sources=["clawhub", "recommended"])
    finally:
        release.set()
    assert page["items"]
    assert next(s for s in page["sources"] if s["source"] == "clawhub")["status"] == "timeout"


def test_registry_search_is_local_never_builds_and_has_provenance(catalogs, isolated, monkeypatch):
    from row_bot.integrations import index
    from row_bot.mcp_client import registry_snapshot
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("search fetched registry"))
    before = sorted(isolated.rglob("*"))
    waiting = search_catalog(sources=["official"], query="notes", refresh=True)
    assert waiting["sources"][0]["status"] == "pending" and not waiting["items"]
    assert waiting["sources"][0]["snapshot_digest"] == registry_snapshot.read_header()["digest"]  # What it is built from.
    assert sorted(isolated.rglob("*")) == before  # Searching never builds or writes the index.
    index.ensure()  # Start-up's job.
    page = search_catalog(sources=["official"], query="notes", refresh=True)
    status = page["sources"][0]
    assert status["snapshot_digest"] == registry_snapshot.read_header()["digest"]
    assert status["snapshot_version"] == "v0.1" and len(status["snapshot_digest"]) == 64
    assert status["fetched_at"]
    assert page["items"] and all(r["lifecycle"] == "available" and not r["installed"] for r in page["items"])


@pytest.mark.parametrize("state", ["deleted", "deprecated", "changed", "missing"])
def test_stale_registry_entry_fails_its_consented_plan_and_saves_nothing(catalogs, isolated, monkeypatch, state, tmp_path):
    from row_bot.mcp_client import config, registry_snapshot
    envelope = {"server": {"name": "org.fixture/tool", "version": "1.0.0", "description": "Fixture tool for tests",
                           "remotes": [{"type": "streamable-http", "url": "https://example.test/mcp"}]},
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}
    use_registry(monkeypatch, tmp_path, marketplace.registry_entries({"servers": [copy.deepcopy(envelope)]}))
    page = search_catalog(sources=["official"], query="fixture")
    item_id = page["items"][0]["id"]
    _, plan = api.read_item(owner_id="owner", item_id=item_id, revision=page["revision"])
    assert plan["supported"] and plan["steps"][0]["type"] == "consent"
    remote = copy.deepcopy(envelope)
    if state == "changed":
        remote["server"]["remotes"][0]["url"] = "https://example.test/changed"
    if state in {"deleted", "deprecated"}:
        remote["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = state
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: {} if state == "missing" else remote)
    checked, revalidate = [], registry_snapshot.revalidate_entry
    monkeypatch.setattr(registry_snapshot, "revalidate_entry", lambda entry: checked.append(entry.id) or revalidate(entry))
    before = config.CONFIG_PATH.read_bytes()
    failed = api.start_plan(context(), plan_id=str(uuid4()), item_id=item_id, revision=page["revision"], digest=plan["digest"])
    assert checked == [item_id.removeprefix("mcp:official:")], "the Registry entry is checked again at consent"
    assert failed["state"] == "failed" and failed["steps"][0]["state"] == "failed"
    assert config.CONFIG_PATH.read_bytes() == before


def hermes_rows(*specs):
    return [{"id": f"hermes:{i}", "name": "Same", "description": "", "publisher": "fixture", "url": "https://github.com/example/repo",
        "subdirectory": sub, "version": "1", "pin": pin, "source_identity": "https://github.com/example/repo#" + sub,
        "platforms": [], "compatibility": compatibility, "reason": "Removed upstream." if compatibility == "unsupported" else ""}
        for i, (sub, pin, compatibility) in enumerate(specs)]


def test_hermes_subpath_and_pin_are_distinct_and_removed_hidden(catalogs, isolated, monkeypatch):
    from row_bot.plugins import hermes_catalog
    rows = hermes_rows(("a", "a"*40, "not_inspected"), ("b", "a"*40, "not_inspected"), ("a", "b"*40, "not_inspected"), ("c", "a"*40, "unsupported"))
    monkeypatch.setattr(hermes_catalog, "read_catalog", lambda **k: {"entries": rows, "status": "cached", "message": "", "fetched_at": 1})
    fetched = []
    monkeypatch.setattr(hermes_catalog, "_public_bytes", lambda url, **k: fetched.append(url) or pytest.fail("fetched " + url))
    page = search_catalog(sources=["hermes"])
    assert page["total"] == 3 and len({row["id"] for row in page["items"]}) == 3
    searched = search_catalog(sources=["hermes"], include_incompatible=True)
    assert searched["total"] == 4
    removed = next(row for row in searched["items"] if row["compatibility"] == "unsupported")
    assert removed["blockers"][0]["code"] == "unsupported" and removed["next_action"]["kind"] == "none"
    # After consent the package check reads the current catalog: a removed package is never fetched or added.
    _, plan = api.read_item(owner_id="owner", item_id=removed["id"], revision=searched["revision"])
    if plan["supported"]:  # Either the plan refuses it, or its check does after consent.
        failed = api.start_plan(context(local_owner=True), plan_id=str(uuid4()), item_id=removed["id"],
                                revision=searched["revision"], digest=plan["digest"])
        assert failed["state"] == "failed" and next(s for s in failed["steps"] if s["type"] == "test")["state"] == "failed"
    assert fetched == [] and not api.read_items(owner_id="owner", kind="plugin")["items"]


def test_a_hermes_package_whose_pin_moved_after_consent_is_never_fetched(catalogs, isolated, monkeypatch):
    from row_bot.plugins import hermes_catalog
    rows = hermes_rows(("a", "a"*40, "not_inspected"))
    monkeypatch.setattr(hermes_catalog, "read_catalog", lambda **k: {"entries": rows, "status": "cached", "message": "", "fetched_at": 1})
    fetched = []

    def refuse(url, **k):
        fetched.append(url)
        raise OSError("fixture network refused")
    monkeypatch.setattr(hermes_catalog, "_public_bytes", refuse)
    page = search_catalog(sources=["hermes"])
    _, plan = api.read_item(owner_id="owner", item_id=page["items"][0]["id"], revision=page["revision"])
    rows[0]["pin"] = "b" * 40  # The catalog moved to another commit after the person agreed.
    failed = api.start_plan(context(local_owner=True), plan_id=str(uuid4()), item_id=page["items"][0]["id"],
                            revision=page["revision"], digest=plan["digest"])
    assert failed["state"] == "failed" and fetched == [], "a pin nobody agreed to is never downloaded"


def test_cancelled_skill_search_does_not_publish_source_cache(catalogs, isolated, monkeypatch):
    cancelled = [False]
    def search(*a, **k):
        cancelled[0] = True
        return [entry()]
    monkeypatch.setattr(catalogs, "search", search)
    with pytest.raises(ValueError, match="cancelled"):
        search_catalog(kind="skill", query="writing", refresh=True, cancelled=lambda: cancelled[0])
    assert not list(isolated.rglob("clawhub_search_*.json"))


def test_clawhub_install_rejects_moderation_after_preview(catalogs, monkeypatch):
    from row_bot.application import client_skill_hub
    from row_bot.skills_hub import clawhub_source
    from tests.subsystem.skills.test_skills_hub_sources import _bundle
    bundle = _bundle("writing", source="clawhub", install_ref="clawhub:publisher/writing@1.0.0")
    monkeypatch.setattr(catalogs, "inspect", lambda _: bundle, raising=False)
    page = search_catalog(sources=["clawhub"], query="writing", refresh=True)
    item_id = page["items"][0]["id"]
    _, plan = api.read_item(owner_id="owner", item_id=item_id, revision=page["revision"])
    checked = []
    preview = client_skill_hub.preview_public_skill
    monkeypatch.setattr(client_skill_hub, "preview_public_skill", lambda **k: checked.append(k) or preview(**k))
    # Removed between the check and the install: the plan's check passes, the install refuses.
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda *a: {"owner": {"handle": "publisher"}, "skill": {"deletedAt": 1}})
    monkeypatch.setattr(client_skill_hub.installer, "install_bundle", lambda *a, **k: pytest.fail("removed skill installed"))
    failed = api.start_plan(context(), plan_id=str(uuid4()), item_id=item_id, revision=page["revision"], digest=plan["digest"])
    assert len(checked) == 1 and [s["state"] for s in failed["steps"]] == ["done", "done", "failed"]
    assert failed["state"] == "failed"


@pytest.mark.parametrize("change", ["unchanged", "unchanged_package", "deleted", "headers", "auth", "env", "runtime", "registry", "transport"])
def test_registry_setup_revalidated_from_envelope_at_configuration_publication(service, catalogs, monkeypatch, change, tmp_path):
    from row_bot.integrations import sources
    from row_bot.mcp_client import config, registry_snapshot
    from tests.subsystem.client_protocol.test_mcp_configuration_api import review, send
    envelope = {"server": {"name": "org.fixture/tool", "version": "1.0.0", "description": "Fixture tool for tests",
                           "remotes": [{"type": "streamable-http", "url": "https://example.test/mcp"}]},
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}
    if change in {"env", "runtime", "registry", "unchanged_package"}:
        envelope["server"].pop("remotes")
        envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-tool", "version": "1.0.0", "transport": {"type": "stdio"}}]
    original = marketplace.registry_entries({"servers": [envelope]})[0]
    use_registry(monkeypatch, tmp_path, [original])
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: envelope)
    with client_for(service) as client:
        _, headers = bootstrap(client)
        page = client.post("/api/v1/integrations/items/search", headers=headers,
                           json={"sources": ["official"], "query": "fixture"}).json()
        item_id = page["items"][0]["id"]
        review_plan = client.post("/api/v1/integrations/plans/review", headers=headers,
                                  json={"item_id": item_id, "revision": page["revision"]})
        assert review_plan.status_code == 200, review_plan.text
        assert review_plan.json()["supported"] and review_plan.json()["consent_token"]
        # What consent saves: the listed recipe, bound to the Registry declarations it was reviewed against.
        listed = sources.catalog_entry(item_id)[1]["entry"]
        description = sources.import_json(registry_snapshot.revalidate_entry(listed))
        imported = json.loads(description)["mcpServers"]
        assert next(iter(imported.values()))["source"]["registry_setup_digest"]
        command = review(client, headers, {"operation": "import", "import_json": description})
        before = config.CONFIG_PATH.read_bytes()
        if change == "deleted": envelope["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
        elif change == "headers": envelope["server"]["remotes"][0]["headers"] = [{"name": "X-Tenant", "value": "alpha", "isRequired": True}]
        elif change == "auth": envelope["server"]["auth"] = {"type": "oauth2", "scopes": ["write"]}
        elif change == "env": envelope["server"]["packages"][0]["environmentVariables"] = [{"name": "TOKEN", "isSecret": True, "isRequired": True}]
        elif change == "runtime": envelope["server"]["packages"][0]["runtimeHint"] = "custom-runtime"
        elif change == "registry": envelope["server"]["packages"][0]["registryBaseUrl"] = "https://packages.example.test"
        elif change == "transport": envelope["server"]["remotes"][0]["type"] = "sse"
        if change not in {"unchanged", "unchanged_package"}:
            with pytest.raises(ValueError):  # Consent re-checks the entry first.
                registry_snapshot.revalidate_entry(listed)
        response = send(client, headers, command)
        if change in {"unchanged", "unchanged_package"}:
            assert response.status_code == 200, response.text
            saved = config.read_saved_configuration().document["servers"][next(iter(imported))]
            assert saved["source"]["registry_setup_digest"] == original.metadata["setup_digest"]
            assert not saved["enabled"]
        else:
            assert response.status_code >= 400
            assert config.CONFIG_PATH.read_bytes() == before
