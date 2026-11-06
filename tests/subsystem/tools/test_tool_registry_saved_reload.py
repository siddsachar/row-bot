"""Tool registry refresh after a separate canonical Settings publication."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem


def test_reload_saved_config_replaces_loaded_enablement_config_and_globals(
    tmp_path, monkeypatch
):
    from row_bot.tools import registry

    data = tmp_path / "row-bot"
    data.mkdir()
    path = data / "tools_config.json"
    path.write_text(
        json.dumps(
            {
                "tools": {"sample": False},
                "tool_configs": {"sample": {"mode": "saved"}},
                "global": {"external_tool_loading_mode": "eager"},
            }
        ),
        encoding="utf-8",
    )
    tool = SimpleNamespace(
        name="sample",
        enabled_by_default=True,
        config_schema={"mode": {"type": "text", "default": "default"}},
    )
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    monkeypatch.setattr(registry, "_tools", {"sample": tool})
    monkeypatch.setattr(registry, "_enabled", {"sample": True})
    monkeypatch.setattr(registry, "_tool_configs", {"sample": {"mode": "stale"}})
    monkeypatch.setattr(
        registry, "_global_config", {"external_tool_loading_mode": "auto"}
    )
    invalidated: list[bool] = []
    monkeypatch.setattr(
        registry, "_invalidate_agent_cache", lambda: invalidated.append(True)
    )

    registry.reload_saved_config()

    assert registry._enabled == {"sample": False}
    assert registry._tool_configs["sample"]["mode"] == "saved"
    assert registry._global_config == {"external_tool_loading_mode": "eager"}
    assert registry.get_external_tool_loading_mode() == "eager"
    assert invalidated == [True]


def _shell_like_tool():
    return SimpleNamespace(
        name="sample",
        enabled_by_default=True,
        config_schema={"blocked_commands": {"type": "text", "default": ""}},
    )


def _unreadable(path, fault: str) -> None:
    if fault == "truncated":
        path.write_bytes(b"")
    elif fault == "half_written":
        path.write_text('{"tools": {"sample": fal', encoding="utf-8")
    else:
        path.unlink()
        path.mkdir()


@pytest.mark.parametrize("fault", ["truncated", "half_written", "not_a_file"])
def test_unreadable_settings_keep_a_switched_off_tool_and_its_deny_list(tmp_path, monkeypatch, fault):
    from row_bot.tools import registry

    data = tmp_path / "row-bot"
    data.mkdir()
    path = data / "tools_config.json"
    path.write_text(json.dumps({"tools": {"sample": False},
                                "tool_configs": {"sample": {"blocked_commands": "curl"}}}), encoding="utf-8")
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    monkeypatch.setattr(registry, "_active_config_path", path)
    monkeypatch.setattr(registry, "_tools", {})
    monkeypatch.setattr(registry, "_enabled", {})
    monkeypatch.setattr(registry, "_tool_configs", {})
    monkeypatch.setattr(registry, "_invalidate_agent_cache", lambda: None)
    registry.register(_shell_like_tool())
    assert (registry.is_enabled("sample"), registry.get_tool_config("sample", "blocked_commands")) == (False, "curl")

    _unreadable(path, fault)
    registry.reload_saved_config()

    assert registry.is_enabled("sample") is False
    assert registry.get_tool_config("sample", "blocked_commands") == "curl"


@pytest.mark.parametrize("fault", ["truncated", "half_written", "not_a_file"])
def test_a_start_with_unreadable_settings_leaves_tools_off_not_at_their_defaults(tmp_path, monkeypatch, fault):
    from row_bot.tools import registry

    data = tmp_path / "row-bot"
    data.mkdir()
    path = data / "tools_config.json"
    path.write_text("{}", encoding="utf-8")
    _unreadable(path, fault)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    monkeypatch.setattr(registry, "_tools", {})
    monkeypatch.setattr(registry, "_enabled", {})
    monkeypatch.setattr(registry, "_tool_configs", {})

    registry.register(_shell_like_tool())

    assert registry.is_enabled("sample") is False
    assert registry.get_enabled_tools() == []


def test_a_first_start_with_no_settings_file_uses_the_defaults(tmp_path, monkeypatch):
    from row_bot.tools import registry

    data = tmp_path / "row-bot"
    data.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    monkeypatch.setattr(registry, "_tools", {})
    monkeypatch.setattr(registry, "_enabled", {})
    monkeypatch.setattr(registry, "_tool_configs", {})

    registry.register(_shell_like_tool())

    assert registry.is_enabled("sample") is True
    assert registry.get_tool_config("sample", "blocked_commands") == ""
