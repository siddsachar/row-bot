from __future__ import annotations

import json

import pytest

from row_bot.plugins.manifest import ManifestError, parse_manifest


def _write_manifest(plugin_dir, payload: dict) -> None:
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _base_manifest(**overrides):
    payload = {
        "id": "example-plugin",
        "name": "Example Plugin",
        "version": "1.0.0",
        "min_row_bot_version": "3.20.0",
        "author": {"name": "Example"},
        "description": "Example plugin",
        "provides": {"tools": [], "skills": []},
    }
    payload.update(overrides)
    return payload


def test_manifest_uses_row_bot_version_field(tmp_path):
    plugin_dir = tmp_path / "plugin"
    _write_manifest(plugin_dir, _base_manifest(min_row_bot_version="3.21.0"))

    manifest = parse_manifest(plugin_dir)

    assert manifest.min_row_bot_version == "3.21.0"


def test_manifest_rejects_missing_row_bot_version_field(tmp_path):
    plugin_dir = tmp_path / "legacy-plugin"
    payload = _base_manifest(min_row_bot_version="3.20.0")
    payload.pop("min_row_bot_version")
    _write_manifest(plugin_dir, payload)

    with pytest.raises(ManifestError, match="min_row_bot_version"):
        parse_manifest(plugin_dir)
