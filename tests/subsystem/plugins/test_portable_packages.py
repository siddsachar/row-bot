from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.plugins.manifest import ManifestError, parse_manifest
from row_bot.plugins.portable import MCP_SCHEMA, SCHEMA, package_id

pytestmark = pytest.mark.platform
SOURCE = "https://github.com/example/portable#package"


def package(root: Path, *, version: str = "preview-1") -> Path:
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"$schema": SCHEMA, "name": "example.notes", "version": version, "future": True}), encoding="utf-8")
    skill = root / "skills" / "summarize"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: summarize\ndescription: Summarize a provided document.\n---\nRead references/guide.md.\n", encoding="utf-8")
    (skill / "references").mkdir()
    (skill / "references" / "guide.md").write_bytes(b"Keep facts intact.\n")
    (root / "plugin_main.py").write_text("raise AssertionError('foreign entry point ran')", encoding="utf-8")
    (root / "mcp.json").write_text(json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {
        "notes": {"type": "streamable-http", "url": "https://example.test/mcp", "headers": {"X-Literal": "${PLUGIN_ROOT}"}},
        "invalid": {"type": "stdio", "command": "sh -c 'bad'"}}}), encoding="utf-8")
    return root


def test_portable_components_have_independent_failure_boundaries(tmp_path, plugin_modules):
    source = package(tmp_path / "source")
    manifest = parse_manifest(source, source_identity=SOURCE)
    assert manifest.id == package_id(SOURCE, "example.notes")
    assert manifest.version == "preview-1" and manifest.license == ""
    assert manifest.package_format == "agent-plugins-1.0.0"
    assert [v["id"] for v in manifest.provides.mcp_servers] == ["notes"]
    assert len(manifest.provides.skills) == 1
    assert {v["component"] for v in manifest.diagnostics} == {"manifest", "mcp/invalid"}
    raw = json.loads((source / "plugin.json").read_text())
    raw["version"] = 12
    (source / "plugin.json").write_text(json.dumps(raw))
    with pytest.raises(ManifestError, match="version"):
        parse_manifest(source, source_identity=SOURCE)


def test_inventory_keeps_incomplete_children_visible_without_claiming_bundle_ready(
        tmp_path, plugin_modules, monkeypatch, reload_for_data_dir):
    from row_bot.application.client_integrations import read_integration
    from row_bot.mcp_client import runtime

    installer, state = (plugin_modules[k] for k in ("installer", "state"))
    reload_for_data_dir(installer.DATA_DIR, "row_bot.skills", "row_bot.mcp_client.config", "row_bot.tasks")
    monkeypatch.setattr(runtime, "get_passive_server_statuses", lambda names: {})
    source = package(tmp_path / "source")
    identity = package_id(SOURCE, "example.notes")
    assert installer.install_plugin(identity, source_dir=source, source_ref=SOURCE).success
    state.set_plugin_health_result(identity, ok=True, checks=[])
    state.set_plugin_enabled(identity, True)
    def contents():
        # SQLite may create its read-lock sidecars even for mode=ro connections.
        return {path: path.read_bytes() for path in installer.DATA_DIR.rglob("*")
            if path.is_file() and not path.name.endswith((".db-shm", ".db-wal"))}
    before = contents()
    row = read_integration("plugin:" + identity)
    # A skipped component is informational: the package is partly supported, and
    # only the included connection that still needs setup holds it back.
    assert row["status"] == "setup" and row["compatibility"] == "partial"
    assert next(child for child in row["children"] if child["kind"] == "skill")["status"] == "ready"
    assert next(child for child in row["children"] if child["kind"] == "mcp")["status"] == "setup"
    assert any("notes" in reason and "accept" in reason for reason in row["reasons"])
    assert before == contents()

    target = {"kind": "plugin", "plugin_id": identity, "server_key": "notes"}
    from row_bot.application import capability_configuration_controls as configuration
    from row_bot.application import capability_policy_controls as policy
    page = configuration.read_mcp_configuration(target=target)
    command = {"command_id": str(uuid4()), "type": "mcp.configuration.control", "expected_revision": "0",
        "payload": {"target": target, "configuration_revision": page.revision,
            "intent": {"operation": "server_enabled", "server_id": page.items[0].server_id, "enabled": False}}}
    receipt = policy.execute_mcp_policy_command(owner_id="fixture", key=command["command_id"], command=command,
        validate=lambda: None, validate_review=lambda _: None)
    assert receipt["status"] == "completed"
    row = read_integration("plugin:" + identity)
    assert row["status"] == "setup"
    assert next(child for child in row["children"] if child["kind"] == "mcp")["status"] == "off"
    state.set_plugin_enabled(identity, False)
    assert read_integration("plugin:" + identity)["status"] == "off"


def test_portable_lifecycle_preserves_bytes_identity_data_and_enablement(tmp_path, plugin_modules):
    installer, state, loader, registry = (plugin_modules[k] for k in ("installer", "state", "loader", "registry"))
    source = package(tmp_path / "first")
    identity = package_id(SOURCE, "example.notes")
    original = (source / "plugin.json").read_bytes()
    assert installer.install_plugin(identity, source_dir=source, source_ref=SOURCE).success
    dest = installer.PLUGINS_DIR / identity
    assert (dest / "plugin.json").read_bytes() == original
    assert loader.classify_stale_legacy_plugin(dest) is None
    state.set_plugin_enabled(identity, True)
    result = next(result for result in loader.load_plugins() if result.plugin_id == identity)
    assert result.success, result.error
    assert "Read references/guide.md" in registry.get_skills_prompt()
    from row_bot.plugins.mcp import plugin_mcp_servers
    servers = plugin_mcp_servers()
    assert len(servers) == 1
    assert next(iter(servers.values()))["headers"] == {"X-Literal": "${PLUGIN_ROOT}"}
    data = installer.DATA_DIR / "plugin_data" / identity
    data.mkdir(parents=True)
    (data / "note.txt").write_text("keep")
    second = package(tmp_path / "second", version="preview-2")
    assert installer.update_plugin(identity, source_dir=second, source_ref=SOURCE).success
    assert state.is_plugin_enabled(identity)
    assert parse_manifest(dest).id == identity
    assert installer.restore_plugin(identity).success
    assert parse_manifest(dest).version == "preview-1"
    assert (data / "note.txt").read_text() == "keep"
    state.set_plugin_enabled(identity, False)
    assert not registry.get_skills_prompt() and not plugin_mcp_servers()
    assert installer.uninstall_plugin(identity).success
    assert (data / "note.txt").read_text() == "keep"


def test_failed_preparation_keeps_working_package(tmp_path, plugin_modules):
    installer = plugin_modules["installer"]
    identity = package_id(SOURCE, "example.notes")
    source = package(tmp_path / "source")
    assert installer.install_plugin(identity, source_dir=source, source_ref=SOURCE).success
    bad = package(tmp_path / "bad")
    (bad / "plugin.json").write_text("{}")
    assert not installer.update_plugin(identity, source_dir=bad, source_ref=SOURCE).success
    assert parse_manifest(installer.PLUGINS_DIR / identity).version == "preview-1"


def test_state_failure_after_publish_recovers_without_reacquisition(tmp_path, plugin_modules, monkeypatch):
    installer, state = plugin_modules["installer"], plugin_modules["state"]
    identity = package_id(SOURCE, "example.notes")
    source = package(tmp_path / "source")
    publish = state.publish_plugin_package
    monkeypatch.setattr(state, "publish_plugin_package", lambda *a, **kw: (_ for _ in ()).throw(OSError("injected")))
    assert not installer.install_plugin(identity, source_dir=source, source_ref=SOURCE).success
    monkeypatch.setattr(state, "publish_plugin_package", publish)
    monkeypatch.setattr(installer, "_prepare_package", lambda *a, **kw: pytest.fail("must not replay acquisition"))
    assert installer.recover_plugin_publication(identity).success
    assert not state.is_plugin_enabled(identity)
    assert parse_manifest(installer.PLUGINS_DIR / identity).id == identity


def test_withdrawn_source_cannot_be_activated_and_keeps_installed_files(tmp_path, plugin_modules, monkeypatch):
    from row_bot.plugins import hermes_catalog
    installer, state = plugin_modules["installer"], plugin_modules["state"]
    identity = package_id(SOURCE, "example.notes")
    assert installer.install_plugin(identity, source_dir=package(tmp_path / "source"), source_ref=SOURCE).success
    monkeypatch.setattr(hermes_catalog, "read_catalog", lambda: {"removed": [{"repo": SOURCE.split("#")[0], "reason": "Revoked"}]})
    with pytest.raises(ValueError, match="package_source_removed"):
        state.set_plugin_enabled(identity, True)
    assert not state.is_plugin_enabled(identity)
    assert (installer.PLUGINS_DIR / identity / "plugin.json").is_file()


def test_subdirectory_update_source_preserves_parent_and_child_identities(tmp_path, plugin_modules, monkeypatch):
    import io
    import zipfile
    from row_bot.application.plugin_commands import read_integration_packages
    from row_bot.plugins import hermes_catalog

    installer = plugin_modules["installer"]
    identity = package_id(SOURCE, "example.notes")
    assert installer.install_plugin(identity, source_dir=package(tmp_path / "first"), source_ref=SOURCE).success
    row = next(row for row in read_integration_packages(validate=lambda: None) if row["plugin_id"] == identity)
    assert row["source_url"] == "https://github.com/example/portable/tree/HEAD/package"
    updated = package(tmp_path / "updated", version="preview-2")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in updated.rglob("*"):
            if path.is_file():
                archive.writestr("repo-pin/package/" + path.relative_to(updated).as_posix(), path.read_bytes())
    calls = []
    def download(url, **kwargs):
        calls.append(url)
        if url == "https://api.github.com/repos/example/portable/commits/HEAD":
            return json.dumps({"sha": "b" * 40}).encode()
        assert url == "https://codeload.github.com/example/portable/zip/" + "b" * 40
        return buffer.getvalue()
    monkeypatch.setattr(hermes_catalog, "_public_bytes", download)
    preview = hermes_catalog.inspect_package(owner_id="fixture", reference=row["source_url"])
    assert preview["plugin_id"] == identity and preview["version"] == "preview-2"
    assert len(calls) == 2
    staged = hermes_catalog.get_preview("fixture", preview["preview_id"])
    assert staged.source_identity == SOURCE
    assert installer.update_plugin(identity, source_dir=staged.root, source_ref=staged.source_identity).success
    after = next(row for row in read_integration_packages(validate=lambda: None) if row["plugin_id"] == identity)
    assert after["children"] == row["children"]
    assert after["recoverable"]
