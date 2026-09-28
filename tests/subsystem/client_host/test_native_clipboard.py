"""The desktop window's clipboard keeps text intact (parity row 14).

Right-click Paste reads the clipboard through the window script, and Copy
falls back to it. On Windows both go through PowerShell with UTF-8 set on the
pipe, so text outside the console code page survives. The clipboard itself is
never touched: subprocess calls are recorded.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import types

import pytest

from row_bot import launcher

TEXT = "café ✓ 日本"


def _script_part(start: str, end: str) -> str:
    source = launcher._WINDOW_SCRIPT
    first = source.index(start)
    return source[first:source.index(end, first)]


@pytest.fixture
def clipboard(monkeypatch):
    namespace: dict = {"sys": types.SimpleNamespace(platform="win32")}
    exec(compile(_script_part("_WINDOWS_CLIPBOARD_READ = [", "\ndef _attach_client_v2"),
                 "<window-script>", "exec"), namespace)
    method = textwrap.dedent(_script_part("    def get_clipboard(self):", "\ndef _on_loaded"))
    exec(compile(method, "<window-script>", "exec"), namespace)
    monkeypatch.setattr(sys, "platform", "win32")
    return namespace


def test_windows_copy_sends_utf8_to_powershell(clipboard, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)

    assert clipboard["_native_clipboard_write"](TEXT) is True

    (command, kwargs), = calls
    assert command == clipboard["_WINDOWS_CLIPBOARD_WRITE"]
    assert command[0] == "powershell" and "clip.exe" not in command
    assert "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false" in command[-1]
    assert kwargs["input"] == TEXT.encode("utf-8")
    assert clipboard["_native_clipboard_write"]("x" * 70000) is False


def test_windows_paste_reads_utf8_and_keeps_the_text_exactly(clipboard, monkeypatch):
    calls = []

    def check_output(command, **kwargs):
        calls.append(command)
        return "﻿".encode("utf-8") + (TEXT + "\r\nsecond line").encode("utf-8")

    monkeypatch.setattr(subprocess, "check_output", check_output)

    assert clipboard["get_clipboard"](None) == TEXT + "\r\nsecond line"
    assert calls == [clipboard["_WINDOWS_CLIPBOARD_READ"]]
    assert "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false" in calls[0][-1]
    assert "Get-Clipboard -Raw" in calls[0][-1]


def test_unreadable_clipboard_reads_as_nothing(clipboard, monkeypatch):
    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("powershell", 3)

    monkeypatch.setattr(subprocess, "check_output", fail)

    assert clipboard["get_clipboard"](None) is None


def test_window_script_stays_ascii():
    # The script is handed to the window process as text in the console code
    # page, which cannot carry other characters.
    assert launcher._WINDOW_SCRIPT.isascii()
