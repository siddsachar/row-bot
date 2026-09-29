from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from row_bot.plugins.manifest import PluginAuthor, PluginManifest, PluginProvides


pytestmark = pytest.mark.subsystem

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_plugin_settings_missing_required_keys_respects_secret_state(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]
    from row_bot.plugins.health import missing_secrets

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

    assert missing_secrets(manifest) == ["Required Key"]

    state.set_plugin_secret("ui-plugin", "REQUIRED_KEY", "configured")

    assert missing_secrets(manifest) == []


def test_plugin_v2_settings_and_secrets_gate_the_recorded_self_test(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]
    from row_bot.plugins.health import (
        missing_secrets,
        missing_settings,
        record_manifest_health,
        run_manifest_health,
    )

    manifest = PluginManifest(
        id="v2-ui-plugin",
        name="V2 UI Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="UI settings contract",
        settings={
            "workspace": {"type": "local_path", "label": "Workspace", "required": True},
            "mode": {"type": "select", "label": "Mode", "options": ["safe"], "default": "safe"},
        },
        secrets={
            "TOKEN": {"type": "secret", "label": "Token", "required": True},
        },
    )

    assert missing_settings(manifest) == ["Workspace"]
    assert missing_secrets(manifest) == ["Token"]

    state.set_plugin_config("v2-ui-plugin", "workspace", "D:/example")
    state.set_plugin_secret("v2-ui-plugin", "TOKEN", "secret-value")

    assert missing_settings(manifest) == []
    assert missing_secrets(manifest) == []

    checks = record_manifest_health(manifest)

    assert checks == [{"label": "Required local setup", "status": "ok"}]
    assert state.get_plugin_health_result("v2-ui-plugin")["ok"] is True

    live_check_manifest = PluginManifest(
        id="live-check-ui-plugin",
        name="Live Check UI Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="UI health contract",
        health_checks=[{"id": "api_probe", "type": "api_probe"}],
    )

    assert run_manifest_health(live_check_manifest) == [
        {"label": "Api Probe", "status": "manual_required"}
    ]
    assert record_manifest_health(live_check_manifest) == [
        {"label": "Api Probe", "status": "manual_required"}
    ]
    assert state.get_plugin_health_result("live-check-ui-plugin")["ok"] is True


def test_declared_plugin_health_checks_are_evaluated_locally(
    plugin_modules: dict[str, Any],
) -> None:
    state = plugin_modules["state"]
    from row_bot.plugins.health import record_manifest_health, run_manifest_health

    channel_manifest = PluginManifest(
        id="channel-health-plugin",
        name="Channel Health Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="Health check contract",
        provides=PluginProvides(channels=[{"id": "fake_channel"}]),
        settings={"target": {"type": "text", "label": "Target", "required": True}},
        health_checks=[{"id": "channel_configured", "type": "channel_configured"}],
    )

    blocked = run_manifest_health(channel_manifest)
    assert {"label": "Target", "status": "missing_setting"} in blocked
    assert {"label": "Channel Configured", "status": "blocked_missing_setup"} in blocked

    state.set_plugin_config("channel-health-plugin", "target", "me")

    assert record_manifest_health(channel_manifest) == [
        {"label": "Channel Configured", "status": "ok"}
    ]

    mcp_manifest = PluginManifest(
        id="mcp-health-plugin",
        name="MCP Health Plugin",
        version="1.0.0",
        min_row_bot_version="0.0.0",
        author=PluginAuthor(name="Tester"),
        description="MCP health check contract",
        provides=PluginProvides(
            mcp_servers=[{"id": "example_mcp", "transport": "stdio", "command": "python"}],
        ),
        health_checks=[{"id": "mcp_starts", "type": "mcp_server_starts"}],
    )

    assert run_manifest_health(mcp_manifest) == [{"label": "Mcp Starts", "status": "ok"}]


def test_agent_collects_plugin_destructive_tool_names() -> None:
    agent = (REPO_ROOT / "src" / "row_bot" / "agent.py").read_text(encoding="utf-8")

    assert "plugin_registry_mod.get_destructive_names(allow_names=allow_set)" in agent


def test_app_startup_loads_plugins_before_mcp_and_schedules_agent_prewarm_after_ready() -> None:
    app_source = (REPO_ROOT / "src" / "row_bot" / "app.py").read_text(encoding="utf-8")

    plugin_index = app_source.index("from row_bot.plugins.loader import refresh_plugin_runtime")
    mcp_index = app_source.index("from row_bot.mcp_client.runtime import discover_enabled_servers")
    ready_index = app_source.rindex("startup_state.ready = True")
    graph_schedule_index = app_source.rindex("_schedule_agent_graph_prewarm()")

    assert plugin_index < mcp_index < ready_index < graph_schedule_index
    assert "refresh_plugin_runtime," in app_source
    assert "discover_mcp=False" in app_source
