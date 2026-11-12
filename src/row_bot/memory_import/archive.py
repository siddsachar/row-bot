"""Reading picked files and the archives inside them, safely and without extracting.

Every readable thing becomes a :class:`Member` that can be opened again as a
byte stream, so a scan and a later conversation read both stream from the
original file. Archive members are streamed with ``ZipFile.open`` (or a
streaming tar read); nothing is written to disk.
"""
from __future__ import annotations

import gzip
import io
import logging
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import IO


logger = logging.getLogger(__name__)

MAX_MEMBERS = 200_000
MAX_TOTAL_BYTES = 64 * 1024 ** 3
MAX_MEMBER_BYTES = 16 * 1024 ** 3
MAX_RATIO = 200
RATIO_FLOOR_BYTES = 8 * 1024 * 1024
MAX_NESTING = 2
HEAD_BYTES = 4096

Opener = Callable[[], AbstractContextManager[IO[bytes]]]


class UnsafeArchiveError(ValueError):
    """An archive that is refused as a whole, with words for the person."""


@dataclass(frozen=True)
class Member:
    """One file to read: a picked file, or a file inside an archive."""

    display: str
    name: str  # path inside its archive (or the file name)
    open: Opener
    head: bytes
    archive: str | None = None
    path: Path | None = None  # the picked file itself (not one inside an archive), for databases

    @property
    def base(self) -> str:
        return normal_name(self.name)


def normal_name(name: str) -> str:
    """A base name compared case-insensitively and with ``_`` the same as ``-``."""
    return PurePosixPath(name.replace("\\", "/")).name.lower().replace("_", "-")


def sniff(head: bytes) -> str:
    """The container kind from the first bytes: zip, gzip, tar, json, html, text or binary."""
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "tar"
    text = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    stripped = text.lstrip()
    if stripped[:1] in (b"[", b"{"):
        return "json"
    lowered = stripped[:64].lower()
    if lowered.startswith((b"<!doctype", b"<html")):
        return "html"
    if b"\x00" in text[:1024]:
        return "binary"
    try:
        text[:1024].decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(text[:1024]) - 4:
            return "binary"
    return "text"


class LimitedReader(io.RawIOBase):
    """A byte stream that refuses to give more than ``limit`` bytes."""

    def __init__(self, raw: IO[bytes], limit: int) -> None:
        self._raw = raw
        self._left = limit

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        data = self._raw.read(len(buffer))
        if len(data) > self._left:
            raise UnsafeArchiveError("a file grew larger than it said it was when unpacked")
        self._left -= len(data)
        buffer[:len(data)] = data
        return len(data)


def _buffered(raw: IO[bytes], limit: int) -> IO[bytes]:
    return io.BufferedReader(LimitedReader(raw, limit), buffer_size=1 << 16)


def _unsafe_name(name: str) -> bool:
    normal = name.replace("\\", "/")
    if normal.startswith("/") or (len(normal) > 1 and normal[1] == ":"):
        return True
    return ".." in PurePosixPath(normal).parts


@contextmanager
def _open_path(path: Path) -> Iterator[IO[bytes]]:
    with path.open("rb") as handle:
        yield handle


def _read_head(opener: Opener) -> bytes:
    with opener() as stream:
        return stream.read(HEAD_BYTES)


def check_zip(archive: zipfile.ZipFile, label: str) -> list[zipfile.ZipInfo]:
    """The archive's files, or :class:`UnsafeArchiveError` for a zip-slip or zip bomb."""
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise UnsafeArchiveError(f"{label} was not read: it holds too many files.")
    total = 0
    for info in infos:
        if _unsafe_name(info.filename):
            raise UnsafeArchiveError(f"{label} was not read: it has files with unsafe paths.")
        if info.is_dir():
            continue
        total += info.file_size
        if info.file_size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
            raise UnsafeArchiveError(f"{label} was not read: it unpacks to far too much data.")
        if info.file_size > RATIO_FLOOR_BYTES and info.file_size > MAX_RATIO * max(1, info.compress_size):
            raise UnsafeArchiveError(f"{label} was not read: it is packed suspiciously tightly.")
        if info.flag_bits & 0x1:
            raise UnsafeArchiveError(f"{label} was not read: it is password protected.")
    return [info for info in infos if not info.is_dir()]


def _zip_member_opener(outer: Opener, chain: tuple[str, ...], name: str,
                       size: int) -> Opener:
    @contextmanager
    def opener() -> Iterator[IO[bytes]]:
        with outer() as base:
            archive = zipfile.ZipFile(base)
            for inner in chain:
                archive = zipfile.ZipFile(archive.open(inner))
            with archive.open(name) as stream:
                yield _buffered(stream, size)
    return opener


def _gzip_opener(outer: Opener) -> Opener:
    @contextmanager
    def opener() -> Iterator[IO[bytes]]:
        with outer() as base, gzip.GzipFile(fileobj=base) as unpacked:
            yield _buffered(unpacked, MAX_MEMBER_BYTES)
    return opener


def _tar_member_opener(path: Path, name: str, size: int) -> Opener:
    @contextmanager
    def opener() -> Iterator[IO[bytes]]:
        with tarfile.open(path, "r|*") as archive:
            for info in archive:
                if info.name == name and info.isfile():
                    stream = archive.extractfile(info)
                    if stream is None:
                        break
                    yield _buffered(stream, size)
                    return
        raise FileNotFoundError(name)
    return opener


class Expansion:
    """The members found in one picked file, plus what was refused or left out."""

    def __init__(self) -> None:
        self.members: list[Member] = []
        self.warnings: list[str] = []
        self.ignored: list[str] = []
        self.inner_zips: list[str] = []


def expand(path: Path) -> Expansion:
    """Every member of a picked file, archives opened up to ``MAX_NESTING`` deep."""
    result = Expansion()
    opener: Opener = partial(_open_path, path)
    try:
        head = _read_head(opener)
    except OSError as exc:
        logger.info("memory import could not open a picked file: %s", exc.__class__.__name__)
        result.warnings.append(f"{path.name} could not be opened.")
        return result
    kind = sniff(head)
    try:
        if kind == "zip":
            _expand_zip(path.name, opener, (), 1, result)
        elif kind == "gzip":
            _expand_gzip(path, opener, result)
        elif kind == "tar":
            _expand_tar(path, result)
        else:
            result.members.append(Member(path.name, path.name, opener, head, path=path))
    except UnsafeArchiveError as exc:
        result.members.clear()
        result.warnings.append(str(exc))
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, gzip.BadGzipFile) as exc:
        logger.info("memory import could not read an archive: %s", exc.__class__.__name__)
        result.members.clear()
        result.warnings.append(f"{path.name} looks like an archive but could not be read; it may be damaged or incomplete.")
    return result


_ATTACHMENT_SUFFIXES = (".dat", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".wav", ".mp3", ".mp4", ".pdf")
_ACCOUNT_SUFFIXES = (".xlsx",)


def _expand_zip(label: str, outer: Opener, chain: tuple[str, ...], depth: int,
                result: Expansion) -> None:
    with outer() as base:
        archive = zipfile.ZipFile(base)
        for inner in chain:
            archive = zipfile.ZipFile(archive.open(inner))
        infos = check_zip(archive, label)
        nested: list[zipfile.ZipInfo] = []
        found: list[Member] = []
        for info in infos:
            lowered = info.filename.lower()
            member_opener = _zip_member_opener(outer, chain, info.filename, info.file_size)
            if lowered.endswith(_ACCOUNT_SUFFIXES):  # a spreadsheet of account details: listed, never read
                found.append(Member(f"{label} › {info.filename}", info.filename, member_opener, b"", archive=label))
                continue
            if lowered.endswith(_ATTACHMENT_SUFFIXES):
                result.ignored.append(info.filename)
                continue
            if info.file_size == 0:
                continue
            with archive.open(info) as stream:
                head = stream.read(HEAD_BYTES)
            kind = sniff(head)
            if kind == "zip":
                nested.append(info)
                continue
            if kind == "gzip":
                gz_opener = _gzip_opener(member_opener)
                found.append(Member(f"{label} › {info.filename}", info.filename, gz_opener,
                                    _read_head(gz_opener), archive=label))
            elif kind in {"json", "text", "html"}:
                found.append(Member(f"{label} › {info.filename}", info.filename, member_opener, head, archive=label))
            else:
                result.ignored.append(info.filename)
    result.members.extend(found)
    if not nested:
        return
    has_data = any(sniff(member.head) == "json" or member.head.lstrip(b"\xef\xbb\xbf").startswith(b"Conversation,")
                   for member in found)
    if has_data or depth >= MAX_NESTING:
        result.inner_zips.extend(info.filename for info in nested)
        return
    for info in nested:
        if normal_name(info.filename).startswith("files-"):
            result.inner_zips.append(info.filename)
            continue
        _expand_zip(f"{label} › {info.filename}", outer, (*chain, info.filename), depth + 1, result)


def _expand_gzip(path: Path, opener: Opener, result: Expansion) -> None:
    gz_opener = _gzip_opener(opener)
    head = _read_head(gz_opener)
    if sniff(head) == "tar":
        _expand_tar(path, result)
        return
    result.members.append(Member(path.name, path.name.removesuffix(".gz"), gz_opener, head))


def _expand_tar(path: Path, result: Expansion) -> None:
    total = 0
    count = 0
    with tarfile.open(path, "r|*") as archive:
        for info in archive:
            count += 1
            if count > MAX_MEMBERS:
                raise UnsafeArchiveError(f"{path.name} was not read: it holds too many files.")
            if _unsafe_name(info.name):
                raise UnsafeArchiveError(f"{path.name} was not read: it has files with unsafe paths.")
            if not info.isfile():
                continue
            total += info.size
            if total > MAX_TOTAL_BYTES:
                raise UnsafeArchiveError(f"{path.name} was not read: it unpacks to far too much data.")
            lowered = info.name.lower()
            if lowered.endswith(_ATTACHMENT_SUFFIXES) or lowered.endswith(_ACCOUNT_SUFFIXES) or not info.size:
                result.ignored.append(info.name)
                continue
            stream = archive.extractfile(info)
            if stream is None:
                continue
            head = stream.read(HEAD_BYTES)
            if sniff(head) in {"json", "text", "html"}:
                result.members.append(Member(f"{path.name} › {info.name}", info.name,
                                             _tar_member_opener(path, info.name, info.size), head, archive=path.name))
            else:
                result.ignored.append(info.name)


def memory_member(display: str, data: bytes) -> Member:
    """A member backed by bytes already in memory (pasted text)."""
    @contextmanager
    def opener() -> Iterator[IO[bytes]]:
        yield io.BytesIO(data)
    return Member(display, display, opener, data[:HEAD_BYTES])
