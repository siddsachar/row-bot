"""The trusted bootstrap's release request (F6): only an explicit ``true``, and on Linux a command
that ends on its own leaves what it deliberately launched while a stop still ends every child."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

WORKER = Path(__file__).resolve().parents[3] / "src/row_bot/developer/process_worker.py"


def _worker(request: dict) -> tuple[int, list[dict]]:
    process = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(WORKER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        output, _errors = process.communicate(json.dumps(request).encode() + b"\n", timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    return process.returncode, [json.loads(line) for line in output.splitlines()]


@pytest.mark.parametrize("release", ["yes", False, 1, None])
def test_a_release_request_runs_nothing_unless_it_says_true(release, tmp_path):
    marker = tmp_path / "ran"
    request = {"argv": [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
               "owner_id": str(uuid4()), "container_id": "0" * 64, "key": "ab" * 32, "release": release}

    code, frames = _worker(request)

    assert (code, frames) == (126, [])
    assert not marker.exists()


def test_release_needs_the_supervised_owner_fields(tmp_path):
    marker = tmp_path / "ran"

    code, frames = _worker({"argv": [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
                            "release": True})

    assert (code, frames) == (126, [])
    assert not marker.exists()


def _supervised(request: dict) -> tuple[int, list[dict]]:
    """As the host runs it: control input stays open (its end of input is a stop request)."""
    process = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(WORKER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()
        code = process.wait(timeout=30)
        frames = [json.loads(line) for line in process.stdout]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    return code, frames


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux supervised bootstrap")
@pytest.mark.parametrize("release", [True, False])
def test_only_a_released_command_leaves_what_it_launched_running(release):
    request = {"argv": ["/bin/sh", "-c", "setsid sleep 600 >/dev/null 2>&1 & echo $!"],
               "owner_id": str(uuid4()), "container_id": "0" * 64, "key": "ab" * 32}
    if release:
        request["release"] = True

    code, frames = _supervised(request)

    launched = int("".join(frame["text"] for frame in frames if frame["event"] == "output").split()[0])
    try:
        assert code == 0
        assert frames[-1]["event"] == "exited" and frames[-1]["exit_code"] == 0
        assert frames[-1]["receipt"]["payload"]["quiesced"] is True
        time.sleep(0.2)
        if release:
            os.kill(launched, 0)  # Still running.
        else:
            with pytest.raises(ProcessLookupError):
                os.kill(launched, 0)
    finally:
        try:
            os.kill(launched, signal.SIGKILL)
        except ProcessLookupError:
            pass
