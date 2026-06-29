from __future__ import annotations

import json
import time
import zipfile
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
    state.set_plugin_health_result(
        "state-plugin",
        ok=True,
        checks=[{"label": "Required local setup", "status": "ok"}],
    )
    state._reset()

    assert state.is_plugin_enabled("state-plugin") is False
    assert state.get_plugin_config("state-plugin", "limit") == 7
    assert state.get_plugin_secret("state-plugin", "API_KEY") == "secret-value"
    assert state.get_plugin_health_result("state-plugin")["ok"] is True

    secrets_text = (state.DATA_DIR / "plugin_secrets.json").read_text(encoding="utf-8")
    secrets_doc = json.loads(secrets_text)
    assert "secret-value" not in secrets_text
    assert secrets_doc["plugins"]["state-plugin"]["API_KEY"]["configured"] is True

    state.remove_plugin_state("state-plugin")
    assert state.is_plugin_enabled("state-plugin") is False
    assert state.get_plugin_config("state-plugin", "limit") is None
    assert state.get_plugin_secret("state-plugin", "API_KEY") is None
    assert state.get_plugin_health_result("state-plugin") == {}


def test_state_records_installed_plugins_disabled_by_default(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]

    state.mark_plugin_installed(
        "installed-plugin",
        version="1.2.3",
        source="marketplace",
        source_ref="plugins/installed-plugin",
    )

    assert state.is_plugin_enabled("installed-plugin") is False
    assert state.get_plugin_install_info("installed-plugin")["version"] == "1.2.3"


def test_state_clears_plugin_health_when_setup_changes(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]

    state.set_plugin_health_result(
        "health-plugin",
        ok=True,
        checks=[{"label": "Required local setup", "status": "ok"}],
    )
    assert state.get_plugin_health_result("health-plugin")["ok"] is True

    state.set_plugin_config("health-plugin", "workspace", "D:/new")
    assert state.get_plugin_health_result("health-plugin") == {}

    state.set_plugin_health_result(
        "health-plugin",
        ok=True,
        checks=[{"label": "Required local setup", "status": "ok"}],
    )
    state.set_plugin_secret("health-plugin", "TOKEN", "secret-value")

    assert state.get_plugin_health_result("health-plugin") == {}


def test_installer_local_install_update_uninstall_and_rollback(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    installer = plugin_modules["installer"]
    devtools = plugin_modules["devtools"]
    installer.PLUGINS_DIR = tmp_path / "installed_plugins"

    source_v1 = write_plugin(tmp_path / "sources", "install-plugin")
    result = installer.install_plugin("install-plugin", source_dir=source_v1)

    assert result.success is True
    assert result.version == "1.0.0"
    assert installer.is_installed("install-plugin") is True
    assert installer.get_installed_version("install-plugin") == "1.0.0"
    assert plugin_modules["state"].is_plugin_enabled("install-plugin") is False

    duplicate = installer.install_plugin("install-plugin", source_dir=source_v1)
    assert duplicate.success is False
    assert "already installed" in duplicate.message.lower()
    plugin_modules["state"].set_plugin_enabled("install-plugin", True)

    source_v2 = write_plugin(
        tmp_path / "sources_v2",
        "install-plugin",
        manifest=manifest_payload("install-plugin", version="2.0.0"),
    )
    plugin_modules["state"].set_plugin_health_result(
        "install-plugin",
        ok=True,
        checks=[{"label": "Required local setup", "status": "ok"}],
    )
    update = installer.update_plugin(
        "install-plugin",
        source_dir=source_v2,
        source="marketplace",
        source_ref="plugins/install-plugin",
        expected_checksum=devtools.compute_plugin_checksum(source_v2),
    )
    assert update.success is True
    assert installer.get_installed_version("install-plugin") == "2.0.0"
    install_info = plugin_modules["state"].get_plugin_install_info("install-plugin")
    assert install_info["source"] == "marketplace"
    assert install_info["source_ref"] == "plugins/install-plugin"
    assert plugin_modules["state"].is_plugin_enabled("install-plugin") is False
    assert plugin_modules["state"].get_plugin_health_result("install-plugin") == {}

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


def test_installer_verifies_expected_checksum_and_cleans_failed_copy(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    installer = plugin_modules["installer"]
    devtools = plugin_modules["devtools"]
    installer.PLUGINS_DIR = tmp_path / "installed_plugins"
    source = write_plugin(tmp_path / "sources", "checksum-plugin")

    ok = installer.install_plugin(
        "checksum-plugin",
        source_dir=source,
        expected_checksum=devtools.compute_plugin_checksum(source),
    )
    assert ok.success is True

    mismatch_source = write_plugin(tmp_path / "mismatch", "checksum-mismatch")
    mismatch = installer.install_plugin(
        "checksum-mismatch",
        source_dir=mismatch_source,
        expected_checksum="sha256:" + "0" * 64,
    )

    assert mismatch.success is False
    assert "checksum mismatch" in mismatch.message.lower()
    assert not (installer.PLUGINS_DIR / "checksum-mismatch").exists()


def test_installer_installs_plugin_from_local_zip_archive(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    installer = plugin_modules["installer"]
    devtools = plugin_modules["devtools"]
    installer.PLUGINS_DIR = tmp_path / "installed_plugins"
    source = write_plugin(tmp_path / "archive_source" / "plugins", "archive-plugin")
    archive = tmp_path / "archive-plugin.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, Path("row-bot-plugins-main") / "plugins" / "archive-plugin" / path.relative_to(source))

    result = installer.install_plugin(
        "archive-plugin",
        archive_url=str(archive),
        source="marketplace",
        source_ref="archives/archive-plugin.zip",
        expected_checksum=devtools.compute_plugin_checksum(source),
    )

    assert result.success is True
    assert installer.get_installed_version("archive-plugin") == "1.0.0"
    assert plugin_modules["state"].is_plugin_enabled("archive-plugin") is False
    assert plugin_modules["state"].get_plugin_install_info("archive-plugin")["source_ref"] == (
        "archives/archive-plugin.zip"
    )


def test_marketplace_parse_search_tags_entry_and_update_detection(
    plugin_modules: dict[str, Any],
) -> None:
    marketplace = plugin_modules["marketplace"]
    raw = {
        "schema_version": 2,
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
                "path": "plugins/alpha-plugin",
                "archive_url": "https://example.test/alpha.zip",
                "checksum": "sha256:abc123",
                "provides": {
                    "native_tools": 2,
                    "mcp_servers": 1,
                    "channels": 1,
                    "skills": 1,
                },
                "permissions": ["network", "account"],
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

    assert index.schema_version == 2
    assert [p.id for p in marketplace.search_plugins(query="alpha", index=index)] == ["alpha-plugin"]
    assert [p.id for p in marketplace.search_plugins(tag="demo", index=index)] == [
        "alpha-plugin",
        "beta-plugin",
    ]
    assert marketplace.get_all_tags(index) == ["alpha", "demo"]
    entry = marketplace.get_entry("alpha-plugin", index)
    assert entry.native_tool_count == 2
    assert entry.mcp_server_count == 1
    assert entry.channel_count == 1
    assert entry.skill_count == 1
    assert entry.permissions == ["network", "account"]
    assert entry.path == "plugins/alpha-plugin"
    assert entry.checksum == "sha256:abc123"
    assert entry.index_source == "unit-test"
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


def test_marketplace_fetch_reads_plain_local_index_path(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marketplace = plugin_modules["marketplace"]
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "source": str(tmp_path),
                "plugins": [
                    {
                        "id": "local-index-plugin",
                        "name": "Local Index Plugin",
                        "version": "1.0.0",
                        "description": "Read from local index",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(marketplace, "DEFAULT_INDEX_URL", str(index_path))

    index = marketplace.fetch_index(force_refresh=True)

    assert index.source == str(tmp_path)
    assert [entry.id for entry in index.plugins] == ["local-index-plugin"]


def test_marketplace_cached_update_lookup_does_not_fetch_network(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace = plugin_modules["marketplace"]
    cache_path = marketplace.DATA_DIR / "marketplace_cache.json"
    marketplace.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "_fetched_at": 0,
                "plugins": [
                    {
                        "id": "cached-update-plugin",
                        "name": "Cached Update Plugin",
                        "version": "2.0.0",
                        "description": "Cached update",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fail_fetch(url: str) -> dict[str, Any]:
        raise AssertionError("cached update lookup must not fetch")

    monkeypatch.setattr(marketplace, "_fetch_from_url", fail_fetch)
    manifest = PluginManifest(
        id="cached-update-plugin",
        name="Cached Update Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="Installed old version",
    )

    index = marketplace.get_cached_index()
    entry = marketplace.get_update_entry(manifest)

    assert index is not None
    assert entry is not None
    assert entry.version == "2.0.0"


def test_marketplace_fetch_falls_back_to_stale_disk_cache_on_refresh_failure(
    plugin_modules: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace = plugin_modules["marketplace"]
    marketplace.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (marketplace.DATA_DIR / "marketplace_cache.json").write_text(
        json.dumps(
            {
                "_fetched_at": 0,
                "plugins": [
                    {
                        "id": "stale-plugin",
                        "name": "Stale Plugin",
                        "version": "1.0.0",
                        "description": "From disk cache",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_fetch(url: str) -> dict[str, Any]:
        raise OSError("offline")

    monkeypatch.setattr(marketplace, "_fetch_from_url", fake_fetch)

    index = marketplace.fetch_index(force_refresh=True)

    assert [entry.id for entry in index.plugins] == ["stale-plugin"]


def test_marketplace_entry_install_kwargs_resolve_local_index_source(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    marketplace = plugin_modules["marketplace"]
    from row_bot.plugins.ui_marketplace import _marketplace_install_kwargs

    plugin_dir = write_plugin(tmp_path / "repo" / "plugins", "local-market-plugin")
    checksum = plugin_modules["devtools"].compute_plugin_checksum(plugin_dir)
    index = marketplace._parse_index(
        {
            "source": str(tmp_path / "repo"),
            "plugins": [
                {
                    "id": "local-market-plugin",
                    "name": "Local Market Plugin",
                    "version": "1.0.0",
                    "description": "Local index install test",
                    "path": "plugins/local-market-plugin",
                    "checksum": checksum,
                }
            ],
        }
    )

    kwargs = _marketplace_install_kwargs(index.plugins[0])

    assert kwargs == {
        "source_dir": plugin_dir,
        "source": "marketplace",
        "source_ref": "plugins/local-market-plugin",
        "archive_url": "",
        "expected_checksum": checksum,
    }


def test_marketplace_entry_install_kwargs_include_archive_url(
    plugin_modules: dict[str, Any],
) -> None:
    marketplace = plugin_modules["marketplace"]
    from row_bot.plugins.ui_marketplace import _marketplace_install_kwargs

    index = marketplace._parse_index(
        {
            "plugins": [
                {
                    "id": "archive-market-plugin",
                    "name": "Archive Market Plugin",
                    "version": "1.0.0",
                    "description": "Archive install test",
                    "archive_url": "file:///tmp/archive-market-plugin.zip",
                    "checksum": "sha256:" + "1" * 64,
                }
            ],
        }
    )

    kwargs = _marketplace_install_kwargs(index.plugins[0])

    assert kwargs["source_dir"] is None
    assert kwargs["source_ref"] == "file:///tmp/archive-market-plugin.zip"
    assert kwargs["archive_url"] == "file:///tmp/archive-market-plugin.zip"
    assert kwargs["expected_checksum"] == "sha256:" + "1" * 64
