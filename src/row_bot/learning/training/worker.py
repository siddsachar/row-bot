"""The training worker: one owned process per run, out of the app (E15).

Owned like plugin workers (``plugins/worker.py``, ``worker_ownership.py``): on Windows a
private kill-on-close job object holds the worker and everything it starts (Triton
spawns ``ptxas`` and ``tcc``), on macOS and Linux its own process group; stopping ends
the whole tree, and so does the worker exiting by itself (stragglers are ended with
it). It has **no conversation owner**: it isn't a background process of a chat and it
isn't a generation, so nothing a chat does (Stop, delete) reaches it, and it never
counts as the person using Row-Bot.

It is started with the training engine's own interpreter (L2b supplies it; tests and
the browser fixture run the fake trainer with the app's Python) in its run folder, with
a minimal environment: no Row-Bot data folder, keys or settings, its home and temp
folders inside the run, and the engines' telemetry switched off (O27, E14). It reads
only its job spec (sent on its stdin) and its dataset lease (the job's own data
folder). What it says is checked message by message (``protocol.Session``); a worker
that breaks the protocol is ended.
"""

from __future__ import annotations

import io
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from row_bot.learning.training import protocol

logger = logging.getLogger(__name__)

PROTOCOL_PATH = Path(protocol.__file__).resolve()
LOG_FILE = "worker.log"
LOG_TAIL_BYTES = 64 * 1024
STOP_WAIT_SECONDS = 10.0

# O27 / E14: set before any engine import. HF_HUB_OFFLINE holds because the worker never
# downloads (weights arrive through the D13 download, L2b); trainers also get report_to="none".
TELEMETRY_ENV: Mapping[str, str] = {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_UPDATE_CHECK": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "HF_DATASETS_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "UNSLOTH_DISABLE_STATISTICS": "1",
    "UNSLOTH_AUTO_INSTALL": "0",
    "USE_HUB_KERNELS": "NO",
    "WANDB_MODE": "disabled",
    "WANDB_DISABLED": "true",
}
# The only variables carried over from the app's environment (a process needs them to run).
_CARRIED = frozenset({"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE",
                      "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL"})


class WorkerError(RuntimeError):
    """A worker that couldn't start or be owned; ``code`` is stable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EngineCommand:
    """How to start a worker: the engine's interpreter and entry point, and its own variables."""

    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    name: str = "engine"


class Engine(Protocol):
    """The training engine (L2b): whether it's installed with its weights, and how to run it."""

    def ready(self) -> bool: ...

    def command(self, job: Any) -> EngineCommand: ...


class NoEngine:
    """No training engine is installed yet (L2b installs one): nothing can train."""

    def ready(self) -> bool:
        return False

    def command(self, job: Any) -> EngineCommand:
        raise WorkerError("engine_missing")


def worker_environment(run: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The worker's whole environment: a few system variables, its own folders, telemetry off."""

    work = Path(run) / "work"
    temp = work / "tmp"
    environment = {key: value for key, value in os.environ.items() if key.upper() in _CARRIED}
    for key, value in (extra or {}).items():
        if key.upper() not in TELEMETRY_ENV and not key.upper().startswith("ROW_BOT"):
            environment[str(key)] = str(value)
    environment.update({
        "HOME": str(work), "USERPROFILE": str(work), "APPDATA": str(work), "LOCALAPPDATA": str(work),
        "XDG_CACHE_HOME": str(temp), "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "PIP_CONFIG_FILE": os.devnull, "NETRC": str(work / ".disabled-netrc"),
    })
    environment.update(TELEMETRY_ENV)
    return environment


def venv_launcher(interpreter: str) -> bool:
    """Whether a Windows interpreter is a virtual environment's ``Scripts\\python.exe`` launcher.

    That launcher starts the real interpreter as its own child straight away, before the
    worker's job object can own it, so the tree could outlive a Stop. Workers run the
    base interpreter instead (``pyvenv.cfg``'s ``home``) with an entry point that adds the
    environment's site-packages, as plugin workers do.
    """

    if os.name != "nt":
        return False
    try:
        path = Path(interpreter)
        return path.parent.name.lower() == "scripts" and (path.parent.parent / "pyvenv.cfg").is_file()
    except (OSError, ValueError):
        return False


def _owner(process: subprocess.Popen) -> Any:
    if os.name != "nt":
        return None
    from row_bot.plugins.worker_ownership import WindowsJob

    return WindowsJob(process)


class TrainingWorker:
    """One worker process for one run of a job.

    ``on_message(message)`` gets each accepted message, in order, on the reader thread;
    ``on_exit(code, violation)`` once, after the process and everything it started have
    ended and every message was delivered (``violation`` is the protocol error that
    ended it, if any).
    """

    def __init__(
        self,
        command: EngineCommand,
        run: Path,
        spec: dict[str, Any],
        *,
        on_message: Callable[[dict[str, Any]], None],
        on_exit: Callable[[int | None, str | None], None],
    ) -> None:
        self.command = command
        self.run = Path(run)
        self.spec = protocol.validate_spec(spec, self.run)
        self.session = protocol.Session(self.spec)
        self._on_message = on_message
        self._on_exit = on_exit
        self._process: subprocess.Popen | None = None
        self._job: Any = None
        self._write_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._tail = bytearray()
        self._reader: threading.Thread | None = None
        self._exited = threading.Event()
        self.violation: str | None = None
        self.returncode: int | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def alive(self) -> bool:
        return self._process is not None and not self._exited.is_set()

    def start(self) -> None:
        if self._process is not None:
            raise WorkerError("worker_started")
        if not self.command.argv or venv_launcher(self.command.argv[0]):
            raise WorkerError("worker_interpreter_unowned")
        work = self.run / "work"
        (work / "tmp").mkdir(parents=True, exist_ok=True)
        (self.run / "checkpoints").mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "cwd": str(work), "env": worker_environment(self.run, self.command.env), "shell": False,
            "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "bufsize": 0,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(list(self.command.argv), **kwargs)
        except OSError as error:
            raise WorkerError("worker_start_failed") from error
        try:
            # The worker waits for its spec: nothing it runs exists before it is owned.
            self._job = _owner(process)
        except Exception:
            process.kill()
            process.wait(STOP_WAIT_SECONDS)
            raise WorkerError("worker_ownership_unavailable") from None
        self._process = process
        self._reader = threading.Thread(target=self._read, name="learning-trainer-reader", daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain, name="learning-trainer-log", daemon=True).start()
        threading.Thread(target=self._wait, name="learning-trainer-wait", daemon=True).start()
        self.send(protocol.start(self.spec))

    def send(self, message: dict[str, Any]) -> bool:
        """Write one host message; False when the worker is gone."""

        raw = protocol.encode(message)
        process = self._process
        if process is None or process.stdin is None or self._exited.is_set():
            return False
        with self._write_lock:
            try:
                view = memoryview(raw)
                while view:
                    written = process.stdin.write(view)
                    if not written:
                        return False
                    view = view[written:]
                process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def request_pause(self) -> bool:
        self.session.request_pause()
        return self.send(protocol.pause())

    def advance(self, steps: int = 1) -> bool:
        """Let a fake-clocked worker take more steps."""

        return self.send(protocol.advance(steps))

    def stop(self) -> bool:
        """End the worker and its whole tree now. True when everything has ended."""

        process = self._process
        if process is None:
            return True
        with self._stop_lock:
            ended = self._end_tree(process)
            try:
                process.wait(STOP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                return False
            return ended

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until the worker has ended and its exit was reported."""

        return self._exited.wait(timeout)

    # ── Threads ─────────────────────────────────────────────────────────

    def _end_tree(self, process: subprocess.Popen) -> bool:
        if self._job is not None:
            return bool(self._job.close())
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                return False
            return True
        if process.poll() is None:
            process.kill()
        return True

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        stream = io.BufferedReader(process.stdout, buffer_size=protocol.MAX_FRAME)
        try:
            while True:
                raw = stream.readline(protocol.MAX_FRAME + 1)
                if not raw:
                    break
                try:
                    message = self.session.accept(protocol.decode(raw))
                except protocol.ProtocolError as error:
                    self.violation = error.code
                    logger.warning("Learning ended a training worker that broke the protocol (%s)", error.code)
                    self._end_tree(process)
                    break
                try:
                    self._on_message(message)
                except Exception:
                    logger.exception("Learning could not handle a training message")
        except (OSError, ValueError):
            pass

    def _drain(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for block in iter(lambda: process.stderr.read(8192), b""):
                self._tail.extend(block)
                if len(self._tail) > LOG_TAIL_BYTES:
                    del self._tail[: len(self._tail) - LOG_TAIL_BYTES]
        except (OSError, ValueError):
            pass

    def _wait(self) -> None:
        process = self._process
        assert process is not None
        code = process.wait()
        if self._reader is not None:
            self._reader.join(STOP_WAIT_SECONDS)
        with self._stop_lock:
            # Anything the worker started and left behind ends with it.
            self._end_tree(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        try:
            (self.run / LOG_FILE).write_bytes(bytes(self._tail))
        except OSError:
            pass
        self.returncode = code
        try:
            self._on_exit(code, self.violation)
        except Exception:
            logger.exception("Learning could not handle a training worker's exit")
        finally:
            self._exited.set()


__all__ = [
    "Engine",
    "EngineCommand",
    "NoEngine",
    "PROTOCOL_PATH",
    "TELEMETRY_ENV",
    "TrainingWorker",
    "WorkerError",
    "venv_launcher",
    "worker_environment",
]
