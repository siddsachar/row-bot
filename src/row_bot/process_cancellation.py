"""Cancellation-aware helpers for Row-Bot owned subprocesses."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Sequence

from row_bot.cancellation import current_cancellation_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessRunResult:
    args: Sequence[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False


def run_cancellable_subprocess(
    args: Sequence[str],
    *,
    cwd: str,
    timeout: int | float,
    text: bool = True,
    encoding: str | None = None,
    errors: str | None = None,
) -> ProcessRunResult:
    """Run a subprocess and bind the current cancellation scope to it."""

    scope = current_cancellation_scope()
    if scope is not None and scope.is_cancelled():
        return ProcessRunResult(
            args=args,
            returncode=130,
            stderr="Command stopped before it started.",
            cancelled=True,
        )

    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": text,
        "shell": False,
    }
    if encoding is not None:
        popen_kwargs["encoding"] = encoding
    if errors is not None:
        popen_kwargs["errors"] = errors
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(list(args), **popen_kwargs)
    unregister = None
    if scope is not None:
        unregister = scope.register(
            lambda: request_process_stop(proc),
            "subprocess.terminate",
        )

    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_process_tree_sync(proc)
            stdout, stderr = proc.communicate()
            return ProcessRunResult(
                args=args,
                returncode=124,
                stdout=_coerce_text(stdout),
                stderr=_coerce_text(stderr),
                timed_out=True,
                cancelled=bool(scope is not None and scope.is_cancelled()),
            )
    finally:
        if unregister is not None:
            unregister()

    cancelled = bool(scope is not None and scope.is_cancelled())
    return ProcessRunResult(
        args=args,
        returncode=130 if cancelled else int(proc.returncode or 0),
        stdout=_coerce_text(stdout),
        stderr=_coerce_text(stderr),
        cancelled=cancelled,
    )


def request_process_stop(proc: subprocess.Popen, *, kill_after: float = 0.75) -> None:
    """Ask an owned subprocess tree to stop without blocking the caller."""

    if proc.poll() is not None:
        return
    _terminate_process_tree(proc)

    def _late_kill() -> None:
        try:
            proc.wait(timeout=kill_after)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            logger.debug("Failed while waiting for process termination", exc_info=True)
            return
        _kill_process_tree(proc)

    threading.Thread(target=_late_kill, name="row-bot-process-cancel", daemon=True).start()


def _stop_process_tree_sync(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    _terminate_process_tree(proc)
    try:
        proc.wait(timeout=0.75)
        return
    except subprocess.TimeoutExpired:
        pass
    _kill_process_tree(proc)
    try:
        proc.wait(timeout=2)
    except Exception:
        logger.debug("Timed out waiting for killed subprocess", exc_info=True)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            logger.debug("Failed to terminate subprocess", exc_info=True)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            logger.debug("Failed to kill subprocess", exc_info=True)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
