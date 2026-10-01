"""One Row-Bot launcher per data folder (B214).

Starting Row-Bot again while it ran (a shortcut, the Start menu) started a
second instance on the next port against the same profile, with its own
channels and schedulers: the running app was probed through the launcher ping,
which needs a launch secret the new launcher doesn't have. Now the first
launcher holds a lock in the data folder; a second start asks it to show its
window and exits, and never starts a server while the lock is held.
"""
from __future__ import annotations

import json
import os
import threading

import pytest

from row_bot import launcher
from row_bot.access.launcher_control import LauncherControlServer

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    held: dict = {}
    started: list[str] = []
    monkeypatch.setattr(launcher, "_row_bot_data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_LAUNCH_FILE_LOGGING", True)
    monkeypatch.setattr(launcher, "_held_instance_locks", held)
    monkeypatch.setattr(launcher, "_INSTANCE_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(launcher, "_has_display_server", lambda: True)
    monkeypatch.setattr(launcher, "_show_splash", lambda port: started.append("splash"))
    monkeypatch.setattr(launcher, "_open_in_browser", lambda port: started.append(f"browser:{port}"))
    monkeypatch.setattr(launcher, "_run_direct", lambda args: started.append("server"))
    monkeypatch.setattr(launcher, "_reset_all_local_dbs", lambda: started.append("reset") or 0)

    class Tray:
        def __init__(self, **_kwargs):
            pass

        def run(self):
            started.append("server")

    monkeypatch.setattr(launcher, "RowBotTray", Tray)
    yield tmp_path, started
    for lock in held.values():
        lock.release()


def _hold(folder):
    lock = launcher._InstanceLock(folder / "launcher.lock")
    assert lock.acquire()
    return lock


def _state(folder, *, phase: str, control: LauncherControlServer | None = None) -> None:
    state = {"app": launcher.APP_PING_ID, "pid": os.getpid(), "port": 8080, "phase": phase,
             "session": "first-launch"}
    if control is not None:
        state.update(control_port=control.port, open_token=control.open_token)
    (folder / "launcher_state.json").write_text(json.dumps(state), encoding="utf-8")


def _control(open_window) -> LauncherControlServer:
    control = LauncherControlServer(lambda: None, open_window=open_window)
    control.start()
    return control


def test_a_second_start_shows_the_running_app_and_starts_nothing(data_dir) -> None:
    folder, started = data_dir
    first = _hold(folder)
    opened = threading.Event()
    control = _control(lambda: opened.set() or True)
    _state(folder, phase="running", control=control)
    try:
        with pytest.raises(SystemExit) as done:
            launcher.main([])
    finally:
        control.stop()
        first.release()

    assert done.value.code == 0
    assert opened.is_set()
    assert started == []


def test_a_second_start_while_the_first_is_starting_leaves_the_window_to_it(data_dir, caplog) -> None:
    folder, started = data_dir
    first = _hold(folder)
    _state(folder, phase="starting")
    try:
        with caplog.at_level("INFO", logger="row_bot.launcher"), pytest.raises(SystemExit) as done:
            launcher.main([])
    finally:
        first.release()

    assert done.value.code == 0
    assert started == []
    assert "starting" in caplog.text


def test_a_headless_instance_is_opened_in_the_browser(data_dir) -> None:
    folder, started = data_dir
    first = _hold(folder)
    control = _control(None)
    _state(folder, phase="running", control=control)
    try:
        with pytest.raises(SystemExit) as done:
            launcher.main([])
    finally:
        control.stop()
        first.release()

    assert done.value.code == 0
    assert started == ["browser:8080"]


def test_a_second_server_start_is_refused_at_once(data_dir, caplog, monkeypatch) -> None:
    folder, started = data_dir
    first = _hold(folder)
    control = _control(lambda: True)
    _state(folder, phase="running", control=control)
    monkeypatch.setattr(launcher, "_INSTANCE_WAIT_SECONDS", 60.0)
    try:
        with caplog.at_level("ERROR", logger="row_bot.launcher"), pytest.raises(SystemExit) as done:
            launcher.main(["--server", "--no-open"])
    finally:
        control.stop()
        first.release()

    assert done.value.code == 1
    assert started == []
    assert "already running" in caplog.text


def test_resetting_data_is_refused_while_row_bot_runs(data_dir) -> None:
    folder, started = data_dir
    first = _hold(folder)
    try:
        with pytest.raises(SystemExit) as done:
            launcher.main(["--reset-db"])
    finally:
        first.release()

    assert done.value.code == 1
    assert started == []


def test_a_second_start_takes_over_once_the_first_has_quit(data_dir) -> None:
    folder, started = data_dir
    first = _hold(folder)
    control = _control(lambda: False)  # quitting: it refuses to show a window
    _state(folder, phase="running", control=control)
    threading.Timer(0.3, first.release).start()
    try:
        launcher.main([])
    finally:
        control.stop()

    assert started == ["splash", "server"]
    state = json.loads((folder / "launcher_state.json").read_text(encoding="utf-8"))
    assert state["pid"] == os.getpid() and state["session"] == launcher._LAUNCH_SESSION_ID


def test_nothing_starts_when_the_running_launcher_never_answers(data_dir) -> None:
    folder, started = data_dir
    first = _hold(folder)
    try:
        with pytest.raises(SystemExit) as done:
            launcher.main([])
    finally:
        first.release()

    assert done.value.code == 1
    assert started == []


def test_the_first_launcher_says_it_is_starting_then_how_to_reach_it(data_dir) -> None:
    folder, started = data_dir
    launcher.main([])
    starting = json.loads((folder / "launcher_state.json").read_text(encoding="utf-8"))
    assert started == ["splash", "server"]
    assert starting["phase"] == "starting" and starting["pid"] == os.getpid()

    control = _control(lambda: True)
    reach = (control.port, control.open_token)
    try:
        launcher._write_launcher_state(port=8080, mode="native", owns_server=True,
                                       launcher_control=control)
    finally:
        control.stop()
    running = json.loads((folder / "launcher_state.json").read_text(encoding="utf-8"))
    assert running["phase"] == "running"
    assert (running["control_port"], running["open_token"]) == reach
