from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakePopen:
    pid: int = 4242
    returncode: int | None = None
    terminated: bool = False
    killed: bool = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
