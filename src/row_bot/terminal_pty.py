"""Cross-platform PTY backend for the interactive terminal.

Provides a unified ``PtySession`` interface that works on both Windows
(via ``pywinpty`` / ConPTY) and Unix/macOS (via the stdlib ``pty`` module).

Static helpers ``detect_shell()`` and ``detect_platform()`` are used by
the agent prompt injection and the shell tool to auto-detect the user's
OS and shell so commands are written in the correct syntax.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"


# ═══════════════════════════════════════════════════════════════════════════
# Platform / shell detection
# ═══════════════════════════════════════════════════════════════════════════

def detect_shell() -> tuple[str, str]:
    """Return ``(shell_path, shell_type)`` for the current platform.

    ``shell_type`` is one of ``"powershell"``, ``"pwsh"``, ``"cmd"``,
    ``"bash"``, ``"zsh"``, ``"fish"``, ``"sh"``.
    """
    if _IS_WINDOWS:
        # Prefer pwsh (PowerShell 7+) over legacy powershell 5.x
        pwsh = shutil.which("pwsh")
        if pwsh:
            return pwsh, "pwsh"
        ps = shutil.which("powershell")
        if ps:
            return ps, "powershell"
        cmd = shutil.which("cmd")
        return cmd or "cmd.exe", "cmd"

    # Unix / macOS
    shell = os.environ.get("SHELL", "")
    if not shell or not os.path.isfile(shell):
        for candidate in ("bash", "zsh", "sh"):
            found = shutil.which(candidate)
            if found:
                shell = found
                break
        else:
            shell = "/bin/sh"

    name = os.path.basename(shell).lower()
    if "zsh" in name:
        shell_type = "zsh"
    elif "bash" in name:
        shell_type = "bash"
    elif "fish" in name:
        shell_type = "fish"
    else:
        shell_type = "sh"

    return shell, shell_type


def _get_shell_version(shell_path: str, shell_type: str) -> str:
    """Get the version string for the detected shell."""
    try:
        if shell_type in ("powershell", "pwsh"):
            result = subprocess.run(
                [shell_path, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                capture_output=True, text=True, timeout=5,
            )
        elif shell_type == "cmd":
            return ""  # cmd doesn't have a useful version command
        else:
            result = subprocess.run(
                [shell_path, "--version"],
                capture_output=True, text=True, timeout=5,
            )
        output = (result.stdout or "").strip().split("\n")[0].strip()
        return output
    except Exception:
        return ""


def detect_platform() -> dict[str, str]:
    """Return a dict describing the current platform and shell.

    Keys: ``os``, ``os_version``, ``arch``, ``shell_path``, ``shell_type``,
    ``shell_version``.
    """
    system = platform.system()
    if system == "Windows":
        os_name = "Windows"
        ver = platform.version()  # e.g. "10.0.22631"
        # Map build number to friendly name
        build = ver.split(".")[-1] if ver else ""
        try:
            build_int = int(build)
            if build_int >= 22000:
                os_name = "Windows 11"
            else:
                os_name = "Windows 10"
        except ValueError:
            pass
        os_version = ver
    elif system == "Darwin":
        os_name = "macOS"
        os_version = platform.mac_ver()[0]  # e.g. "14.3"
    else:
        os_name = system  # "Linux"
        os_version = platform.release()

    arch = platform.machine()  # "AMD64", "x86_64", "arm64"
    shell_path, shell_type = detect_shell()
    shell_version = _get_shell_version(shell_path, shell_type)

    return {
        "os": os_name,
        "os_version": os_version,
        "arch": arch,
        "shell_path": shell_path,
        "shell_type": shell_type,
        "shell_version": shell_version,
    }


# ═══════════════════════════════════════════════════════════════════════════
# PTY Session
# ═══════════════════════════════════════════════════════════════════════════

class PtySession:
    """A cross-platform pseudo-terminal session.

    On Windows, uses ``pywinpty`` (ConPTY).  On Unix/macOS, uses the
    stdlib ``pty`` module with non-blocking reads.
    """

    def __init__(
        self,
        cols: int = 120,
        rows: int = 30,
        cwd: str | None = None,
        shell: str | None = None,
    ):
        self.cols = cols
        self.rows = rows
        self._cwd = cwd or os.path.expanduser("~")
        self._closed = False
        self._lock = threading.Lock()

        shell_path, shell_type = detect_shell()
        self.shell_path = shell or shell_path
        self.shell_type = shell_type

        if _IS_WINDOWS:
            self._init_windows()
        else:
            self._init_unix()

    # ── Windows (pywinpty) ─────────────────────────────────────────────

    def _init_windows(self) -> None:
        try:
            from winpty import PtyProcess  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "pywinpty is required for terminal on Windows. "
                "Install with: pip install pywinpty"
            )

        # PtyProcess.spawn expects a command string
        self._process = PtyProcess.spawn(
            self.shell_path,
            dimensions=(self.rows, self.cols),
            cwd=self._cwd,
        )
        self._fd = None  # not used on Windows

    # ── Unix/macOS (stdlib pty) ────────────────────────────────────────

    def _init_unix(self) -> None:
        import fcntl
        import pty
        import struct
        import termios

        master_fd, slave_fd = pty.openpty()

        # Set initial terminal size
        winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLUMNS"] = str(self.cols)
        env["LINES"] = str(self.rows)

        self._process = subprocess.Popen(
            [self.shell_path, "-l"],  # login shell
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self._cwd,
            env=env,
            preexec_fn=os.setsid,
            close_fds=True,
        )

        os.close(slave_fd)

        # Make master_fd non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self._fd = master_fd

    # ── Public API ─────────────────────────────────────────────────────

    def write(self, data: str) -> None:
        """Write data to the PTY stdin (e.g. keystrokes from xterm.js)."""
        if self._closed:
            return
        with self._lock:
            if _IS_WINDOWS:
                self._process.write(data)
            else:
                os.write(self._fd, data.encode("utf-8", errors="replace"))

    def read(self, size: int = 65536) -> str:
        """Non-blocking read from the PTY stdout.

        Returns an empty string if no data is available or on error.
        """
        if self._closed:
            return ""
        try:
            if _IS_WINDOWS:
                # PtyProcess.read is non-blocking with timeout=0
                return self._process.read(size)
            else:
                data = os.read(self._fd, size)
                return data.decode("utf-8", errors="replace")
        except (OSError, EOFError):
            return ""

    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY to new dimensions."""
        if self._closed:
            return
        self.cols = cols
        self.rows = rows
        with self._lock:
            if _IS_WINDOWS:
                try:
                    self._process.setwinsize(rows, cols)
                except Exception:
                    pass
            else:
                import fcntl
                import struct
                import termios

                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                try:
                    fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
                except Exception:
                    pass

    def is_alive(self) -> bool:
        """Check whether the shell process is still running."""
        if self._closed:
            return False
        if _IS_WINDOWS:
            return self._process.isalive()
        else:
            return self._process.poll() is None

    def kill(self) -> None:
        """Kill the PTY process."""
        if self._closed:
            return
        self._closed = True
        try:
            if _IS_WINDOWS:
                self._process.close(force=True)
            else:
                import signal
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    self._process.kill()
                try:
                    os.close(self._fd)
                except OSError:
                    pass
        except Exception:
            logger.debug("Error killing PTY", exc_info=True)

    def close(self) -> None:
        """Gracefully close the PTY (send exit, then kill if needed)."""
        if self._closed:
            return
        try:
            self.write("exit\n")
        except Exception:
            pass
        import time
        time.sleep(0.2)
        if self.is_alive():
            self.kill()
        self._closed = True

    @property
    def pid(self) -> int | None:
        """Return the PID of the underlying shell process."""
        try:
            if _IS_WINDOWS:
                return self._process.pid
            else:
                return self._process.pid
        except Exception:
            return None
