"""Registry envelopes retain deployment/setup identity without copying secrets."""
import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from row_bot.application import client_integrations
from row_bot.mcp_client import marketplace, registry_snapshot
from tests.helpers.registry import use_registry

pytestmark = pytest.mark.platform


@pytest.fixture
def envelope():
    path = Path(__file__).parents[2] / "fixtures/integrations/registry-v01.json"
    return json.loads(path.read_text())["servers"][0]


def test_required_header_deployments_survive_parser_snapshot_and_search(envelope, tmp_path, monkeypatch):
    second = copy.deepcopy(envelope)
    second["server"]["name"] = "org.other/notes"
    for row, tenant in ((envelope, "alpha"), (second, "beta")):
        row["server"]["remotes"][0]["headers"] = [{"name": "X-Tenant", "value": tenant, "isRequired": True}]
    entries = marketplace.registry_entries({"servers": [envelope, second]})
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    use_registry(monkeypatch, tmp_path, entries)
    page = client_integrations.search_integrations(owner_id="declarations", sources=["official"], query="notes",
                                                   include_incompatible=True)
    assert len(page["items"]) == 2
    assert len({r["canonical_identity"] for r in page["items"]}) == 2
    for item in page["items"]:
        assert item["compatibility"] == "unsupported"
        assert not item["actions"]
        assert "header" in " ".join(item["reasons"]).lower()
    assert client_integrations.search_integrations(owner_id="declarations", sources=["official"])["total"] == 0
    assert client_integrations.search_integrations(owner_id="declarations", sources=["official"], query="notes",
                                                   include_incompatible=False)["total"] == 2  # Searched: shown with reasons.
    assert not any(e.install for e in registry_snapshot.read_snapshot()["entries"])


@pytest.mark.parametrize("change", ["headers", "auth", "variables", "environment", "runtime", "arguments", "registry", "hash", "unknown"])
def test_material_declarations_invalidate_pinned_review(envelope, monkeypatch, change):
    if change in {"environment", "runtime", "arguments", "registry", "hash"}:
        envelope["server"].pop("remotes")
        envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-notes", "version": "1.2.3", "transport": {"type": "stdio"}}]
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    cfg = marketplace.entry_to_server_config(entry)
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a: envelope)
    registry_snapshot.revalidate_entry(entry)
    registry_snapshot.revalidate_configuration(cfg)
    server = envelope["server"]
    if change == "headers": server["remotes"][0]["headers"] = [{"name": "X-Tenant", "value": "alpha", "isRequired": True}]
    elif change == "auth": server["auth"] = {"type": "oauth2", "scopes": ["notes.write"]}
    elif change == "variables": server["remotes"][0]["variables"] = {"tenant": {"isRequired": True}}
    elif change == "environment": server["packages"][0]["environmentVariables"] = [{"name": "TENANT", "value": "alpha", "isRequired": True}]
    elif change == "runtime": server["packages"][0]["runtimeHint"] = "custom-runtime"
    elif change == "arguments": server["packages"][0]["runtimeArguments"] = [{"type": "named", "name": "--tenant", "value": "alpha"}]
    elif change == "registry": server["packages"][0]["registryBaseUrl"] = "https://packages.example.test"
    elif change == "hash": server["packages"][0]["fileSha256"] = "a" * 64
    else: server["remotes"][0]["futureSetup"] = {"required": True}
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_entry(entry)
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_configuration(cfg)
    assert marketplace.registry_entries({"servers": [envelope]})[0].install is None


def test_secret_header_is_never_copied_and_changed_value_invalidates(envelope, monkeypatch):
    headers = [{"name": "Authorization", "value": "synthetic-secret-alpha", "isSecret": True, "isRequired": True}]
    envelope["server"]["remotes"][0]["headers"] = headers
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    assert "synthetic-secret-alpha" not in json.dumps(asdict(entry))
    assert entry.install is None
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a: envelope)
    headers[0]["value"] = "synthetic-secret-beta"
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_entry(entry)
    with pytest.raises(ValueError, match="unsupported"):
        marketplace.entry_to_server_config(entry)


def test_legacy_snapshot_without_declaration_binding_cannot_authorize_import(envelope, monkeypatch):
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    entry.metadata.pop("setup_digest", None)
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a: envelope)
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_entry(entry)


@pytest.mark.parametrize("oversized", ["bytes", "variants"])
def test_oversized_setup_is_rejected_instead_of_truncated(envelope, oversized):
    if oversized == "bytes":
        envelope["server"]["remotes"][0]["headers"] = [{"name": "X-Tenant", "value": "x" * 65536}]
    else:
        envelope["server"]["remotes"] *= 17
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    assert entry.install is None and "setup_digest" not in entry.metadata
    assert "x" * 64 not in json.dumps(asdict(entry))
    with pytest.raises(ValueError, match="unsupported"):
        marketplace.entry_to_server_config(entry)


def test_shipped_snapshot_records_keep_their_declaration_binding(envelope, tmp_path, monkeypatch):
    entries = marketplace.registry_entries({"servers": [envelope]})
    use_registry(monkeypatch, tmp_path, entries)
    result = registry_snapshot.read_snapshot()
    assert result["schema_version"] == 3
    assert result["entries"] and all(e.metadata["setup_digest"] for e in result["entries"])
