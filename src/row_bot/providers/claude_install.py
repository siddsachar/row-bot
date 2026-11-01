"""Claude Code on request (H14): Anthropic's own installer steps, done in Python, when the person asks.

"Use my Claude plan" on a Row-Bot without Claude Code, or "Update Claude Code", runs the steps Anthropic's
native installer (``https://claude.ai/install.sh`` and ``install.ps1``) takes, and nothing else:

1. read the newest version from Anthropic's release location (``<releases>/latest``);
2. read that release's ``manifest.json`` and the SHA-256 checksum it lists for this platform;
3. download the unmodified binary into Claude Code's own download folder (``~/.claude/downloads``);
4. check the checksum before the file is ever made executable (a mismatch deletes it);
5. finish with the binary's own ``install latest``, as Anthropic's installer does, which puts Claude Code
   where it always lives (``~/.local/bin/claude`` and ``~/.local/share/claude``), so ``find_claude_code``
   finds it; then delete the download.

The binary is never changed and never goes into Row-Bot's own environment or image. Nothing here runs at
start-up: the only network use is that download, from Anthropic, after the click. The install runs with
Claude Code's own environment (``claude_code_environment``: no Row-Bot secrets, telemetry and automatic
updates off). What the installer prints is not kept or logged.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import httpx

from row_bot.providers.claude_subscription import (
    MINIMUM_CLAUDE_CODE_VERSION,
    claude_code_environment,
    claude_code_workdir,
    claude_plan_status,
    forget_claude_plan_status,
    parse_version,
)

logger = logging.getLogger(__name__)

#: Anthropic's release location, the one its native installer and set-up docs use.
CLAUDE_CODE_RELEASES = "https://downloads.claude.ai/claude-code-releases"
#: The release channel the native installer installs by default.
INSTALL_TARGET = "latest"
_VERSION_TEXT = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
_CHECKSUM = re.compile(r"[0-9a-f]{64}")
_MAX_BINARY_BYTES = 1024 * 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
#: Room for the download plus the copy ``install`` makes, and a little to spare.
_SPARE_BYTES = 64 * 1024 * 1024
_INSTALL_SECONDS = 600.0
_TIMEOUT = httpx.Timeout(30.0, connect=15.0)

Reason = Literal["no_internet", "download_failed", "checksum_mismatch", "disk_full", "unsupported_platform",
                 "install_failed"]
Step = Literal["", "download", "check", "install"]


class InstallFailed(Exception):
    """Claude Code couldn't be installed; ``reason`` is the plain reason's code."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


@dataclass(frozen=True)
class InstallState:
    """What Settings shows while Claude Code installs: never a path, a URL or the installer's output."""

    state: Literal["idle", "running", "done", "failed"] = "idle"
    step: Step = ""
    received: int = 0
    total: int = 0
    version: str = ""
    reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def claude_code_platform() -> str | None:
    """The platform name Anthropic's releases use for this computer; None where Claude Code doesn't run."""
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if sys.platform == "win32":
        if sys.maxsize <= 2**32:
            return None
        return "win32-arm64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "win32-x64"
    if arch is None:
        return None
    if sys.platform == "darwin":
        if arch == "x64" and _translated_by_rosetta():
            arch = "arm64"
        return f"darwin-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}-musl" if _uses_musl() else f"linux-{arch}"
    return None


def _translated_by_rosetta() -> bool:
    try:
        answer = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True,
                                timeout=5, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return answer.stdout.strip() == "1"


def _uses_musl() -> bool:
    if Path("/lib/libc.musl-x86_64.so.1").exists() or Path("/lib/libc.musl-aarch64.so.1").exists():
        return True
    try:
        answer = subprocess.run(["ldd", "/bin/ls"], capture_output=True, text=True, timeout=5, check=False,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return "musl" in (answer.stdout + answer.stderr).lower()


def _disk_full(exc: OSError) -> bool:
    return exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)} or getattr(exc, "winerror", None) in {39, 112}


def _remove(path: Path) -> None:
    """Delete a download; Windows may hold a just-run binary for a moment."""
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == 4:
                logger.debug("Couldn't delete Claude Code's download", exc_info=True)
                return
            time.sleep(0.5)


class ClaudeCodeInstaller:
    """Runs one install (or update) at a time and keeps its progress for Settings."""

    def __init__(self, *, releases: str | None = None,
                 client_factory: Callable[[], httpx.Client] | None = None) -> None:
        self._releases = releases
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._state = InstallState()
        self._thread: threading.Thread | None = None

    def state(self) -> InstallState:
        with self._lock:
            return self._state

    def running(self) -> bool:
        with self._lock:
            return self._state.state == "running"

    def start(self) -> InstallState:
        """Begin installing in the background; an install already running carries on."""
        with self._lock:
            if self._state.state == "running":
                return self._state
            self._state = InstallState("running", "download")
            self._thread = threading.Thread(target=self._work, name="claude-code-install", daemon=True)
            self._thread.start()
            return self._state

    def wait(self, timeout: float | None = None) -> InstallState:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return self.state()

    def _set(self, **change: Any) -> None:
        with self._lock:
            self._state = InstallState(**{**asdict(self._state), **change})

    def _work(self) -> None:
        try:
            version = self.install()
        except InstallFailed as failed:
            logger.info("Claude Code couldn't be installed (%s)", failed.reason)
            self._set(state="failed", reason=failed.reason)
        except Exception:
            logger.warning("Claude Code's install stopped unexpectedly", exc_info=True)
            self._set(state="failed", reason="install_failed")
        else:
            logger.info("Claude Code %s installed", version)
            self._set(state="done", step="", version=version, reason="")

    # ── The steps ──────────────────────────────────────────────────────

    def install(self) -> str:
        """Install the newest Claude Code; its version, or ``InstallFailed`` with a plain reason."""
        target = claude_code_platform()
        if target is None:
            raise InstallFailed("unsupported_platform")
        releases = (self._releases or CLAUDE_CODE_RELEASES).rstrip("/")
        windows = target.startswith("win32-")
        try:
            with self._client() as client:
                version = self._latest(client, releases)
                checksum, size = self._manifest(client, releases, version, target)
                downloads = Path.home() / ".claude" / "downloads"
                self._check_room(downloads, size)
                binary = downloads / f"claude-{version}-{target}{'.exe' if windows else ''}"
                try:
                    self._download(client, f"{releases}/{version}/{target}/claude{'.exe' if windows else ''}",
                                   binary, checksum, size)
                    self._set(step="install")
                    self._run_install(binary)
                finally:
                    _remove(binary)
        except httpx.TransportError as exc:
            raise InstallFailed("no_internet" if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                                                                   httpx.ProxyError)) else "download_failed")
        forget_claude_plan_status()
        status = claude_plan_status(fresh=True)
        if status.state == "not_installed":
            raise InstallFailed("install_failed")
        return status.version or version

    def _client(self) -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=_TIMEOUT, follow_redirects=True, trust_env=True)

    def _get(self, client: httpx.Client, url: str, limit: int) -> bytes:
        with client.stream("GET", url) as answer:
            if answer.status_code != 200:
                raise InstallFailed("download_failed")
            body = bytearray()
            for chunk in answer.iter_bytes():
                body += chunk
                if len(body) > limit:
                    raise InstallFailed("download_failed")
            return bytes(body)

    def _latest(self, client: httpx.Client, releases: str) -> str:
        version = self._get(client, f"{releases}/{INSTALL_TARGET}", 128).decode("ascii", "replace").strip()
        if not _VERSION_TEXT.fullmatch(version):
            raise InstallFailed("download_failed")
        newest, minimum = parse_version(version), parse_version(MINIMUM_CLAUDE_CODE_VERSION)
        if newest is None or minimum is None or newest < minimum:
            raise InstallFailed("download_failed")
        return version

    def _manifest(self, client: httpx.Client, releases: str, version: str, target: str) -> tuple[str, int]:
        try:
            manifest = json.loads(self._get(client, f"{releases}/{version}/manifest.json", _MAX_MANIFEST_BYTES))
        except ValueError:
            raise InstallFailed("download_failed") from None
        entry = manifest.get("platforms", {}).get(target) if isinstance(manifest, dict) else None
        if not isinstance(entry, dict):
            raise InstallFailed("unsupported_platform")
        checksum = entry.get("checksum")
        size = entry.get("size")
        if not isinstance(checksum, str) or not _CHECKSUM.fullmatch(checksum):
            raise InstallFailed("download_failed")
        if type(size) is not int or not 0 < size <= _MAX_BINARY_BYTES:
            size = 0
        return checksum, size

    def _check_room(self, downloads: Path, size: int) -> None:
        try:
            downloads.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(downloads).free
        except OSError as exc:
            raise InstallFailed("disk_full" if _disk_full(exc) else "install_failed") from None
        if size and free < 2 * size + _SPARE_BYTES:
            raise InstallFailed("disk_full")

    def _download(self, client: httpx.Client, url: str, binary: Path, checksum: str, size: int) -> None:
        digest = hashlib.sha256()
        received = 0
        self._set(step="download", received=0, total=size)
        try:
            with client.stream("GET", url) as answer, open(binary, "wb") as sink:
                if answer.status_code != 200:
                    raise InstallFailed("download_failed")
                for chunk in answer.iter_bytes(1024 * 1024):
                    received += len(chunk)
                    if received > (size or _MAX_BINARY_BYTES):
                        raise InstallFailed("checksum_mismatch")
                    digest.update(chunk)
                    sink.write(chunk)
                    self._set(received=received)
        except OSError as exc:
            raise InstallFailed("disk_full" if _disk_full(exc) else "install_failed") from None
        self._set(step="check")
        if digest.hexdigest() != checksum:
            raise InstallFailed("checksum_mismatch")
        # Only now, checked against Anthropic's manifest, may it run.
        if os.name != "nt":
            os.chmod(binary, 0o755)

    def _run_install(self, binary: Path) -> None:
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            answer = subprocess.run([str(binary), "install", INSTALL_TARGET], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                                    timeout=_INSTALL_SECONDS, cwd=str(claude_code_workdir()),
                                    env=claude_code_environment(), **options)
        except (OSError, subprocess.SubprocessError):
            logger.info("Claude Code's own install step didn't run", exc_info=True)
            raise InstallFailed("install_failed") from None
        if answer.returncode != 0:
            logger.info("Claude Code's own install step ended with %s", answer.returncode)
            raise InstallFailed("install_failed")


_installer: ClaudeCodeInstaller | None = None
_installer_lock = threading.Lock()


def claude_code_installer() -> ClaudeCodeInstaller:
    global _installer
    with _installer_lock:
        if _installer is None:
            _installer = ClaudeCodeInstaller()
        return _installer
