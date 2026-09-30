"""Developer tools are on for people who upgrade, once (B259).

Before the switch to on-by-default, saving any tool setting wrote every
tool's value, so upgraded profiles hold ``developer: false`` nobody chose.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem

NOTICE = "Developer tools are now on · Settings › Tools"


@pytest.fixture
def profile(tmp_path, monkeypatch):
    from row_bot.tools import registry

    data = tmp_path / "row-bot"
    data.mkdir()
    path = data / "tools_config.json"
    developer = SimpleNamespace(name="developer", enabled_by_default=True, config_schema={})
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    monkeypatch.setattr(registry, "_tools", {"developer": developer})
    monkeypatch.setattr(registry, "_enabled", {})
    monkeypatch.setattr(registry, "_tool_configs", {})
    monkeypatch.setattr(registry, "_global_config", {})
    monkeypatch.setattr(registry, "_active_config_path", path)
    monkeypatch.setattr(registry, "_invalidate_agent_cache", lambda: None)
    return path


def _save(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")


def _notices():
    from row_bot.application.app_notices import app_notices

    return [notice.title for notice in app_notices.since(0)]


def _run():
    from row_bot.application.developer_tool_upgrade import turn_on_developer_tools_once

    return turn_on_developer_tools_once()


def test_an_upgraded_profile_gets_developer_tools_on_with_one_notice(profile):
    from row_bot.tools import registry

    # Written by an older version while off was the default.
    _save(profile, {"tools": {"developer": False, "shell": True}, "tool_configs": {}})
    before = _notices().count(NOTICE)
    assert _run() is True
    assert registry.is_enabled("developer") is True
    saved = json.loads(profile.read_text(encoding="utf-8"))
    assert saved["tools"] == {"developer": True, "shell": True}
    assert _notices().count(NOTICE) == before + 1
    # Once only: nothing changes and nothing is announced again.
    assert _run() is False
    assert _notices().count(NOTICE) == before + 1


def test_developer_tools_turned_off_after_the_switch_stay_off(profile):
    from row_bot.tools import registry

    _save(profile, {"tools": {"shell": True}, "tool_configs": {}})
    assert _run() is False
    # The person turns it off under the new default.
    registry.set_enabled("developer", False)
    before = _notices().count(NOTICE)
    assert _run() is False
    assert registry.is_enabled("developer") is False
    assert json.loads(profile.read_text(encoding="utf-8"))["tools"]["developer"] is False
    assert _notices().count(NOTICE) == before


def test_a_new_install_has_developer_tools_on_without_a_notice(profile):
    from row_bot.tools import registry

    before = _notices().count(NOTICE)
    assert _run() is False
    assert registry.is_enabled("developer") is True
    assert "developer" not in json.loads(profile.read_text(encoding="utf-8"))["tools"]
    assert _notices().count(NOTICE) == before
