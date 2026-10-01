"""The native window's pywebview bridge under the shell's CSP (no 'unsafe-eval')."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from row_bot.webview_csp import csp_safe_api_js, install_csp_safe_bridge


def test_pywebviews_api_functions_are_built_without_eval() -> None:
    """macOS's WebKit refuses new Function() under the shell's policy, so the
    page's window.pywebview.api never had a function to call."""
    import webview.util as util

    code = (Path(util.get_js_dir()) / "api.js").read_text(encoding="utf-8")
    assert "new Function" in code
    safe = csp_safe_api_js(code)
    assert "new Function" not in safe
    assert safe.count("window.pywebview._jsApiCallback(funcName, Array.prototype.slice.call(arguments), __id)") == 1


def test_a_bridge_result_returns_to_the_page_without_eval(monkeypatch) -> None:
    """pywebview returned every result through evaluate_js's eval(), which the
    policy refuses: a call reached Python but its answer never reached the page."""
    import webview.util as util

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(util, "load_js_files", util.load_js_files)
    monkeypatch.setattr(util, "js_bridge_call", util.js_bridge_call)
    assert install_csp_safe_bridge()

    sent: list[str] = []
    answered = threading.Event()

    def run_js(code: str) -> None:
        sent.append(code)
        answered.set()

    def evaluate_js(_code: str) -> None:
        pytest.fail("the result went through eval()")

    window = SimpleNamespace(_functions={"double": lambda value: value * 2}, _js_api=None,
                             run_js=run_js, evaluate_js=evaluate_js)
    util.js_bridge_call(window, "double", [21], "call-1")
    assert answered.wait(5)
    assert '_returnValues["double"]["call-1"]' in sent[0] and "42" in sent[0]


def test_windows_keeps_pywebviews_own_bridge(monkeypatch) -> None:
    import webview.util as util

    monkeypatch.setattr(sys, "platform", "win32")
    before = util.js_bridge_call
    assert install_csp_safe_bridge() is False
    assert util.js_bridge_call is before


def test_a_pywebview_without_those_internals_keeps_the_window_running(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "webview.util", None)
    assert install_csp_safe_bridge() is False
