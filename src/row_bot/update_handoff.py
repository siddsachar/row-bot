"""Detached Windows updater handoff helper.

The running app cannot wait for itself to exit before starting the installer.
This helper is launched detached, waits for Row-Bot processes/port to stop, and
then starts the installer.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import pathlib
import socket
import subprocess
import sys
import time

from row_bot.data_paths import get_row_bot_data_dir

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x00000102


def _log_path() -> pathlib.Path:
    return get_row_bot_data_dir() / "update-handoff.log"


def _write_log(message: str) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {message}\n")


def _port_responds(port: int) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
            return True
    except OSError:
        return False


def _wait_for_pid(pid: int, timeout: float) -> bool:
    if pid <= 0:
        return True
    if os.name != "nt":
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.25)
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(pid),
    )
    if not handle:
        return True
    try:
        result = kernel32.WaitForSingleObject(handle, int(max(0.0, timeout) * 1000))
        return result == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_port_stop(port: int, timeout: float) -> bool:
    if port <= 0:
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not _port_responds(port):
            return True
        time.sleep(0.35)
    return not _port_responds(port)


def _taskkill_pid(pid: int) -> None:
    if os.name != "nt" or pid <= 0:
        return
    _write_log(f"forcing target pid {pid}")
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - helper must keep moving
        _write_log(f"taskkill failed for pid {pid}: {exc}")


def _launch_installer(installer: pathlib.Path) -> None:
    _write_log(f"starting installer: {installer}")
    subprocess.Popen(
        [
            str(installer),
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ],
        close_fds=True,
    )


def run_handoff(
    installer: pathlib.Path,
    *,
    app_pid: int,
    launcher_pid: int,
    port: int,
    timeout: float,
) -> int:
    _write_log(
        "handoff started "
        f"installer={installer} app_pid={app_pid} launcher_pid={launcher_pid} port={port}"
    )
    if not installer.exists():
        _write_log(f"installer missing: {installer}")
        return 2

    app_exited = _wait_for_pid(app_pid, min(timeout, 20.0))
    _write_log(f"app pid {app_pid} exited={app_exited}")
    port_stopped = _wait_for_port_stop(port, min(timeout, 20.0))
    _write_log(f"port {port} stopped={port_stopped}")
    launcher_exited = _wait_for_pid(launcher_pid, min(timeout, 10.0))
    _write_log(f"launcher pid {launcher_pid} exited={launcher_exited}")

    if not app_exited:
        _taskkill_pid(app_pid)
    if not port_stopped and _port_responds(port):
        _write_log(f"port {port} still responds after wait")
    if launcher_pid != app_pid and not launcher_exited:
        _taskkill_pid(launcher_pid)
    _wait_for_port_stop(port, 5.0)
    _launch_installer(installer)
    _write_log("handoff complete")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Row-Bot Windows update handoff")
    parser.add_argument("--installer", required=True)
    parser.add_argument("--app-pid", type=int, default=0)
    parser.add_argument("--launcher-pid", type=int, default=0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run_handoff(
            pathlib.Path(args.installer),
            app_pid=args.app_pid,
            launcher_pid=args.launcher_pid,
            port=args.port,
            timeout=args.timeout,
        )
    except Exception as exc:  # noqa: BLE001 - write diagnostics before exiting
        _write_log(f"handoff failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
