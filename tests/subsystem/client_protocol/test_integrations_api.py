"""Common inventory and MCP credential authority, using only isolated fakes."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from uuid import uuid4

import pytest

from row_bot import secret_store
from row_bot.application import client_integrations, client_mcp_auth
from row_bot.mcp_client import config
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


def test_recommendations_are_passive_and_inspection_is_owner_bound(isolated, monkeypatch):
    from row_bot.mcp_client import marketplace
    from row_bot.plugins import hermes_catalog
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: pytest.fail("passive fetch"))
    monkeypatch.setattr(hermes_catalog, "_public_bytes", lambda *a, **k: pytest.fail("passive fetch"))
    before = {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    page = client_integrations.search_integrations(owner_id="local")
    assert {row["name"] for row in page["items"]} >= {"Notion MCP", "Linear MCP"}
    assert not any(row["name"] == "Local text tools" for row in page["items"])
    page = client_integrations.search_integrations(owner_id="local", sources=["examples"])
    assert before == {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    bundle = next(row for row in page["items"] if row["name"] == "Local text tools")
    with pytest.raises(ValueError, match="preview_expired"):
        client_integrations.preview_integration(owner_id="other", revision=page["revision"], item_id=bundle["id"])
    inspected = client_integrations.preview_integration(owner_id="local", revision=page["revision"], item_id=bundle["id"])["plugin"]
    assert len(inspected["skills"]) == 2 and len(inspected["servers"]) == 1
    assert inspected["diagnostics"] == []


def test_inventory_http_auth_and_read_have_no_effects(service, isolated, monkeypatch):
    from row_bot.plugins import loader
    monkeypatch.setattr(loader, "load_plugins", lambda *a, **k: pytest.fail("inventory executed plugin"))
    before = {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    with client_for(service) as client:
        assert client.get("/api/v1/settings/integrations").status_code == 401
        _, headers = bootstrap(client)
        response = client.get("/api/v1/settings/integrations", headers=headers)
        assert response.status_code == 200, response.text
        assert any(row["name"] == "Work" for row in response.json()["items"])
    # Bootstrap may create security state; integration reads never rewrite config.
    assert config.CONFIG_PATH.read_bytes() == before[config.CONFIG_PATH]


@pytest.mark.parametrize(("enabled", "global_enabled", "catalog", "runtime_status", "expected"), [
    (False, False, None, None, "setup"),
    (True, True, None, "connected", "setup"),
    (True, False, {}, "connected", "off"),
    (False, True, {}, "connected", "off"),
    (True, True, {}, "connected", "ready"),
    (True, True, {}, "failed", "attention"),
    (True, True, {}, "disconnected", "setup"),
])
def test_mcp_inventory_readiness_requires_accepted_tools_and_active_policy(
        isolated, monkeypatch, enabled, global_enabled, catalog, runtime_status, expected):
    from row_bot.mcp_client import runtime
    document = json.loads(config.CONFIG_PATH.read_text())
    document["enabled"] = global_enabled
    document["servers"]["Work"].update(enabled=enabled, tools={} if catalog is None else {"catalog": catalog})
    config.CONFIG_PATH.write_text(json.dumps(document))
    monkeypatch.setattr(runtime, "get_passive_server_statuses", lambda names: {"Work": {"status": runtime_status}})
    row = next(row for row in client_integrations.read_integrations()["items"] if row["name"] == "Work")
    assert row["status"] == expected
    assert bool(row["reasons"]) == (expected in {"setup", "attention"})


def test_secret_nonce_replay_and_callback_redaction(service, isolated):
    with client_for(service) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/settings/mcp/configuration", headers=headers).json()
        body = {"server_id": page["items"][0]["server_id"], "configuration_revision": page["revision"],
            "action": "start", "mode": "api_key", "label": "Work",
            "bindings": [{"kind": "header", "name": "Authorization", "key": "token", "prefix": "Bearer "}]}
        review = client.post("/api/v1/settings/mcp/auth/review", headers=headers, json=body)
        assert review.status_code == 200, review.text
        identity = str(uuid4())
        command = {**body, "client_session_id": headers["X-Client-Session"], "command_id": identity,
            "nonce": review.json()["nonce"], "values": {"token": "fixture-private-key"}}
        url = "/api/v1/settings/mcp/auth/commands"
        bad = client.post(url, headers={**headers, "Idempotency-Key": identity}, json={**command, "nonce": "invalid"})
        assert bad.status_code != 200
        first = client.post(url, headers={**headers, "Idempotency-Key": identity}, json=command)
        assert first.status_code == 200, first.text
        assert first.json()["state"] == "signed_in"
        assert "fixture-private-key" not in first.text + config.CONFIG_PATH.read_text()
        replay = client.post(url, headers={**headers, "Idempotency-Key": identity}, json=command)
        assert replay.status_code == 200 and replay.json()["state"] == "signed_in"
        callback = client.get("/api/v1/settings/mcp/auth/callback?state=wrong&code=fixture-secret-code")
        assert callback.status_code == 400 and "fixture-secret-code" not in callback.text
        assert callback.headers["referrer-policy"] == "no-referrer"


def test_reviewed_package_uses_same_owner_across_http_preview_install_and_recovery(service, isolated, monkeypatch, reload_for_data_dir):
    from row_bot.plugins import loader
    from row_bot.runtime import admissions
    reload_for_data_dir(isolated, "row_bot.plugins.installer")
    monkeypatch.setattr(loader, "refresh_plugin_runtime", lambda *a, **k: None)
    with client_for(service) as client:
        _, headers = bootstrap(client)
        preview = client.post("/api/v1/settings/integrations/preview", headers=headers,
            json={"kind": "plugin", "reference": "bundled:local-text-tools"})
        assert preview.status_code == 200, preview.text
        package = preview.json()["plugin"]
        body = {"action": "install", "plugin_id": package["plugin_id"], "preview_id": package["preview_id"]}
        review = client.post("/api/v1/settings/plugins/lifecycle/review", headers=headers, json=body)
        assert review.status_code == 200, review.text
        identity = str(uuid4())
        command = {**body, "command_id": identity, "client_session_id": headers["X-Client-Session"], "revision": review.json()["revision"]}
        response = client.post("/api/v1/settings/plugins/lifecycle/commands", headers={**headers, "Idempotency-Key": identity}, json=command)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed", response.text
        _, new_headers = bootstrap(client)
        receipt = client.get("/api/v1/settings/plugins/lifecycle/commands/" + identity, headers=new_headers)
        assert receipt.status_code == 200 and receipt.json()["status"] == "completed"
        reconcile = f"/api/v1/settings/integrations/operations/plugin/{identity}/reconcile"
        assert client.post(reconcile).status_code in {401, 403}
        settled = client.post(reconcile, headers=new_headers)
        assert settled.status_code == 200 and settled.json()["settled"]
        assert not admissions.read_unfinished_target_commands("settings:plugin:lifecycle:" + package["plugin_id"])["items"]


def test_mcp_publication_recovery_is_explicit_authenticated_and_does_not_resave(service, isolated, monkeypatch):
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
        url = "/api/v1/settings/integrations/operations/mcp/" + command["command_id"] + "/reconcile"
        assert client.post(url).status_code in {401, 403}
        result = client.post(url, headers=headers)
        assert result.status_code == 200 and result.json()["settled"], result.text
        receipt = client.get("/api/v1/commands/" + command["command_id"], headers=headers)
        assert receipt.json()["status"] == "completed"
        assert "private-value" not in result.text + receipt.text


@pytest.mark.parametrize("query", ["", "writing", "https://example.test/SKILL.md"])
def test_skill_discovery_passive_reads_do_not_fetch_or_create_cache(isolated, monkeypatch, query):
    from row_bot.skills_hub import source_registry
    monkeypatch.setattr(source_registry, "_in_background", lambda *a, **k: pytest.fail("passive source execution"))
    before = {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    page = client_integrations.search_integrations(owner_id="fixture", sources=["clawhub"], query=query)
    assert not page["items"]
    assert before == {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}


@pytest.mark.parametrize("stale", [False, True])
def test_skill_discovery_reuses_saved_results_and_preserves_preview_authority(isolated, monkeypatch, stale):
    from row_bot.skills_hub import source_registry
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
    live = client_integrations.search_integrations(owner_id="fixture", sources=["clawhub"], query="writing", refresh=True)
    assert len(live["items"]) == 1 and source.calls == 1
    # Re-create the registry, as after process restart, and forbid every fetch.
    monkeypatch.setattr(source_registry, "_DEFAULT_REGISTRY", source_registry.SkillSourceRegistry([source]))
    monkeypatch.setattr(source, "search", lambda *a, **k: pytest.fail("cached search fetched"))
    if stale:
        now = source_registry.time.time()
        monkeypatch.setattr(source_registry.time, "time", lambda: now + source_registry.BROWSE_CACHE_TTL_SECONDS + 1)
    saved = client_integrations.search_integrations(owner_id="fixture", sources=["clawhub"], query="writing")
    assert saved["items"] == live["items"]
    assert saved["sources"][0]["status"] == ("stale" if stale else "cached")
    inspected = client_integrations.preview_integration(owner_id="fixture", revision=saved["revision"], item_id=saved["items"][0]["id"])
    assert inspected["skill"]["skill_name"] == "writing"
    with pytest.raises(ValueError, match="preview_expired"):
        client_integrations.preview_integration(owner_id="other", revision=saved["revision"], item_id=saved["items"][0]["id"])
