"""Guided setup projects canonical owner facts without executing integrations."""
# ruff: noqa: F811
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from row_bot.application import client_integrations, capability_configuration_controls as configuration
from row_bot.mcp_client import config
from tests.subsystem.client_protocol.test_integrations_api import isolated  # noqa: F401

pytestmark = pytest.mark.platform


@pytest.mark.parametrize(("source", "auth", "transport", "mode", "status"), [
    ({"auth_mode": "none"}, {}, "stdio", "none", "setup"),
    ({}, {}, "stdio", "unknown", "setup"),
    ({}, {}, "streamable_http", "unknown", "setup"),
    ({"auth_mode": "oauth", "requires_auth": True}, {}, "streamable_http", "oauth", "disconnected"),
    ({"auth_mode": "oauth"}, {"mode": "none"}, "streamable_http", "oauth", "disconnected"),
    ({"auth_mode": "api_key", "auth_bindings": [{"kind": "header", "name": "X-API-Key", "key": "token", "prefix": ""}]}, {}, "streamable_http", "api_key", "disconnected"),
    ({"auth_mode": "unsupported"}, {}, "streamable_http", "unsupported", "setup"),
])
def test_inventory_projects_actual_conditional_auth_without_credentials(isolated, source, auth, transport, mode, status):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Work"].update(transport=transport, source=source)
    if auth:
        document["servers"]["Work"]["auth"] = auth
    config.CONFIG_PATH.write_text(json.dumps(document))
    row = next(row for row in client_integrations.read_integrations(kind="mcp")["items"] if row["name"] == "Work")
    assert row["setup"]["auth_mode"] == mode
    assert row["status"] == status
    assert not row["setup"]["credential_configured"]
    assert "credential_ref" not in json.dumps(row)
    if mode == "api_key":
        assert row["setup"]["bindings"][0]["name"] == "X-API-Key"


@pytest.mark.parametrize(("optional", "child_enabled", "runtime", "expected"), [
    (False, False, "connected", "setup"),
    (False, True, "failed", "attention"),
    (False, True, "connected", "ready"),
    (True, False, "connected", "ready"),
])
def test_required_child_overrides_healthy_parent_and_optional_is_explicit(isolated, monkeypatch, optional, child_enabled, runtime, expected):
    from row_bot.application import plugin_commands
    from row_bot.plugins import state
    server_id = configuration.read_mcp_configuration().items[0].server_id
    page = configuration.read_mcp_configuration()
    server = replace(page.items[0], enabled=child_enabled, runtime_status=runtime)
    monkeypatch.setattr(configuration, "read_mcp_configuration", lambda **_: replace(page, items=(server,)))
    monkeypatch.setattr(config, "read_saved_configuration", lambda: SimpleNamespace(document={"enabled": True, "servers": {"Work": {"transport": "streamable_http", "tools": {"catalog": {}}, "enabled": child_enabled}}}))
    monkeypatch.setattr(state, "get_mcp_child_overrides", lambda *_: {"enabled": child_enabled})
    monkeypatch.setattr(plugin_commands, "read_integration_packages", lambda **_: [{"plugin_id": "fixture-package", "installed": True, "enabled": True, "health": "passed", "setup_complete": True, "name": "Fixture package", "description": "Fixture", "package_format": "agent-plugins-1.0.0", "version": "1", "capabilities": {}, "children": [{"kind": "mcp", "owner_ref": server_id, "name": "Work", "server_key": "work", "optional": optional}]}])
    row = client_integrations.read_integration("plugin:fixture-package")
    assert row["status"] == expected
    assert row["children"][0]["required"] is not optional
    if not child_enabled:
        assert any("Work" in reason for reason in row["reasons"])


def test_skill_preview_contains_complete_instructions_scripts_and_review_identity(isolated, monkeypatch):
    from row_bot.application import client_skill_hub as hub
    from row_bot.skills_hub.models import SkillFile
    from tests.subsystem.client_protocol.test_skill_hub_api import _fake_catalog, _bundle
    _fake_catalog(monkeypatch)
    bundle = _bundle()
    long_text = bundle.files[0].text + "Full instructions. " * 1000 + "END OF INSTRUCTIONS"
    bundle.files = [SkillFile.from_text("SKILL.md", long_text), SkillFile.from_text("scripts/check.py", "print('reviewed fixture')"), SkillFile.from_bytes("image.png", b"\x89PNG\x00")]
    bundle.metadata.update(version="v3", audit="upstream scan; not a guarantee")
    bundle.frontmatter["allowed-tools"] = "Read"
    monkeypatch.setattr(hub.catalog, "inspect_entry", lambda _: bundle)
    search = hub.search_public_skills(owner_id="review", query="sample")
    preview = hub.preview_public_skill(owner_id="review", revision=search["revision"], entry_id="fixture:sample")
    assert preview["review_files"][0]["text"].endswith("END OF INSTRUCTIONS")
    assert preview["review_files"][1]["executable"]
    assert preview["review_files"][1]["text"] == "print('reviewed fixture')"
    assert preview["review_files"][2]["text"] is None
    assert preview["version"] == "v3" and preview["requirements"] == ["Read"]
    assert "upstream scan" in str(preview["provenance"])
    assert preview["content_hash"] == bundle.content_hash


def test_invalid_declared_bindings_are_blocked_without_poisoning_inventory(isolated):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Work"]["source"] = {"auth_mode": "api_key", "auth_bindings": [{"kind": "env", "name": "PATH", "key": "token"}]}
    config.CONFIG_PATH.write_text(json.dumps(document))
    row = next(row for row in client_integrations.read_integrations(kind="mcp")["items"] if row["name"] == "Work")
    assert row["setup"]["auth_mode"] == "unsupported"
    assert row["setup"]["bindings"] == []
    assert row["status"] == "setup"
