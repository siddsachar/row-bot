from __future__ import annotations

import importlib
from pathlib import Path

from row_bot.migration.row_bot_legacy_rebrand import TOOL_ID_REPLACEMENTS


LEGACY_STATUS_TOOL_ID = next(key for key, value in TOOL_ID_REPLACEMENTS.items() if value == "row_bot_status")
LEGACY_UPDATER_TOOL_ID = next(key for key, value in TOOL_ID_REPLACEMENTS.items() if value == "row_bot_updater")
LEGACY_UPDATE_SETTING_ID = next(key for key, value in TOOL_ID_REPLACEMENTS.items() if value == "row_bot_update_setting")


def test_row_bot_agent_visible_tool_ids_are_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))

    import row_bot.tools as tools # noqa: F401
    import row_bot.tools.row_bot_status_tool as row_bot_status_tool
    import row_bot.tools.updater_tool as updater_tool
    from row_bot.tools import registry

    importlib.reload(row_bot_status_tool)
    importlib.reload(updater_tool)

    status_tool = registry.get_tool("row_bot_status")
    updater_tool = registry.get_tool("row_bot_updater")

    assert status_tool is not None
    assert updater_tool is not None
    assert registry.get_tool(LEGACY_STATUS_TOOL_ID) is None
    assert registry.get_tool(LEGACY_UPDATER_TOOL_ID) is None

    status_subtools = {tool.name for tool in status_tool.as_langchain_tools()}
    assert "row_bot_status" in status_subtools
    assert "row_bot_update_setting" in status_subtools
    assert LEGACY_STATUS_TOOL_ID not in status_subtools
    assert LEGACY_UPDATE_SETTING_ID not in status_subtools

    updater_subtools = {tool.name for tool in updater_tool.as_langchain_tools()}
    assert updater_subtools == {"row_bot_check_for_updates", "row_bot_install_update"}


def test_row_bot_status_guide_and_voice_bridge_use_row_bot_ids():
    from row_bot.voice.agent_bridge import REALTIME_ALLOWED_BRIDGE_TOOLS, REALTIME_ALLOWED_TOOLS, REALTIME_WAIT_TOOL

    guide = Path("tool_guides/row_bot_status_guide/SKILL.md").read_text(encoding="utf-8")

    assert "name: row_bot_status_guide" in guide
    assert "  - row_bot_status" in guide
    assert "row_bot_update_setting" in guide
    assert "row_bot_create_skill" in guide
    assert "row_bot_patch_skill" in guide
    assert LEGACY_STATUS_TOOL_ID not in guide
    assert REALTIME_ALLOWED_BRIDGE_TOOLS == ("row_bot_agent_consult", "row_bot_agent_control")
    assert REALTIME_ALLOWED_TOOLS == ("row_bot_agent_consult", "row_bot_agent_control", REALTIME_WAIT_TOOL)
