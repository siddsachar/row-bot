from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from row_bot.plugins.manifest import PluginAuthor, PluginManifest

from .conftest import manifest_payload, write_plugin


pytestmark = pytest.mark.subsystem


def test_state_persists_enable_config_and_keyring_secret_metadata(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]

    state.set_plugin_enabled("state-plugin", False)
    state.set_plugin_config("state-plugin", "limit", 7)
    state.set_plugin_secret("state-plugin", "API_KEY", "secret-value")
    state._reset()

    assert state.is_plugin_enabled("state-plugin") is False
    assert state.get_plugin_config("state-plugin", "limit") == 7
    assert state.get_plugin_secret("state-plugin", "API_KEY") == "secret-value"

    secrets_text = (state.DATA_DIR / "plugin_secrets.json").read_text(encoding="utf-8")
    secrets_doc = json.loads(secrets_text)
    assert "secret-value" not in secrets_text
    assert secrets_doc["plugins"]["state-plugin"]["API_KEY"]["configured"] is True

    state.remove_plugin_state("state-plugin")
    assert state.is_plugin_enabled("state-plugin") is True
    assert state.get_plugin_config("state-plugin", "limit") is None
    assert state.get_plugin_secret("state-plugin", "API_KEY") is None


def test_installer_local_install_update_uninstall_and_rollback(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    installer = plugin_modules["installer"]
    installer.PLUGINS_DIR = tmp_path / "installed_plugins"

    source_v1 = write_plugin(tmp_path / "sources", "install-plugin")
    result = installer.install_plugin("install-plugin", source_dir=source_v1)

    assert result.success is True
    assert result.version == "1.0.0"
    assert installer.is_installed("install-plugin") is True
    assert installer.get_installed_version("install-plugin") == "1.0.0"

    duplicate = installer.install_plugin("install-plugin", source_dir=source_v1)
    assert duplicate.success is False
    assert "already installed" in duplicate.message.lower()

    source_v2 = write_plugin(
        tmp_path / "sources_v2",
        "install-plugin",
        manifest=manifest_payload("install-plugin", version="2.0.0"),
    )
    update = installer.update_plugin("install-plugin", source_dir=source_v2)
    assert update.success is True
    assert installer.get_installed_version("install-plugin") == "2.0.0"

    unsafe_source = write_plugin(
        tmp_path / "sources_bad",
        "install-plugin",
        manifest=manifest_payload("install-plugin", version="3.0.0"),
        main="import os\ndef register(api):\n    os.system('echo bad')\n",
    )
    failed_update = installer.update_plugin("install-plugin", source_dir=unsafe_source)
    assert failed_update.success is False
    assert installer.get_installed_version("install-plugin") == "2.0.0"

    uninstall = installer.uninstall_plugin("install-plugin")
    assert uninstall.success is True
    assert installer.is_installed("install-plugin") is False
    assert installer.uninstall_plugin("missing-plugin").success is False


def test_installer_rejects_local_source_without_manifest(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    installer = plugin_modules["installer"]
    installer.PLUGINS_DIR = tmp_path / "installed_plugins"
    source = tmp_path / "source" / "missing-manifest"
    source.mkdir(parents=True)
    (source / "plugin_main.py").write_text("def register(api): pass\n", encoding="utf-8")

    result = installer.install_plugin("missing-manifest", source_dir=source)

    assert result.success is False
    assert "plugin.json" in result.message
    assert not (installer.PLUGINS_DIR / "missing-manifest").exists()


def test_marketplace_parse_search_tags_entry_and_update_detection(
    plugin_modules: dict[str, Any],
) -> None:
    marketplace = plugin_modules["marketplace"]
    raw = {
        "schema_version": 1,
        "generated": "2026-06-22T00:00:00Z",
        "source": "unit-test",
        "plugins": [
            {
                "id": "alpha-plugin",
                "name": "Alpha Plugin",
                "version": "2.0.0",
                "description": "Alpha tools",
                "author": {"name": "Tester", "github": "tester"},
                "tags": ["demo", "alpha"],
                "provides": {"tools": 2, "skills": 1},
                "verified": True,
            },
            {
                "id": "beta-plugin",
                "name": "Beta Plugin",
                "version": "1.0.0",
                "description": "Beta tools",
                "tags": ["demo"],
            },
        ],
    }

    index = marketplace._parse_index(raw)

    assert index.schema_version == 1
    assert [p.id for p in marketplace.search_plugins(query="alpha", index=index)] == ["alpha-plugin"]
    assert [p.id for p in marketplace.search_plugins(tag="demo", index=index)] == [
        "alpha-plugin",
        "beta-plugin",
    ]
    assert marketplace.get_all_tags(index) == ["alpha", "demo"]
    assert marketplace.get_entry("alpha-plugin", index).tool_count == 2
    assert marketplace.get_entry("missing-plugin", index) is None

    marketplace._cached_index = index
    marketplace._cache_timestamp = time.time()
    installed = [
        PluginManifest(
            id="alpha-plugin",
            name="Alpha Plugin",
            version="1.0.0",
            min_row_bot_version="0.0.0",
            author=PluginAuthor(name="Tester"),
            description="Installed old version",
        )
    ]
    assert marketplace.check_updates(installed) == [
        {
            "plugin_id": "alpha-plugin",
            "name": "Alpha Plugin",
            "installed_version": "1.0.0",
            "latest_version": "2.0.0",
        }
    ]


def test_marketplace_fetch_uses_patched_source_and_disk_cache(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace = plugin_modules["marketplace"]
    calls: list[str] = []
    raw = {
        "plugins": [
            {
                "id": "cached-plugin",
                "name": "Cached Plugin",
                "version": "1.0.0",
                "description": "Cached plugin",
            }
        ]
    }

    def fake_fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        return dict(raw)

    monkeypatch.setattr(marketplace, "_fetch_from_url", fake_fetch)
    index = marketplace.fetch_index(force_refresh=True)
    marketplace._reset()
    cached = marketplace.fetch_index(force_refresh=False)

    assert calls == [marketplace.DEFAULT_INDEX_URL]
    assert index.plugins[0].id == "cached-plugin"
    assert cached.plugins[0].id == "cached-plugin"
    assert (marketplace.DATA_DIR / "marketplace_cache.json").exists()
