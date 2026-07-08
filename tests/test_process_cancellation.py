from __future__ import annotations

import threading

from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.process_cancellation import run_cancellable_subprocess


class _FakeProcess:
    pid = 1234

    def __init__(self) -> None:
        self.started = threading.Event()
        self.terminated = threading.Event()
        self.returncode = None

    def communicate(self, timeout=None):
        self.started.set()
        if not self.terminated.wait(timeout=2):
            raise AssertionError("fake process was not terminated")
        self.returncode = -15
        return "partial stdout", "partial stderr"

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.terminated.set()

    def wait(self, timeout=None):
        if not self.terminated.wait(timeout=timeout):
            import subprocess

            raise subprocess.TimeoutExpired(["fake"], timeout=timeout)
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        self.terminated.set()


def test_run_cancellable_subprocess_terminates_process_when_scope_is_cancelled(monkeypatch) -> None:
    import row_bot.process_cancellation as process_cancellation

    fake = _FakeProcess()
    monkeypatch.setattr(process_cancellation.subprocess, "Popen", lambda *_args, **_kwargs: fake)
    scope = CancellationScope()
    result_holder = []

    def run() -> None:
        with use_cancellation_scope(scope):
            result_holder.append(
                run_cancellable_subprocess(["fake"], cwd=".", timeout=30)
            )

    worker = threading.Thread(target=run)
    worker.start()
    assert fake.started.wait(timeout=1)

    scope.cancel("test")
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert fake.terminated.is_set() is True
    assert result_holder[0].cancelled is True
    assert result_holder[0].returncode == 130
    assert result_holder[0].stdout == "partial stdout"
    assert result_holder[0].stderr == "partial stderr"
