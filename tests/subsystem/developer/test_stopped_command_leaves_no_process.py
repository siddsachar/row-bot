"""Stopping a workspace command ends the command's processes, not only the worker (B191).

On Windows the command runs under a small worker process inside a kill-on-close
job. Started through a virtual environment's `python.exe` redirector, the worker
sat in the redirector's own job, which lets children break away silently: the
command left the owning job and outlived Stop.
"""
from __future__ import annotations

import sys
import time
import uuid

import psutil
import pytest

from row_bot.developer import runtime


pytestmark = [pytest.mark.subsystem, pytest.mark.platform, pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Client-platform local process containment is supported on Windows and Linux",
)]


def _processes_with(marker: str) -> list[psutil.Process]:
    found = []
    for process in psutil.process_iter(["cmdline"]):
        try:
            if any(marker in part for part in process.info["cmdline"] or ()):
                found.append(process)
        except psutil.Error:
            continue
    return found


def test_stop_ends_a_long_running_command(tmp_path) -> None:
    marker = f"row-bot-b191-{uuid.uuid4().hex}"
    state = runtime.launch_tracked_process(
        tmp_path, [sys.executable, "-c", f"import threading;threading.Event().wait()  # {marker}"],
        "synthetic long-running command",
    )
    try:
        deadline = time.monotonic() + 30
        while not _processes_with(marker) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _processes_with(marker), "the command never started"

        runtime.stop_tracked_process(state)
        assert state.done.wait(30) and state.quiesced

        deadline = time.monotonic() + 30  # Windows lists a terminated process until it is torn down.
        while _processes_with(marker) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _processes_with(marker), "the stopped command is still running"
    finally:
        for process in _processes_with(marker):
            process.kill()
