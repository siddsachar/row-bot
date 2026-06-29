from __future__ import annotations

import json

import pytest

from row_bot.plugins.manifest import ManifestError, parse_manifest


def _write_manifest(plugin_dir, payload: dict) -> None:
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _base_manifest(**overrides):
    payload = {
        "schema_version": 2,
        "id": "example-plugin",
        "name": "Example Plugin",
        "version": "1.0.0",
        "min_row_bot_version": "3.20.0",
        "author": {"name": "Example"},
        "description": "Example plugin",
        "provides": {
            "native_tools": [],
            "mcp_servers": [],
            "channels": [],
            "skills": [],
        },
        "permissions": [],
        "settings": {},
        "secrets": {},
        "auth": {},
        "health_checks": [],
    }
    payload.update(overrides)
    return payload


def test_manifest_uses_row_bot_version_field(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_manifest(plugin_dir, _base_manifest(min_row_bot_version="3.21.0"))

    manifest = parse_manifest(plugin_dir)

    assert manifest.min_row_bot_version == "3.21.0"


def test_manifest_accepts_valid_v2_surfaces(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_manifest(
        plugin_dir,
        _base_manifest(
            provides={
                "native_tools": [{"id": "search_mail", "entrypoint": "plugin_main.py"}],
                "mcp_servers": [{"id": "office_mcp", "command": "python -m office_mcp"}],
                "channels": [{"id": "matrix"}],
                "skills": [{"id": "office_usage", "path": "skills/office_usage/SKILL.md"}],
            },
            permissions=["network", "account", "external_send"],
            settings={"tenant": {"type": "text", "label": "Tenant", "required": True}},
            secrets={"api_key": {"type": "secret", "label": "API Key", "required": True}},
            auth={"microsoft": {"type": "oauth2_pkce", "label": "Microsoft"}},
            health_checks=[{"id": "settings_present", "type": "required_settings"}],
        ),
    )

    manifest = parse_manifest(plugin_dir)

    assert manifest.schema_version == 2
    assert manifest.native_tool_count == 1
    assert manifest.mcp_server_count == 1
    assert manifest.channel_count == 1
    assert manifest.skill_count == 1
    assert manifest.permissions == ["network", "account", "external_send"]


def test_manifest_rejects_unknown_extension_surface(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_manifest(
        plugin_dir,
        _base_manifest(
            provides={
                "native_tools": [],
                "mcp_servers": [],
                "channels": [],
                "skills": [],
                "custom_ui": [{"id": "panel"}],
            }
        ),
    )

    with pytest.raises(ManifestError, match="unsupported extension surface"):
        parse_manifest(plugin_dir)


def test_manifest_rejects_missing_schema_version(tmp_path):
    plugin_dir = tmp_path / "legacy-plugin"
    payload = _base_manifest()
    payload.pop("schema_version")
    _write_manifest(plugin_dir, payload)

    with pytest.raises(ManifestError, match="schema_version"):
        parse_manifest(plugin_dir)


def test_manifest_rejects_missing_row_bot_version_field(tmp_path):
    plugin_dir = tmp_path / "legacy-plugin"
    payload = _base_manifest(min_row_bot_version="3.20.0")
    payload.pop("min_row_bot_version")
    _write_manifest(plugin_dir, payload)

    with pytest.raises(ManifestError, match="min_row_bot_version"):
        parse_manifest(plugin_dir)
