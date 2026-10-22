"""Large pinned downloads that resume: the training engine's wheels and the starting model's files.

Each file is pinned in code or in the engine's lock: where it comes from, its SHA-256 and
(usually) its size. A download goes into ``<name>.part`` next to where the file belongs and
picks up where it stopped (an HTTP ``Range`` request), after a Stop, a dropped connection or
Row-Bot closing. Only a whole file whose size and SHA-256 match its pin is renamed into place;
anything else is deleted and reported, never used. Requests go through the one safe path for
downloads (``integrations.safe.open_stream``): reviewed hosts only, checked addresses, no
credentials, never a Hugging Face token.

Nothing here runs unless the person asked for the download (``training/downloads.py``).
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024
ATTEMPTS = 4
# Where pinned downloads may come from: PyPI's files, the PyTorch wheel index (and the bucket it
# redirects to), Hugging Face (and its CDN for large files), and llama.cpp's GitHub release and
# source archive (and the hosts GitHub redirects those to).
HOSTS: frozenset[str] = frozenset({
    "files.pythonhosted.org",
    "download.pytorch.org",
    "download-r2.pytorch.org",
    "huggingface.co",
    ".huggingface.co",
    ".hf.co",
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+%-]{0,199}\Z")


class FetchError(RuntimeError):
    """A download that didn't finish; ``code`` is stable: ``network``, ``refused``, ``mismatch``,
    ``out_of_disk`` or ``stopped``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Stopped(FetchError):
    """The person (or Learning turning off) stopped the download; what arrived is kept."""

    def __init__(self) -> None:
        super().__init__("stopped")


@dataclass(frozen=True)
class Pinned:
    """One file to download: where from, what it is called on disk, and what it must be."""

    url: str
    name: str
    sha256: str
    size: int | None = None

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or self.name.endswith(".part") or ".." in self.name:
            raise ValueError("pinned file name")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("pinned sha256")


class Response(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_bytes(self, chunk_size: int | None = None) -> Iterator[bytes]: ...


Opener = Callable[[str, Mapping[str, str]], AbstractContextManager[Response]]


def safe_opener(hosts: Iterable[str] = HOSTS, *, timeout: float = 30.0) -> Opener:
    """The product's opener: Row-Bot's one safe path, limited to ``hosts``."""

    reviewed = tuple(hosts)

    def opener(url: str, headers: Mapping[str, str]) -> AbstractContextManager[Response]:
        from row_bot.integrations.safe import open_stream

        return open_stream(url, hosts=reviewed, headers=headers, timeout=timeout, redirects=3)

    return opener


def part_path(folder: Path, item: Pinned) -> Path:
    return Path(folder) / f"{item.name}.part"


def on_disk(folder: Path, item: Pinned) -> int:
    """Bytes of ``item`` already in ``folder`` (the whole file, or a partial one)."""

    for path in (Path(folder) / item.name, part_path(folder, item)):
        try:
            return path.stat().st_size
        except OSError:
            continue
    return 0


def sha256_file(path: Path, check: Callable[[], None] = lambda: None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK * 4), b""):
            digest.update(block)
            check()
    return digest.hexdigest()


def _content_range_total(value: str) -> int | None:
    match = re.fullmatch(r"\s*bytes\s+(?:\d+-\d+|\*)/(\d+)\s*", value or "")
    return int(match.group(1)) if match else None


def _content_range_start(value: str) -> int | None:
    match = re.fullmatch(r"\s*bytes\s+(\d+)-\d+/(?:\d+|\*)\s*", value or "")
    return int(match.group(1)) if match else None


def rename(source: Path, target: Path, *, seconds: float = 30.0) -> None:
    """``os.replace`` that waits out Windows holding a just-written file or folder (an antivirus
    scan, a child process's last handle: WinError 5 or 32) for up to ``seconds``."""

    deadline = time.monotonic() + seconds
    wait = 0.02
    while True:
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32) or time.monotonic() > deadline:
                raise
        time.sleep(wait)
        wait = min(wait * 2, 1.0)


def _disk_full(error: OSError) -> bool:
    return error.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)) or getattr(error, "winerror", None) in (39, 112)


class Fetcher:
    """Downloads pinned files into a folder, resuming partial ones (one file at a time)."""

    def __init__(self, *, opener: Opener | None = None, retry_wait: float = 2.0) -> None:
        self.opener = opener or safe_opener()
        self.retry_wait = retry_wait
        self._wait = threading.Event()

    def size(self, item: Pinned, check: Callable[[], None] = lambda: None) -> int:
        """How big ``item`` is, from the server (for files whose size isn't pinned): the first
        byte only, so nothing else is downloaded."""

        if item.size is not None:
            return item.size
        check()
        try:
            with self.opener(item.url, {"Range": "bytes=0-0"}) as response:
                total = _content_range_total(response.headers.get("content-range", ""))
                if response.status_code == 200:
                    announced = response.headers.get("content-length", "")
                    total = int(announced) if announced.isdigit() else None
                if not total:
                    raise FetchError("network")
                return total
        except FetchError:
            raise
        except ValueError as error:
            raise FetchError("refused") from error
        except Exception as error:  # network trouble of any kind
            logger.info("Learning could not ask how big a download is (%s)", type(error).__name__)
            raise FetchError("network") from error

    def fetch(
        self,
        item: Pinned,
        folder: Path,
        *,
        progress: Callable[[int], None] = lambda _bytes: None,
        check: Callable[[], None] = lambda: None,
    ) -> Path:
        """Download ``item`` into ``folder`` (or finish a partial download) and verify it.

        ``progress`` gets this file's bytes on disk as they grow; ``check`` raises
        :class:`Stopped` to stop (what arrived stays for next time). Returns the verified file.
        """

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / item.name
        if target.is_file() and (item.size is None or target.stat().st_size == item.size):
            progress(target.stat().st_size)
            return target
        part = part_path(folder, item)
        failures = 0
        while True:
            check()
            try:
                self._download(item, part, progress, check)
                break
            except (Stopped, FetchError) as error:
                if error.code != "network":
                    raise
                failures += 1
                if failures >= ATTEMPTS:
                    raise
                logger.info("Learning's download was interrupted; it continues where it stopped")
                self._wait.wait(self.retry_wait * failures)
        size = part.stat().st_size
        if (item.size is not None and size != item.size) or sha256_file(part, check) != item.sha256:
            part.unlink(missing_ok=True)
            logger.warning("A Learning download didn't match its pinned fingerprint and was deleted")
            raise FetchError("mismatch")
        rename(part, target)
        progress(size)
        return target

    def _download(self, item: Pinned, part: Path, progress: Callable[[int], None],
                  check: Callable[[], None]) -> None:
        offset = part.stat().st_size if part.is_file() else 0
        if item.size is not None and offset > item.size:
            part.unlink()
            offset = 0
        if item.size is not None and offset == item.size:
            return  # complete: verified by the caller
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with self.opener(item.url, headers) as response:
                if offset and response.status_code == 206:
                    if _content_range_start(response.headers.get("content-range", "")) != offset:
                        raise FetchError("network")
                    mode = "ab"
                elif response.status_code == 200:
                    mode, offset = "wb", 0  # the server sent the whole file again
                else:
                    raise FetchError("network")
                written = offset
                limit = item.size if item.size is not None else None
                with open(part, mode) as handle:
                    # As it arrives (not gathered into chunks): a dropped connection loses nothing.
                    for block in response.iter_bytes():
                        if not block:
                            continue
                        written += len(block)
                        if limit is not None and written > limit:
                            raise FetchError("mismatch")
                        handle.write(block)
                        progress(written)
                        check()
                    handle.flush()
                    os.fsync(handle.fileno())
                if limit is not None and written < limit:
                    raise FetchError("network")  # the connection ended early; it resumes from here
        except (Stopped, FetchError):
            raise
        except OSError as error:
            if _disk_full(error):
                raise FetchError("out_of_disk") from error
            logger.info("Learning's download connection failed (%s)", type(error).__name__)
            raise FetchError("network") from error
        except ValueError as error:
            raise FetchError("refused") from error
        except Exception as error:
            if getattr(getattr(error, "response", None), "status_code", 0) == 416 and item.size is not None:
                # The range was past the end: the file is (or claims to be) whole; verified next.
                return
            logger.info("Learning's download failed (%s)", type(error).__name__)
            raise FetchError("network") from error

    def close(self) -> None:
        self._wait.set()


def describe(error: BaseException) -> dict[str, Any]:
    """A download error as a reason code (for the task's state)."""

    return {"reason": error.code if isinstance(error, FetchError) else "network"}


__all__ = [
    "CHUNK",
    "FetchError",
    "Fetcher",
    "HOSTS",
    "Opener",
    "Pinned",
    "Stopped",
    "describe",
    "on_disk",
    "part_path",
    "rename",
    "safe_opener",
    "sha256_file",
]
