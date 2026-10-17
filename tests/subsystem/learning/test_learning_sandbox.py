"""The throwaway sandboxes Learning's tests run in: the network and new processes are off, a
time limit ends the run, nothing leaks in from the environment, and the folder goes away."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from row_bot.learning.sandbox import Sandbox, SandboxError, container_command, run_python

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_a_sandbox_is_a_fresh_folder_outside_the_data_folder_and_goes_away(data_dir) -> None:
    with Sandbox() as box:
        root = box.root
        assert root.is_dir() and data_dir not in root.parents and root.name.startswith("row-bot-learning-")
        box.write("notes/a.txt", "hello")
        assert box.read("notes/a.txt") == "hello" and box.files() == {"notes/a.txt": "hello"}
        for bad in ("../escape.txt", "/etc/passwd", "C:/Windows/win.ini", ""):
            with pytest.raises(SandboxError):
                box.path(bad)
    assert not root.exists()
    with pytest.raises(SandboxError, match="never made inside"):
        Sandbox(parent=data_dir / "learning")


def test_code_runs_with_the_network_off(data_dir) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with Sandbox() as box:
            box.write("reach.py", f"import socket\nsocket.create_connection(('127.0.0.1', {port}), timeout=2)\n")
            box.write("raw.py", f"import _socket\ns = _socket.socket()\ns.connect(('127.0.0.1', {port}))\n")
            box.write("lookup.py", "import socket\nsocket.getaddrinfo('example.com', 443)\n")
            for script in ("reach.py", "raw.py", "lookup.py"):
                result = run_python(box, script, backend="local", timeout=10)
                assert not result.passed and "the network is off in this sandbox" in result.stderr_tail, script
        listener.settimeout(0.2)
        with pytest.raises(OSError):
            listener.accept()  # nothing ever connected
    finally:
        listener.close()


def test_code_cannot_start_other_processes(data_dir) -> None:
    with Sandbox() as box:
        box.write("spawn.py", "import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'print(1)'])\n")
        box.write("system.py", "import os\nos.system('echo hi')\n")
        for script in ("spawn.py", "system.py"):
            result = run_python(box, script, backend="local", timeout=10)
            assert not result.passed and "starting processes is off" in result.stderr_tail
        if sys.platform == "win32":
            # Below the interpreter's guard, the job object allows one process only.
            box.write("winapi.py", "import _winapi\n_winapi.CreateProcess(None, 'cmd.exe /c echo hi', None, None, "
                                   "False, 0, None, None, None)\n")
            result = run_python(box, "winapi.py", backend="local", timeout=10)
            assert not result.passed and "quota" in result.stderr_tail.lower()


def test_nothing_from_the_environment_reaches_the_code(data_dir, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sandbox-test-" + "k" * 20)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    with Sandbox() as box:
        box.write("env.py", "import os\nassert 'OPENAI_API_KEY' not in os.environ\nassert 'HTTPS_PROXY' not in "
                            "os.environ\nassert os.environ['PATH'] == ''\nprint('clean')\n")
        result = run_python(box, "env.py", backend="local", timeout=10)
    assert result.passed and "clean" in result.stdout_tail


def test_unittest_results_come_back_counted(data_dir) -> None:
    with Sandbox() as box:
        box.write("solution.py", "def double(x):\n    return x * 2\n")
        box.write("check_tests.py", "import unittest\nfrom solution import double\n\n\nclass T(unittest.TestCase):\n"
                                    "    def test_one(self):\n        self.assertEqual(double(2), 4)\n\n"
                                    "    def test_two(self):\n        self.assertEqual(double(3), 7)\n")
        result = run_python(box, "check_tests.py", backend="local", timeout=10)
    assert (result.passed, result.tests, result.failures, result.timed_out) == (False, 2, 1, False)


@pytest.mark.slow
def test_a_run_past_its_time_limit_is_ended(data_dir) -> None:
    with Sandbox() as box:
        box.write("loop.py", "while True:\n    pass\n")
        result = run_python(box, "loop.py", backend="local", timeout=1.0)
    assert result.timed_out and not result.passed and result.seconds < 15


def test_only_files_in_its_own_sandbox_folders_are_run(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(SandboxError):
        run_python(tmp_path, "x.py", backend="local")
    with Sandbox() as box, pytest.raises(SandboxError):
        run_python(box, "../x.py", backend="local")


def test_a_container_run_has_no_network_and_capped_resources() -> None:
    command = container_command("docker", "image:tag", Path("/tmp/row-bot-learning-x"), "check_tests.py", 20)
    joined = " ".join(command)
    assert command[:3] == ["docker", "run", "--rm"]
    for expected in ("--network none", "--memory 768m", "--pids-limit 32", "--cap-drop ALL",
                     "--security-opt no-new-privileges", "image:tag timeout 21 python3 -I -S"):
        assert expected in joined
