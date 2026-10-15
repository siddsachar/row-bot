"""Real background processes: Stop ends the whole tree, quitting stops everything, a long
command carries on in the background, and a server's local address is verified (F1, F6)."""

from __future__ import annotations

import os
import socket
import sys
import time

import psutil
import pytest

from row_bot import background_processes as processes
from row_bot.tools import shell_tool

pytestmark = [pytest.mark.subsystem, pytest.mark.platform, pytest.mark.slow]

FAMILY = """
import subprocess, sys, time
mode = sys.argv[1]
if mode == "grandchild":
    time.sleep(600)
elif mode == "child":
    grandchild = subprocess.Popen([sys.executable, __file__, "grandchild"])
    print("grandchild", grandchild.pid, flush=True)
    time.sleep(600)
else:
    child = subprocess.Popen([sys.executable, __file__, "child"])
    print("child", child.pid, flush=True)
    time.sleep(600)
"""

OPENER = """
import subprocess, sys
kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
opened = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], **kwargs)
print("opened", opened.pid, flush=True)
"""

SERVER = """
import socket, time
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
print(f"Serving on http://127.0.0.1:{server.getsockname()[1]}/", flush=True)
time.sleep(600)
"""


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    yield
    processes.stop_all(timeout=20)


def _python(tmp_path, name: str, source: str, *args: str) -> str:
    script = tmp_path / f"{name}.py"
    script.write_text(source, encoding="utf-8", newline="\n")
    parts = [sys.executable, str(script), *args]
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts)
    return " ".join("'" + part.replace("'", "'\\''") + "'" for part in parts)


def _start(tmp_path, command: str, conversation: str = "conversation-a") -> processes.BackgroundProcess:
    return processes.start(conversation, shell_tool.shell_argv(command, report_cwd=False), command=command,
                           folder=str(tmp_path))


def _pid_after(record: processes.BackgroundProcess, label: str, timeout: float = 30) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in record.log.text()[0].splitlines():
            if line.startswith(label + " "):
                return int(line.split()[1])
        time.sleep(0.1)
    raise AssertionError(f"no {label} line in {record.log.text()[0]!r}")


def _gone(pid: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def _kill(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass


def test_stop_ends_the_whole_tree_a_child_and_its_grandchild(tmp_path):
    record = _start(tmp_path, _python(tmp_path, "family", FAMILY, "parent"))
    child = _pid_after(record, "child")
    grandchild = _pid_after(record, "grandchild")
    assert psutil.pid_exists(child) and psutil.pid_exists(grandchild)

    processes.stop(record.process_id)

    assert processes.wait(record, 30)
    assert record.status == "stopped"
    assert _gone(child) and _gone(grandchild)


def test_quitting_stops_every_process(tmp_path):
    records = [_start(tmp_path, _python(tmp_path, "family", FAMILY, "parent"), conversation)
               for conversation in ("conversation-a", "conversation-b")]
    children = [_pid_after(record, "child") for record in records]

    assert processes.stop_all(by="quit", timeout=30) == 2

    assert [record.status for record in records] == ["stopped", "stopped"]
    assert all(_gone(pid) for pid in children)


def test_a_command_past_the_inline_limit_becomes_a_background_process_and_the_turn_returns(tmp_path):
    session = shell_tool.ShellSession(str(tmp_path))
    command = _python(tmp_path, "family", FAMILY, "parent")
    started = time.monotonic()

    result = session.run_command(command, timeout=2, conversation_id="conversation-a")

    assert time.monotonic() - started < 20
    assert result["exit_code"] is None and result["process_id"].startswith("proc-")
    record = processes.get(result["process_id"])
    assert record.kind == "command" and record.running
    child = _pid_after(record, "child")
    processes.stop(record.process_id, by="agent")
    assert processes.wait(record, 30) and _gone(child)


def test_a_finished_command_leaves_what_it_opened_but_a_process_takes_it_along(tmp_path):
    command = _python(tmp_path, "opener", OPENER)
    finished = shell_tool.ShellSession(str(tmp_path)).run_command(command, conversation_id="conversation-a")
    opened = int(finished["output"].split()[1])
    try:
        assert finished["exit_code"] == 0
        time.sleep(1)
        assert not _gone(opened, timeout=0.5)  # An app it opened keeps running, as before.
    finally:
        _kill(opened)

    record = _start(tmp_path, command)
    owned = _pid_after(record, "opened")
    assert processes.wait(record, 30)
    assert _gone(owned)  # start_process takes everything it started with it.


def test_a_server_is_verified_by_the_port_it_listens_on(tmp_path):
    record = _start(tmp_path, _python(tmp_path, "server", SERVER))
    deadline = time.monotonic() + 30
    while not record.urls and time.monotonic() < deadline:
        time.sleep(0.1)
    origin = record.urls[0]
    port = int(origin.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        pass

    deadline = time.monotonic() + 30
    while not processes.owns_local_origin(origin) and time.monotonic() < deadline:
        record._ports_checked = 0.0
        time.sleep(0.2)

    assert processes.owns_local_origin(origin)
    assert any(server["verified"] and server["port"] == port for server in processes.local_servers())
    processes.stop(record.process_id)
    assert processes.wait(record, 30)
    assert not processes.owns_local_origin(origin)
