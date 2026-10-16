"""Throwaway places for Learning's tests to run in (E11, D17).

- :class:`Sandbox`: a fresh folder in the system's temporary folder (never inside
  Row-Bot's data folder), removed when the test is done. A task's files are seeded
  there and every file tool the model uses is confined to it.
- :func:`run_python`: runs a Python file from a sandbox in a child process with the
  network off, a time limit, an output cap and a scrubbed environment, and ends the
  whole process tree at the limit.

Two backends:

- ``container``: Developer Studio's container runtime (Docker or Podman) with
  ``--network none``, a memory, CPU and process cap and the folder mounted, used only
  when the engine answers and the Developer sandbox image is already on this computer
  (Row-Bot never pulls an image here).
- ``local`` (the fallback): ``python -I -S`` in the folder, with a scrubbed
  environment (no keys, no proxies, an empty ``PATH``), socket creation, name lookups
  and process creation refused inside the interpreter before the task's code is
  imported, and on Windows a job object that allows one process (so no child
  processes), caps its memory and ends it at the limit; on Linux and macOS resource
  limits (CPU time, memory, file size, open files) and a process group that is ended
  at the limit.

What ``local`` can't stop: code that deliberately undoes the interpreter's guards
(``ctypes``, re-importing ``_socket`` by other means) can reach the network on Linux
and macOS, and file access is not confined to the folder by the OS. It runs only
code Row-Bot's own tests check (small coding tasks and practice); a container is the
stronger option when available.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Backend = Literal["auto", "local", "container"]

DEFAULT_TIMEOUT = 20.0
OUTPUT_TAIL = 4_000
MEMORY_LIMIT = 768 * 1024 * 1024
_PREFIX = "row-bot-learning-"
_BOOTSTRAP = "_row_bot_sandbox_boot.py"
_RESULT = "_row_bot_sandbox_result.json"

# Installed in the child before the task's code runs: the network and new processes are off.
_GUARD = r'''
import os, socket, subprocess, sys

def _refuse(*args, **kwargs):
    raise PermissionError("the network is off in this sandbox")

class _NoSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise PermissionError("the network is off in this sandbox")

def _no_process(*args, **kwargs):
    raise PermissionError("starting processes is off in this sandbox")

socket.socket = _NoSocket
socket.SocketType = _NoSocket
for _name in ("create_connection", "create_server", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
              "gethostbyaddr", "socketpair", "fromfd"):
    if hasattr(socket, _name):
        setattr(socket, _name, _refuse)
import _socket
_socket.socket = _NoSocket
for _name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
    if hasattr(_socket, _name):
        setattr(_socket, _name, _refuse)
subprocess.Popen = _no_process
for _name in ("system", "popen", "execv", "execve", "execl", "execle", "execlp", "execlpe", "execvp", "execvpe",
              "spawnv", "spawnve", "spawnl", "spawnle", "fork", "forkpty", "posix_spawn", "posix_spawnp", "startfile"):
    if hasattr(os, _name):
        setattr(os, _name, _no_process)
'''

_RUNNER = r'''
import json, runpy, sys
sys.stdin.read()  # the parent closes stdin once the process limits are in place
sys.path.insert(0, ".")
sys.argv = [sys.argv[1]]
target = sys.argv[0]
result = {"ok": False}
try:
    if target.endswith("_tests.py"):
        import unittest
        suite = unittest.defaultTestLoader.loadTestsFromName(target[:-3])
        outcome = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
        result = {"ok": outcome.wasSuccessful(), "tests": outcome.testsRun,
                  "failures": len(outcome.failures), "errors": len(outcome.errors)}
    else:
        runpy.run_path(target, run_name="__main__")
        result = {"ok": True}
finally:
    with open("''' + _RESULT + r'''", "w", encoding="utf-8") as handle:
        json.dump(result, handle)
sys.exit(0 if result.get("ok") else 1)
'''


class SandboxError(RuntimeError):
    """A sandbox could not be made or used safely."""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


class Sandbox:
    """A throwaway folder for one task. Use it as a context manager; ``close()`` removes it.

    The folder is made in the system's temporary folder, never in Row-Bot's data folder.
    :meth:`path` resolves a task-relative path and refuses anything outside the folder.
    """

    def __init__(self, *, parent: Path | str | None = None) -> None:
        base = Path(parent) if parent is not None else Path(tempfile.gettempdir())
        from row_bot.data_paths import get_row_bot_data_dir

        data_dir = get_row_bot_data_dir(create=False)
        if _inside(base, data_dir):
            raise SandboxError("a sandbox is never made inside Row-Bot's data folder")
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=str(base))).resolve()
        self.closed = False

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def path(self, relative: str) -> Path:
        """The sandbox path for a task-relative path; refuses absolute paths and escapes."""

        text = str(relative or "").replace("\\", "/").strip()
        if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            raise SandboxError(f"{relative!r} is not a path inside the task's folder")
        target = (self.root / text).resolve()
        if not _inside(target, self.root):
            raise SandboxError(f"{relative!r} is outside the task's folder")
        return target

    def write(self, relative: str, text: str) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(str(text).encode("utf-8"))
        return target

    def read(self, relative: str) -> str | None:
        target = self.path(relative)
        if not target.is_file():
            return None
        return target.read_bytes().decode("utf-8", "replace")

    def files(self) -> dict[str, str]:
        """Every text file in the folder (task-relative paths, ``/`` separators)."""

        found: dict[str, str] = {}
        for item in sorted(self.root.rglob("*")):
            if item.is_file() and not item.is_symlink() and not item.name.startswith("_row_bot_sandbox"):
                relative = item.relative_to(self.root).as_posix()
                if "__pycache__" in relative:
                    continue
                found[relative] = item.read_bytes().decode("utf-8", "replace")
        return found

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if not self.root.name.startswith(_PREFIX) or self.root.is_symlink():
            logger.warning("Learning left a sandbox it did not recognise in place")
            return
        shutil.rmtree(self.root, ignore_errors=True)


@dataclass(frozen=True)
class RunResult:
    """How a sandboxed run ended. ``passed`` is the runner's own verdict (tests passed,
    or the script ran to the end)."""

    returncode: int | None
    timed_out: bool
    passed: bool
    backend: str
    seconds: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    tests: int = 0
    failures: int = 0
    errors: int = 0


def _scrubbed_env(folder: Path) -> dict[str, str]:
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PATH": "",
        "HOME": str(folder),
        "USERPROFILE": str(folder),
        "TMP": str(folder),
        "TEMP": str(folder),
        "TMPDIR": str(folder),
        "NO_PROXY": "*",
    }
    for name in ("SYSTEMROOT", "SystemRoot", "WINDIR"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - OUTPUT_TAIL))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


class _WindowsLimits:
    """A job object for one process: one process only, a memory cap, ended on close."""

    def __init__(self, process: subprocess.Popen[bytes], memory: int) -> None:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("user_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                        ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise SandboxError("sandbox_limits_unavailable")
        limits = Extended()
        # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS (one process: no children) | PROCESS_MEMORY
        limits.basic.flags = 0x2000 | 0x0008 | 0x0100
        limits.basic.active_limit = 1
        limits.process_memory = memory
        assigned = kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) \
            and kernel.AssignProcessToJobObject(self._handle, int(process._handle))  # type: ignore[attr-defined]
        if not assigned:
            kernel.CloseHandle(self._handle)
            self._handle = None
            raise SandboxError("sandbox_limits_unavailable")

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, None
            self._kernel.TerminateJobObject(handle, 137)
            self._kernel.CloseHandle(handle)


def _posix_limits(timeout: float, memory: int) -> None:  # pragma: no cover - runs in the child on POSIX
    import resource

    seconds = int(timeout) + 2
    for limit, value in ((resource.RLIMIT_CPU, seconds), (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
                         (resource.RLIMIT_NOFILE, 64)):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass
    if sys.platform != "darwin":  # macOS does not enforce RLIMIT_AS
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        except (ValueError, OSError):
            pass


def _prepare(folder: Path, target: str) -> None:
    (folder / _BOOTSTRAP).write_bytes((_GUARD + "\n" + _RUNNER).encode("utf-8"))
    (folder / _RESULT).unlink(missing_ok=True)
    if not (folder / target).is_file():
        raise SandboxError(f"{target!r} is not in the sandbox")


def _result(folder: Path, returncode: int | None, timed_out: bool, backend: str, started: float,
            stdout: Path, stderr: Path) -> RunResult:
    try:
        verdict = json.loads((folder / _RESULT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        verdict = {}
    return RunResult(
        returncode=returncode,
        timed_out=timed_out,
        passed=bool(verdict.get("ok")) and returncode == 0 and not timed_out,
        backend=backend,
        seconds=round(time.monotonic() - started, 3),
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        tests=int(verdict.get("tests") or 0),
        failures=int(verdict.get("failures") or 0),
        errors=int(verdict.get("errors") or 0),
    )


def _interpreter() -> str:
    """The base interpreter: a virtual environment's launcher would start a second process."""

    base = getattr(sys, "_base_executable", "") or sys.executable
    return base if Path(base).is_file() else sys.executable


def _run_local(folder: Path, target: str, timeout: float) -> RunResult:
    outputs = Path(tempfile.mkdtemp(prefix=_PREFIX + "out-"))
    stdout, stderr = outputs / "stdout.txt", outputs / "stderr.txt"
    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    try:
        with open(stdout, "wb") as out, open(stderr, "wb") as err:
            kwargs: dict[str, object] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            else:
                kwargs["start_new_session"] = True
                kwargs["preexec_fn"] = lambda: _posix_limits(timeout, MEMORY_LIMIT)
            process = subprocess.Popen(  # noqa: S603 - our own interpreter on our own runner
                [_interpreter(), "-I", "-S", _BOOTSTRAP, target],
                cwd=str(folder), env=_scrubbed_env(folder), stdin=subprocess.PIPE, stdout=out, stderr=err,
                **kwargs,  # type: ignore[arg-type]
            )
            limits = None
            try:
                if os.name == "nt":
                    try:
                        limits = _WindowsLimits(process, MEMORY_LIMIT)
                    except SandboxError:
                        process.kill()
                        raise
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _end(process, limits)
                    returncode = process.wait(timeout=10)
            finally:
                if limits is not None:
                    limits.close()
        return _result(folder, returncode, timed_out, "local", started, stdout, stderr)
    finally:
        shutil.rmtree(outputs, ignore_errors=True)


def _end(process: subprocess.Popen[bytes], limits: _WindowsLimits | None) -> None:
    if limits is not None:
        limits.close()
        return
    try:
        import signal

        os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        process.kill()


_container_probe: tuple[str, str] | None = None
_probe_lock = threading.Lock()


def container_runtime() -> tuple[str, str] | None:
    """(binary, image) when a container engine answers and the Developer image is local; cached."""

    global _container_probe
    with _probe_lock:
        if _container_probe is not None:
            return _container_probe if _container_probe[0] else None
        found = ("", "")
        try:
            from row_bot.developer import sandbox_runtime
            from row_bot.developer.state import DEFAULT_SANDBOX_IMAGE

            probe = sandbox_runtime.detect_container_runtime()
            if probe.available and sandbox_runtime._docker_image_exists(probe.binary, DEFAULT_SANDBOX_IMAGE):
                found = (probe.binary, DEFAULT_SANDBOX_IMAGE)
        except Exception:
            logger.debug("Learning found no container runtime", exc_info=True)
        _container_probe = found
        return found if found[0] else None


def container_command(binary: str, image: str, folder: Path, target: str, timeout: float) -> list[str]:
    """The ``docker run`` for one sandboxed run: no network, capped, the folder mounted, removed after."""

    return [
        binary, "run", "--rm", "--network", "none", "--memory", f"{MEMORY_LIMIT // (1024 * 1024)}m",
        "--cpus", "1", "--pids-limit", "32", "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--env", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{folder}:/work", "-w", "/work", image,
        "timeout", str(int(timeout) + 1), "python3", "-I", "-S", _BOOTSTRAP, target,
    ]


def _run_container(folder: Path, target: str, timeout: float, runtime: tuple[str, str]) -> RunResult:
    outputs = Path(tempfile.mkdtemp(prefix=_PREFIX + "out-"))
    stdout, stderr = outputs / "stdout.txt", outputs / "stderr.txt"
    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    try:
        with open(stdout, "wb") as out, open(stderr, "wb") as err:
            try:
                completed = subprocess.run(  # noqa: S603 - the container engine Developer Studio uses
                    container_command(runtime[0], runtime[1], folder, target, timeout),
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout + 30, check=False,
                )
                returncode = completed.returncode
                timed_out = returncode == 124
            except subprocess.TimeoutExpired:
                timed_out = True
        return _result(folder, returncode, timed_out, "container", started, stdout, stderr)
    finally:
        shutil.rmtree(outputs, ignore_errors=True)


def run_python(sandbox: Sandbox | Path, target: str, *, timeout: float = DEFAULT_TIMEOUT,
               backend: Backend = "auto") -> RunResult:
    """Run ``target`` (a ``.py`` file in the sandbox folder; a ``*_tests.py`` file runs as
    unittest tests) with the network off and a time limit."""

    folder = sandbox.root if isinstance(sandbox, Sandbox) else Path(sandbox)
    if not folder.name.startswith(_PREFIX):
        raise SandboxError("Learning runs code only in its own sandbox folders")
    name = str(target or "").replace("\\", "/")
    if "/" in name or not name.endswith(".py"):
        raise SandboxError("the file to run is a .py file at the top of the sandbox")
    _prepare(folder, name)
    timeout = max(0.5, float(timeout))
    if backend in ("auto", "container"):
        runtime = container_runtime()
        if runtime is not None:
            return _run_container(folder, name, timeout, runtime)
        if backend == "container":
            raise SandboxError("no container runtime with the sandbox image is available")
    return _run_local(folder, name, timeout)


__all__ = [
    "Backend",
    "DEFAULT_TIMEOUT",
    "RunResult",
    "Sandbox",
    "SandboxError",
    "container_command",
    "container_runtime",
    "run_python",
]
