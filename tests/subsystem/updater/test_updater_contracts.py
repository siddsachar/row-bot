from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.subsystem


def _reload_updater(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    sys.modules.pop("row_bot.updater", None)
    import row_bot.updater as updater

    return updater


def test_updater_public_api_manifest_and_version_contracts(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)

    for attr in (
        "UpdateInfo",
        "UpdateState",
        "check_for_updates",
        "download_update",
        "install_and_restart",
        "verify_os_signature",
        "get_update_state",
        "set_channel",
        "skip_version",
        "parse_manifest",
        "compare_versions",
        "summary_for_status",
        "start_update_scheduler",
        "stop_update_scheduler",
        "is_dev_install",
        "UpdateError",
    ):
        assert hasattr(updater, attr), attr

    assert updater.compare_versions("3.17.0", "3.18.0") > 0
    assert updater.compare_versions("3.17.0", "3.17.0") == 0
    assert updater.compare_versions("3.17.0", "3.16.5") < 0
    assert updater.compare_versions("not-a-version", "3.18.0") == 0

    body = (
        "# Notes\n\n<!-- row-bot-update-manifest -->\n"
        "```manifest\nschema: 1\nfiles:\n"
        "  Row-Bot-3.18.0-Windows-x64.exe: sha256=" + "a" * 64 + "\n"
        "  Row-Bot-3.18.0-macOS-arm64.dmg: sha256=" + "b" * 64 + "\n"
        "```\n"
    )
    parsed = updater.parse_manifest(body)
    assert parsed["Row-Bot-3.18.0-Windows-x64.exe"] == "a" * 64
    assert parsed["Row-Bot-3.18.0-macOS-arm64.dmg"] == "b" * 64
    assert updater.parse_manifest("") == {}
    assert updater.parse_manifest("plain release notes, no manifest") == {}
    assert updater.parse_manifest("<!-- row-bot-update-manifest -->\n```manifest\nfiles:\n  bad-line\n```\n") == {}


def test_update_state_persists_channel_and_skipped_versions(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)

    state = updater.get_update_state()
    assert state.channel == "stable"
    updater.skip_version("9.9.9")
    updater.set_channel("beta")

    updater._state = None
    reloaded = updater.get_update_state()
    assert reloaded.channel == "beta"
    assert "9.9.9" in reloaded.skipped_versions


def test_update_status_tool_self_knowledge_and_manifest_helpers(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    from scripts.append_sha_manifest import build_manifest_block, merge_into_body

    summary = updater.summary_for_status()
    for key in (
        "current_version",
        "channel",
        "auto_check",
        "last_check",
        "last_success",
        "update_available",
        "available_version",
        "skipped_versions",
        "dev_install",
    ):
        assert key in summary
    assert isinstance(summary["update_available"], bool)
    assert isinstance(summary["skipped_versions"], list)

    block = build_manifest_block({"a.exe": "x" * 64, "b.dmg": "y" * 64})
    merged = merge_into_body("# Notes", block)
    updated = merge_into_body(merged, build_manifest_block({"new.exe": "z" * 64}))
    assert updated.count("<!-- row-bot-update-manifest -->") == 1
    assert "new.exe" in updated
    assert "a.exe" not in updated


def test_updater_runtime_wiring_contracts(tmp_path, monkeypatch) -> None:
    updater = _reload_updater(monkeypatch, tmp_path)
    from row_bot.tools import registry
    from row_bot.tools.row_bot_status_tool import _QUERY_HANDLERS

    tool = registry.get_tool("row_bot_updater")
    assert tool is not None
    tool_names = {tool.name for tool in tool.as_langchain_tools()}
    assert {"row_bot_check_for_updates", "row_bot_install_update"} <= tool_names

    assert "updates" in _QUERY_HANDLERS
    assert "**Updates**" in _QUERY_HANDLERS["updates"]()

    iss = Path("installer/row_bot_setup.iss").read_text(encoding="utf-8")
    status_bar = Path("src/row_bot/ui/status_bar.py").read_text(encoding="utf-8")
    settings = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")
    app = Path("src/row_bot/app.py").read_text(encoding="utf-8")
    guide = Path("tool_guides/updater_guide/SKILL.md").read_text(encoding="utf-8")

    assert "CloseApplications=yes" in iss
    for relative in ("updater.py", "tools/updater_tool.py", "ui/update_dialog.py"):
        assert Path("src/row_bot", relative).is_file()
    assert "_refresh_update_pill" in status_bar
    assert "build_update_section" in settings
    assert "start_update_scheduler" in app
    assert "name: updater_guide" in guide
    assert "row_bot_updater" in guide
