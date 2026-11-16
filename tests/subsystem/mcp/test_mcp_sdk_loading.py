"""The MCP SDK at load: one that failed to load says so and loads on a later use (RM4)."""

from __future__ import annotations

import builtins
import importlib.util
import threading

import pytest

from row_bot import status_checks
from row_bot.mcp_client import runtime


pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]

_BOUND = ("ClientSession", "StdioServerParameters", "stdio_client", "streamablehttp_client", "sse_client",
          "_SDK_SESSION", "_AppsSession", "_sdk_failure", "_sdk_failed_at")


@pytest.fixture
def unloaded_sdk(monkeypatch: pytest.MonkeyPatch):
    """The runtime as if its SDK import had not happened yet; put back as it was afterwards."""
    for name in _BOUND:
        monkeypatch.setattr(runtime, name, getattr(runtime, name))
    monkeypatch.setattr(runtime, "ClientSession", None)
    started = threading.Event()
    monkeypatch.setattr(runtime, "discover_enabled_servers", started.set)
    return started


def _sdk_import_fails(monkeypatch: pytest.MonkeyPatch, error: ImportError):
    real_import = builtins.__import__

    def racing_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", racing_import)
    return real_import


def test_an_installed_sdk_that_failed_to_load_says_so_and_loads_on_a_later_use(unloaded_sdk, monkeypatch) -> None:
    real_import = _sdk_import_fails(monkeypatch, ImportError(
        "cannot import name 'tournament' from partially initialized module 'networkx.algorithms'",
        name="networkx.algorithms"))
    runtime._load_sdk()
    monkeypatch.setattr(builtins, "__import__", real_import)  # The race is over.

    assert runtime.sdk_available() is False
    assert runtime.sdk_state() == "failed"
    assert "Restart Row-Bot" in runtime.sdk_problem() and "not installed" not in runtime.sdk_problem()
    assert status_checks.check_mcp().detail != "SDK missing"

    monkeypatch.setattr(runtime, "_sdk_failed_at", float("-inf"))  # A use after the retry interval.
    assert runtime.sdk_available() is True
    from mcp import ClientSession

    assert runtime.ClientSession is ClientSession
    assert unloaded_sdk.wait(5), "the enabled servers start once the SDK loads"


def test_sdk_missing_is_said_only_when_the_package_is_not_installed(unloaded_sdk, monkeypatch) -> None:
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *args: None if name == "mcp" else real_find_spec(name, *args))
    _sdk_import_fails(monkeypatch, ModuleNotFoundError("No module named 'mcp'", name="mcp"))
    runtime._load_sdk()

    assert runtime.sdk_state() == "missing"
    assert runtime.sdk_problem() == "Python package 'mcp' is not installed"
    assert status_checks.check_mcp().detail == "SDK missing"
