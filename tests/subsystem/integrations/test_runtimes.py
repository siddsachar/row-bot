"""Runtimes in a plan: nothing runs before consent, the exact lock is reviewed in place before anything
is installed, and the installed package is bound to that lock. Faked tools and registry only."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from uuid import uuid4

import pytest

from row_bot.application import client_integrations as api
from row_bot.integrations import facts, plans
from row_bot.mcp_client import config, packages
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.mcp.test_bundles import signers  # noqa: F401
from tests.subsystem.mcp.test_integration_packages import NPM, Tools, self_contained

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]


def context(**fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=True, **fields)


@pytest.fixture
def npm_server(owner, tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "mcp_packages").mkdir(exist_ok=True)
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"] = {**NPM, "enabled": False}
    config.CONFIG_PATH.write_text(json.dumps(document))
    from row_bot.mcp_client import requirements
    monkeypatch.setattr(requirements, "check_requirement",
                        lambda requirement, env=None: requirements.RuntimeCheck(requirement=requirement, available=True))
    tools = Tools(monkeypatch, tmp_path)
    fetched = self_contained(monkeypatch)
    facts.invalidate()
    return tools, fetched


def item():
    return next(row for row in facts.inventory()[0] if row["name"] == "Synthetic")["id"]


def test_the_exact_lock_is_reviewed_before_anything_is_installed(npm_server):
    tools, fetched = npm_server
    _, plan = api.read_item(owner_id="owner", item_id=item())
    step = next(s for s in plan["steps"] if s["type"] == "runtime")
    assert step["runtime"]["id"] == "npm_package" and not fetched and not tools.calls  # Reading a plan contacts nothing.
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item(), digest=plan["digest"])
    step = next(s for s in paused["steps"] if s["type"] == "runtime")
    assert paused["pause"] == "digest_changed" and step["state"] == "waiting"
    assert step["review"]["items"][0]["name"] == "fixture-mcp" and step["review"]["items"][0]["integrity"].startswith("sha512-")
    assert len(fetched) == 1 and not list((packages._folder("x").parent).glob("[0-9a-f]" * 32))  # Metadata only; nothing laid out.
    again = plans.resume(context(), plan_id)  # Continuing without what was shown changes nothing.
    assert again["pause"] == "digest_changed" and len(fetched) == 1
    done = plans.resume(context(review_digest=step["review"]["digest"]), plan_id)
    assert done["pause"] == "access", done
    launch = config.read_saved_configuration().document["servers"]["Synthetic"]["managed_launch"]
    assert launch["lock_digest"] == step["review"]["digest"] and launch["integrity"] == step["review"]["items"][0]["integrity"]
    assert not tools.calls  # A self-contained package never runs npm at all.


def test_a_container_app_needs_docker_on_this_computer(owner, monkeypatch):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Synthetic"] = {"transport": "stdio", "command": "docker", "enabled": False,
                                        "args": ["run", "-i", "--rm", "ghcr.io/example/notes:1.0.0"]}
    config.CONFIG_PATH.write_text(json.dumps(document))
    monkeypatch.setattr("row_bot.mcp_client.requirements.shutil.which", lambda *a, **k: None)
    facts.invalidate()
    _, plan = api.read_item(owner_id="owner", item_id=item())
    assert not plan["supported"] and "Install Docker Desktop" in plan["unsupported_reason"]


def test_a_desktop_app_is_looked_for_only_on_this_computer(monkeypatch):
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    reached = []
    original = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", lambda address, **k: reached.append(address) or original(address, **k))
    try:
        assert plans.local_app_open({"port": port})
    finally:
        listener.close()
    assert not plans.local_app_open({"port": port})
    assert {host for host, _ in reached} == {"127.0.0.1"}


def test_a_package_for_a_desktop_app_waits_until_the_app_is_open(monkeypatch):
    from row_bot.integrations import apps
    row = facts.finish(facts.entry("plugin", "hermes:blender", "Blender", installed=False, lifecycle="available",
                                   app=apps.catalog()[0]["blender"].ref()))
    plan = plans.compute(row, {"kind": "plugin", "reference": "hermes:blender"})
    step = next(s for s in plan["steps"] if s["type"] == "local_app_check")
    assert step["state"] == "pending" and step["local_app"]["label"] == "Blender 5.1 with its MCP add-on"
    record = {**plan, "app_id": "blender"}
    monkeypatch.setattr(plans, "local_app_open", lambda check: check == {"port": 9876} and False)
    assert plans._local_app(context(), record, step) == "resume" and "check again" in step["message"]
    monkeypatch.setattr(plans, "local_app_open", lambda check: True)
    assert plans._local_app(context(), record, step) == "done"


SERVER = """
const lines = require('readline').createInterface({input: process.stdin});
lines.on('line', (line) => {
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({jsonrpc: '2.0', id: request.id, result: {
    protocolVersion: '2025-11-25', capabilities: {tools: {}}, serverInfo: {name: 'fixture', version: process.env.NOTES_TOKEN}}}) + String.fromCharCode(10));
  process.exit(0);
});
"""


@pytest.fixture
def bundled(owner, tmp_path, monkeypatch):
    from tests.subsystem.mcp.test_bundles import archive
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "mcp_packages").mkdir(exist_ok=True)
    from row_bot.mcp_client import requirements
    monkeypatch.setattr(requirements, "check_requirement",
                        lambda requirement, env=None: requirements.RuntimeCheck(requirement=requirement, available=True))
    from row_bot import secret_store
    from tests.subsystem.plugins.conftest import MemoryKeyring
    secret_store._set_backend_for_tests(MemoryKeyring())
    yield lambda signer=None: archive(files={"server/index.js": SERVER, "server/lib/README": "bundled"})
    secret_store._set_backend_for_tests(None)


def test_a_picked_bundle_is_reviewed_then_unpacked_privately_and_asks_for_its_settings(bundled, tmp_path):
    page = api.upload_file(owner_id="owner", data=bundled(), filename="notes.mcpb")
    (row,) = page["items"]
    assert row["method"] == "local" and row["compatibility"] != "unsupported"
    _, plan = api.read_item(owner_id="owner", item_id=row["id"], revision=page["revision"])
    types = [(s["type"], (s.get("runtime") or {}).get("id")) for s in plan["steps"]]
    assert ("runtime", "mcpb") in types and ("inputs", None) in types and plan["supported"]
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=row["id"], revision=page["revision"], digest=plan["digest"])
    assert paused["pause"] == "digest_changed", paused
    review = next(s for s in paused["steps"] if s["type"] == "runtime")["review"]
    assert "Not signed" in review["lines"][0]
    assert review["items"][0]["integrity"].startswith("sha256:")
    assert not [p for p in (tmp_path / "mcp_packages").iterdir() if len(p.name) == 32]  # Nothing unpacked yet.
    asking = plans.resume(context(review_digest=review["digest"]), plan_id)
    assert asking["pause"] == "inputs", asking
    done = plans.resume(context(inputs={"api_key": "synthetic-bundle-key", "folder": str(tmp_path)}), plan_id)
    assert done["pause"] == "access", done
    saved = next(cfg for name, cfg in config.read_saved_configuration().document["servers"].items() if cfg.get("bundle"))
    launch = saved["managed_launch"]
    root = tmp_path / "mcp_packages" / launch["id"]
    assert launch["kind"] == "mcpb" and (root / "server/index.js").is_file()
    assert "synthetic-bundle-key" not in config.CONFIG_PATH.read_text()
    from row_bot.mcp_client import requirements
    requirements_node = requirements.managed_command_path
    requirements.managed_command_path = lambda runtime, command: "managed-node"
    try:
        command, args = packages.resolve_launch(saved, args=[str(a) for a in saved["args"]])
    finally:
        requirements.managed_command_path = requirements_node
    assert command == "managed-node" and args[0] == str(root) + "/server/index.js"
    (root / "server/index.js").write_text("// changed after review")
    with pytest.raises(ValueError, match="integrity_changed"):
        packages.resolve_launch(saved)


def test_a_bundle_changed_after_signing_is_refused_when_picked(owner, tmp_path, monkeypatch, signers):
    from row_bot.application.client_platform import ClientPlatformError
    from tests.subsystem.mcp.test_bundles import archive, sign
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    signed = bytearray(sign(archive(), signers["ec"]))
    signed[60] ^= 0xFF  # One changed byte inside the signed archive.
    with pytest.raises(ClientPlatformError, match="bundle_signature_invalid"):
        api.upload_file(owner_id="owner", data=bytes(signed), filename="notes.mcpb")


@pytest.mark.slow
def test_a_bundle_runs_with_node_from_its_private_folder(bundled, tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not on this computer")
    page = api.upload_file(owner_id="owner", data=bundled(), filename="notes.mcpb")
    (row,) = page["items"]
    _, plan = api.read_item(owner_id="owner", item_id=row["id"], revision=page["revision"])
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=row["id"], revision=page["revision"], digest=plan["digest"])
    review = next(s for s in paused["steps"] if s["type"] == "runtime")["review"]
    plans.resume(context(review_digest=review["digest"]), plan_id)
    saved = next(cfg for cfg in config.read_saved_configuration().document["servers"].values() if cfg.get("bundle"))
    command, args = packages.resolve_launch(saved, args=[str(a).replace("{folder}", str(tmp_path)) for a in saved["args"]])
    result = subprocess.run([command, *args], input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n', capture_output=True,
                            text=True, timeout=30, env={**{key: value for key, value in __import__("os").environ.items()
                                                       if key in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}, "NOTES_TOKEN": "ok"})
    assert result.returncode == 0 and result.stdout, result.stderr
    assert json.loads(result.stdout.splitlines()[0])["result"]["serverInfo"]["version"] == "ok"


def test_a_hermes_recipe_is_read_after_consent_and_shown_before_it_is_saved(owner, monkeypatch):
    from row_bot.mcp_client import requirements
    from row_bot.plugins import hermes_mcp
    monkeypatch.setattr(requirements, "check_requirement",
                        lambda requirement, env=None: requirements.RuntimeCheck(requirement=requirement, available=True))
    reads = []
    recipe = {"manifest_version": 1, "name": "notes", "transport": {"type": "http", "url": "https://mcp.notes.example.test/mcp"},
              "auth": {"type": "none"}}
    monkeypatch.setattr(hermes_mcp, "read_recipe", lambda name, pin: reads.append((name, pin)) or hermes_mcp.normalize_recipe(
        recipe, name=name, pin=pin, source_url="https://github.com/NousResearch/hermes-agent"))
    monkeypatch.setattr("row_bot.mcp_client.auth.discover_sign_in", lambda url: {"required": False})
    row = facts.finish(facts.entry("mcp", "hermes_mcp:notes", "notes", installed=False, lifecycle="available"))
    reference = {"kind": "hermes_mcp", "name": "notes", "pin": "e" * 40}
    plan = plans.compute(row, reference)
    assert plan["supported"] and not reads  # Nothing is read until the person agrees.
    with pytest.raises(plans.PlanError, match="owner_local_only"):  # Its package is set up on this computer only.
        plans.start(plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=False),
                    row, reference, digest=plan["digest"], plan_id=str(uuid4()))
    assert not reads
    plan_id = str(uuid4())
    paused = plans.start(context(), row, reference, digest=plan["digest"], plan_id=plan_id)
    review = next(s for s in paused["steps"] if s["type"] == "consent")["review"]
    assert paused["pause"] == "digest_changed" and review["lines"] == ["Connects to https://mcp.notes.example.test/mcp."]
    assert not config.read_saved_configuration().document["servers"].get("notes")  # Shown before anything is saved.
    done = plans.resume(context(review_digest=review["digest"]), plan_id)
    assert done["pause"] == "access", done
    assert config.read_saved_configuration().document["servers"]["notes"]["source"]["pin"] == "e" * 40
    assert reads == [("notes", "e" * 40)]


def test_a_recipes_keys_become_declared_inputs():
    from row_bot.plugins import hermes_mcp
    recipe = {"manifest_version": 1, "name": "notes", "auth": {"type": "api_key", "env": [{"name": "NOTES_TOKEN"}]},
              "transport": {"type": "stdio", "command": "npx", "args": ["-y", "notes-mcp@1.2.3"]}}
    cfg = json.loads(hermes_mcp.normalize_recipe(recipe, name="notes", pin="e" * 40, source_url="")["import_json"])["mcpServers"]["notes"]
    assert cfg["env"] == {"NOTES_TOKEN": "{notes_token}"} and "auth" not in cfg
    assert [(i["key"], i["secret"], i["target"]) for i in cfg["inputs"]] == [("notes_token", True, "env")]


def _registry_bundle(data: bytes, digest: str | None = None) -> tuple[dict, dict]:
    """A Registry record publishing an MCP bundle, as its catalog row and reference."""
    import hashlib
    from row_bot.integrations import sources
    from row_bot.mcp_client import marketplace
    record = {"server": {"name": "io.github.fixture/notes", "description": "Reads notes.", "version": "1.2.0",
                         "packages": [{"registryType": "mcpb", "version": "1.2.0", "transport": {"type": "stdio"},
                                       "identifier": "https://github.com/fixture/notes/releases/download/v1.2.0/notes.mcpb",
                                       "fileSha256": digest or hashlib.sha256(data).hexdigest()}]},
              "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "isLatest": True,
                                                                        "updatedAt": "2026-10-01T00:00:00Z"}}}
    (entry,) = marketplace.registry_entries({"servers": [record]})
    row, reference = sources.SOURCES["official"].row(entry)
    return facts.finish(row), reference


def test_a_registry_bundle_is_downloaded_after_consent_and_used_only_if_it_matches_its_checksum(bundled, tmp_path,
                                                                                                 monkeypatch):
    from row_bot.integrations import safe
    from row_bot.mcp_client import registry_snapshot
    data = bundled()
    row, reference = _registry_bundle(data)
    assert reference["entry"].install["bundle"]["sha256"].startswith("sha256:") and row["compatibility"] != "unsupported"
    plan = plans.compute(row, reference)
    assert plan["supported"] and "checked against its Registry checksum" in plan["consent"]["downloads"][0]
    fetched: list[str] = []
    monkeypatch.setattr(safe, "fetch", lambda url, **kwargs: fetched.append(url) or data)
    monkeypatch.setattr(registry_snapshot, "revalidate_entry", lambda entry: entry)
    assert fetched == []  # Reading the item downloaded nothing.
    plan_id = str(uuid4())
    paused = plans.start(context(), row, reference, digest=plan["digest"], plan_id=plan_id)
    assert fetched == ["https://github.com/fixture/notes/releases/download/v1.2.0/notes.mcpb"]
    assert paused["pause"] == "digest_changed", paused  # The bundle's own reviewed install comes next.
    review = next(s for s in paused["steps"] if s["type"] == "runtime")["review"]
    assert review["items"][0]["integrity"] == reference["entry"].install["bundle"]["sha256"]
    asking = plans.resume(context(review_digest=review["digest"]), plan_id)
    assert asking["pause"] == "inputs", asking  # Its manifest's settings.
    saved = next(cfg for cfg in config.read_saved_configuration().document["servers"].values() if cfg.get("bundle"))
    assert saved["bundle"]["url"].endswith("notes.mcpb") and saved["source"]["marketplace"] == "official"


def test_a_registry_bundle_that_does_not_match_its_checksum_is_never_used(bundled, tmp_path, monkeypatch):
    from row_bot.integrations import safe
    from row_bot.mcp_client import registry_snapshot
    data = bundled()
    row, reference = _registry_bundle(data, digest="0" * 64)
    plan = plans.compute(row, reference)
    monkeypatch.setattr(safe, "fetch", lambda url, **kwargs: data)
    monkeypatch.setattr(registry_snapshot, "revalidate_entry", lambda entry: entry)
    ended = plans.start(context(), row, reference, digest=plan["digest"], plan_id=str(uuid4()))
    consent = next(step for step in ended["steps"] if step["type"] == "consent")
    assert ended["state"] == "failed" and "didn't match the checksum" in consent["message"], ended
    assert not [cfg for cfg in config.read_saved_configuration().document["servers"].values() if cfg.get("bundle")]
    assert not [path for path in (tmp_path / "mcp_packages").iterdir() if len(path.name) == 32]


@pytest.mark.parametrize("change", [{"fileSha256": ""}, {"fileSha256": "abc"}, {"identifier": "http://example.test/notes.mcpb"},
                                    {"identifier": "https://user:pass@example.test/notes.mcpb"}, {"runtimeHint": "node"}])
def test_a_registry_bundle_without_an_exact_checksum_or_https_address_is_not_offered(change):
    from row_bot.mcp_client import marketplace
    package = {"registryType": "mcpb", "version": "1.0.0", "transport": {"type": "stdio"},
               "identifier": "https://example.test/notes.mcpb", "fileSha256": "a" * 64, **change}
    record = {"server": {"name": "io.github.fixture/notes", "version": "1.0.0", "packages": [package]},
              "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "isLatest": True}}}
    (entry,) = marketplace.registry_entries({"servers": [record]})
    assert entry.install is None and "add it from a file" in entry.notes[0]
