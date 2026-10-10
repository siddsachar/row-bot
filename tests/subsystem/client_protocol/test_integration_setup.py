"""Guided setup projects canonical owner facts without executing integrations."""
# ruff: noqa: F811
import json

import pytest

from row_bot.application import client_integrations, capability_configuration_controls as configuration
from row_bot.integrations import facts
from row_bot.mcp_client import config
from tests.subsystem.client_protocol.test_integrations_api import isolated  # noqa: F401

pytestmark = pytest.mark.platform


def work():
    """The typed entry for the Work connection, and the setup facts its owner reads (never a secret)."""
    row = next(row for row in client_integrations.read_items(owner_id="local", kind="mcp")["items"] if row["name"] == "Work")
    return row, facts.read(row["id"])["setup"]


@pytest.mark.parametrize(("source", "auth", "transport", "mode", "readiness", "method"), [
    ({"auth_mode": "none"}, {}, "stdio", "none", "needs_setup", "local"),
    ({}, {}, "stdio", "unknown", "needs_setup", "local"),
    ({}, {}, "streamable_http", "unknown", "needs_setup", "hosted"),
    ({"auth_mode": "oauth", "requires_auth": True}, {}, "streamable_http", "oauth", "needs_sign_in", "hosted_sign_in"),
    ({"auth_mode": "oauth"}, {"mode": "none"}, "streamable_http", "oauth", "needs_sign_in", "hosted_sign_in"),
    ({"auth_mode": "api_key", "auth_bindings": [{"kind": "header", "name": "X-API-Key", "key": "token", "prefix": ""}]}, {}, "streamable_http", "api_key", "needs_key", "api_key"),
    ({"auth_mode": "unsupported"}, {}, "streamable_http", "unsupported", "needs_setup", "hosted"),
])
def test_inventory_projects_actual_conditional_auth_without_credentials(isolated, source, auth, transport, mode, readiness, method):
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Work"].update(transport=transport, source=source)
    if auth:
        document["servers"]["Work"]["auth"] = auth
    config.CONFIG_PATH.write_text(json.dumps(document))
    row, setup = work()
    assert setup["auth_mode"] == mode
    assert (row["readiness"], row["method"]) == (readiness, method)
    assert not setup["credential_configured"]
    assert "credential_ref" not in json.dumps(row) + json.dumps(setup)
    if mode == "api_key":
        assert setup["bindings"][0]["name"] == "X-API-Key"


@pytest.mark.parametrize(("optional", "child_enabled", "runtime", "expected"), [
    (False, False, "connected", "needs_setup"),
    (False, True, "failed", "attention"),
    (False, True, "connected", "ready"),
    (True, False, "connected", "ready"),
])
def test_required_child_overrides_healthy_parent_and_optional_is_explicit(isolated, monkeypatch, optional, child_enabled, runtime, expected):
    from row_bot.application import plugin_commands
    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import state
    server_id = configuration.read_mcp_configuration().items[0].server_id
    child = {"transport": "streamable_http", "url": "https://example.test/mcp", "tools": {"catalog": {}}, "enabled": child_enabled}
    monkeypatch.setattr(state, "read_mcp_child_configuration", lambda _target: config.SavedMcpConfiguration(
        {"enabled": True, "servers": {"Work": child}}, "0" * 64, True))
    monkeypatch.setattr(mcp_runtime, "get_passive_server_statuses", lambda names: {"Work": {"status": runtime}})
    monkeypatch.setattr(state, "get_mcp_child_overrides", lambda *_: {"enabled": child_enabled})
    monkeypatch.setattr(plugin_commands, "read_integration_packages", lambda **_: [{"plugin_id": "fixture-package", "installed": True, "enabled": True, "health": "passed", "setup_complete": True, "name": "Fixture package", "description": "Fixture", "package_format": "agent-plugins-1.0.0", "version": "1", "capabilities": {}, "children": [{"kind": "mcp", "owner_ref": server_id, "name": "Work", "server_key": "work", "optional": optional}]}])
    row = next(row for row in client_integrations.read_items(owner_id="local", kind="plugin")["items"]
               if row["id"] == "plugin:fixture-package")
    assert row["readiness"] == expected
    assert row["children"][0]["required"] is not optional
    if not child_enabled:
        assert any("Work" in b["message"] for b in row["blockers"])


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
    row, setup = work()
    assert setup["auth_mode"] == "unsupported"
    assert setup["bindings"] == []
    assert row["readiness"] == "needs_setup" and row["blockers"][0]["code"] == "auth_unsupported"
