"""ffmpeg for video editing, frames of a video and screen recording (F3, decision D4).

Row-Bot never ships ffmpeg. It uses the ``ffmpeg`` on PATH when there is one. Otherwise, the
first time something needs it, and only after the person agrees through the approval gate (a
network download: Ask asks, Auto's reviewer may approve it when the person asked for the work,
Hands-off allows it, Block refuses), it downloads the pinned ``imageio-ffmpeg`` wheel for this
computer from PyPI by its exact address through the one safe fetcher, checks the wheel and the
program inside it against the SHA-256 fingerprints committed below, and unpacks only the ffmpeg
program into the data folder. Installed versions and the signed app bundle are never touched,
and pip never runs.

Licence (checked on 14 Oct 2026 from the wheels themselves: the configuration and licence
strings built into each program): the programs are GPL builds of FFmpeg, version 3 or later on
Windows and Linux and version 2 or later on Apple silicon. Row-Bot runs ffmpeg as a separate
program and doesn't distribute it; the person downloads it from PyPI. The Intel Mac wheel's
program is built with ``--enable-nonfree`` ("not legally redistributable"), so it is never
offered: on an Intel Mac, ffmpeg comes from PATH.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

PACKAGE = "imageio-ffmpeg"
PACKAGE_VERSION = "0.6.0"
HOST = "files.pythonhosted.org"
_FOLDER = "ffmpeg"
_RECORD = "install.json"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Wheel:
    """One reviewed imageio-ffmpeg wheel and the ffmpeg program inside it."""

    key: str
    platform: str
    filename: str
    url: str
    size: int
    sha256: str
    member: str
    binary_size: int
    binary_sha256: str
    ffmpeg_version: str
    build: str
    licence: str
    build_source: str


# Fingerprints from PyPI's JSON API (https://pypi.org/pypi/imageio-ffmpeg/0.6.0/json) and from the
# downloaded wheels, recorded while developing; never fetched at runtime.
WHEELS: dict[str, Wheel] = {
    "windows-x86_64": Wheel(
        key="windows-x86_64",
        platform="Windows",
        filename="imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl",
        url=("https://files.pythonhosted.org/packages/2c/c6/fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b"
             "016a73d/imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl"),
        size=31246824,
        sha256="02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a",
        member="imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe",
        binary_size=87638016,
        binary_sha256="2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3",
        ffmpeg_version="7.1",
        build="gyan.dev essentials build",
        licence="GPL version 3 or later",
        build_source="https://www.gyan.dev/ffmpeg/builds/",
    ),
    "macos-aarch64": Wheel(
        key="macos-aarch64",
        platform="Mac with Apple silicon",
        filename="imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl",
        url=("https://files.pythonhosted.org/packages/40/5c/f3d8a657d362cc93b81aab8feda487317da5b5d31c0e1fdfd5e98"
             "6e55d17/imageio_ffmpeg-0.6.0-py3-none-macosx_11_0_arm64.whl"),
        size=21113891,
        sha256="b1ae3173414b5fc5f538a726c4e48ea97edc0d2cdc11f103afee655c463fa742",
        member="imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1",
        binary_size=49368728,
        binary_sha256="6d175a4743ca50256e89a8cdd731100f9cee33bd79aeea46894d209410dc6617",
        ffmpeg_version="7.1",
        build="static build for macOS",
        licence="GPL version 2 or later",
        build_source="https://www.osxexperts.net/",
    ),
    "linux-x86_64": Wheel(
        key="linux-x86_64",
        platform="Linux (x86-64)",
        filename="imageio_ffmpeg-0.6.0-py3-none-manylinux2014_x86_64.whl",
        url=("https://files.pythonhosted.org/packages/a0/2d/43c8522a2038e9d0e7dbdf3a61195ecc31ca576fb1527a528c877"
             "e87d973/imageio_ffmpeg-0.6.0-py3-none-manylinux2014_x86_64.whl"),
        size=29498237,
        sha256="c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282",
        member="imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2",
        binary_size=79826272,
        binary_sha256="e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
        ffmpeg_version="7.0.2",
        build="johnvansickle.com static build",
        licence="GPL version 3 or later",
        build_source="https://johnvansickle.com/ffmpeg/",
    ),
    "linux-aarch64": Wheel(
        key="linux-aarch64",
        platform="Linux (ARM64)",
        filename="imageio_ffmpeg-0.6.0-py3-none-manylinux2014_aarch64.whl",
        url=("https://files.pythonhosted.org/packages/33/e7/1925bfbc563c39c1d2e82501d8372734a5c725e53ac3b31b4c2d08"
             "1e895b/imageio_ffmpeg-0.6.0-py3-none-manylinux2014_aarch64.whl"),
        size=25632706,
        sha256="1d47bebd83d2c5fc770720d211855f208af8a596c82d17730aa51e815cdee6dc",
        member="imageio_ffmpeg/binaries/ffmpeg-linux-aarch64-v7.0.2",
        binary_size=51134160,
        binary_sha256="6bb182d0d75d23028db82e9e4f723ca69b853d055698486e6984ddb2c06fb8ce",
        ffmpeg_version="7.0.2",
        build="johnvansickle.com static build",
        licence="GPL version 3 or later",
        build_source="https://johnvansickle.com/ffmpeg/",
    ),
}


class FfmpegError(RuntimeError):
    """ffmpeg couldn't be found or installed; the message says why in plain words."""


class FfmpegStopped(FfmpegError):
    """The download was stopped (the person's Stop, or the turn's)."""


# ── Which ffmpeg ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ffmpeg:
    """An ffmpeg program Row-Bot may run."""

    executable: str
    source: Literal["path", "row_bot"]
    version: str
    licence: str
    # Tests run a fake ffmpeg through an interpreter.
    prefix: tuple[str, ...] = ()

    def command(self, *args: object) -> list[str]:
        return [*self.prefix, self.executable, *(str(arg) for arg in args)]

    @property
    def folder(self) -> str:
        """The folder holding it, to put first on another program's PATH (Cua's screen recording)."""
        return str(Path(self.executable).parent)

    def describe(self) -> str:
        """``ffmpeg 7.1 (GPL version 3 or later), downloaded from PyPI``; ``ffmpeg 6.1 from this computer``."""
        version = f"ffmpeg {self.version}" if self.version else "ffmpeg"
        licence = f" ({self.licence})" if self.licence else ""
        where = "downloaded from PyPI" if self.source == "row_bot" else "from this computer"
        return f"{version}{licence}, {where}"


def _program_name(system: str | None = None) -> str:
    return "ffmpeg.exe" if (system or platform.system()).casefold() == "windows" else "ffmpeg"


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """``windows-x86_64``, ``macos-aarch64``, ``linux-x86_64``…: this computer, as the wheels name it."""
    name = (system or platform.system()).casefold()
    arch = (machine or platform.machine()).casefold()
    os_name = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(name, name)
    arch = {"amd64": "x86_64", "x64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64",
            "aarch64": "aarch64"}.get(arch, arch)
    return f"{os_name}-{arch}"


def current_wheel(system: str | None = None, machine: str | None = None) -> Wheel | None:
    """The reviewed wheel for this computer, or None when Row-Bot can't offer one here."""
    if system is None and sys.maxsize <= 2**32:
        return None  # A 32-bit Python: none of the offered programs fit.
    return WHEELS.get(platform_key(system, machine))


def size_words(size: int) -> str:
    """About how big a download is, for people: ``30 MB``."""
    return f"{max(1, round(size / (1024 * 1024)))} MB"


def unavailable_reason(system: str | None = None, machine: str | None = None) -> str:
    """What to do when Row-Bot can't download ffmpeg for this computer, in plain words."""
    key = platform_key(system, machine)
    if key == "macos-x86_64":
        return ("Row-Bot can't download ffmpeg for an Intel Mac (that build's licence doesn't allow passing it "
                "on). Install ffmpeg yourself, for example with Homebrew: brew install ffmpeg, then restart "
                "Row-Bot and try again.")
    if key.startswith("windows-"):
        return ("Row-Bot can't download ffmpeg for this kind of Windows computer. Install it yourself (for "
                "example with: winget install Gyan.FFmpeg), restart Row-Bot and try again.")
    return ("Row-Bot can't download ffmpeg for this computer. Install ffmpeg with your package manager so it's "
            "on PATH, restart Row-Bot and try again.")


def install_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def install_folder(wheel: Wheel) -> Path:
    return install_root() / f"{PACKAGE}-{PACKAGE_VERSION}-{wheel.key}"


def _on_path() -> str | None:
    """``ffmpeg`` on PATH: only absolute PATH folders, never the current folder or Row-Bot's data folder."""
    from row_bot.data_paths import is_private_data_path

    name = _program_name()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        candidate = Path(entry) / name
        try:
            if not candidate.is_file() or (os.name != "nt" and not os.access(candidate, os.X_OK)):
                continue
        except OSError:
            continue
        if is_private_data_path(candidate):
            continue
        return str(candidate)
    return None


_LICENCE_FLAGS = re.compile(r"--enable-(gpl|version3|nonfree)\b")
_SYSTEM_INFO: dict[tuple[str, int, int], tuple[str, str]] = {}


def licence_from_configuration(text: str) -> str:
    """The licence ffmpeg's own configure gives a build with these flags."""
    flags = set(_LICENCE_FLAGS.findall(text or ""))
    if "nonfree" in flags:
        return "nonfree, not redistributable"
    if "gpl" in flags:
        return "GPL version 3 or later" if "version3" in flags else "GPL version 2 or later"
    return "LGPL version 3 or later" if "version3" in flags else "LGPL version 2.1 or later"


def _system_ffmpeg(executable: str) -> Ffmpeg:
    """The ffmpeg on PATH, with its version and licence read once from ``ffmpeg -version``."""
    try:
        info = os.stat(executable)
        key = (executable, info.st_size, info.st_mtime_ns)
    except OSError:
        key = (executable, 0, 0)
    if key not in _SYSTEM_INFO:
        version, licence = "", ""
        try:
            result = subprocess.run(  # noqa: S603 - the ffmpeg found on PATH, fixed arguments
                [executable, "-hide_banner", "-version"], capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = (result.stdout or "") + (result.stderr or "")
            match = re.search(r"ffmpeg version (\S+)", text)
            version = match.group(1) if match else ""
            licence = licence_from_configuration(text) if "configuration:" in text else ""
        except (OSError, subprocess.SubprocessError):
            logger.debug("Could not read the version of ffmpeg on PATH", exc_info=True)
        _SYSTEM_INFO[key] = (version[:80], licence)
    version, licence = _SYSTEM_INFO[key]
    return Ffmpeg(executable=executable, source="path", version=version, licence=licence)


_VERIFIED: dict[str, tuple[int, int]] = {}


def _digest(path: Path, limit: int) -> str:
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK):
            read += len(chunk)
            if read > limit:
                return ""
            digest.update(chunk)
    return digest.hexdigest()


def installed(wheel: Wheel | None = None) -> Ffmpeg | None:
    """The ffmpeg Row-Bot downloaded before, when it is still exactly the reviewed program."""
    wheel = wheel or current_wheel()
    if wheel is None:
        return None
    executable = install_folder(wheel) / _program_name()
    try:
        info = executable.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size != wheel.binary_size:
        return None
    seen = (info.st_size, info.st_mtime_ns)
    if _VERIFIED.get(str(executable)) != seen:
        try:
            if _digest(executable, wheel.binary_size) != wheel.binary_sha256:
                logger.warning("The downloaded ffmpeg no longer matches its fingerprint; it won't be used")
                return None
        except OSError:
            return None
        _VERIFIED[str(executable)] = seen
    return Ffmpeg(executable=str(executable), source="row_bot", version=wheel.ffmpeg_version,
                  licence=wheel.licence)


def find() -> Ffmpeg | None:
    """The ffmpeg to use: the one on PATH first, else the one Row-Bot downloaded; None when neither."""
    on_path = _on_path()
    if on_path:
        return _system_ffmpeg(on_path)
    return installed()


_ENCODERS: dict[str, str] = {}
# H.264 encoders, best first: x264, then the computer's own (macOS, Windows, graphics cards).
_H264 = ("libx264", "h264_videotoolbox", "h264_mf", "h264_nvenc", "h264_qsv", "h264_amf")


def h264_encoder(ffmpeg: Ffmpeg) -> str:
    """The H.264 encoder this ffmpeg has; every program Row-Bot downloads has x264."""
    if ffmpeg.source == "row_bot":
        return "libx264"
    if ffmpeg.executable not in _ENCODERS:
        try:
            result = subprocess.run(  # noqa: S603 - the resolved ffmpeg, fixed arguments
                ffmpeg.command("-hide_banner", "-encoders"), capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), encoding="utf-8", errors="replace",
            )
            text = result.stdout or ""
        except (OSError, subprocess.SubprocessError):
            text = ""
        _ENCODERS[ffmpeg.executable] = next(
            (name for name in _H264 if re.search(rf"\s{re.escape(name)}\s", text)), "")
    if not _ENCODERS[ffmpeg.executable]:
        raise FfmpegError("The ffmpeg on this computer can't make H.264 (MP4) videos. Install a full ffmpeg build, "
                          "restart Row-Bot and try again.")
    return _ENCODERS[ffmpeg.executable]


# ── Asking first ────────────────────────────────────────────────────────────

# What needs ffmpeg, as the question names it.
PURPOSES: dict[str, str] = {
    "edit": "Video editing",
    "frames": "Looking at frames of a video",
    "screen": "Recording your screen",
}


def consent_request(purpose: str, tool: str, *, wheel: Wheel | None = None, also: str = "") -> dict[str, Any]:
    """The approval card for downloading ffmpeg: what, from where, how big, and its licence."""
    wheel = wheel or current_wheel()
    if wheel is None:
        raise FfmpegError(unavailable_reason())
    subject = PURPOSES.get(purpose, "This")
    size = size_words(wheel.size)
    description = (
        f"Row-Bot will download {PACKAGE} {PACKAGE_VERSION} for {wheel.platform} ({size}) from PyPI "
        f"({HOST}), check it against the fingerprint saved in Row-Bot, and keep only its ffmpeg program in "
        f"Row-Bot's data folder. ffmpeg is free software ({wheel.licence}); Row-Bot runs it as a separate "
        "program. This happens once."
    )
    return {
        "tool": tool,
        "label": "Download ffmpeg",
        "question": f"{subject} needs ffmpeg (about {size}) from PyPI. Download it now?",
        "description": description + (f" {also}" if also else ""),
        "risk_class": "medium",
        "args": {
            "download": f"{PACKAGE} {PACKAGE_VERSION} ({wheel.platform}, {size})",
            "from": HOST,
            "licence": f"ffmpeg {wheel.ffmpeg_version}, {wheel.licence}",
        },
    }


def consent_risks(wheel: Wheel | None = None) -> tuple[str, ...]:
    wheel = wheel or current_wheel()
    size = size_words(wheel.size) if wheel else "about 30 MB"
    return (f"downloads a program (ffmpeg, {size}) from PyPI and keeps it in Row-Bot's data folder",)


# ── Downloading ─────────────────────────────────────────────────────────────


@dataclass
class DownloadState:
    """The download now (or the last one), for the cards that wait on it."""

    status: Literal["idle", "downloading", "unpacking", "installed", "failed", "stopped"] = "idle"
    received: int = 0
    total: int = 0
    error: str = ""
    listeners: list[Callable[[], None]] = field(default_factory=list)


_STATE = DownloadState()
_INSTALL_LOCK = threading.Lock()


def download_state() -> dict[str, Any]:
    return {"status": _STATE.status, "received": _STATE.received, "total": _STATE.total, "error": _STATE.error}


def _set(**changes: Any) -> None:
    for name, value in changes.items():
        setattr(_STATE, name, value)


def _safe_folder(folder: Path) -> None:
    """Refuse a data folder (or one of its parts) that became a link."""
    for path in (install_root(), folder):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise FfmpegError("Row-Bot's ffmpeg folder isn't a plain folder, so nothing was installed.")


def _replace(source: Path, target: Path) -> None:
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except OSError as exc:  # Antivirus may hold a just-written program for a moment.
            if getattr(exc, "winerror", None) not in (5, 32) or attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def _unpack(wheel: Wheel, data: bytes, check: Callable[[], None]) -> Path:
    """Write only the ffmpeg program from the verified wheel, checked against its own fingerprint."""
    folder = install_folder(wheel)
    _safe_folder(folder)
    folder.mkdir(parents=True, exist_ok=True)
    _safe_folder(folder)
    target = folder / _program_name()
    partial = folder / f".{target.name}.{uuid.uuid4().hex}.part"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                member = archive.getinfo(wheel.member)
            except KeyError:
                raise FfmpegError("The ffmpeg download didn't hold the expected program; nothing was installed.") \
                    from None
            if member.file_size != wheel.binary_size or member.is_dir():
                raise FfmpegError("The ffmpeg download didn't hold the expected program; nothing was installed.")
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                                 | getattr(os, "O_NOFOLLOW", 0), 0o700)
            digest, written = hashlib.sha256(), 0
            with archive.open(member) as source, os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(_CHUNK):
                    written += len(chunk)
                    if written > wheel.binary_size:
                        break
                    digest.update(chunk)
                    output.write(chunk)
                    check()
                output.flush()
                os.fsync(output.fileno())
        if written != wheel.binary_size or digest.hexdigest() != wheel.binary_sha256:
            raise FfmpegError("The ffmpeg program didn't match the fingerprint saved in Row-Bot; nothing was "
                              "installed.")
        if os.name != "nt":
            os.chmod(partial, 0o755)
        _replace(partial, target)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
    record = {
        "package": PACKAGE, "package_version": PACKAGE_VERSION, "wheel": wheel.filename, "url": wheel.url,
        "wheel_sha256": wheel.sha256, "binary_sha256": wheel.binary_sha256, "ffmpeg_version": wheel.ffmpeg_version,
        "build": wheel.build, "licence": wheel.licence, "build_source": wheel.build_source,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    (folder / _RECORD).write_text(json.dumps(record, indent=2), encoding="utf-8", newline="\n")
    _remove_older(folder)
    return target


def _remove_older(current: Path) -> None:
    """Earlier pins' programs go once a newer one is in place."""
    import shutil

    for folder in install_root().glob(f"{PACKAGE}-*"):
        try:
            if folder == current or folder.is_symlink() or not folder.is_dir():
                continue
            shutil.rmtree(folder)
        except OSError:
            logger.debug("Could not remove an older ffmpeg", exc_info=True)


def install(*, check: Callable[[], None] = lambda: None) -> Ffmpeg:
    """Download, verify and unpack ffmpeg for this computer (the person has agreed).

    One download at a time; a second caller waits and then uses the first one's. ``check``
    stops the download by raising (``FfmpegStopped``). Raises FfmpegError with plain words.
    """
    from row_bot.integrations.safe import fetch

    wheel = current_wheel()
    if wheel is None:
        raise FfmpegError(unavailable_reason())
    with _INSTALL_LOCK:
        found = installed(wheel)
        if found is not None:
            return found
        _set(status="downloading", received=0, total=wheel.size, error="")
        try:
            data = fetch(wheel.url, hosts={HOST}, max_bytes=wheel.size, timeout=60, redirects=0,
                         headers={"User-Agent": f"Row-Bot-ffmpeg/{PACKAGE_VERSION}"}, check=check,
                         progress=lambda received, _total: _set(received=received),
                         refused="ffmpeg_source_refused", too_large="ffmpeg_download_too_large")
            if len(data) != wheel.size or hashlib.sha256(data).hexdigest() != wheel.sha256:
                raise FfmpegError("The ffmpeg download didn't match the fingerprint saved in Row-Bot, so it wasn't "
                                  "used. Nothing was installed.")
            _set(status="unpacking", received=wheel.size)
            _unpack(wheel, data, check)
        except FfmpegStopped:
            _set(status="stopped", error="")
            raise
        except FfmpegError as exc:
            _set(status="failed", error=str(exc))
            raise
        except ValueError as exc:
            message = ("The ffmpeg download was larger than expected, so it was stopped. Nothing was installed."
                       if str(exc) == "ffmpeg_download_too_large" else
                       "Row-Bot couldn't download ffmpeg from PyPI. Check the internet connection and try again.")
            _set(status="failed", error=message)
            raise FfmpegError(message) from None
        except Exception as exc:
            logger.warning("Could not download ffmpeg", exc_info=True)
            message = "Row-Bot couldn't download ffmpeg from PyPI. Check the internet connection and try again."
            _set(status="failed", error=message)
            raise FfmpegError(message) from exc
        found = installed(wheel)
        if found is None:
            _set(status="failed", error="ffmpeg was downloaded but couldn't be checked; nothing was installed.")
            raise FfmpegError(_STATE.error)
        _set(status="installed")
        logger.info("Downloaded ffmpeg %s from PyPI", wheel.ffmpeg_version)
        return found


def turn_check() -> Callable[[], None]:
    """Stop with the turn: the turn's Stop ends a download it is waiting for."""
    try:
        from row_bot.cancellation import current_cancellation_scope

        scope = current_cancellation_scope()
    except Exception:
        scope = None

    def check() -> None:
        if scope is not None and scope.is_cancelled():
            raise FfmpegStopped("The download of ffmpeg was stopped.")

    return check


def ensure(purpose: str, tool: str, *, also: str = "", risks: tuple[str, ...] = ()) -> tuple[Ffmpeg | None, str]:
    """ffmpeg for work in a turn: found, or (after asking) downloaded while the turn waits.

    Returns (ffmpeg, approval line) or (None, the refusal in plain words). One approval at
    most, so a tool that also asks for its own action asks for both on this one card (``also``).
    """
    from row_bot.tools.approval_gate import approval_check

    found = find()
    if found is not None:
        return found, ""
    wheel = current_wheel()
    if wheel is None:
        return None, unavailable_reason()
    subject = PURPOSES.get(purpose, "This")
    refusal, line = approval_check(
        consent_request(purpose, tool, wheel=wheel, also=also),
        risks=consent_risks(wheel) + tuple(risks),
        blocked_message=(f"BLOCKED: {subject} needs ffmpeg, and downloading it needs approval, but this chat is "
                         "on Block. Install ffmpeg yourself or change the approval level."),
        cancelled_message="The person didn't allow downloading ffmpeg; nothing was downloaded.",
    )
    if refusal:
        return None, refusal
    try:
        return install(check=turn_check()), line
    except FfmpegError as exc:
        return None, str(exc)
