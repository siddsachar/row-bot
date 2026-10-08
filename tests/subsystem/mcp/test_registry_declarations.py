"""Registry envelopes retain deployment/setup identity without copying secrets."""
import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from row_bot.mcp_client import marketplace, registry_snapshot
from tests.helpers.registry import search_catalog, use_registry

pytestmark = pytest.mark.platform


@pytest.fixture
def envelope():
    path = Path(__file__).parents[2] / "fixtures/integrations/registry-v01.json"
    return json.loads(path.read_text())["servers"][0]


def test_fixed_header_deployments_stay_distinct_through_parser_snapshot_and_search(envelope, tmp_path, monkeypatch):
    second = copy.deepcopy(envelope)
    second["server"]["name"] = "org.other/notes"
    second["server"]["description"] = "Notes for the beta tenant"
    for row, tenant in ((envelope, "alpha"), (second, "beta")):
        row["server"]["remotes"][0]["headers"] = [{"name": "X-Tenant", "value": tenant, "isRequired": True}]
    entries = marketplace.registry_entries({"servers": [envelope, second]})
    assert [e.install["headers"] for e in entries] == [{"X-Tenant": "alpha"}, {"X-Tenant": "beta"}]  # A fixed value stays fixed.
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    use_registry(monkeypatch, tmp_path, entries)
    page = search_catalog("declarations", sources=["official"], query="notes")
    # Distinct deployments of one endpoint are never merged: each keeps only its own listing.
    assert len({r["id"] for r in page["items"]}) == 2 and all(len(r["attributions"]) == 1 for r in page["items"])
    assert all(item["compatibility"] != "unsupported" for item in page["items"])
    assert all(e.install and e.metadata["setup_digest"] for e in registry_snapshot.read_snapshot()["entries"])


def test_declared_headers_variables_and_arguments_become_inputs(envelope):
    remote = envelope["server"]["remotes"][0]
    remote["url"] = "https://{tenant}.notes.example.test/mcp"
    remote["variables"] = {"tenant": {"description": "Your workspace", "isRequired": True}}
    remote["headers"] = [{"name": "Authorization", "value": "Bearer {api_key}",
                          "variables": {"api_key": {"isSecret": True, "isRequired": True}}},
                         {"name": "X-Region", "choices": ["eu", "us"], "default": "eu"}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["url"] == "https://{tenant}.notes.example.test/mcp"
    assert entry.install["headers"] == {"Authorization": "Bearer {api_key}", "X-Region": "{x_region}"}
    found = {item["key"]: item for item in entry.install["inputs"]}
    assert (found["api_key"]["secret"], found["api_key"]["required"], found["api_key"]["target"]) == (True, True, "header")
    assert (found["tenant"]["target"], found["tenant"]["secret"]) == ("url_variable", False)
    assert found["x_region"]["choices"] == ["eu", "us"] and found["x_region"]["default"] == "eu"
    assert entry.metadata["auth_mode"] == "api_key"
    cfg = marketplace.entry_to_server_config(entry)
    assert cfg["inputs"] == entry.install["inputs"] and cfg["source"]["auth_mode"] == "api_key"
    envelope["server"].pop("remotes")
    envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-notes", "version": "1.2.3",
        "transport": {"type": "stdio"}, "environmentVariables": [{"name": "NOTES_TOKEN", "isSecret": True, "isRequired": True}],
        "packageArguments": [{"type": "named", "name": "--workspace", "valueHint": "workspace"},
                             {"type": "positional", "value": "serve"}]}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["args"] == ["fixture-notes@1.2.3", "--workspace", "{workspace}", "serve"]
    assert entry.install["env"] == {"NOTES_TOKEN": "{notes_token}"}
    assert {i["key"]: i["flag"] for i in entry.install["inputs"]} == {"notes_token": "", "workspace": "--workspace"}


def test_package_runtimes_are_pinned_and_dozens_of_optional_settings_stay_at_their_defaults(envelope):
    envelope["server"].pop("remotes")
    envelope["server"]["packages"] = [{"registryType": "pypi", "identifier": "notes", "version": "1.2.3", "transport": {"type": "stdio"},
        "runtimeArguments": [{"type": "named", "name": "--from", "value": "notes[mcp]"}, {"type": "positional", "value": "notes-mcp"}]}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["args"] == ["--from", "notes[mcp]==1.2.3", "notes-mcp"]  # The record's own version, never unpinned.
    settings = [{"type": "named", "name": f"--option{n}", "description": "A tuning option"} for n in range(40)]
    envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-notes", "version": "1.2.3", "transport": {"type": "stdio"},
        "packageArguments": [*settings, {"type": "named", "name": "--workspace", "isRequired": True}],
        "environmentVariables": [{"name": f"NOTES_{n}", "format": "number"} for n in range(40)] + [{"name": "NOTES_KEY", "isRequired": True}]}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["args"] == ["fixture-notes@1.2.3", "--workspace", "{workspace}"]
    assert entry.install["env"] == {"NOTES_KEY": "{notes_key}"}
    assert [(i["key"], i["secret"], i["required"]) for i in entry.install["inputs"]] == [("notes_key", True, True), ("workspace", False, True)]


def test_a_credential_is_secret_by_its_name_even_when_its_listing_forgets_to_say(envelope):
    remote = envelope["server"]["remotes"][0]
    remote["headers"] = [{"name": "Authorization", "value": "Bearer {access_token}", "variables": {"access_token": {"isRequired": True}}}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["inputs"][0]["secret"] and entry.metadata["auth_mode"] == "api_key"
    envelope["server"].pop("remotes")
    envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-notes", "version": "1.2.3",
        "transport": {"type": "stdio"}, "packageArguments": [{"type": "named", "name": "--api-key", "valueHint": "your_value"}]}]
    # A key is never put on a command line, which any program on this computer can read. Secret by its flag,
    # whatever its hint says (and a bare secret flag is never a switch): an optional one is left out...
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["args"] == ["fixture-notes@1.2.3"] and not entry.install.get("inputs")
    envelope["server"]["packages"][0]["packageArguments"] = [{"type": "named", "name": "--apiSecret"}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install["args"] == ["fixture-notes@1.2.3"]
    # ...and one the server needs means Row-Bot doesn't offer the recipe.
    envelope["server"]["packages"][0]["packageArguments"] = [{"type": "named", "name": "--api-key", "isRequired": True}]
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install is None and "command line" in " ".join(entry.notes)


@pytest.mark.parametrize(("declaration", "reason"), [
    ({"packages": [{"registryType": "oci", "identifier": "ghcr.io/example/notes", "transport": {"type": "stdio"}}]}, "no fixed version"),
    ({"packages": [{"registryType": "oci", "identifier": "ghcr.io/example/notes:1.0.0", "transport": {"type": "stdio"},
                    "environmentVariables": [{"name": "DOCKER_HOST", "value": "tcp://203.0.113.5:2375"}]}]}, "unsupported"),
    ({"remotes": [{"type": "streamable-http", "url": "https://x.example.test/mcp",
                   "headers": [{"name": "payment-signature", "isRequired": True}]}]}, "x402"),
    ({"remotes": [{"type": "streamable-http", "url": "https://{host}/mcp", "variables": {"host": {}}}]}, "add it from a link"),
    ({"packages": [{"registryType": "oci", "identifier": "ghcr.io/example/notes:1.0.0", "transport": {"type": "stdio"},
                    "runtimeArguments": [{"type": "named", "name": "-v", "value": "/:/host"}]}]}, "Docker for access"),
    ({"packages": [{"registryType": "npm", "identifier": "fixture-notes", "version": "1.2.3", "transport": {"type": "stdio"},
                    "environmentVariables": [{"name": "NODE_OPTIONS", "value": "--require evil.js"}]}]}, "unsupported"),
])
def test_declarations_that_could_move_the_destination_or_reach_the_computer_stay_unsupported(envelope, declaration, reason):
    envelope["server"].pop("remotes")
    envelope["server"].update(declaration)
    (entry,) = marketplace.registry_entries({"servers": [envelope]})
    assert entry.install is None and reason in " ".join(entry.notes)


@pytest.mark.parametrize("change", ["headers", "auth", "variables", "environment", "runtime", "arguments", "registry", "hash", "unknown"])
def test_material_declarations_invalidate_pinned_review(envelope, monkeypatch, change):
    if change in {"environment", "runtime", "arguments", "registry", "hash"}:
        envelope["server"].pop("remotes")
        envelope["server"]["packages"] = [{"registryType": "npm", "identifier": "fixture-notes", "version": "1.2.3", "transport": {"type": "stdio"}}]
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    cfg = marketplace.entry_to_server_config(entry)
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: envelope)
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
    # Any change to what is declared needs a new review, even one Row-Bot could also express.
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_entry(entry)
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_configuration(cfg)
    if change in {"auth", "registry", "unknown", "arguments"}:
        assert marketplace.registry_entries({"servers": [envelope]})[0].install is None


def test_secret_header_is_never_copied_and_changed_value_invalidates(envelope, monkeypatch):
    headers = [{"name": "Authorization", "value": "synthetic-secret-alpha", "isSecret": True, "isRequired": True}]
    envelope["server"]["remotes"][0]["headers"] = headers
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    assert "synthetic-secret-alpha" not in json.dumps(asdict(entry))
    assert entry.install["headers"] == {"Authorization": "{authorization}"}  # The person supplies their own.
    assert entry.install["inputs"][0]["secret"] and not entry.install["inputs"][0]["default"]
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: envelope)
    headers[0]["value"] = "synthetic-secret-beta"
    with pytest.raises(ValueError, match="changed"):
        registry_snapshot.revalidate_entry(entry)
    assert "synthetic-secret" not in json.dumps(marketplace.entry_to_server_config(entry))


def test_legacy_snapshot_without_declaration_binding_cannot_authorize_import(envelope, monkeypatch):
    entry = marketplace.registry_entries({"servers": [envelope]})[0]
    entry.metadata.pop("setup_digest", None)
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a, **k: envelope)
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
    assert result["schema_version"] == registry_snapshot.SCHEMA
    assert result["entries"] and all(e.metadata["setup_digest"] for e in result["entries"])
