from __future__ import annotations

from row_bot.tools.browser_tool import _browser_runs_headless


def test_server_browser_headless_flag_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("ROW_BOT_BROWSER_HEADLESS", raising=False)
    assert _browser_runs_headless() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("ROW_BOT_BROWSER_HEADLESS", value)
        assert _browser_runs_headless() is True
    monkeypatch.setenv("ROW_BOT_BROWSER_HEADLESS", "0")
    assert _browser_runs_headless() is False
