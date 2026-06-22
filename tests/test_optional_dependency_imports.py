from __future__ import annotations

import importlib

import pytest


def test_optional_tool_import_helper_skips_missing_optional_module(monkeypatch):
    import row_bot.tools as tools

    real_import = tools.importlib.import_module

    def fake_import(name: str):
        if name == "row_bot.tools.browser_tool":
            raise ImportError("No module named 'playwright'")
        return real_import(name)

    monkeypatch.setattr(tools.importlib, "import_module", fake_import)

    tools._import_tool_module("row_bot.tools.browser_tool", optional=True)
    with pytest.raises(ImportError):
        tools._import_tool_module("row_bot.tools.browser_tool", optional=False)


def test_channel_loader_skips_missing_optional_adapter(monkeypatch):
    import row_bot.app as app_mod

    real_import = importlib.import_module

    def fake_import(name: str):
        if name == "row_bot.channels.telegram":
            raise ImportError("No module named 'telegram'")
        return real_import(name)

    monkeypatch.setattr(app_mod, "_CHANNEL_MODULES", ("row_bot.channels.telegram",))
    monkeypatch.setattr(importlib, "import_module", fake_import)

    skipped = app_mod._load_channel_modules()

    assert skipped
    assert skipped[0].startswith("row_bot.channels.telegram:")
