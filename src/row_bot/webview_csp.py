"""pywebview's JavaScript bridge under the shell's Content Security Policy.

The shell allows no ``'unsafe-eval'``. macOS's WebKit (and WebKitGTK) apply that
policy to the scripts pywebview runs in the page, and pywebview builds each
``window.pywebview.api`` function with ``new Function()`` and returns every
result through ``evaluate_js``, which wraps the code in ``eval()``. Both are
refused, so the native window's page never reaches the native bridge
("Desktop features are reconnecting" for good). Windows' WebView2 exempts
embedder scripts, which hid this until the macOS app.

The native window installs two equivalents that need no eval: plain closures
for the API functions, and results returned with ``run_js``. The policy stays
as strict as it is.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

logger = logging.getLogger(__name__)

_FUNCTION_STUB = re.compile(r"new Function\(\s*sanitize_params\(params\),\s*funcBody\s*\)")
# What pywebview's generated body does, as a closure over the function's name.
_CLOSURE_STUB = (
    "(function (funcName) { return function () {"
    " var __id = (Math.random() + '').substring(2);"
    " var promise = new Promise(function (resolve, reject) {"
    " window.pywebview._checkValue(funcName, resolve, reject, __id); });"
    " window.pywebview._jsApiCallback(funcName, Array.prototype.slice.call(arguments), __id);"
    " return promise; }; })(funcName)"
)


def csp_safe_api_js(js_code: str) -> str:
    """pywebview's injected API code with its ``new Function`` stubs replaced
    by closures; unchanged (and a warning) if pywebview's code has moved on."""
    replaced, count = _FUNCTION_STUB.subn(_CLOSURE_STUB, js_code)
    if count != 1:
        logger.warning("pywebview's API builder changed; its functions may not reach a strict page")
    return replaced


class _RunJsWindow:
    """A window whose ``evaluate_js`` runs the code as is (``run_js``)."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def evaluate_js(self, script: str, *_args: Any) -> Any:
        return self._window.run_js(script)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._window, name)


def install_csp_safe_bridge() -> bool:
    """Make pywebview's bridge work under the shell's policy (not on Windows,
    whose WebView2 already does). Returns whether it was installed."""
    if sys.platform == "win32":
        return False
    import webview.util as util

    load_js_files = util.load_js_files
    js_bridge_call = util.js_bridge_call

    def csp_safe_load_js_files(window: Any, platform: str) -> tuple[str, str]:
        js_code, finish_script = load_js_files(window, platform)
        return csp_safe_api_js(js_code), finish_script

    def csp_safe_js_bridge_call(window: Any, func_name: str, param: Any, value_id: str) -> None:
        return js_bridge_call(_RunJsWindow(window), func_name, param, value_id)

    util.load_js_files = csp_safe_load_js_files
    util.js_bridge_call = csp_safe_js_bridge_call
    # The platform modules import js_bridge_call by name.
    for name in ("webview.platforms.cocoa", "webview.platforms.gtk"):
        try:
            module = __import__(name, fromlist=["js_bridge_call"])
        except Exception:
            continue
        if getattr(module, "js_bridge_call", None) is js_bridge_call:
            module.js_bridge_call = csp_safe_js_bridge_call
    return True
