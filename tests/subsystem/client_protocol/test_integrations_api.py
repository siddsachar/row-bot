"""Common inventory and MCP credential authority, using only isolated fakes."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from uuid import uuid4

import pytest

from row_bot import secret_store
from row_bot.application import client_integrations, client_mcp_auth
from row_bot.mcp_client import config
from tests.helpers.registry import search_catalog
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = pytest.mark.platform


@pytest.fixture
def isolated(tmp_path, monkeypatch, reload_for_data_dir):
    reload_for_data_dir(tmp_path, "row_bot.tasks", "row_bot.mcp_client.config", "row_bot.plugins.state", "row_bot.skills")
    secret_store._set_backend_for_tests(MemoryKeyring())
    monkeypatch.setattr(client_mcp_auth, "_FLOWS", {})
    config.CONFIG_PATH.write_text(json.dumps({"version": 1, "enabled": False, "servers": {"Work": {
        "transport": "streamable_http", "url": "https://example.test/mcp", "enabled": False}}}))
    yield tmp_path
    secret_store._set_backend_for_tests(None)


def test_recommendations_are_passive_and_packages_are_inspected_only_after_consent_for_one_owner(isolated, monkeypatch):
    from row_bot.application.client_plugin_lifecycle import review_plugin_lifecycle
    from row_bot.integrations import plans
    from row_bot.mcp_client import marketplace
    from row_bot.plugins import hermes_catalog
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("passive fetch"))
    monkeypatch.setattr(hermes_catalog, "_public_bytes", lambda *a, **k: pytest.fail("passive fetch"))
    inspect = hermes_catalog.inspect_package
    monkeypatch.setattr(hermes_catalog, "inspect_package", lambda **k: pytest.fail("inspected before consent"))

    def files():  # SQLite may create its read-lock sidecars even for reads.
        return {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file() and not p.name.endswith((".db-shm", ".db-wal"))}
    before = files()
    page = search_catalog("local")
    assert {row["name"] for row in page["items"]} >= {"Notion MCP", "Linear MCP"}
    assert not any(row["name"] == "Local text tools" for row in page["items"])
    page = search_catalog("local", sources=["examples"])
    bundle = next(row for row in page["items"] if row["name"] == "Local text tools")
    _, plan = client_integrations.read_item(owner_id="local", item_id=bundle["id"], revision=page["revision"])
    assert plan["intent"] == "add" and [s["type"] for s in plan["steps"]] == ["consent", "test", "enable"]
    with pytest.raises(plans.PlanError, match="owner_local_only"):
        client_integrations.start_plan(plans.Context("local", "local", lambda: None), plan_id=str(uuid4()),
                                       item_id=bundle["id"], revision=page["revision"], digest=plan["digest"])
    assert before == files(), "searching, reviewing and a refused start neither inspect nor write"
    # After consent the plan's check inspects the package for its own owner; another owner cannot use that review.
    inspected = inspect(owner_id="local", reference="bundled:local-text-tools")
    assert len(inspected["skills"]) == 2 and len(inspected["servers"]) == 1
    assert inspected["diagnostics"] == []
    with pytest.raises(ValueError, match="preview_expired"):
        review_plugin_lifecycle("install", inspected["plugin_id"], validate=lambda: None, owner_id="other",
                                preview_id=inspected["preview_id"])
    assert review_plugin_lifecycle("install", inspected["plugin_id"], validate=lambda: None, owner_id="local",
                                   preview_id=inspected["preview_id"])["plugin_id"] == inspected["plugin_id"]


def test_inventory_http_auth_and_read_have_no_effects(service, isolated, monkeypatch):
    from row_bot.plugins import loader
    monkeypatch.setattr(loader, "load_plugins", lambda *a, **k: pytest.fail("inventory executed plugin"))
    before = {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    with client_for(service) as client:
        assert client.get("/api/v1/integrations/items").status_code == 401
        _, headers = bootstrap(client)
        response = client.get("/api/v1/integrations/items", headers=headers)
        assert response.status_code == 200, response.text
        work = next(row for row in response.json()["items"] if row["name"] == "Work")
        assert client.get("/api/v1/integrations/detail", params={"item_id": work["id"]}).status_code == 401
        detail = client.get("/api/v1/integrations/detail", params={"item_id": work["id"]}, headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["entry"]["id"] == work["id"] and detail.json()["plan"]["consent_token"] == ""
    # Bootstrap may create security state; integration reads never rewrite config.
    assert config.CONFIG_PATH.read_bytes() == before[config.CONFIG_PATH]


@pytest.mark.parametrize(("enabled", "global_enabled", "catalog", "runtime_status", "lifecycle", "readiness", "action"), [
    (False, False, None, None, "off", "needs_setup", "continue_setup"),
    (True, True, None, "connected", "installed", "needs_setup", "continue_setup"),
    (True, False, {}, "connected", "off", "ready", "turn_on"),
    (False, True, {}, "connected", "off", "ready", "turn_on"),
    (True, True, {}, "connected", "installed", "ready", "try"),
    (True, True, {}, "failed", "installed", "attention", "fix"),
    (True, True, {}, "disconnected", "installed", "needs_setup", "continue_setup"),
])
def test_mcp_inventory_readiness_requires_accepted_tools_and_active_policy(
        isolated, monkeypatch, enabled, global_enabled, catalog, runtime_status, lifecycle, readiness, action):
    from row_bot.mcp_client import runtime
    document = json.loads(config.CONFIG_PATH.read_text())
    document["enabled"] = global_enabled
    document["servers"]["Work"].update(enabled=enabled, tools={} if catalog is None else {"catalog": catalog})
    config.CONFIG_PATH.write_text(json.dumps(document))
    monkeypatch.setattr(runtime, "get_passive_server_statuses", lambda names: {"Work": {"status": runtime_status}})
    row = next(row for row in client_integrations.read_items(owner_id="local")["items"] if row["name"] == "Work")
    assert (row["lifecycle"], row["readiness"], row["next_action"]["kind"]) == (lifecycle, readiness, action)
    blocking = [b for b in row["blockers"] if b["severity"] == "blocking"]
    assert bool(blocking) == (readiness != "ready") and all(b["message"] for b in blocking)


def test_sign_in_callback_never_echoes_its_code(service, isolated):
    with client_for(service) as client:
        callback = client.get("/api/v1/settings/mcp/auth/callback?state=wrong&code=fixture-secret-code")
        assert callback.status_code == 400 and "fixture-secret-code" not in callback.text
        assert callback.headers["referrer-policy"] == "no-referrer"


def test_reviewed_package_uses_same_owner_across_http_plan_install_and_recovery(service, isolated, monkeypatch, reload_for_data_dir):
    from row_bot.integrations import plans
    from row_bot.plugins import loader
    from row_bot.runtime import admissions
    reload_for_data_dir(isolated, "row_bot.plugins.installer")
    monkeypatch.setattr(loader, "refresh_plugin_runtime", lambda *a, **k: None)
    monkeypatch.setattr(plans, "_spawn", lambda work: work())  # The background run finishes before the response.
    with client_for(service) as client:
        _, headers = bootstrap(client)
        page = client.post("/api/v1/integrations/items/search", headers=headers, json={"sources": ["examples"]}).json()
        bundle = next(row for row in page["items"] if row["name"] == "Local text tools")
        review = client.post("/api/v1/integrations/plans/review", headers=headers,
                             json={"item_id": bundle["id"], "revision": page["revision"]})
        assert review.status_code == 200, review.text
        plan_id = str(uuid4())
        body = {"plan_id": plan_id, "item_id": bundle["id"], "revision": page["revision"], "digest": review.json()["digest"],
                "consent_token": review.json()["consent_token"]}
        # The package is inspected and installed by one owner: the install consumes that owner's own review.
        response = client.post("/api/v1/integrations/plans", headers={**headers, "Idempotency-Key": plan_id}, json=body)
        assert response.status_code == 200, response.text
        # What the package runs and may do is shown before anything is added; continuing agrees to exactly that.
        review = next(s for s in response.json()["steps"] if s["type"] == "test")["review"]
        assert response.json()["pause"] == "digest_changed" and "Asks to run programs on this computer." in review["lines"]
        response = client.post(f"/api/v1/integrations/plans/{plan_id}/continue", headers=headers,
                               json={"review_digest": review["digest"]})
        assert response.status_code == 200 and response.json()["state"] == "completed", response.text
        assert next(s for s in response.json()["steps"] if s["type"] == "test")["message"] == "2 skills and 1 connections found."
        _, new_headers = bootstrap(client)
        receipt = "/api/v1/integrations/plans/" + plan_id
        assert client.get(receipt).status_code in {401, 403}
        settled = client.get(receipt, headers=new_headers)
        assert settled.status_code == 200 and settled.json()["state"] == "completed"
        installed = client.get("/api/v1/integrations/items", params={"kind": "plugin"}, headers=new_headers).json()["items"]
        package = next(row for row in installed if row["kind"] == "plugin")
        assert package["compatibility"] == "supported" and package["installed"]
        plugin_id = package["id"].removeprefix("plugin:")
        assert not admissions.read_unfinished_target_commands("settings:plugin:lifecycle:" + plugin_id)["items"]


def test_mcp_publication_recovery_needs_a_session_and_does_not_resave(service, isolated, monkeypatch):
    from row_bot.runtime import admissions
    from tests.subsystem.client_protocol.test_mcp_configuration_api import review, send
    persist = admissions.command_progress
    def lose_completion(owner_id, key, result):
        if result.get("status") == "completed":
            raise OSError("lost response")
        return persist(owner_id, key, result)
    with client_for(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers)
        monkeypatch.setattr(admissions, "command_progress", lose_completion)
        failed = send(client, headers, command)
        assert failed.status_code >= 400, failed.text
        monkeypatch.setattr(admissions, "command_progress", persist)
        monkeypatch.setattr(config, "publish_saved_configuration", lambda *a, **k: pytest.fail("repeated publication"))
        url = "/api/v1/integrations/items?kind=mcp"
        assert client.get(url).status_code in {401, 403}
        assert admissions.read_unfinished_target_commands("settings:mcp")["items"], "a refused read settles nothing"
        result = client.get(url, headers=headers)
        assert result.status_code == 200, result.text
        assert all(b["code"] != "configuration_recovery" for row in result.json()["items"] for b in row["blockers"])
        assert not admissions.read_unfinished_target_commands("settings:mcp")["items"]
        receipt = client.get("/api/v1/commands/" + command["command_id"], headers=headers)
        assert receipt.json()["status"] == "completed"
        assert "private-value" not in result.text + receipt.text


@pytest.mark.parametrize("query", ["", "writing", "https://example.test/SKILL.md"])
def test_skill_discovery_passive_reads_do_not_fetch_or_create_cache(isolated, monkeypatch, query):
    from row_bot.skills_hub import source_registry
    monkeypatch.setattr(source_registry, "_in_background", lambda *a, **k: pytest.fail("passive source execution"))
    before = {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    page = search_catalog("fixture", sources=["clawhub"], query=query)
    assert not page["items"]
    assert before == {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}


@pytest.mark.parametrize("stale", [False, True])
def test_skill_discovery_reuses_saved_results_and_preserves_preview_authority(isolated, monkeypatch, stale):
    from row_bot.application import client_skill_hub as hub
    from row_bot.integrations import plans
    from row_bot.skills_hub import clawhub_source, source_registry
    from row_bot.skills_hub.installer import InstallResult
    from row_bot.skills_hub.models import SkillHubEntry
    from tests.subsystem.skills.test_skills_hub_sources import _bundle

    class Source:
        id = "clawhub"
        supports_browse = supports_search = True
        calls = 0

        def search(self, query, *, limit):
            self.calls += 1
            return [SkillHubEntry(id="clawhub:example/writing", name="Writing", description="Writing notes",
                source="clawhub", source_id="clawhub", install_ref="clawhub:example/writing")]

        def inspect(self, entry):
            return _bundle("writing", source="clawhub", install_ref=entry.install_ref)

    source = Source()
    monkeypatch.setattr(source_registry, "_DEFAULT_REGISTRY", source_registry.SkillSourceRegistry([source]))
    live = search_catalog("fixture", sources=["clawhub"], query="writing", refresh=True)
    assert len(live["items"]) == 1 and source.calls == 1
    # Re-create the registry, as after process restart, and forbid every fetch.
    monkeypatch.setattr(source_registry, "_DEFAULT_REGISTRY", source_registry.SkillSourceRegistry([source]))
    monkeypatch.setattr(source, "search", lambda *a, **k: pytest.fail("cached search fetched"))
    if stale:
        now = source_registry.time.time()
        monkeypatch.setattr(source_registry.time, "time", lambda: now + source_registry.BROWSE_CACHE_TTL_SECONDS + 1)
    saved = search_catalog("fixture", sources=["clawhub"], query="writing")
    assert saved["items"] == live["items"]
    assert saved["sources"][0]["status"] == ("stale" if stale else "cached")
    item_id = saved["items"][0]["id"]
    with pytest.raises(ValueError, match="not_found"):
        client_integrations.read_item(owner_id="other", item_id=item_id, revision=saved["revision"])
    _, plan = client_integrations.read_item(owner_id="fixture", item_id=item_id, revision=saved["revision"])
    with pytest.raises(ValueError, match="not_found"):
        client_integrations.start_plan(plans.Context("other", "other", lambda: None), plan_id=str(uuid4()), item_id=item_id,
                                       revision=saved["revision"], digest=plan["digest"])
    installed, rechecked = [], []
    monkeypatch.setattr(clawhub_source, "revalidate_bundle", rechecked.append)  # Moderation has its own test.
    monkeypatch.setattr(hub.installer, "install_bundle", lambda bundle, *, enabled: installed.append(bundle.root_name)
                        or InstallResult(True, "Skill installed.", skill_name=bundle.root_name))
    done = client_integrations.start_plan(plans.Context("fixture", "fixture", lambda: None), plan_id=str(uuid4()),
                                          item_id=item_id, revision=saved["revision"], digest=plan["digest"])
    assert done["state"] == "completed", (done["message"], done["steps"])
    assert installed == ["writing"], "the saved result's own skill was checked and added after consent"
    assert [bundle.install_ref for bundle in rechecked] == ["clawhub:example/writing"]
