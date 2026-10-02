"""Bundle setup uses the existing MCP owners and stays out of standalone config."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from row_bot.application import capability_configuration_controls as configuration
from row_bot.application import capability_policy_controls as policy
from row_bot.application import capability_runtime_controls as runtime_controls
from row_bot.application import client_mcp_auth as auth_owner
from row_bot.mcp_client import config, targets
from row_bot.plugins.portable import package_id
from tests.subsystem.plugins.test_portable_packages import package, SOURCE

pytestmark = pytest.mark.platform


@pytest.fixture
def child(plugin_modules, tmp_path, reload_for_data_dir):
    root = plugin_modules["state"].DATA_DIR
    reload_for_data_dir(root, "row_bot.tasks", "row_bot.mcp_client.config")
    source = package(tmp_path / "package")
    identity = package_id(SOURCE, "example.notes")
    assert plugin_modules["installer"].install_plugin(identity, source_dir=source, source_ref=SOURCE).success
    return {"kind": "plugin", "plugin_id": identity, "server_key": "notes"}


def test_off_child_can_be_reviewed_and_configured_without_enabling_parent_or_siblings(child, plugin_modules):
    page = configuration.read_mcp_configuration(target=child)
    assert page.enabled is False and page.items[0].enabled is False
    server_id = page.items[0].server_id
    reviewed = runtime_controls.review_mcp_runtime_command(page.revision, server_id, "test", None,
        target=child, validate=lambda: None)
    assert reviewed["operation"] == "test"
    with pytest.raises(runtime_controls.CapabilityRuntimeError, match="disabled"):
        runtime_controls.review_mcp_runtime_command(page.revision, server_id, "connect", None, target=child, validate=lambda: None)
    result = auth_owner.execute_auth(owner_id="fixture", command_id=str(uuid4()), server_id=server_id,
        configuration_revision=page.revision, target=child, action="start", mode="api_key", label="Work",
        bindings=[{"kind": "header", "name": "Authorization", "key": "token", "prefix": "Bearer "}], values={"token": "fixture-secret"})
    assert result["state"] == "signed_in"
    assert not config.CONFIG_PATH.exists()
    state = plugin_modules["state"]
    assert not state.is_plugin_enabled(child["plugin_id"])
    assert "fixture-secret" not in state._STATE_PATH.read_text()
    assert state.get_mcp_child_overrides(child["plugin_id"], "notes")["auth"]["label"] == "Work"
    from row_bot.application.plugin_commands import read_plugin_detail
    assert read_plugin_detail(child["plugin_id"], validate=lambda: None)["enabled"] is False


def test_child_policy_cannot_change_parent_switch_or_immutable_source(child):
    page = configuration.read_mcp_configuration(target=child)
    def execute(intent):
        command = {"command_id": str(uuid4()), "type": "mcp.configuration.save", "expected_revision": "0",
            "payload": {"target": child, "configuration_revision": page.revision, "intent": intent}}
        return configuration.execute_mcp_configuration_command(owner_id="fixture", key=command["command_id"],
            command=command, validate=lambda: None, validate_review=lambda _: None)
    with pytest.raises(config.McpConfigurationError, match="plugin_child_source_immutable"):
        execute({"operation": "edit", "server_id": page.items[0].server_id,
            "fields": {"url": "https://different.example.test/mcp"}})
    with targets.scope(child):
        assert next(iter(config.read_saved_configuration().document["servers"].values()))["url"] == "https://example.test/mcp"
    assert not config.CONFIG_PATH.exists()


def test_parent_disable_or_source_update_invalidates_child_review(child, plugin_modules, tmp_path):
    page = configuration.read_mcp_configuration(target=child)
    plugin_modules["state"].set_plugin_enabled(child["plugin_id"], True)
    with pytest.raises(runtime_controls.CapabilityRuntimeError, match="revision_conflict"):
        runtime_controls.review_mcp_runtime_command(page.revision, page.items[0].server_id, "test", None,
            target=child, validate=lambda: None)
    page = configuration.read_mcp_configuration(target=child)
    source = package(tmp_path / "new-package", version="preview-2")
    assert plugin_modules["installer"].update_plugin(child["plugin_id"], source_dir=source, source_ref=SOURCE).success
    with pytest.raises(runtime_controls.CapabilityRuntimeError, match="revision_conflict"):
        runtime_controls.review_mcp_runtime_command(page.revision, page.items[0].server_id, "test", None,
            target=child, validate=lambda: None)
