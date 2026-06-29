from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import manifest_payload, write_plugin


pytestmark = pytest.mark.subsystem

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_plugin_templates_and_examples_validate(plugin_modules: dict[str, Any]) -> None:
    devtools = plugin_modules["devtools"]
    roots = [
        REPO_ROOT / "src" / "row_bot" / "plugins" / "templates",
        REPO_ROOT / "examples" / "plugins",
    ]
    plugin_dirs = [
        path
        for root in roots
        for path in root.iterdir()
        if (path / "plugin.json").exists()
    ]

    assert plugin_dirs
    results = [devtools.validate_plugin_path(path) for path in plugin_dirs]

    assert all(result.ok for result in results)


def test_build_index_produces_v2_marketplace_shape(plugin_modules: dict[str, Any]) -> None:
    devtools = plugin_modules["devtools"]

    index = devtools.build_index(REPO_ROOT / "examples", source="unit-test")
    hello = next(entry for entry in index["plugins"] if entry["id"] == "hello-tool")
    fake_channel = next(entry for entry in index["plugins"] if entry["id"] == "fake-channel")

    assert index["schema_version"] == 2
    assert index["source"] == "unit-test"
    assert hello["provides"]["native_tools"] == 1
    assert hello["checksum"].startswith("sha256:")
    assert fake_channel["provides"]["channels"] == 1
    assert "external_send" in fake_channel["permissions"]


def test_local_link_reload_loads_enabled_plugin_from_source(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    devtools = plugin_modules["devtools"]
    state = plugin_modules["state"]
    registry = plugin_modules["registry"]
    plugin_dir = write_plugin(tmp_path / "source", "linked-plugin")

    linked = devtools.link_plugin(plugin_dir)

    assert linked.ok is True
    assert state.is_plugin_enabled("linked-plugin") is False
    assert devtools.iter_linked_plugin_dirs()["linked-plugin"] == plugin_dir.resolve()

    state.set_plugin_enabled("linked-plugin", True)
    reloaded = devtools.reload_plugin("linked-plugin")

    assert reloaded.ok is True
    assert registry.get_plugin_tool_names() == ["sample_tool"]


def test_doctor_reports_missing_required_settings_and_secrets(
    plugin_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    devtools = plugin_modules["devtools"]
    plugin_dir = write_plugin(
        tmp_path,
        "doctor-plugin",
        manifest=manifest_payload(
            "doctor-plugin",
            settings={"workspace": {"type": "local_path", "label": "Workspace", "required": True}},
            secrets={"TOKEN": {"type": "secret", "label": "Token", "required": True}},
        ),
    )

    result = devtools.doctor_plugin(plugin_dir)

    assert result.ok is False
    assert any("Missing required settings: Workspace" in warning for warning in result.warnings)
    assert any("Missing required secrets: Token" in warning for warning in result.warnings)


def test_plugin_system_v2_developer_docs_cover_local_marketplace_flow() -> None:
    guide = (REPO_ROOT / "docs" / "PLUGIN_SYSTEM_V2.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

    for required in (
        "native_tools",
        "mcp_servers",
        "channels",
        "skills",
        "uv run python scripts/validate_plugin.py",
        "uv run python scripts/build_plugin_index.py",
        "ROW_BOT_PLUGIN_INDEX_URL",
        "plain local `index.json` path",
        "`archive_url`",
        "installed disabled",
        "must not add arbitrary app panels",
        "do not render `build_custom_ui`",
        "do not block local enablement",
    ):
        assert required in guide

    assert "AST scans block dangerous constructs" in architecture
    assert "v2 plugins do not install Python dependencies" in architecture
    assert "Native Plugin Center" in architecture
