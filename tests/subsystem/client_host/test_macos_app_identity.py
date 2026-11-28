"""On macOS the processes that open windows run as the Row-Bot app (B385).

The macOS host (installer/macos/RowBotTrayHost.m) tells the launcher its own program; the
launcher runs the app window and the splash with ``<host> --run-python <this Python>``, so
the Dock, Cmd-Tab and Force Quit show Row-Bot and its icon, not "Python 3.12". A host older
than 5.3 doesn't say, and they run as this Python, as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import row_bot.launcher as launcher

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def host(tmp_path, monkeypatch) -> Path:
    program = tmp_path / "Row-Bot.app" / "Contents" / "MacOS" / "row-bot"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"\xcf\xfa\xed\xfe")
    program.chmod(0o755)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.os, "access", lambda path, mode: Path(path) == program)
    monkeypatch.setenv(launcher._HOST_EXECUTABLE_ENV, str(program))
    return program


def _window_command(monkeypatch) -> list[str]:
    started = []

    class Window:
        pid = 2222
        stdin = None

        def __init__(self, args, **kwargs):
            started.append(args)
            self.stdin = self
            Path(kwargs["env"]["ROW_BOT_WINDOW_READY_FILE"]).write_text("ready", encoding="utf-8")

        def write(self, _text):
            pass

        def close(self):
            pass

        def poll(self):
            return None

        def wait(self):
            return 0

    monkeypatch.setattr(launcher, "_has_display_server", lambda: True)
    monkeypatch.setattr(launcher.subprocess, "Popen", Window)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    assert launcher._open_window(8092, 18092) is not None
    return started[0]


def test_the_window_runs_as_the_row_bot_app(host, monkeypatch) -> None:
    command = _window_command(monkeypatch)

    assert command[:4] == [str(host), "--run-python", sys.base_prefix, "-"]


def test_the_splash_runs_as_the_row_bot_app(host, tmp_path, monkeypatch) -> None:
    started = []

    def popen(command, **_kwargs):
        started.append(command)
        raise OSError("not started in this test")

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    launcher._start_launcher_helper(name="splash_tk", source="pass", args=[], ready_marker=tmp_path / "ready",
                                    log_path=tmp_path / "splash.log", ready_timeout=0.1)

    assert started[0][:4] == [str(host), "--run-python", sys.base_prefix, "-c"]


def test_under_an_older_host_the_window_runs_as_this_python(host, monkeypatch) -> None:
    monkeypatch.delenv(launcher._HOST_EXECUTABLE_ENV)

    assert _window_command(monkeypatch)[:2] == [sys.executable, "-"]


def test_elsewhere_the_window_runs_as_this_python(host, monkeypatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "win32")

    assert _window_command(monkeypatch)[:2] == [sys.executable, "-"]
