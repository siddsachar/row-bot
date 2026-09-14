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
                "global": {"compression_mode": "deep"},
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
    monkeypatch.setattr(registry, "_global_config", {"compression_mode": "off"})
    invalidated: list[bool] = []
    monkeypatch.setattr(
        registry, "_invalidate_agent_cache", lambda: invalidated.append(True)
    )

    registry.reload_saved_config()

    assert registry._enabled == {"sample": False}
    assert registry._tool_configs["sample"]["mode"] == "saved"
    assert registry._global_config == {"compression_mode": "deep"}
    assert invalidated == [True]
