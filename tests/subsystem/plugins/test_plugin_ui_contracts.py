from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from row_bot.plugins.manifest import PluginAuthor, PluginManifest


pytestmark = pytest.mark.subsystem

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_plugin_settings_missing_required_keys_respects_secret_state(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]
    from row_bot.plugins.ui_settings import _get_missing_keys

    manifest = PluginManifest(
        id="ui-plugin",
        name="UI Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="UI settings contract",
        settings={
            "api_keys": {
                "REQUIRED_KEY": {"label": "Required Key", "required": True},
                "OPTIONAL_KEY": {"label": "Optional Key", "required": False},
            }
        },
    )

    assert _get_missing_keys(manifest) == ["Required Key"]

    state.set_plugin_secret("ui-plugin", "REQUIRED_KEY", "configured")

    assert _get_missing_keys(manifest) == []


def test_plugin_ui_entrypoints_are_importable_callables() -> None:
    from row_bot.plugins.ui_marketplace import open_marketplace_dialog
    from row_bot.plugins.ui_plugin_dialog import open_plugin_dialog
    from row_bot.plugins.ui_settings import build_plugins_tab

    assert callable(build_plugins_tab)
    assert callable(open_plugin_dialog)
    assert callable(open_marketplace_dialog)


def test_settings_dialog_contains_plugins_tab_wiring() -> None:
    settings_path = REPO_ROOT / "src" / "row_bot" / "ui" / "settings.py"
    source = settings_path.read_text(encoding="utf-8")

    ast.parse(source)
    assert 'tab_plugins = ui.tab("Plugins"' in source
    assert '"Plugins": tab_plugins' in source
    assert "_build_plugins_tab" in source
    assert "open_marketplace_dialog(on_install" in source


def test_plugin_rich_return_markers_are_rendered_in_streaming_and_reload_paths() -> None:
    streaming = (REPO_ROOT / "src" / "row_bot" / "ui" / "streaming.py").read_text(encoding="utf-8")
    render = (REPO_ROOT / "src" / "row_bot" / "ui" / "render.py").read_text(encoding="utf-8")

    for marker in ("__IMAGE__:", "__HTML__:", "__CHART__:"):
        assert marker in streaming
        assert marker in render
    assert "render_image_with_save" in streaming
    assert "ui.html(" in streaming


def test_agent_collects_plugin_destructive_tool_names() -> None:
    agent = (REPO_ROOT / "src" / "row_bot" / "agent.py").read_text(encoding="utf-8")

    assert "plugin_registry_mod.get_destructive_names()" in agent
    assert "plugin_registry_mod.get_destructive_names(allow_names=allow_set)" in agent
