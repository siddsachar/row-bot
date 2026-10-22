"""Every network connection a process tree holds, sampled while an opt-in Learning end-to-end test
runs (the training engine must make none but loopback)."""

from __future__ import annotations

import os
import threading
from typing import Any


class Connections:
    """Every connection the test process's children (and, with ``me``, the test process itself)
    hold, sampled about ten times a second (``ticks``: how many times it looked)."""

    def __init__(self, *, me: bool = False) -> None:
        import psutil

        self._psutil = psutil
        self._me = me
        self.ticks = 0
        self.samples = 0
        self.sockets: set[tuple] = set()
        self.remote: set[tuple] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        me = self._psutil.Process(os.getpid())
        while not self._stop.is_set():
            self.ticks += 1
            try:
                children = me.children(recursive=True)
            except self._psutil.Error:
                children = []
            for child in ([me] if self._me else []) + children:
                try:
                    for connection in child.net_connections(kind="inet"):
                        self.samples += 1
                        if connection.raddr:
                            item = (connection.raddr.ip, connection.raddr.port)
                            self.sockets.add(item)
                            if not (item[0].startswith("127.") or item[0] == "::1"):
                                self.remote.add(item)
                except self._psutil.Error:
                    continue
            self._stop.wait(0.1)

    def summary(self) -> dict[str, Any]:
        return {"ticks": self.ticks, "samples": self.samples, "sockets": len(self.sockets),
                "remote": sorted(map(list, self.remote))}

    def __enter__(self) -> Connections:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(5)


class GpuMemory:
    """The graphics card's memory in use, sampled with ``nvidia-smi`` every ``every`` seconds (peak, MiB)."""

    def __init__(self, every: float = 2.0) -> None:
        self.every = every
        self.peak = 0
        self.samples = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        import subprocess

        while not self._stop.is_set():
            try:
                output = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                        capture_output=True, text=True, timeout=10,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                used = max(int(float(line.strip())) for line in output.splitlines() if line.strip())
                self.peak = max(self.peak, used)
                self.samples += 1
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self._stop.wait(self.every)

    def __enter__(self) -> GpuMemory:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(15)


__all__ = ["Connections", "GpuMemory"]
