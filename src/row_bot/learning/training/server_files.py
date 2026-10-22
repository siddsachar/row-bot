"""The server Row-Bot runs personal models in: its pinned files, installed and removed (O75).

``llama-server`` from the same llama.cpp release as the export tools (b11669, O52), in its CUDA build
for this computer and driver, with the CUDA runtime it needs beside it (about 0.6 GB on Windows). It
joins the training engine's download behind the same ask (``downloads.py``): pinned, checked against
its SHA-256, resumable after a Stop or a restart, and counted in the download's size.

It lives in ``<data>/runtimes/learning/server/``, apart from the engine, so an engine installed before
the server existed stays installed (only the server downloads). Like the engine it is published
atomically: the files are unpacked into a staging folder, the server is asked for its version (it
must start here), and ``installed.json`` is written last. **Remove engine** removes it too.

Row-Bot has a build for Windows and Linux on x86-64 with an NVIDIA driver of 570 or newer (the same
computers the engine trains on); elsewhere the server is ``unavailable`` and personal models run in
Ollama, LM Studio or a server the person starts.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from row_bot.learning.training import fetch, lockfile
from row_bot.learning.training.engine import (
    EngineError, Run, _member_path, _plain_dir, _read_json, _remove_tree, _tidy, _write_json, folder_bytes,
    learning_runtimes, run_owned,
)
from row_bot.learning.training.lockfile import LockedWheel

logger = logging.getLogger(__name__)

FORMAT = 1
SERVER_FOLDER = "server"
INSTALLED_FILE = "installed.json"
DOWNLOADS = "downloads"
EXECUTABLES = ("llama-server.exe", "llama-server")
CHECK_SECONDS = 120.0

ServerState = Literal["not_installed", "installed", "damaged", "outdated", "unavailable"]


def server_dir(root: Path | None = None) -> Path:
    """``<data>/runtimes/learning/server``: the server's files, downloads and run folder."""

    return learning_runtimes(root) / SERVER_FOLDER


def downloads_dir(root: Path | None = None) -> Path:
    return server_dir(root) / DOWNLOADS


def plan(variant: str | None, platforms: Iterable[str] | None = None) -> tuple[LockedWheel, ...]:
    """The pinned files for ``variant`` here (empty: no build for this computer)."""

    return lockfile.server_files(variant, platforms) if variant else ()


def download_bytes(variant: str | None, platforms: Iterable[str] | None = None) -> int:
    return sum(item.known_size or 0 for item in plan(variant, platforms))


def identity(files: Sequence[LockedWheel]) -> str:
    """What is installed, as the files' names and fingerprints (changes with a new pinned release)."""

    return "\n".join(sorted(f"{item.filename} {item.sha256}" for item in files))


@dataclass(frozen=True)
class ServerFiles:
    """The installed server, if any, and whether it can run."""

    state: ServerState
    variant: str | None = None
    folder: Path | None = None
    executable: Path | None = None
    bytes: int = 0
    release: str | None = None

    @property
    def installed(self) -> bool:
        return self.state == "installed"


def executable_in(folder: Path) -> Path | None:
    """``llama-server`` inside an unpacked build (its archive may nest it in a folder)."""

    for name in EXECUTABLES:
        found = sorted(path for path in Path(folder).rglob(name) if path.is_file())
        if found:
            return found[0]
    return None


def library_dirs(folder: Path) -> list[Path]:
    """Folders holding the build's shared libraries (Linux: they go on ``LD_LIBRARY_PATH``)."""

    found = {path.parent for path in Path(folder).rglob("*.so*") if path.is_file()}
    return sorted(found)


_status_lock = threading.Lock()
_status_cache: dict[str, tuple[tuple, ServerFiles, float]] = {}
STATUS_SECONDS = 60.0


def forget_status() -> None:
    with _status_lock:
        _status_cache.clear()


def status(root: Path | None = None, *, variant: str | None = None, refresh: bool = False,
           platforms: Iterable[str] | None = None) -> ServerFiles:
    """The installed server's state (a few file checks, cached for a minute). ``variant``: the build
    this computer takes now; when given, a build for another one reads as outdated."""

    base = server_dir(root)
    marker = base / INSTALLED_FILE
    try:
        info = marker.stat()
        key = (str(base), variant, info.st_mtime_ns, info.st_size)
    except OSError:
        key = (str(base), variant, None, None)
    now = time.monotonic()
    with _status_lock:
        cached = _status_cache.get(str(base))
        if cached is not None and cached[0] == key and not refresh and now - cached[2] < STATUS_SECONDS:
            return cached[1]
    found = _read_status(base, variant, platforms)
    with _status_lock:
        _status_cache[str(base)] = (key, found, now)
    return found


def _read_status(base: Path, variant: str | None, platforms: Iterable[str] | None) -> ServerFiles:
    installed = _read_json(base / INSTALLED_FILE)
    if installed is None or installed.get("format") != FORMAT:
        if variant is not None and not plan(variant, platforms):
            return ServerFiles("unavailable", variant)
        return ServerFiles("not_installed", variant)
    name = str(installed.get("folder") or "")
    folder = base / name
    built_for = installed.get("variant") if installed.get("variant") in lockfile.VARIANTS else None
    if not name or "/" in name or "\\" in name or name.startswith(".") or not _plain_dir(folder):
        return ServerFiles("damaged", built_for)
    executable = executable_in(folder)
    size = int(installed.get("bytes") or 0)
    if executable is None:
        return ServerFiles("damaged", built_for, folder, bytes=size)
    wanted = variant or built_for
    expected = plan(wanted, platforms) if wanted else ()
    if not expected or identity(expected) != installed.get("identity"):
        return ServerFiles("outdated", built_for, folder, executable, size, installed.get("release"))
    return ServerFiles("installed", built_for, folder, executable, size, installed.get("release"))


def unpack(archives: Sequence[Path], target: Path) -> Path:
    """Unpack the verified build and its CUDA runtime into ``target`` (members that would land outside
    are refused; links in a tarball stay inside it). Returns ``target``."""

    target.mkdir(parents=True, exist_ok=True)
    for archive_path in archives:
        if archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    path = _member_path(member.filename)
                    if member.is_dir() or path is None:
                        continue
                    destination = target.joinpath(*path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as reader, open(destination, "wb") as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
        else:
            with tarfile.open(archive_path, "r:*") as archive:
                archive.extractall(target, filter="data")
    if os.name != "nt":
        for path in target.rglob("*"):
            if path.is_file() and not path.is_symlink() and (path.name in EXECUTABLES or ".so" in path.name):
                os.chmod(path, 0o755)
    return target


def server_environment(folder: Path, home: Path) -> dict[str, str]:
    """A process environment for ``llama-server``: nothing of the person's (no keys, no ``LLAMA_*``
    settings that could turn on its tools or downloads), its own home and caches, and on Linux its
    libraries on the loader's path."""

    keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL", "CUDA_VISIBLE_DEVICES")
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    temp = home / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home),
        "XDG_CACHE_HOME": str(temp), "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
        # Its model cache is an empty folder of its own: it lists only the models Row-Bot gives it.
        "LLAMA_CACHE": str(home / "cache"),
    })
    (home / "cache").mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        found = [str(path) for path in library_dirs(folder)]
        if found:
            environment["LD_LIBRARY_PATH"] = os.pathsep.join(found)
    return environment


def version_check(executable: Path, folder: Path, home: Path, *, check: Callable[[], None] = lambda: None,
                  runner: Callable[..., Run] = run_owned) -> str:
    """``llama-server --version``: the server starts here and names its build. Raises EngineError."""

    result = runner([str(executable), "--version"], cwd=home, env=server_environment(folder, home),
                    timeout=CHECK_SECONDS, check=check)
    text = f"{result.stdout}\n{result.stderr}"
    if result.code != 0 or lockfile.LLAMA_CPP_RELEASE.removeprefix("b") not in text:
        logger.warning("Learning's model server didn't start for its check (exit %s)", result.code)
        raise EngineError("check_failed")
    return text.strip().splitlines()[-1][:200] if text.strip() else ""


@dataclass
class ServerInstaller:
    """Installs the server for ``variant`` (the person agreed to the download)."""

    variant: str
    root: Path | None = None
    fetcher: fetch.Fetcher = field(default_factory=fetch.Fetcher)
    runner: Callable[..., Run] = run_owned
    run_check: bool = True
    platforms: Iterable[str] | None = None
    on_progress: Callable[[int, int], None] = lambda _done, _total: None

    def files(self) -> tuple[LockedWheel, ...]:
        return plan(self.variant, self.platforms)

    def pinned(self) -> list[fetch.Pinned]:
        return [fetch.Pinned(item.url, item.filename, item.sha256, item.known_size) for item in self.files()]

    def total(self) -> int:
        return sum(item.size or 0 for item in self.pinned())

    def downloaded(self) -> int:
        folder = downloads_dir(self.root)
        return sum(fetch.on_disk(folder, item) for item in self.pinned())

    def install(self, check: Callable[[], None] = lambda: None) -> ServerFiles:
        """Download what is missing, unpack, check that it starts, and publish. Raises EngineError
        (``install_failed``, ``check_failed``) or ``fetch.FetchError`` / ``fetch.Stopped``."""

        items = self.pinned()
        if not items:
            raise EngineError("install_failed")
        with _install_lock:
            base = server_dir(self.root)
            folder = downloads_dir(self.root)
            folder.mkdir(parents=True, exist_ok=True)
            total = self.total()
            before = {item.name: fetch.on_disk(folder, item) for item in items}
            done = sum(before.values())
            self.on_progress(done, total)
            paths: list[Path] = []
            for item in items:
                base_done = done - before[item.name]

                def moved(current: int, base_done: int = base_done) -> None:
                    self.on_progress(base_done + current, total)

                paths.append(self.fetcher.fetch(item, folder, progress=moved, check=check))
                before[item.name] = item.size or paths[-1].stat().st_size
                done = base_done + before[item.name]
            for leftover in base.glob(".staging-*"):
                _remove_tree(leftover)
            identifier = f"{lockfile.LLAMA_CPP_RELEASE}-{self.variant}-{uuid.uuid4().hex[:6]}"
            staging = base / f".staging-{identifier}"
            try:
                check()
                try:
                    unpack(paths, staging / "bin")
                except (OSError, zipfile.BadZipFile, tarfile.TarError, ValueError) as error:
                    logger.warning("Learning couldn't unpack its model server (%s)", type(error).__name__)
                    raise EngineError("install_failed") from error
                executable = executable_in(staging / "bin")
                if executable is None:
                    raise EngineError("install_failed")
                if self.run_check:
                    version_check(executable, staging / "bin", staging / "work", check=check, runner=self.runner)
                _remove_tree(staging / "work")
                size = folder_bytes(staging)
                check()
                final = base / identifier
                fetch.rename(staging, final)
            except BaseException:
                _remove_tree(staging)
                raise
            previous = _read_json(base / INSTALLED_FILE)
            _write_json(base / INSTALLED_FILE, {
                "format": FORMAT, "folder": identifier, "variant": self.variant, "release": lockfile.LLAMA_CPP_RELEASE,
                "identity": identity(self.files()), "bytes": size, "installed_at": time.time(),
            })
            forget_status()
            if previous and previous.get("folder") and previous.get("folder") != identifier:
                old = base / str(previous["folder"])
                if _plain_dir(old) and old.parent == base:
                    _remove_tree(old)
            _remove_tree(folder)
            logger.info("Learning installed its model server (%s)", self.variant)
            return status(self.root, variant=self.variant, refresh=True, platforms=self.platforms)


_install_lock = threading.RLock()


def remove(root: Path | None = None) -> int:
    """Remove the server's files (and its run folder and downloads). Returns the bytes freed. The
    caller stops a running server first."""

    with _install_lock:
        base = server_dir(root)
        freed = folder_bytes(base)
        (base / INSTALLED_FILE).unlink(missing_ok=True)
        forget_status()
        if os.path.lexists(base):
            doomed = base.with_name(f".removing-{uuid.uuid4().hex[:8]}")
            try:
                fetch.rename(base, doomed, seconds=5.0)
            except OSError:
                doomed = base
            _remove_tree(doomed)
        _tidy(root)
        return freed


__all__ = [
    "EXECUTABLES",
    "ServerFiles",
    "ServerInstaller",
    "download_bytes",
    "downloads_dir",
    "executable_in",
    "forget_status",
    "identity",
    "library_dirs",
    "plan",
    "remove",
    "server_dir",
    "server_environment",
    "status",
    "unpack",
    "version_check",
]
