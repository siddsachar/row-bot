"""The server never outlives its launcher on Windows, however the launcher ends.

A launcher that was killed, crashed or was ended by its quit watchdog never stopped its
server, and Windows doesn't end a child with its parent: the server ran on alone, the
next start adopted it, and quitting that one left it running (20 Nov).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import row_bot.launcher as launcher

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

_SRC = Path(launcher.__file__).resolve().parents[1]


def test_every_server_the_launcher_starts_is_tied_to_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    started, tied = [], []

    def fake_popen(cmd, **_kwargs):
        started.append(SimpleNamespace(pid=4242 + len(started), cmd=cmd))
        return started[-1]

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "_end_with_this_launcher", lambda proc: tied.append(proc) or True)
    server = launcher._RowBotProcess(port=8417, host="127.0.0.1")
    try:
        server.start()
        server._proc = None  # a restart starts a new one
        server.start()
    finally:
        server._close_log_handle()

    assert tied == started and len(started) == 2



def test_the_window_the_launcher_opens_is_tied_to_it_too(tmp_path, monkeypatch) -> None:
    # A window left after its launcher ended showed a dead app (F16).
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    tied = []

    class FakeWindow:
        pid = 4343
        stdin = SimpleNamespace(write=lambda _text: None, close=lambda: None)

        def poll(self):
            return None

    window = FakeWindow()
    monkeypatch.setattr(launcher, "_has_display_server", lambda: True)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_args, **_kwargs: window)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher, "_end_with_this_launcher", lambda proc: tied.append(proc) or True)

    assert launcher._open_window(port=8417, control_port=18417) is window
    assert tied == [window]

_STAND_IN = textwrap.dedent(
    """
    import subprocess, sys, time
    sys.path.insert(0, sys.argv[1])
    from row_bot.launcher import _end_with_this_launcher
    server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                              creationflags=subprocess.CREATE_NO_WINDOW)
    tied = _end_with_this_launcher(server) if sys.argv[2] == "tie" else False
    print(server.pid, tied, flush=True)
    time.sleep(120)
    """
)


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
@pytest.mark.parametrize("tie", [True, False], ids=["tied", "untied"])
def test_a_killed_launcher_takes_its_server_with_it(tmp_path, tie: bool) -> None:
    psutil = pytest.importorskip("psutil")
    env = dict(os.environ, ROW_BOT_DATA_DIR=str(tmp_path), ROW_BOT_INSTALL_COUNT="0")
    stand_in = subprocess.Popen(
        [sys.executable, "-c", _STAND_IN, str(_SRC), "tie" if tie else "leave"],
        env=env, stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    server_tree = []
    try:
        pid, tied = stand_in.stdout.readline().split()
        assert tied == str(tie)
        server = psutil.Process(int(pid))
        # A venv's python.exe runs the real interpreter as its child: that one must go too.
        in_venv = sys.prefix != sys.base_prefix
        deadline = time.monotonic() + 10
        while True:
            server_tree = [server, *server.children(recursive=True)]
            if not in_venv or len(server_tree) > 1 or time.monotonic() > deadline:
                break
            time.sleep(0.05)

        stand_in.kill()  # ends without any clean-up, as a crash or the quit watchdog would
        stand_in.wait(10)
        _gone, alive = psutil.wait_procs(server_tree, timeout=10)

        assert bool(alive) is not tie
    finally:
        if stand_in.poll() is None:
            stand_in.kill()
        for process in server_tree:
            try:
                process.kill()
            except psutil.Error:
                pass
