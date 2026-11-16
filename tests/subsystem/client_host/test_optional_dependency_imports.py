from __future__ import annotations

import builtins
import importlib
import sys

import pytest


def test_optional_tool_import_helper_skips_missing_optional_module(monkeypatch):
    import row_bot.tools as tools

    real_import = tools.importlib.import_module

    def fake_import(name: str):
        if name == "row_bot.tools.browser_tool":
            raise ImportError("No module named 'playwright'")
        return real_import(name)

    monkeypatch.setattr(tools.importlib, "import_module", fake_import)
    # A skipped module is dropped from sys.modules: put back the one already imported afterwards.
    monkeypatch.delitem(sys.modules, "row_bot.tools.browser_tool", raising=False)

    tools._import_tool_module("row_bot.tools.browser_tool", optional=True)
    with pytest.raises(ImportError):
        tools._import_tool_module("row_bot.tools.browser_tool", optional=False)


def test_an_installed_optional_tool_that_failed_to_import_is_tried_again(monkeypatch):
    import row_bot.tools as tools

    calls: list[str] = []

    def flaky_import(name: str):
        calls.append(name)
        if len(calls) == 1:  # A start-up race (RM4): its packages are there.
            raise ImportError("cannot import name 'x' from partially initialized module 'json'", name="json")
        return None

    monkeypatch.setattr(tools.importlib, "import_module", flaky_import)

    tools._import_tool_module("row_bot.tools.mcp_tool", optional=True)

    assert calls == ["row_bot.tools.mcp_tool", "row_bot.tools.mcp_tool"]


@pytest.mark.parametrize(
    ("module_name", "optional_root"),
    [
        ("row_bot.native_client", "webview"),
        ("row_bot.terminal_pty", "winpty"),
    ],
)
def test_native_client_modules_import_without_optional_platform_runtime(
    monkeypatch,
    module_name: str,
    optional_root: str,
) -> None:
    """Importing shared client code must not load an OS integration eagerly."""

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == optional_root or name.startswith(f"{optional_root}."):
            raise ModuleNotFoundError(f"No module named {optional_root!r}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.delitem(sys.modules, module_name, raising=False)  # put back afterwards

    imported = importlib.import_module(module_name)

    assert imported.__name__ == module_name
