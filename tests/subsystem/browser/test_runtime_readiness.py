from __future__ import annotations

import inspect

from row_bot.tools.browser_tool import BrowserSession


def test_browser_startup_has_no_hidden_install_or_csp_bypass() -> None:
    source = inspect.getsource(BrowserSession)
    assert "playwright\", \"install" not in source
    assert "bypass_csp" not in source
    assert "explicit installation is required" in source
