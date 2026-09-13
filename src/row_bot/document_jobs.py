"""Durable, FIFO coordination for the bounded document-ingestion pipeline."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import stat
from typing import Any
import pathlib
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterator

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MIN_STAGING_FREE_BYTES = 2 * 1024 * 1024 * 1024
EMBEDDING_BATCH_SIZE = 32
INDEX_SEGMENT_CHUNKS = 2_000
REDUCTION_GROUP_SIZE = 8
LEASE_SECONDS = 90

JOB_STATUSES = {
    "staging",
    "queued",
    "indexing",
    "searchable",
    "extracting",
    "completed",
    "failed",
    "cancelled",
    "skipped_duplicate",
}
JOB_STAGES = {
    "upload",
    "parse",
    "embed",
    "index_commit",
    "knowledge_map",
    "knowledge_reduce",
    "knowledge_commit",
    "finalize",
}
BATCH_STATUSES = {
    "staging",
    "queued",
    "running",
    "paused",
    "completed",
    "completed_with_errors",
    "cancelled",
}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "skipped_duplicate"}
ACTIVE_JOB_STATUSES = {"indexing", "extracting"}

_JOB_TRANSITIONS = {
    "staging": {"queued", "failed", "cancelled", "skipped_duplicate"},
    "queued": {"indexing", "failed", "cancelled"},
    "indexing": {"queued", "searchable", "failed", "cancelled"},
    "searchable": {"extracting", "completed", "failed", "cancelled"},
    "extracting": {"searchable", "completed", "failed", "cancelled"},
    "completed": set(),
    "failed": {"queued"},
    "cancelled": set(),
    "skipped_duplicate": set(),
}

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DATABASE_LOCKS = weakref.WeakValueDictionary()
_DATABASE_LOCKS_GUARD = threading.Lock()


def _database_write_lock(path: pathlib.Path):
    """Share the existing writer boundary across service instances for one DB."""
    key = os.path.normcase(str(path.resolve()))
    with _DATABASE_LOCKS_GUARD:
        lock = _DATABASE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DATABASE_LOCKS[key] = lock
        return lock


def _rename_source_no_replace(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Retain a source name atomically without overwriting a recovery entry."""
    if sys.platform == "win32":
        os.rename(source, destination)  # Windows rename rejects existing destinations.
        return
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = library.renamex_np
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            arguments = (os.fsencode(source.absolute()), os.fsencode(destination.absolute()), 4)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            rename = library.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            arguments = (-100, os.fsencode(source.absolute()), -100, os.fsencode(destination.absolute()), 1)  # RENAME_NOREPLACE
        else:
            raise AttributeError("Unsupported exclusive rename platform")
    except AttributeError as exc:
        raise DocumentJobError("Exclusive source retention is unavailable; preserve for review") from exc
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(source))


class DocumentJobError(RuntimeError):
    """Base class for durable ingestion errors."""


class InvalidJobTransition(DocumentJobError):
    """Raised when a caller requests an illegal state transition."""


class DocumentCancelled(DocumentJobError):
    """Raised at a pipeline cancellation checkpoint."""


@dataclass(frozen=True)
class DocumentBatch:
    id: str
    created_at: str
    updated_at: str
    status: str
    pause_requested: bool
    cancel_requested: bool


@dataclass(frozen=True)
class DocumentJob:
    id: str
    batch_id: str
    sequence: int
    original_name: str
    stored_name: str
    staged_path: str
    content_sha256: str
    size_bytes: int
    extension: str
    status: str
    stage: str
    index_progress_current: int
    index_progress_total: int
    extraction_progress_current: int
    extraction_progress_total: int
    attempt: int
    cancel_requested: bool
    error_code: str
    error_message: str
    created_at: str
    updated_at: str
    started_at: str
    searchable_at: str
    completed_at: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_original_name(original_name: str) -> str:
    """Return a cross-platform, basename-only upload name."""
    normalized = unicodedata.normalize("NFC", str(original_name or ""))
    normalized = normalized.replace("\\", "/").split("/")[-1].strip()
    normalized = _UNSAFE_FILENAME.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip(" ._")
    if not normalized:
        normalized = "document"
    path = pathlib.Path(normalized)
    stem = path.stem[:120].rstrip(" .") or "document"
    suffix = path.suffix[:20].lower()
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}{suffix}"


def _open_protected_source(path, *, dir_fd=None) -> int:
    """Open a no-follow source; Windows also denies concurrent write/delete."""
    if os.name != "nt":
        return os.open(path,os.O_RDONLY | os.O_NOFOLLOW | getattr(os,"O_NONBLOCK",0),dir_fd=dir_fd)
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32",use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                       wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path),0x80000000,0x1,None,3,0x00200000,None)
    if handle == wintypes.HANDLE(-1).value:
        raise DocumentJobError("Document source is unavailable")
    try:
        return msvcrt.open_osfhandle(handle,os.O_RDONLY | os.O_BINARY)
    except Exception:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)
        raise


def is_safe_document_name(value: object) -> bool:
    """A bounded display basename; stored names still use the sanitizer."""
    return (isinstance(value,str) and 0 < len(value) <= 256 and value not in {".",".."}
            and not _UNSAFE_FILENAME.search(value)
            and not any(0xD800 <= ord(character) <= 0xDFFF for character in value))


def collision_safe_stored_name(original_name: str, job_id: str) -> str:
    safe = pathlib.Path(sanitize_original_name(original_name))
    suffix = safe.suffix.lower()
    stem = safe.stem[:96] or "document"
    return f"{stem}-{job_id[:10]}{suffix}"


def _row_job(row: sqlite3.Row) -> DocumentJob:
    values = dict(row)
    values["cancel_requested"] = bool(values["cancel_requested"])
    return DocumentJob(**values)


def _row_batch(row: sqlite3.Row) -> DocumentBatch:
    values = dict(row)
    values["pause_requested"] = bool(values["pause_requested"])
    values["cancel_requested"] = bool(values["cancel_requested"])
    return DocumentBatch(**values)


def document_control_snapshot(conn: sqlite3.Connection, batch_ids: list[str]) -> dict:
    """Bounded private review over one existing SQLite snapshot; no initialization."""
    previous = conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
    try:
        return _document_control_snapshot(conn, batch_ids)
    finally:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous)


def _document_control_snapshot(conn: sqlite3.Connection, batch_ids: list[str]) -> dict:
    if (not isinstance(batch_ids, list) or not 1 <= len(batch_ids) <= 50
            or len(set(batch_ids)) != len(batch_ids)
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value) for value in batch_ids)):
        raise DocumentJobError("Invalid document control scope")
    placeholders = ",".join("?" for _ in batch_ids)
    statements = {
        "batches": f"SELECT * FROM document_batches WHERE id IN ({placeholders}) ORDER BY id",
        "jobs": f"SELECT * FROM document_jobs WHERE batch_id IN ({placeholders}) ORDER BY id",
        "records": f"SELECT * FROM document_records WHERE document_id IN (SELECT id FROM document_jobs WHERE batch_id IN ({placeholders})) ORDER BY document_id",
        "removals": f"SELECT id,target FROM document_removals WHERE target IN (SELECT id FROM document_jobs WHERE batch_id IN ({placeholders})) ORDER BY id",
    }
    result, used, count = {}, 0, 0
    for kind, sql in statements.items():
        result[kind] = []
        for row in conn.execute(sql, batch_ids):
            item = dict(row)
            count += 1
            used += len(json.dumps(item, ensure_ascii=True))
            if used > 2 * 1024 * 1024 or count > 4000:
                raise DocumentJobError("Document control review exceeds its work budget")
            result[kind].append(item)
    if len(result["batches"]) != len(batch_ids):
        raise DocumentJobError("Document batch is unavailable")
    return result


class DocumentJobService:
    """Own queue persistence, validated transitions, recovery, and controls."""

    def __init__(
        self,
        data_dir: str | pathlib.Path | None = None,
        *,
        now: Callable[[], str] = utc_now,
        monotonic: Callable[[], float] = time.time,
    ) -> None:
        self.data_dir = pathlib.Path(data_dir or get_row_bot_data_dir())
        self.root = self.data_dir / "document_ingestion"
        self.staging_root = self.root / "staging"
        self.work_root = self.root / "work"
        self.completed_root = self.root / "completed"
        self.db_path = self.root / "jobs.db"
        self._now = now
        self._monotonic = monotonic
        self._write_lock = _database_write_lock(self.db_path)
        self.processing_policy_resolver: Callable[[dict], Any] | None = None
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.completed_root.mkdir(parents=True, exist_ok=True)
        self._initialize_or_recover_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextlib.contextmanager
    def source_snapshot(self, job: DocumentJob, *, validate: Callable[[], None]):
        """Bounded reviewed bytes in existing work storage, pinned through parsing.

        No candidate is replaced or deleted. Windows denies leaf write/delete;
        POSIX parsing uses the held descriptor, never a subsequently swapped name.
        """
        from row_bot.file_ownership import guard_directory, directory_identity
        self._owned_source_locations(job)
        if (not re.fullmatch(r"[a-f0-9]{64}",job.content_sha256) or job.extension not in {".pdf",".doc",".docx",".txt",".md",".html",".htm",".epub"}
                or type(job.size_bytes) is not int or not 0 < job.size_bytes <= MAX_UPLOAD_BYTES):
            raise DocumentJobError("Invalid document parser snapshot identity")
        source = pathlib.Path(job.staged_path).absolute()
        root = self.work_root.absolute()
        def leaf(directory, name, descriptor):
            return name if descriptor is not None else directory / name
        validate()
        with guard_directory(root,directory_identity(root,parent=True)) as root_fd:
            validate()
            try:
                os.mkdir(leaf(root,job.id,root_fd),dir_fd=root_fd)
            except FileExistsError:
                pass
            work = root / job.id
            with guard_directory(work,directory_identity(work,parent=True)) as work_fd:
                name = f"source-{job.content_sha256}{job.extension}"
                candidate = leaf(work,name,work_fd)
                try:
                    os.stat(candidate,dir_fd=work_fd,follow_symlinks=False)
                except FileNotFoundError:
                    with guard_directory(source.parent,directory_identity(source.parent,parent=True)) as source_parent:
                        source_leaf = leaf(source.parent,source.name,source_parent)
                        source_fd = os.open(source_leaf,os.O_RDONLY | getattr(os,"O_NOFOLLOW",0) | getattr(os,"O_NONBLOCK",0),dir_fd=source_parent)
                        try:
                            before = os.fstat(source_fd)
                            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != job.size_bytes or not 0 < before.st_size <= MAX_UPLOAD_BYTES:
                                raise DocumentJobError("Document source changed before parsing")
                            validate()
                            fd = os.open(candidate,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600,dir_fd=work_fd)
                            try:
                                digest,total = hashlib.sha256(),0
                                while chunk := os.read(source_fd,UPLOAD_CHUNK_BYTES):
                                    validate()
                                    total += len(chunk)
                                    if total > job.size_bytes or shutil.disk_usage(work).free - len(chunk) < MIN_STAGING_FREE_BYTES:
                                        raise DocumentJobError("Document snapshot exceeds staging budget")
                                    offset = 0
                                    while offset < len(chunk):
                                        validate()
                                        written = os.write(fd,chunk[offset:])
                                        if written <= 0:
                                            raise OSError("Document snapshot made no progress")
                                        offset += written
                                    digest.update(chunk)
                                os.fsync(fd)
                            finally:
                                os.close(fd)
                            after = os.fstat(source_fd)
                            named = os.stat(source_leaf,dir_fd=source_parent,follow_symlinks=False)
                            if (total != job.size_bytes or digest.hexdigest() != job.content_sha256 or after.st_nlink != 1
                                    or (before.st_dev,before.st_ino,before.st_mtime_ns) != (after.st_dev,after.st_ino,after.st_mtime_ns)
                                    or (after.st_dev,after.st_ino) != (named.st_dev,named.st_ino)):
                                raise DocumentJobError("Document source changed while capturing parser bytes")
                        finally:
                            os.close(source_fd)
                validate()
                fd = _open_protected_source(candidate,dir_fd=work_fd)
                try:
                    initial = os.fstat(fd)
                    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or initial.st_size != job.size_bytes:
                        raise DocumentJobError("Document snapshot changed")
                    digest,total = hashlib.sha256(),0
                    while chunk := os.read(fd,UPLOAD_CHUNK_BYTES):
                        validate()
                        total += len(chunk)
                        if total > job.size_bytes:
                            raise DocumentJobError("Document snapshot exceeds reviewed size")
                        digest.update(chunk)
                    if total != job.size_bytes or digest.hexdigest() != job.content_sha256:
                        raise DocumentJobError("Document snapshot differs from reviewed bytes")
                    os.lseek(fd,0,os.SEEK_SET)
                    def check():
                        current = os.fstat(fd)
                        if (initial.st_dev,initial.st_ino,initial.st_size,initial.st_mtime_ns,1) != (
                                current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns,current.st_nlink):
                            raise DocumentJobError("Document snapshot changed during processing")
                    check()
                    validate()
                    path = str(work / name) if os.name == "nt" else (
                        f"/proc/self/fd/{fd}" if pathlib.Path("/proc/self/fd").is_dir() else f"/dev/fd/{fd}")
                    yield path, job.extension, check
                    validate()
                    check()
                finally:
                    os.close(fd)

    def _initialize_or_recover_schema(self) -> None:
        try:
            self._initialize_schema()
        except sqlite3.DatabaseError as exc:
            corrupt = self.db_path.with_name(
                f"jobs.corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.db"
            )
            logger.error("Recovering corrupt document-ingestion DB to %s: %s", corrupt, exc)
            if self.db_path.exists():
                os.replace(self.db_path, corrupt)
            for suffix in ("-wal", "-shm"):
                sidecar = pathlib.Path(f"{self.db_path}{suffix}")
                with contextlib.suppress(FileNotFoundError):
                    sidecar.unlink()
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_batches (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS document_jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    extension TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    index_progress_current INTEGER NOT NULL DEFAULT 0,
                    index_progress_total INTEGER NOT NULL DEFAULT 0,
                    extraction_progress_current INTEGER NOT NULL DEFAULT 0,
                    extraction_progress_total INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    searchable_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(batch_id) REFERENCES document_batches(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS document_batch_admissions (
                    batch_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    policy_digest TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'staged',
                    FOREIGN KEY(batch_id) REFERENCES document_batches(id) ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_document_jobs_batch_sequence
                    ON document_jobs(batch_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_document_jobs_status_fifo
                    ON document_jobs(status, sequence, created_at);

                CREATE TABLE IF NOT EXISTS document_records (
                    document_id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    searchable_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_document_records_hash
                    ON document_records(content_sha256);

                CREATE TABLE IF NOT EXISTS document_worker_leases (
                    name TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_removals (
                    id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_document_removals_target
                    ON document_removals(target, created_at);

                CREATE TABLE IF NOT EXISTS document_map_summaries (
                    job_id TEXT NOT NULL,
                    window_index INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, window_index),
                    FOREIGN KEY(job_id) REFERENCES document_jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS document_reduce_summaries (
                    job_id TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    group_index INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, level, group_index),
                    FOREIGN KEY(job_id) REFERENCES document_jobs(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute("PRAGMA user_version=1")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(document_batch_admissions)")}
            for column in ("processing_command_id", "processing_owner_id", "conversation_id", "source_digest", "authority_digest", "processing_lock_identity"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE document_batch_admissions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"document ingestion integrity check failed: {integrity}")

    def create_batch(self, *, batch_id: str | None = None, client_admission: dict | None = None,
                     validate: Callable[[], None] | None = None) -> str:
        batch_id = batch_id or uuid.uuid4().hex
        strict = client_admission is not None
        if strict:
            if (not re.fullmatch(r"client_[a-f0-9]{32}",batch_id) or client_admission.keys() != {"owner_id","command_id"}
                    or not isinstance(client_admission["owner_id"],str) or not 1 <= len(client_admission["owner_id"]) <= 256
                    or str(uuid.UUID(client_admission["command_id"])) != client_admission["command_id"] or validate is None):
                raise DocumentJobError("Invalid client batch admission")
        elif not re.fullmatch(r"[a-f0-9]{32}",batch_id):
            raise DocumentJobError("Invalid document batch identity")
        now = self._now()
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            conn.execute(
                """
                INSERT INTO document_batches
                    (id, created_at, updated_at, status, pause_requested, cancel_requested)
                VALUES (?, ?, ?, 'staging', ?, 0)
                """,
                (batch_id, now, now, int(strict)),
            )
            if strict:
                conn.execute("INSERT INTO document_batch_admissions(batch_id,owner_id,command_id) VALUES (?,?,?)",
                    (batch_id,client_admission["owner_id"],client_admission["command_id"]))
            if validate is not None:
                validate()
        return batch_id

    def _processing_sources(self, conn, batch_id):
        rows = conn.execute("SELECT id,sequence,original_name,stored_name,content_sha256,size_bytes,extension FROM document_jobs WHERE batch_id=? ORDER BY sequence,id",(batch_id,)).fetchmany(51)
        if not 1 <= len(rows) <= 50:
            raise DocumentJobError("Document processing source scope unavailable")
        return hashlib.sha256(json.dumps([dict(row) for row in rows],sort_keys=True,separators=(",",":")).encode()).hexdigest()

    def processing_admission(self, batch_id: str) -> dict:
        with contextlib.closing(self._connect()) as conn:
            return self._processing_admission_in_connection(conn,batch_id)

    @contextlib.contextmanager
    def _processing_scope_lock(self, batch_id: str):
        if not re.fullmatch(r"client_[a-f0-9]{32}", batch_id):
            raise DocumentJobError("Invalid document processing batch identity")
        from row_bot.file_ownership import DirectoryOwnershipError, directory_identity, guard_directory
        parent = self.root.absolute()
        name = f"processing-{batch_id}.lock"
        descriptor = None
        admitted = False
        try:
            with guard_directory(parent, directory_identity(parent, parent=True)) as directory:
                if os.name == "nt":
                    import ctypes
                    from ctypes import wintypes
                    import msvcrt
                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    create = kernel.CreateFileW
                    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                    create.restype = wintypes.HANDLE
                    # OPEN_REPARSE_POINT, no sharing: neither leaf replacement
                    # nor an overlapping opener can change our lock object.
                    handle = create(str(parent / name), 0xC0000000, 0, None, 4, 0x00200080, None)
                    if handle == wintypes.HANDLE(-1).value:
                        raise OSError("processing lock unavailable")
                    try:
                        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                    except BaseException:
                        close = kernel.CloseHandle
                        close.argtypes = [wintypes.HANDLE]
                        close.restype = wintypes.BOOL
                        close(handle)
                        raise
                else:
                    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                        0o600, dir_fd=directory)
                leaf = name if directory is not None else parent / name
                opened = os.fstat(descriptor)
                named = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                def identity(info) -> tuple:
                    return (info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", info.st_ctime_ns))
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or getattr(named, "st_file_attributes", 0) & 0x400
                        or not stat.S_ISREG(named.st_mode) or identity(opened) != identity(named)):
                    raise OSError("processing lock ownership unavailable")
                # Byte-range locks may extend beyond EOF. Never write a
                # sentinel through an existing file merely to lock its region.
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                current = os.fstat(descriptor)
                named = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                if current.st_nlink != 1 or identity(current) != identity(opened) or identity(named) != identity(opened):
                    raise OSError("processing lock identity changed")
                # A POSIX name can be unlinked while its old inode is locked.
                # Pin the object in the existing admission, so reopening a new
                # inode never becomes false evidence that the old scope ended.
                parent_info = os.fstat(directory) if directory is not None else parent.stat()
                token = f"{parent_info.st_dev}:{parent_info.st_ino}:0:" + ":".join(map(str, identity(opened)))
                if directory is None:
                    connection = self._connect()
                else:
                    # Keep the identity transaction under the same held data
                    # directory, including SQLite's ordinary WAL/SHM sidecars.
                    # Never fall back to the mutable configured pathname.
                    bridge = None
                    for candidate in (pathlib.Path(f"/proc/self/fd/{directory}"), pathlib.Path(f"/dev/fd/{directory}")):
                        try:
                            info = candidate.stat()
                            if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (parent_info.st_dev, parent_info.st_ino):
                                bridge = candidate
                                break
                        except OSError:
                            continue
                    if bridge is None:
                        raise DocumentJobError("Document processing descriptor SQLite path unavailable")
                    for suffix in ("", "-wal", "-shm"):
                        try:
                            info = os.stat("jobs.db" + suffix, dir_fd=directory, follow_symlinks=False)
                        except FileNotFoundError:
                            if suffix:
                                continue
                            raise
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise DocumentJobError("Document processing database ownership unavailable")
                    database_info = os.stat("jobs.db", dir_fd=directory, follow_symlinks=False)
                    try:
                        connection = sqlite3.connect((bridge / "jobs.db").as_uri() + "?mode=rw", uri=True, timeout=10)
                    except sqlite3.Error as error:
                        raise DocumentJobError("Document processing descriptor SQLite path unavailable") from error
                with contextlib.closing(connection) as conn, conn:
                    if directory is not None:
                        current_database = os.stat("jobs.db", dir_fd=directory, follow_symlinks=False)
                        if (current_database.st_dev, current_database.st_ino) != (database_info.st_dev, database_info.st_ino):
                            raise DocumentJobError("Document processing database ownership changed")
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("SELECT processing_lock_identity FROM document_batch_admissions WHERE batch_id=?", (batch_id,)).fetchone()
                    if row is None or row[0] not in ("", token):
                        raise DocumentJobError("Document processing work lock identity changed")
                    if not row[0]:
                        conn.execute("UPDATE document_batch_admissions SET processing_lock_identity=? WHERE batch_id=?", (token, batch_id))
                admitted = True
                yield
        except (OSError, DirectoryOwnershipError) as error:
            if admitted:
                raise  # Preserve provider/body failures after lock admission.
            raise DocumentJobError("Document processing is active or its work lock is unavailable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)  # Releases only this acquired OS lock.

    @contextlib.contextmanager
    def processing_scope(self, batch_id: str):
        if not batch_id.startswith("client_"):
            yield None
            return
        with self._processing_scope_lock(batch_id):
            proof = self.processing_admission(batch_id)
            policy = self.processing_policy_resolver(proof)
            def validate_work():
                current = self.processing_admission(batch_id)
                if current != proof:
                    raise DocumentJobError("Document processing admission changed")
                batch = self.get_batch(batch_id)
                # Pause prevents the next claim; an already admitted stage finishes
                # its durable checkpoint. Only Cancel interrupts that stage.
                if batch.cancel_requested:
                    raise DocumentCancelled("Document batch stopped")
            with policy.worker_scope(proof["policy_digest"],validate_work=validate_work) as captured:
                yield captured

    def _processing_admission_in_connection(self, conn, batch_id):
        row = conn.execute("SELECT * FROM document_batch_admissions WHERE batch_id=?",(batch_id,)).fetchone()
        if row is None or row["state"] != "authorized" or self.processing_policy_resolver is None:
            raise DocumentJobError("Document processing requires current reviewed policy")
        proof = {key: row[key] for key in ("batch_id", "owner_id", "processing_owner_id",
            "conversation_id", "processing_command_id", "source_digest", "policy_digest", "authority_digest")}
        if (not re.fullmatch(r"[a-f0-9]{64}",proof["policy_digest"])
                or not proof["processing_owner_id"] or not proof["conversation_id"]
                or self._processing_sources(conn,batch_id) != proof["source_digest"]):
            raise DocumentJobError("Document processing source scope changed")
        policy = self.processing_policy_resolver(proof)
        policy.validate_admission(proof)
        return proof

    def authorize_processing(self, batch_id: str, *, expected_snapshot: dict, proof: dict,
                             validate: Callable[[], None]) -> None:
        with self._processing_scope_lock(batch_id), self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            validate()
            if document_control_snapshot(conn,[batch_id]) != expected_snapshot:
                raise DocumentJobError("Document queue changed before processing admission")
            batch = conn.execute("SELECT * FROM document_batches WHERE id=?",(batch_id,)).fetchone()
            row = conn.execute("SELECT * FROM document_batch_admissions WHERE batch_id=?",(batch_id,)).fetchone()
            if (not batch_id.startswith("client_") or not row or row["owner_id"] != proof.get("owner_id")
                    or not batch or batch["status"] not in {"paused","queued"} or batch["cancel_requested"]
                    or expected_snapshot["removals"] or any(job["status"] in {"staging","indexing","extracting"} for job in expected_snapshot["jobs"])):
                raise DocumentJobError("Document processing admission unavailable")
            if proof.get("source_digest") != self._processing_sources(conn,batch_id):
                raise DocumentJobError("Document processing source scope changed")
            if any(not re.fullmatch(r"[a-f0-9]{64}",str(proof.get(key,""))) for key in ("policy_digest","source_digest","authority_digest")):
                raise DocumentJobError("Document processing proof unavailable")
            if (proof.keys() != {"batch_id", "owner_id", "processing_owner_id", "conversation_id",
                    "processing_command_id", "source_digest", "policy_digest", "authority_digest"}
                    or proof["batch_id"] != batch_id
                    or not isinstance(proof["processing_owner_id"], str) or not 1 <= len(proof["processing_owner_id"]) <= 256
                    or not isinstance(proof["conversation_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", proof["conversation_id"])):
                raise DocumentJobError("Document processing proof unavailable")
            uuid.UUID(proof["processing_command_id"])
            validate()
            conn.execute("UPDATE document_batch_admissions SET state='authorized',processing_command_id=?,processing_owner_id=?,conversation_id=?,policy_digest=?,source_digest=?,authority_digest=? WHERE batch_id=?",
                (proof["processing_command_id"],proof["processing_owner_id"],proof["conversation_id"],proof["policy_digest"],proof["source_digest"],proof["authority_digest"],batch_id))
            conn.execute("UPDATE document_batches SET status='queued',pause_requested=0,updated_at=? WHERE id=?",(self._now(),batch_id))
            validate()
            conn.commit()
        _wake_supervisor()

    def create_staging_job(
        self,
        batch_id: str,
        sequence: int,
        original_name: str,
        *, job_id: str | None = None, validate: Callable[[], None] | None = None,
    ) -> DocumentJob:
        strict = job_id is not None
        job_id = job_id or uuid.uuid4().hex
        if strict and (not re.fullmatch(r"upload_[a-f0-9]{32}",job_id) or not batch_id.startswith("client_") or validate is None):
            raise DocumentJobError("Invalid reviewed upload identity")
        safe_name = sanitize_original_name(original_name)
        stored_name = collision_safe_stored_name(safe_name, job_id)
        staged_path = self.staging_root / job_id / stored_name
        now = self._now()
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            batch = conn.execute(
                "SELECT status FROM document_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None or batch["status"] != "staging":
                raise DocumentJobError("Uploads can only be added while a batch is staging")
            if batch_id.startswith("client_") and (not strict or not conn.execute(
                    "SELECT 1 FROM document_batch_admissions WHERE batch_id=? AND state='staged'",(batch_id,)).fetchone()):
                raise DocumentJobError("Reviewed batch admission is unavailable")
            conn.execute(
                """
                INSERT INTO document_jobs (
                    id, batch_id, sequence, original_name, stored_name, staged_path,
                    content_sha256, size_bytes, extension, status, stage,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', 0, ?, 'staging', 'upload', ?, ?)
                """,
                (
                    job_id,
                    batch_id,
                    int(sequence),
                    str(original_name or safe_name),
                    stored_name,
                    str(staged_path),
                    pathlib.Path(safe_name).suffix.lower(),
                    now,
                    now,
                ),
            )
            if validate is not None:
                validate()
        return self.get_job(job_id)

    def complete_staging(
        self,
        job_id: str,
        content_sha256: str,
        size_bytes: int,
        staged_path: str | pathlib.Path,
        *, validate: Callable[[], None] | None = None, retain_duplicate: bool = False,
        expected_identity: tuple[int,int] | None = None,
    ) -> DocumentJob:
        now = self._now()
        duplicate = False
        with self._write_lock, contextlib.ExitStack() as held, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            row = conn.execute(
                "SELECT * FROM document_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "staging":
                raise InvalidJobTransition(f"{row['status']} -> queued")
            if str(row["batch_id"]).startswith("client_"):
                if expected_identity is None or validate is None or not retain_duplicate:
                    raise DocumentJobError("Reviewed upload proof is required")
                if row["cancel_requested"] or conn.execute("SELECT cancel_requested FROM document_batches WHERE id=?",
                        (row["batch_id"],)).fetchone()[0]:
                    raise DocumentCancelled("Document upload was cancelled")
                self._hold_reviewed_upload(held,row,pathlib.Path(staged_path),size_bytes,content_sha256,expected_identity,validate)
            duplicate = conn.execute(
                "SELECT 1 FROM document_records WHERE content_sha256=? LIMIT 1",
                (content_sha256,),
            ).fetchone() is not None
            status = "skipped_duplicate" if duplicate else "queued"
            completed_at = now if duplicate else ""
            conn.execute(
                """
                UPDATE document_jobs
                SET content_sha256=?, size_bytes=?, staged_path=?, status=?,
                    updated_at=?, completed_at=?, error_code='', error_message=''
                WHERE id=?
                """,
                (
                    content_sha256,
                    int(size_bytes),
                    str(staged_path),
                    status,
                    now,
                    completed_at,
                    job_id,
                ),
            )
            if validate is not None:
                validate()
            conn.commit()
        if duplicate and not retain_duplicate:
            with contextlib.suppress(FileNotFoundError):
                pathlib.Path(staged_path).unlink()
        return self.get_job(job_id)

    def _hold_reviewed_upload(self, held, row, source, size, expected_hash, expected_identity, validate):
        from row_bot.file_ownership import directory_identity, guard_directory
        import stat
        source = source.absolute()
        if source != (self.staging_root / row["id"] / row["stored_name"]).absolute() or not 0 < size <= MAX_UPLOAD_BYTES:
            raise DocumentJobError("Upload source is outside its owner or budget")
        self._validate_source_path(source)
        descriptor = held.enter_context(guard_directory(source.parent,directory_identity(source.parent,parent=True)))
        leaf = source.name if descriptor is not None else source
        fd = _open_protected_source(leaf,dir_fd=descriptor)
        held.callback(os.close,fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != size
                or (before.st_dev,before.st_ino) != tuple(expected_identity)):
            raise DocumentJobError("Upload source identity changed")
        digest,remaining = hashlib.sha256(),size
        while remaining:
            validate()
            part = os.read(fd,min(UPLOAD_CHUNK_BYTES,remaining))
            if not part:
                raise DocumentJobError("Upload source ended early")
            digest.update(part)
            remaining -= len(part)
        after = os.fstat(fd)
        named = os.stat(leaf,dir_fd=descriptor,follow_symlinks=False)
        if (digest.hexdigest() != expected_hash or after.st_size != size or after.st_mtime_ns != before.st_mtime_ns
                or after.st_nlink != 1 or (named.st_dev,named.st_ino) != tuple(expected_identity)):
            raise DocumentJobError("Upload source bytes changed")

    def fail_staging(self, job_id: str, code: str, message: str) -> DocumentJob:
        return self.transition_job(
            job_id,
            "failed",
            stage="upload",
            error_code=code,
            error_message=message,
        )

    def finish_batch_staging(self, batch_id: str, *, paused: bool = False,
                             validate: Callable[[], None] | None = None) -> DocumentBatch:
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            batch = conn.execute(
                "SELECT * FROM document_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise KeyError(batch_id)
            if batch["status"] != "staging":
                conn.commit()
                return _row_batch(batch)
            rows = conn.execute(
                "SELECT status FROM document_jobs WHERE batch_id=?", (batch_id,)
            ).fetchall()
            statuses = {row["status"] for row in rows}
            if "staging" in statuses:
                raise DocumentJobError("Cannot queue a batch with unfinished uploads")
            status = self._terminal_batch_status(statuses) if not (statuses - TERMINAL_JOB_STATUSES) else "queued"
            if batch_id.startswith("client_"):
                if not paused or validate is None:
                    raise DocumentJobError("Reviewed uploads must remain paused until processing approval")
                status = "paused"
            now = self._now()
            conn.execute(
                "UPDATE document_batches SET status=?, pause_requested=?, updated_at=? WHERE id=?",
                (status, int(paused or batch["pause_requested"]), now, batch_id),
            )
            if validate is not None:
                validate()
            conn.commit()
        _wake_supervisor()
        return self.get_batch(batch_id)

    def get_job(self, job_id: str) -> DocumentJob:
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM document_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _row_job(row)

    def get_batch(self, batch_id: str) -> DocumentBatch:
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM document_batches WHERE id=?", (batch_id,)
            ).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return _row_batch(row)

    def list_jobs(self, batch_id: str | None = None) -> list[DocumentJob]:
        query = "SELECT * FROM document_jobs"
        params: tuple[object, ...] = ()
        if batch_id is not None:
            query += " WHERE batch_id=?"
            params = (batch_id,)
        query += " ORDER BY created_at, batch_id, sequence"
        with contextlib.closing(self._connect()) as conn, conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_job(row) for row in rows]

    def list_batches(self, *, include_finished: bool = True) -> list[DocumentBatch]:
        query = "SELECT * FROM document_batches"
        if not include_finished:
            query += " WHERE status NOT IN ('completed','completed_with_errors','cancelled')"
        query += " ORDER BY created_at, id"
        with contextlib.closing(self._connect()) as conn, conn:
            rows = conn.execute(query).fetchall()
        return [_row_batch(row) for row in rows]

    def list_document_records(self) -> list[dict[str, object]]:
        with contextlib.closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM document_records ORDER BY searchable_at, document_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_document_record(self, document_id: str, *, validate: Callable[[], None] | None = None) -> bool:
        """Remove one durable searchable-record row by document ID."""
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            cur = conn.execute(
                "DELETE FROM document_records WHERE document_id=?",
                (document_id,),
            )
        return bool(cur.rowcount)

    def get_removal(self, removal_id: str) -> dict | None:
        """Read one durable removal intent/result from the existing jobs owner."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM document_removals WHERE id=?", (removal_id,)).fetchone()
        if row is None:
            return None
        return {"removal_id": row["id"], "target": row["target"],
                "snapshot": json.loads(row["snapshot"]), "result": json.loads(row["result"])}

    def latest_removal(self, target: str) -> dict | None:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id FROM document_removals WHERE target=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (target,),
            ).fetchone()
        return self.get_removal(str(row["id"])) if row else None

    def begin_removal(self, target: str, snapshot: dict, result: dict, *, removal_id: str) -> dict:
        """Persist recovery information before any removal effects."""
        if not re.fullmatch(r"[a-f0-9]{32}", removal_id):
            raise ValueError("Invalid document removal ID")
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO document_removals VALUES (?,?,?,?,?,?)",
                (removal_id, target, json.dumps(snapshot), json.dumps(result), self._now(), self._now()),
            )
        existing = self.get_removal(removal_id)
        if existing is None or existing["target"] != target:
            raise ValueError("Document removal ID belongs to another target")
        return existing

    def save_removal_result(self, removal_id: str, result: dict) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE document_removals SET result=?, updated_at=? WHERE id=?",
                (json.dumps(result), self._now(), removal_id),
            )
            if not cursor.rowcount:
                raise KeyError(removal_id)

    def save_removal_snapshot(self, removal_id: str, snapshot: dict) -> None:
        """Persist the acknowledged worker's final cleanup set before effects."""
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "UPDATE document_removals SET snapshot=?, updated_at=? WHERE id=?",
                (json.dumps(snapshot), self._now(), removal_id),
            )
            if not cursor.rowcount:
                raise KeyError(removal_id)

    def clear_document_records(self) -> int:
        """Clear searchable-record metadata after indexes are retired."""
        with contextlib.closing(self._connect()) as conn, conn:
            count = int(conn.execute("SELECT COUNT(*) FROM document_records").fetchone()[0])
            conn.execute("DELETE FROM document_records")
        return count

    def cancel_all_batches(self) -> int:
        """Persist cancellation for all unfinished batches."""
        with contextlib.closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT id FROM document_batches
                WHERE status NOT IN ('completed','completed_with_errors','cancelled')
                """
            ).fetchall()
        for row in rows:
            self.cancel_batch(str(row["id"]))
        return len(rows)

    def retire_document_source(
        self, document_id: str, *, record: dict | None = None,
        retirement_id: str | None = None,
        validate: Callable[[], None] | None = None,
    ) -> pathlib.Path | None:
        """Move one retained source to a recoverable retired directory."""
        if validate is None:
            return self._retire_document_source(document_id, record=record, retirement_id=retirement_id)
        with self._write_lock:
            validate()
            return self._retire_document_source(document_id, record=record, retirement_id=retirement_id, validate=validate)

    def _retire_document_source(
        self, document_id: str, *, record: dict | None = None,
        retirement_id: str | None = None, validate: Callable[[], None] | None = None,
    ) -> pathlib.Path | None:
        if record is None:
            with contextlib.closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM document_records WHERE document_id=?", (document_id,),
                ).fetchone()
            record = dict(row) if row else None
        if record is None:
            return None
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", document_id):
            raise DocumentJobError("Invalid document source ID")
        stored_name = str(record["stored_name"])
        if pathlib.Path(stored_name).name != stored_name or "/" in stored_name or "\\" in stored_name:
            raise DocumentJobError("Invalid retained document filename")
        source = pathlib.Path(str(record["staged_path"]))
        candidates = {self.completed_root / document_id / stored_name,
                      self.staging_root / document_id / stored_name}
        if source.absolute() not in {path.absolute() for path in candidates}:
            raise DocumentJobError("Document source does not match its owned staging/completed location")
        for path in candidates:
            self._validate_source_path(path)
        if retirement_id is not None and not re.fullmatch(r"[a-f0-9]{32}", retirement_id):
            raise DocumentJobError("Invalid document retirement ID")
        retired = self.root / "retired" / (retirement_id or document_id) / stored_name
        self._validate_source_path(retired)
        present = [path for path in candidates if path.is_file()]
        expected_hash = str(record.get("content_sha256") or "")
        pending = [path for path in retired.parent.iterdir()
                   if path.name.startswith(f".{stored_name}.") and path.name.endswith(".retiring")] if retired.parent.exists() else []
        if not present and not retired.exists() and len(pending) == 1:
            self._validate_source_path(pending[0])
            if expected_hash and self._source_hash(pending[0]) != expected_hash:
                raise DocumentJobError("Interrupted source differs from its saved hash; preserve for review")
            if validate is not None:
                validate()
            os.link(pending[0], retired)
        if retired.is_file():
            if present:
                raise DocumentJobError("Both retained and live sources exist; preserve for review")
            if expected_hash and self._source_hash(retired) != expected_hash:
                raise DocumentJobError("Retained source differs from its saved hash; preserve for review")
            return retired
        if len(present) != 1:
            raise DocumentJobError("Retained source is missing or ambiguous; preserve for review")
        source = present[0]
        if expected_hash and self._source_hash(source) != expected_hash:
            raise DocumentJobError("Retained source was externally edited; preserve for review")
        retired.parent.mkdir(parents=True, exist_ok=True)
        self._validate_source_path(source)
        self._validate_source_path(retired)
        intermediate = retired.with_name(f".{stored_name}.{uuid.uuid4().hex}.retiring")
        if validate is not None:
            validate()
        os.rename(source, intermediate)
        if validate is not None:
            validate()
        os.link(intermediate, retired)
        if expected_hash and self._source_hash(retired) != expected_hash:
            raise DocumentJobError("Retained source changed during retirement; its copy is preserved")
        return retired

    def _validate_source_path(self, path: pathlib.Path) -> None:
        root = self.root.absolute()
        path = path.absolute()
        if path == root or root not in path.parents:
            raise DocumentJobError("Document source is outside the ingestion owner")
        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise DocumentJobError("Linked document source is preserved for review")
        if root.is_symlink() or root.is_junction():
            raise DocumentJobError("Linked document ingestion owner")

    @staticmethod
    def _source_hash(path: pathlib.Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for data in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(data)
        return digest.hexdigest()

    def retire_all_document_sources(self) -> int:
        """Recoverably retire every retained source before a clear-all."""
        records = self.list_document_records()
        retired = 0
        for record in records:
            if self.retire_document_source(str(record["document_id"])) is not None:
                retired += 1
        return retired

    def health(self) -> dict[str, object]:
        """Return read-only database and interrupted-work health details."""
        db_ok = False
        error = ""
        known_ids: set[str] = set()
        missing_sources = 0
        try:
            with contextlib.closing(self._connect()) as conn, conn:
                db_ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                rows = conn.execute(
                    "SELECT id, status, staged_path FROM document_jobs"
                ).fetchall()
            known_ids = {str(row["id"]) for row in rows}
            missing_sources = sum(
                not pathlib.Path(str(row["staged_path"])).is_file()
                for row in rows
                if row["status"] in {
                    "staging",
                    "queued",
                    "indexing",
                    "searchable",
                    "extracting",
                }
            )
        except Exception as exc:
            error = str(exc)
        staging_orphans = sum(
            child.is_dir() and child.name not in known_ids
            for child in self.staging_root.iterdir()
        )
        work_orphans = sum(
            child.is_dir()
            and child.name not in known_ids
            and not child.name.startswith("rebuild-")
            for child in self.work_root.iterdir()
        )
        return {
            "db_ok": db_ok,
            "error": error,
            "missing_sources": int(missing_sources),
            "staging_orphans": int(staging_orphans),
            "work_orphans": int(work_orphans),
        }

    def transition_job(
        self,
        job_id: str,
        new_status: str,
        *,
        stage: str | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> DocumentJob:
        if new_status not in JOB_STATUSES:
            raise InvalidJobTransition(f"Unknown status: {new_status}")
        if stage is not None and stage not in JOB_STAGES:
            raise DocumentJobError(f"Unknown document stage: {stage}")
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM document_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = row["status"]
            if new_status not in TERMINAL_JOB_STATUSES and conn.execute(
                "SELECT 1 FROM document_removals WHERE target=? LIMIT 1", (job_id,),
            ).fetchone():
                raise DocumentCancelled("Document removal is pending")
            if new_status != current and new_status not in _JOB_TRANSITIONS[current]:
                raise InvalidJobTransition(f"{current} -> {new_status}")
            now = self._now()
            started_at = row["started_at"]
            searchable_at = row["searchable_at"]
            completed_at = row["completed_at"]
            if new_status in ACTIVE_JOB_STATUSES and not started_at:
                started_at = now
            if new_status == "searchable" and not searchable_at:
                searchable_at = now
            if new_status in TERMINAL_JOB_STATUSES:
                completed_at = now
            conn.execute(
                """
                UPDATE document_jobs
                SET status=?, stage=COALESCE(?, stage), updated_at=?,
                    started_at=?, searchable_at=?, completed_at=?,
                    error_code=?, error_message=?
                WHERE id=?
                """,
                (
                    new_status,
                    stage,
                    now,
                    started_at,
                    searchable_at,
                    completed_at,
                    error_code,
                    error_message,
                    job_id,
                ),
            )
            conn.commit()
        return self.get_job(job_id)

    def update_progress(
        self,
        job_id: str,
        *,
        stage: str,
        current: int,
        total: int = 0,
    ) -> None:
        if stage not in JOB_STAGES:
            raise DocumentJobError(f"Unknown document stage: {stage}")
        prefix = "index" if stage in {"parse", "embed", "index_commit"} else "extraction"
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                f"""
                UPDATE document_jobs
                SET stage=?, {prefix}_progress_current=?, {prefix}_progress_total=?,
                    updated_at=?
                WHERE id=?
                """,
                (stage, max(0, int(current)), max(0, int(total)), self._now(), job_id),
            )

    def mark_searchable(self, job_id: str, *, validate: Callable[[], None] | None = None) -> DocumentJob:
        with self._write_lock:
            if self.latest_removal(job_id) is not None:
                raise DocumentCancelled("Document removal is pending")
            job = self.get_job(job_id)
            if job.batch_id.startswith("client_") and validate is None:
                def validate():
                    self.processing_admission(job.batch_id)
            return self._publish_job_record(job, "searchable", pathlib.Path(job.staged_path),
                **({"validate":validate} if validate is not None else {}))

    def _publish_job_record(self, job: DocumentJob, status: str, source: pathlib.Path, *,
                            validate: Callable[[], None] | None = None) -> DocumentJob:
        """Commit searchable/completed state and its matching record together."""
        with contextlib.ExitStack() as held, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            row = conn.execute("SELECT * FROM document_jobs WHERE id=?", (job.id,)).fetchone()
            if row is None:
                raise KeyError(job.id)
            if conn.execute("SELECT 1 FROM document_removals WHERE target=? LIMIT 1", (job.id,)).fetchone():
                raise DocumentCancelled("Document removal is pending")
            batch = conn.execute("SELECT cancel_requested FROM document_batches WHERE id=?", (job.batch_id,)).fetchone()
            if row["status"] != "completed" and (row["cancel_requested"] or (batch and batch[0])):
                raise DocumentCancelled("Document was cancelled")
            if any(row[key] != getattr(job, key) for key in ("stored_name", "content_sha256", "size_bytes", "staged_path", "stage")):
                raise DocumentJobError("Document identity changed during publication")
            allowed = {"indexing", "searchable", "extracting"} if status == "searchable" else {"searchable", "extracting", "completed"}
            if status == "completed" and row["stage"] == "finalize":
                allowed.add("failed")
            if row["status"] not in allowed:
                raise InvalidJobTransition(f"{row['status']} -> {status}")
            if status == "completed":
                self._validate_source_path(source)
                if (source != self.completed_root / job.id / job.stored_name or not source.is_file()
                        or source.stat().st_size != job.size_bytes or self._source_hash(source) != job.content_sha256):
                    raise DocumentJobError("Completed source changed before record publication")
            if status == "searchable" and job.batch_id.startswith("client_"):
                current = source.stat()
                self._hold_reviewed_upload(held,row,source,job.size_bytes,job.content_sha256,
                    (current.st_dev,current.st_ino),validate or (lambda:None))
            now = self._now()
            searchable_at = row["searchable_at"] or now
            completed_at = ""
            if status == "completed":
                completed_at = (row["completed_at"] if row["status"] == "completed" else "") or now
            conn.execute(
                """INSERT INTO document_records (
                    document_id, original_name, stored_name, staged_path,
                    content_sha256, size_bytes, searchable_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    original_name=excluded.original_name, stored_name=excluded.stored_name,
                    staged_path=excluded.staged_path, content_sha256=excluded.content_sha256,
                    size_bytes=excluded.size_bytes, searchable_at=excluded.searchable_at,
                    completed_at=excluded.completed_at""",
                (job.id, row["original_name"], job.stored_name, str(source), job.content_sha256,
                 job.size_bytes, searchable_at, completed_at),
            )
            conn.execute(
                """UPDATE document_jobs SET status=?, stage=?, staged_path=?, searchable_at=?,
                    completed_at=?, updated_at=?, error_code='', error_message='' WHERE id=?""",
                (status, "index_commit" if status == "searchable" else "finalize", str(source),
                 searchable_at, completed_at, now, job.id),
            )
            published = conn.execute("SELECT * FROM document_jobs WHERE id=?", (job.id,)).fetchone()
            if validate is not None:
                validate()
            conn.commit()
        return _row_job(published)

    def _owned_source_locations(self, job: DocumentJob) -> tuple[pathlib.Path, pathlib.Path]:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", job.id):
            raise DocumentJobError("Invalid document ID")
        if pathlib.Path(job.stored_name).name != job.stored_name or "/" in job.stored_name or "\\" in job.stored_name:
            raise DocumentJobError("Invalid document source name")
        staged = self.staging_root / job.id / job.stored_name
        completed = self.completed_root / job.id / job.stored_name
        if pathlib.Path(job.staged_path).absolute() not in {staged.absolute(), completed.absolute()}:
            raise DocumentJobError("Document source is outside its owned locations")
        for path in (staged, completed):
            self._validate_source_path(path)
        if not re.fullmatch(r"[a-f0-9]{64}", job.content_sha256):
            raise DocumentJobError("Document source hash is unavailable")
        return staged, completed

    def _validate_source_bytes(self, job: DocumentJob, source: pathlib.Path) -> None:
        self._validate_source_path(source)
        if not source.is_file() or source.stat().st_size != job.size_bytes or self._source_hash(source) != job.content_sha256:
            raise DocumentJobError("Document source changed; preserve for review")

    def _completed_source(self, job: DocumentJob, *, validate: Callable[[], None] | None = None) -> pathlib.Path:
        validate = validate or (lambda: None)
        validate()
        staged, completed = self._owned_source_locations(job)
        recovery = self.root / "recovery_orphans" / f"finalize-{job.id}"
        self._validate_source_path(recovery)
        retained = []
        if recovery.exists():
            for entry in recovery.iterdir():
                if len(retained) >= 100:
                    raise DocumentJobError("Too many retained source entries; preserve for review")
                self._validate_source_bytes(job, entry)
                if not completed.is_file() or not os.path.samefile(entry, completed):
                    raise DocumentJobError("Retained finalization source identity differs; preserve for review")
                retained.append(entry)
        for path in (staged, completed):
            if path.exists() and (not path.is_file() or path.stat().st_size != job.size_bytes
                                  or self._source_hash(path) != job.content_sha256):
                raise DocumentJobError("Document source changed; preserve both locations for review")
        if staged.exists() and completed.exists() and not os.path.samefile(staged, completed):
            raise DocumentJobError("Both document source locations exist; preserve for review")
        if not completed.exists():
            if not staged.is_file():
                raise DocumentJobError("Document source is missing from both owned locations")
            validate()
            completed.parent.mkdir(parents=True, exist_ok=True)
            self._validate_source_path(staged)
            self._validate_source_path(completed)
            validate()
            os.link(staged, completed)  # No overwrite; an interrupted link is recognizable by identity.
        if staged.exists():
            if not os.path.samefile(staged, completed) or self._source_hash(completed) != job.content_sha256:
                raise DocumentJobError("Document source changed during finalization; preserve for review")
            if retained:
                raise DocumentJobError("Staging source reappeared after retention; preserve for review")
            validate()
            recovery.mkdir(parents=True, exist_ok=True)
            saved = recovery / f"{uuid.uuid4().hex}.source"
            self._validate_source_path(staged)
            self._validate_source_path(saved)
            validate()
            _rename_source_no_replace(staged, saved)
            # The leaf name may have been replaced after validation. Keep the
            # moved bytes even then; never unlink an uncertain source version.
            self._validate_source_bytes(job, saved)
            if not os.path.samefile(saved, completed):
                raise DocumentJobError("Staging source changed during retention; preserve for review")
            validate()
            with contextlib.suppress(OSError):
                staged.parent.rmdir()
        return completed

    def mark_completed(self, job_id: str, *, validate: Callable[[], None] | None = None) -> DocumentJob:
        initial = self.get_job(job_id)
        if initial.batch_id.startswith("client_") and validate is None:
            def validate():
                self.processing_admission(initial.batch_id)
        authority_error = None
        def authority():
            nonlocal authority_error
            if validate is not None:
                try:
                    validate()
                except Exception as error:
                    authority_error = error
                    raise
        with self._write_lock:
            authority()
            if self.latest_removal(job_id) is not None:
                raise DocumentCancelled("Document removal is pending")
            job = self.get_job(job_id)
            if job.status != "completed":
                self.raise_if_cancelled(job_id)
            if job.status not in {"searchable", "extracting", "completed"} and not (job.status == "failed" and job.stage == "finalize"):
                raise InvalidJobTransition(f"{job.status} -> completed")
            # Existing stage is the durable intent. Completed means all three
            # source placement, job state and record agree, never just intent.
            if job.stage != "finalize":
                with contextlib.closing(self._connect()) as conn, conn:
                    authority()
                    conn.execute("UPDATE document_jobs SET stage='finalize', updated_at=? WHERE id=?", (self._now(), job_id))
                    authority()
                job = self.get_job(job_id)
            try:
                completed = self._completed_source(job, validate=authority) if validate is not None else self._completed_source(job)
                if job.status != "completed":
                    self.raise_if_cancelled(job_id)
                if job.status == "completed" and not job.error_code and self._record_is_current(job, completed):
                    return job
                return (self._publish_job_record(job, "completed", completed, validate=authority) if validate is not None
                        else self._publish_job_record(job, "completed", completed))
            except DocumentCancelled:
                raise
            except Exception as exc:
                if authority_error is not None:
                    raise authority_error
                authority()
                self._record_finalization_failure(job_id, exc)
                raise

    def _record_is_current(self, job: DocumentJob, source: pathlib.Path) -> bool:
        if job.staged_path != str(source) or not job.searchable_at or not job.completed_at:
            return False
        with contextlib.closing(self._connect()) as conn:
            record = conn.execute("SELECT * FROM document_records WHERE document_id=?", (job.id,)).fetchone()
        return record is not None and all(record[key] == getattr(job, key) for key in (
            "original_name", "stored_name", "staged_path", "content_sha256", "size_bytes", "searchable_at", "completed_at"))

    def _record_finalization_failure(self, job_id: str, error: Exception) -> None:
        try:
            with contextlib.closing(self._connect()) as conn, conn:
                conn.execute(
                    """UPDATE document_jobs SET status='failed', stage='finalize',
                        error_code='finalization_incomplete', error_message=?, updated_at=?
                        WHERE id=? AND status != 'cancelled' AND NOT EXISTS (
                            SELECT 1 FROM document_removals WHERE target=document_jobs.id)""",
                    (str(error)[:2000], self._now(), job_id),
                )
        except Exception:
            logger.warning("Finalization failure could not be recorded; saved intent remains", exc_info=True)

    def _recover_finalizations(self) -> dict[str, int]:
        counts = {"finalization_recovered": 0, "finalization_incomplete": 0}
        after = ""
        while True:
            with contextlib.closing(self._connect()) as conn:
                rows = conn.execute(
                    """SELECT * FROM document_jobs WHERE id>? AND (
                        status='completed' OR (stage='finalize' AND status IN ('searchable','extracting','failed'))
                        OR (status IN ('searchable','extracting') AND NOT EXISTS (
                            SELECT 1 FROM document_records WHERE document_id=document_jobs.id)))
                        AND NOT EXISTS (SELECT 1 FROM document_removals WHERE target=document_jobs.id)
                        ORDER BY id LIMIT 100""", (after,),
                ).fetchall()
            if not rows:
                return counts
            for row in rows:
                after = str(row["id"])
                with self._write_lock:
                    job = self.get_job(after)
                    if self.latest_removal(after) is not None:
                        continue
                    is_finalizing = job.status == "completed" or job.stage == "finalize"
                    try:
                        if is_finalizing:
                            was_current = self._record_is_current(job, self.completed_root / job.id / job.stored_name)
                            self.mark_completed(job.id)
                            counts["finalization_recovered"] += not was_current
                        else:
                            self._owned_source_locations(job)
                            if pathlib.Path(job.staged_path).is_file():
                                self._validate_source_bytes(job, pathlib.Path(job.staged_path))
                                self.mark_searchable(job.id)
                                counts["finalization_recovered"] += 1
                    except DocumentCancelled:
                        continue
                    except Exception as exc:
                        if is_finalizing:
                            self._record_finalization_failure(job.id, exc)
                        else:
                            logger.warning("Searchable record recovery remains pending", exc_info=True)
                        counts["finalization_incomplete"] += 1

    def mark_failed(
        self,
        job_id: str,
        code: str,
        message: str,
        *,
        stage: str | None = None,
    ) -> DocumentJob:
        return self.transition_job(
            job_id,
            "failed",
            stage=stage,
            error_code=code,
            error_message=message[:2000],
        )

    def _strict_control(self, action: str, target: str | None, expected: dict,
                        validate: Callable[[], None] | None):
        """Reviewed client effects share the canonical jobs writer and snapshots."""
        validate = validate or (lambda: None)
        expected = json.loads(json.dumps(expected))
        batch_ids = [row["id"] for row in expected["batches"]]
        finalized = False
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            validate()
            current = document_control_snapshot(conn, batch_ids)
            if current != expected:
                raise DocumentJobError("Document control scope changed")
            batches = {row["id"]: row for row in current["batches"]}
            jobs = {row["id"]: row for row in current["jobs"]}
            now = self._now()
            if action == "clear":
                for batch_id, batch in batches.items():
                    selected = [row for row in jobs.values() if row["batch_id"] == batch_id]
                    if (batch["status"] not in {"completed", "completed_with_errors", "cancelled"}
                            or any(row["status"] not in TERMINAL_JOB_STATUSES or
                                   (row["stage"] == "finalize" and row["status"] == "failed") for row in selected)):
                        raise DocumentJobError("Document batch is not safely finished")
                    for row in selected:
                        if row["status"] == "completed":
                            job = _row_job(row)
                            _, completed = self._owned_source_locations(job)
                            record = next((item for item in current["records"] if item["document_id"] == job.id), None)
                            removed = any(item["target"] == job.id for item in current["removals"])
                            if not removed and (not record or any(record[key] != getattr(job, key) for key in (
                                    "staged_path", "content_sha256", "size_bytes", "searchable_at", "completed_at"))
                                    or pathlib.Path(job.staged_path) != completed):
                                raise DocumentJobError("Document finalization remains incomplete")
                            if not removed:
                                self._validate_source_bytes(job, completed)
                    validate()
                    conn.execute("DELETE FROM document_batches WHERE id=?", (batch_id,))
                result = len(batch_ids)
                # Strict clearing retires queue rows only. Source, staging and
                # work bytes stay recoverable; no broad recovery or tree delete.
            elif action in {"pause", "resume", "cancel_batch"}:
                if target not in batches or len(batches) != 1:
                    raise DocumentJobError("Document batch is outside the review")
                if batches[target]["status"] in {"completed", "completed_with_errors", "cancelled"}:
                    raise InvalidJobTransition("Document batch is already finished")
                if action == "cancel_batch":
                    conn.execute("UPDATE document_batches SET cancel_requested=1,pause_requested=0,status='cancelled',updated_at=? WHERE id=?", (now,target))
                    validate()
                    conn.execute("""UPDATE document_jobs SET cancel_requested=1,
                        status=CASE WHEN status IN ('staging','queued','searchable') THEN 'cancelled' ELSE status END,
                        completed_at=CASE WHEN status IN ('staging','queued','searchable') THEN ? ELSE completed_at END,
                        updated_at=? WHERE batch_id=? AND status NOT IN ('completed','failed','cancelled','skipped_duplicate')""", (now,now,target))
                else:
                    if action == "resume" and current["removals"]:
                        raise DocumentCancelled("Document removal is pending")
                    conn.execute("UPDATE document_batches SET pause_requested=?,status=?,updated_at=? WHERE id=?",
                                 (int(action == "pause"), "paused" if action == "pause" else "queued",now,target))
                result = _row_batch(conn.execute("SELECT * FROM document_batches WHERE id=?", (target,)).fetchone())
            else:
                if target not in jobs or len(batches) != 1:
                    raise DocumentJobError("Document job is outside the review")
                job = _row_job(jobs[target])
                if action == "cancel_job":
                    if job.status in TERMINAL_JOB_STATUSES:
                        raise InvalidJobTransition("Document job is already finished")
                    immediate = job.status in {"staging", "queued", "searchable"}
                    conn.execute("UPDATE document_jobs SET cancel_requested=1,status=?,updated_at=?,completed_at=? WHERE id=?",
                                 ("cancelled" if immediate else job.status,now,now if immediate else "",target))
                elif action == "retry":
                    if job.status != "failed":
                        raise InvalidJobTransition("Only failed document jobs can be retried")
                    if current["removals"]:
                        raise DocumentCancelled("Document removal is pending")
                    self._owned_source_locations(job)
                    if not 0 < job.size_bytes <= MAX_UPLOAD_BYTES:
                        raise DocumentJobError("Document source exceeds the ingestion budget")
                    if job.stage != "finalize":
                        self._validate_source_bytes(job, pathlib.Path(job.staged_path))
                        searchable = any(row["document_id"] == job.id for row in current["records"])
                        if not searchable:
                            self._retain_retry_work(job.id, validate=validate)
                        validate()
                        conn.execute("""UPDATE document_jobs SET status=?,stage=?,cancel_requested=0,
                            index_progress_current=0,index_progress_total=0,error_code='',error_message='',completed_at='',updated_at=? WHERE id=?""",
                            ("searchable" if searchable else "queued", "knowledge_map" if searchable else "parse",now,target))
                    else:
                        conn.execute("UPDATE document_jobs SET cancel_requested=0 WHERE id=?", (target,))
                        finalized = True
                    validate()
                    conn.execute("UPDATE document_batches SET status='queued',cancel_requested=0,pause_requested=0,updated_at=? WHERE id=?", (now,job.batch_id))
                else:
                    raise DocumentJobError("Invalid document control")
                result = _row_job(conn.execute("SELECT * FROM document_jobs WHERE id=?", (target,)).fetchone())
            validate()
            conn.commit()
            if finalized:
                result = self.mark_completed(target, validate=validate)
                with contextlib.closing(self._connect()) as final_conn, final_conn:
                    final_conn.execute("BEGIN IMMEDIATE")
                    validate()
                    self._finalize_batch_in_connection(final_conn, result.batch_id)
                    validate()
                    final_conn.commit()
        validate()
        if action != "clear":
            _wake_supervisor()
        return result

    def _retain_retry_work(self, job_id: str, *, validate: Callable[[], None]) -> None:
        from row_bot.file_ownership import directory_identity, guard_directory
        from row_bot.developer.edits import _rename_edit_no_replace
        import stat

        parent = self.work_root.absolute()
        self._validate_source_path(parent / job_id)
        with guard_directory(parent, directory_identity(parent, parent=True)) as descriptor:
            leaf = job_id if descriptor is not None else parent / job_id
            try:
                before = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISDIR(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
                raise DocumentJobError("Document retry work is not an owned directory")
            # Retain in the same guarded owner, avoiding another parent or any
            # recursive delete. A replaced leaf is retained then rejected.
            name = f".retry-{job_id}-{uuid.uuid4().hex}"
            destination = name if descriptor is not None else parent / name
            validate()
            _rename_edit_no_replace(leaf, destination, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            after = os.stat(destination, dir_fd=descriptor, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise DocumentJobError("Document retry work changed; retained for review")

    def pause_batch(self, batch_id: str, paused: bool = True, *, expected_snapshot: dict | None = None,
                    validate: Callable[[], None] | None = None) -> DocumentBatch:
        if expected_snapshot is not None:
            return self._strict_control("pause" if paused else "resume", batch_id, expected_snapshot, validate)
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT status FROM document_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(batch_id)
            if row["status"] in {"completed", "completed_with_errors", "cancelled"}:
                return self.get_batch(batch_id)
            status = "paused" if paused else "queued"
            conn.execute(
                """
                UPDATE document_batches
                SET pause_requested=?, status=?, updated_at=?
                WHERE id=?
                """,
                (int(paused), status, self._now(), batch_id),
            )
        if not paused:
            _wake_supervisor()
        return self.get_batch(batch_id)

    def cancel_job(self, job_id: str, *, validate: Callable[[], None] | None = None,
                   expected_snapshot: dict | None = None) -> DocumentJob:
        if expected_snapshot is not None:
            return self._strict_control("cancel_job", job_id, expected_snapshot, validate)
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            row = conn.execute(
                "SELECT status FROM document_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            status = row["status"]
            now = self._now()
            immediate = status in {"staging", "queued", "searchable"}
            conn.execute(
                """
                UPDATE document_jobs
                SET cancel_requested=1, status=?, updated_at=?, completed_at=?
                WHERE id=?
                """,
                ("cancelled" if immediate else status, now, now if immediate else "", job_id),
            )
            conn.commit()
        _wake_supervisor()
        return self.get_job(job_id)

    def cancel_batch(self, batch_id: str, *, validate: Callable[[], None] | None = None,
                     expected_snapshot: dict | None = None) -> DocumentBatch:
        if expected_snapshot is not None:
            return self._strict_control("cancel_batch", batch_id, expected_snapshot, validate)
        now = self._now()
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            conn.execute(
                """
                UPDATE document_batches
                SET cancel_requested=1, pause_requested=0, status='cancelled', updated_at=?
                WHERE id=?
                """,
                (now, batch_id),
            )
            conn.execute(
                """
                UPDATE document_jobs
                SET cancel_requested=1,
                    status=CASE
                        WHEN status IN ('staging','queued','searchable') THEN 'cancelled'
                        ELSE status
                    END,
                    completed_at=CASE
                        WHEN status IN ('staging','queued','searchable') THEN ?
                        ELSE completed_at
                    END,
                    updated_at=?
                WHERE batch_id=? AND status NOT IN ('completed','failed','cancelled','skipped_duplicate')
                """,
                (now, now, batch_id),
            )
            conn.commit()
        _wake_supervisor()
        return self.get_batch(batch_id)

    def should_cancel(self, job_id: str) -> bool:
        with contextlib.closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT j.cancel_requested AS job_cancel, b.cancel_requested AS batch_cancel
                FROM document_jobs j
                JOIN document_batches b ON b.id=j.batch_id
                WHERE j.id=?
                """,
                (job_id,),
            ).fetchone()
        return row is None or bool(row["job_cancel"] or row["batch_cancel"])

    def raise_if_cancelled(self, job_id: str) -> None:
        if self.should_cancel(job_id):
            raise DocumentCancelled(f"Document {job_id} was cancelled")

    def retry_failed(self, job_id: str, *, expected_snapshot: dict | None = None,
                     validate: Callable[[], None] | None = None) -> DocumentJob:
        if expected_snapshot is not None:
            return self._strict_control("retry", job_id, expected_snapshot, validate)
        if self.latest_removal(job_id) is not None:
            raise DocumentJobError("This document has a removal intent; upload it again instead.")
        job = self.get_job(job_id)
        if job.status != "failed":
            raise InvalidJobTransition(f"{job.status} -> queued")
        if job.stage == "finalize":
            with self._write_lock:
                with contextlib.closing(self._connect()) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if conn.execute("SELECT 1 FROM document_removals WHERE target=? LIMIT 1", (job_id,)).fetchone():
                        raise DocumentCancelled("Document removal is pending")
                    current = conn.execute("SELECT status,stage FROM document_jobs WHERE id=?", (job_id,)).fetchone()
                    if current is None or current["status"] != "failed" or current["stage"] != "finalize":
                        raise InvalidJobTransition("Finalization retry is no longer current")
                    conn.execute("UPDATE document_jobs SET cancel_requested=0 WHERE id=?", (job_id,))
                    conn.execute("UPDATE document_batches SET status='queued',cancel_requested=0,pause_requested=0,updated_at=? WHERE id=?",
                                 (self._now(), job.batch_id))
                finalized = self.mark_completed(job_id)
                self.finalize_batch(job.batch_id)
                return finalized
        if not pathlib.Path(job.staged_path).is_file():
            raise DocumentJobError("The staged source is missing; upload the document again.")
        with contextlib.closing(self._connect()) as conn, conn:
            searchable_record = conn.execute(
                "SELECT 1 FROM document_records WHERE document_id=?",
                (job_id,),
            ).fetchone()
        retry_status = "searchable" if searchable_record else "queued"
        retry_stage = "knowledge_map" if searchable_record else "parse"
        if not searchable_record:
            work_dir = self.work_root / job.id
            self._remove_owned_tree(work_dir, self.work_root)
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                f"""
                UPDATE document_jobs
                SET status='{retry_status}', stage='{retry_stage}', cancel_requested=0,
                    index_progress_current=0, index_progress_total=0,
                    error_code='', error_message='', completed_at='', updated_at=?
                WHERE id=?
                """,
                (self._now(), job_id),
            )
            conn.execute(
                """
                UPDATE document_batches
                SET status='queued', cancel_requested=0, pause_requested=0, updated_at=?
                WHERE id=?
                """,
                (self._now(), job.batch_id),
            )
        _wake_supervisor()
        return self.get_job(job_id)

    def clear_finished(self, *, expected_snapshot: dict | None = None,
                       validate: Callable[[], None] | None = None) -> int:
        if expected_snapshot is not None:
            return self._strict_control("clear", None, expected_snapshot, validate)
        self._recover_finalizations()
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id FROM document_batches
                WHERE status IN ('completed','completed_with_errors','cancelled')
                  AND NOT EXISTS (
                      SELECT 1 FROM document_jobs
                      WHERE batch_id=document_batches.id
                        AND status IN ('staging','queued','indexing','searchable','extracting')
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM document_jobs WHERE batch_id=document_batches.id
                        AND stage='finalize' AND status='failed'
                  )
                """
            ).fetchall()
            batch_ids = [row["id"] for row in rows]
            # A removal receipt owns its own recovery. Do not erase a legacy
            # completed source which that operation has not yet retired.
            batch_ids = [batch_id for batch_id in batch_ids if not any(
                pathlib.Path(str(row["staged_path"])).absolute() !=
                (self.completed_root / str(row["id"]) / str(row["stored_name"])).absolute()
                for row in conn.execute("SELECT id,stored_name,staged_path FROM document_jobs WHERE batch_id=? AND status='completed'", (batch_id,))
            )]
            job_rows = []
            for batch_id in batch_ids:
                job_rows.extend(
                    conn.execute(
                        "SELECT id, status FROM document_jobs WHERE batch_id=?",
                        (batch_id,),
                    ).fetchall()
                )
                conn.execute("DELETE FROM document_batches WHERE id=?", (batch_id,))
            conn.commit()
        for row in job_rows:
            self._remove_owned_tree(self.work_root / row["id"], self.work_root)
            self._remove_owned_tree(self.staging_root / row["id"], self.staging_root)
        return len(batch_ids)

    def claim_next(self, owner: str, *, lease_seconds: int = LEASE_SECONDS) -> DocumentJob | None:
        now_epoch = self._monotonic()
        skipped_duplicate_paths: list[pathlib.Path] = []
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT owner, expires_at FROM document_worker_leases WHERE name='coordinator'"
            ).fetchone()
            if lease and lease["owner"] != owner and float(lease["expires_at"]) > now_epoch:
                conn.rollback()
                return None
            conn.execute(
                """
                INSERT INTO document_worker_leases(name, owner, heartbeat_at, expires_at)
                VALUES ('coordinator', ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner=excluded.owner,
                    heartbeat_at=excluded.heartbeat_at,
                    expires_at=excluded.expires_at
                """,
                (owner, now_epoch, now_epoch + lease_seconds),
            )
            active = conn.execute(
                """
                SELECT 1 FROM document_jobs
                WHERE status IN ('indexing','extracting')
                LIMIT 1
                """
            ).fetchone()
            if active is not None:
                conn.commit()
                return None
            batches = conn.execute(
                """
                SELECT * FROM document_batches
                WHERE status IN ('queued','running')
                  AND pause_requested=0 AND cancel_requested=0
                ORDER BY created_at, id
                """
            ).fetchall()
            for batch in batches:
                if str(batch["id"]).startswith("client_"):
                    try:
                        self._processing_admission_in_connection(conn,batch["id"])
                    except Exception:
                        conn.execute("UPDATE document_batches SET status='paused',pause_requested=1 WHERE id=?",(batch["id"],))
                        continue
                while True:
                    queued = conn.execute(
                        """
                        SELECT * FROM document_jobs
                        WHERE batch_id=? AND status='queued' AND cancel_requested=0
                        ORDER BY sequence, created_at
                        LIMIT 1
                        """,
                        (batch["id"],),
                    ).fetchone()
                    if queued is None:
                        break
                    duplicate = conn.execute(
                        """
                        SELECT 1 FROM document_records
                        WHERE content_sha256=? LIMIT 1
                        """,
                        (queued["content_sha256"],),
                    ).fetchone()
                    if duplicate is None:
                        break
                    now = self._now()
                    conn.execute(
                        """
                        UPDATE document_jobs
                        SET status='skipped_duplicate', stage='finalize',
                            completed_at=?, updated_at=?
                        WHERE id=?
                        """,
                        (now, now, queued["id"]),
                    )
                    if not str(batch["id"]).startswith("client_"):
                        skipped_duplicate_paths.append(pathlib.Path(str(queued["staged_path"])))
                candidate = queued
                new_status = "indexing"
                stage = "parse"
                if candidate is None:
                    candidate = conn.execute(
                        """
                        SELECT * FROM document_jobs
                        WHERE batch_id=? AND status='searchable' AND cancel_requested=0
                        ORDER BY sequence, created_at
                        LIMIT 1
                        """,
                        (batch["id"],),
                    ).fetchone()
                    new_status = "extracting"
                    stage = "knowledge_map"
                if candidate is None:
                    if not str(batch["id"]).startswith("client_"):
                        self._finalize_batch_in_connection(conn, batch["id"])
                    continue
                now = self._now()
                conn.execute(
                    """
                    UPDATE document_jobs
                    SET status=?, stage=?, attempt=attempt+1,
                        started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,
                        updated_at=?
                    WHERE id=?
                    """,
                    (new_status, stage, now, now, candidate["id"]),
                )
                conn.execute(
                    "UPDATE document_batches SET status='running', updated_at=? WHERE id=?",
                    (now, batch["id"]),
                )
                claimed = conn.execute(
                    "SELECT * FROM document_jobs WHERE id=?", (candidate["id"],)
                ).fetchone()
                if str(batch["id"]).startswith("client_"):
                    try:
                        self._processing_admission_in_connection(conn,batch["id"])
                    except Exception:
                        conn.rollback()
                        self.pause_batch(batch["id"])
                        return None
                conn.commit()
                for path in skipped_duplicate_paths:
                    with contextlib.suppress(FileNotFoundError):
                        path.unlink()
                return _row_job(claimed)
            conn.commit()
        for path in skipped_duplicate_paths:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        return None

    def heartbeat(self, owner: str, *, lease_seconds: int = LEASE_SECONDS) -> bool:
        now = self._monotonic()
        with contextlib.closing(self._connect()) as conn, conn:
            cur = conn.execute(
                """
                UPDATE document_worker_leases
                SET heartbeat_at=?, expires_at=?
                WHERE name='coordinator' AND owner=?
                """,
                (now, now + lease_seconds, owner),
            )
        return bool(cur.rowcount)

    def release_lease(self, owner: str) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM document_worker_leases WHERE name='coordinator' AND owner=?",
                (owner,),
            )

    def recover_unfinished(self) -> dict[str, int]:
        finalizations = self._recover_finalizations()
        recovered_indexing = 0
        recovered_extracting = 0
        missing = 0
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            acknowledged = conn.execute(
                """UPDATE document_jobs SET status='cancelled', error_code='cancelled',
                    completed_at=?, updated_at=? WHERE status IN ('queued','indexing','searchable','extracting')
                    AND (cancel_requested=1 OR EXISTS (
                        SELECT 1 FROM document_batches WHERE id=document_jobs.batch_id AND cancel_requested=1)
                        OR EXISTS (SELECT 1 FROM document_removals WHERE target=document_jobs.id))""",
                (self._now(), self._now()),
            ).rowcount
            conn.execute(
                "DELETE FROM document_worker_leases WHERE expires_at<=?",
                (self._monotonic(),),
            )
            rows = conn.execute(
                """
                SELECT * FROM document_jobs
                WHERE status IN ('queued','indexing','searchable','extracting')
                  AND stage != 'finalize'
                  AND NOT EXISTS (SELECT 1 FROM document_removals WHERE target=document_jobs.id)
                """
            ).fetchall()
            for row in rows:
                source_exists = pathlib.Path(row["staged_path"]).is_file()
                if not source_exists:
                    conn.execute(
                        """
                        UPDATE document_jobs
                        SET status='failed', error_code='missing_staged_source',
                            error_message=?, completed_at=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            "The staged source is missing. Remove this entry and upload the document again.",
                            self._now(),
                            self._now(),
                            row["id"],
                        ),
                    )
                    missing += 1
                    continue
                if row["status"] == "indexing":
                    conn.execute(
                        "UPDATE document_jobs SET status='queued', stage='parse', updated_at=? WHERE id=?",
                        (self._now(), row["id"]),
                    )
                    recovered_indexing += 1
                elif row["status"] == "extracting":
                    conn.execute(
                        """
                        UPDATE document_jobs
                        SET status='searchable', stage='knowledge_map', updated_at=?
                        WHERE id=?
                        """,
                        (self._now(), row["id"]),
                    )
                    recovered_extracting += 1
            conn.commit()
        for row in rows:
            if row["status"] == "indexing":
                self._remove_owned_tree(self.work_root / row["id"] / "index", self.work_root)
        removed_temps = 0
        for temp in self.staging_root.glob("*/*.uploading"):
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
                removed_temps += 1
        known_ids = {job.id for job in self.list_jobs()}
        recovered_orphans = 0
        orphan_root = self.root / "recovery_orphans"
        for owner, label in (
            (self.staging_root, "staging"),
            (self.work_root, "work"),
        ):
            for child in owner.iterdir():
                if (
                    not child.is_dir()
                    or child.name in known_ids
                    or child.name.startswith("rebuild-")
                ):
                    continue
                destination = orphan_root / (
                    f"{label}-{child.name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(child, destination)
                recovered_orphans += 1
        return {
            **finalizations,
            "cancellations_acknowledged": acknowledged,
            "indexing_restarted": recovered_indexing,
            "extraction_resumed": recovered_extracting,
            "missing_sources_failed": missing,
            "upload_temps_removed": removed_temps,
            "orphan_directories_retired": recovered_orphans,
        }

    def active_summary(self) -> dict[str, object]:
        with contextlib.closing(self._connect()) as conn, conn:
            active = conn.execute(
                """
                SELECT j.*, b.pause_requested AS batch_paused
                FROM document_jobs j
                JOIN document_batches b ON b.id=j.batch_id
                WHERE j.status IN ('indexing','extracting')
                ORDER BY j.started_at, j.sequence
                LIMIT 1
                """
            ).fetchone()
            remaining = conn.execute(
                """
                SELECT COUNT(*) FROM document_jobs
                WHERE status IN ('staging','queued','indexing','searchable','extracting')
                """
            ).fetchone()[0]
            paused = conn.execute(
                "SELECT COUNT(*) FROM document_batches WHERE pause_requested=1"
            ).fetchone()[0]
        return {
            "active": dict(active) if active else None,
            "remaining": int(remaining),
            "paused": bool(paused),
        }

    def store_map_summary(self, job_id: str, window_index: int, summary: str) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO document_map_summaries
                    (job_id, window_index, summary, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_id, int(window_index), summary, self._now()),
            )

    def iter_map_summaries(self, job_id: str) -> Iterator[tuple[int, str]]:
        with contextlib.closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                SELECT window_index, summary FROM document_map_summaries
                WHERE job_id=? ORDER BY window_index
                """,
                (job_id,),
            )
            while True:
                rows = cursor.fetchmany(32)
                if not rows:
                    break
                for row in rows:
                    yield int(row["window_index"]), str(row["summary"])

    def last_map_window(self, job_id: str) -> int:
        with contextlib.closing(self._connect()) as conn, conn:
            value = conn.execute(
                "SELECT MAX(window_index) FROM document_map_summaries WHERE job_id=?",
                (job_id,),
            ).fetchone()[0]
        return int(value) if value is not None else -1

    def store_reduce_summary(
        self,
        job_id: str,
        level: int,
        group_index: int,
        summary: str,
    ) -> None:
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO document_reduce_summaries
                    (job_id, level, group_index, summary, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, int(level), int(group_index), summary, self._now()),
            )

    def iter_reduce_summaries(self, job_id: str, level: int) -> Iterator[tuple[int, str]]:
        with contextlib.closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                SELECT group_index, summary FROM document_reduce_summaries
                WHERE job_id=? AND level=? ORDER BY group_index
                """,
                (job_id, int(level)),
            )
            while True:
                rows = cursor.fetchmany(32)
                if not rows:
                    break
                for row in rows:
                    yield int(row["group_index"]), str(row["summary"])

    def count_reduce_summaries(self, job_id: str, level: int) -> int:
        with contextlib.closing(self._connect()) as conn, conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM document_reduce_summaries
                    WHERE job_id=? AND level=?
                    """,
                    (job_id, int(level)),
                ).fetchone()[0]
            )

    def finalizable_batches(self) -> list[str]:
        with contextlib.closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT b.id
                FROM document_batches b
                WHERE b.status IN ('queued','running')
                  AND NOT EXISTS (
                      SELECT 1 FROM document_jobs j
                      WHERE j.batch_id=b.id
                        AND j.status IN ('staging','queued','indexing','searchable','extracting')
                  )
                ORDER BY b.created_at, b.id
                """
            ).fetchall()
        result = []
        for row in rows:
            identifier = str(row["id"])
            if identifier.startswith("client_"):
                try:
                    self.processing_admission(identifier)
                except Exception:
                    continue
            result.append(identifier)
        return result

    def finalize_batch(self, batch_id: str, *, validate: Callable[[], None] | None = None) -> DocumentBatch:
        with self._write_lock, contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if validate is not None:
                validate()
            if batch_id.startswith("client_"):
                self._processing_admission_in_connection(conn,batch_id)
            self._finalize_batch_in_connection(conn, batch_id)
            if validate is not None:
                validate()
            conn.commit()
        return self.get_batch(batch_id)

    def _finalize_batch_in_connection(self, conn: sqlite3.Connection, batch_id: str) -> None:
        statuses = {
            row["status"]
            for row in conn.execute(
                "SELECT status FROM document_jobs WHERE batch_id=?", (batch_id,)
            ).fetchall()
        }
        if statuses - TERMINAL_JOB_STATUSES:
            return
        status = self._terminal_batch_status(statuses)
        conn.execute(
            "UPDATE document_batches SET status=?, updated_at=? WHERE id=?",
            (status, self._now(), batch_id),
        )

    @staticmethod
    def _terminal_batch_status(statuses: set[str]) -> str:
        if statuses and statuses <= {"cancelled"}:
            return "cancelled"
        if "failed" in statuses or "cancelled" in statuses:
            return "completed_with_errors"
        return "completed"

    @staticmethod
    def _remove_owned_tree(path: pathlib.Path, owner: pathlib.Path) -> None:
        path = path.resolve()
        owner = owner.resolve()
        if path == owner or owner not in path.parents:
            raise DocumentJobError(f"Refusing cleanup outside {owner}")
        if path.exists():
            shutil.rmtree(path)


class _SupervisorProcessLock:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if self.handle.read(1) == b"":
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle is None:
            return
        with contextlib.suppress(OSError):
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


class DocumentSupervisor:
    """Single-flight background coordinator for sequential heavy operations."""

    def __init__(self, service: DocumentJobService) -> None:
        self.service = service
        self.owner = f"{os.getpid()}-{uuid.uuid4().hex}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_lock = _SupervisorProcessLock(service.root / "supervisor.lock")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.running:
            return False
        if not self._process_lock.acquire():
            logger.info("Document supervisor already owns the process lock")
            return False
        recovery = self.service.recover_unfinished()
        if any(recovery.values()):
            logger.info("Recovered unfinished document ingestion: %s", recovery)
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="row-bot-document-ingestion",
        )
        self._thread.start()
        return True

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                job = self.service.claim_next(self.owner)
                if job is None:
                    self._finalize_ready_batches()
                    self._wake.wait(30)
                    self._wake.clear()
                    continue
                try:
                    self._process_job(job)
                except DocumentCancelled:
                    current = self.service.get_job(job.id)
                    if current.status in ACTIVE_JOB_STATUSES:
                        self.service.transition_job(
                            job.id,
                            "cancelled",
                            stage=current.stage,
                            error_code="cancelled",
                            error_message="Cancelled by the user.",
                        )
                except Exception as exc:
                    logger.exception("Document ingestion failed for %s", job.original_name)
                    current = self.service.get_job(job.id)
                    if current.status in ACTIVE_JOB_STATUSES:
                        self.service.mark_failed(
                            job.id,
                            f"{current.stage}_failed",
                            str(exc),
                            stage=current.stage,
                        )
                    if job.batch_id.startswith("client_"):
                        self.service.pause_batch(job.batch_id)
                finally:
                    self.service.heartbeat(self.owner)
                    self._finalize_ready_batches()
        finally:
            self.service.release_lease(self.owner)
            self._process_lock.release()

    def _process_job(self, job: DocumentJob) -> None:
        from row_bot.documents import index_document_job
        from row_bot.document_extraction import extract_document_job, captured_extraction_policy
        with self.service.processing_scope(job.batch_id) as policy:
            if policy is None:
                if job.status == "indexing":
                    index_document_job(job,self.service)
                    self.service.raise_if_cancelled(job.id)
                    self.service.mark_searchable(job.id)
                elif job.status == "extracting":
                    extract_document_job(job,self.service)
                    self.service.raise_if_cancelled(job.id)
                    self.service.mark_completed(job.id)
                return
            with self.service.source_snapshot(job,validate=policy.validate) as (path,extension,source_check):
                policy.add_source_guard(source_check)
                policy.validate()
                if job.status == "indexing":
                    index_document_job(job,self.service,embedding_config=policy.embedding_config,
                        embedding=policy.embedding,validate=policy.validate,source_path=path,original_extension=extension)
                    self.service.raise_if_cancelled(job.id)
                    self.service.mark_searchable(job.id,validate=policy.validate)
                elif job.status == "extracting":
                    with captured_extraction_policy(policy):
                        extract_document_job(job,self.service,source_path=path,original_extension=extension)
                    self.service.raise_if_cancelled(job.id)
                    self.service.mark_completed(job.id,validate=policy.validate)

    def _finalize_ready_batches(self) -> None:
        for batch_id in self.service.finalizable_batches():
            def cancelled(batch_id=batch_id):
                if self._stop.is_set():
                    return True
                current = self.service.get_batch(batch_id)
                return current.pause_requested or current.cancel_requested

            try:
                with self.service.processing_scope(batch_id) as policy:
                    strict = {"validate":policy.validate} if policy is not None else {}
                    if _finalize_shared_knowledge_indexes(cancelled=cancelled, **strict) is False:
                        continue  # More bounded work remains for the next existing tick.
                    if cancelled():
                        continue
                    batch = self.service.finalize_batch(batch_id, **strict)
            except Exception:
                logger.warning(
                    "Document batch final consistency refresh failed for %s",
                    batch_id,
                    exc_info=True,
                )
                if not cancelled():
                    # Resume is explicit approval to retry failed projection
                    # work; completed source/extraction jobs are not repeated.
                    self.service.pause_batch(batch_id)
                continue
            _notify_batch_complete(self.service, batch)


def _finalize_shared_knowledge_indexes(*, cancelled: Callable[[], bool] | None = None, validate: Callable[[], None] | None = None) -> bool | None:
    import row_bot.knowledge_graph as kg

    outcome = kg.repair_projections(max_entities=1000, cancelled=cancelled, **({"validate":validate} if validate is not None else {}))
    if cancelled is not None and cancelled():
        raise DocumentCancelled("Document projection finalization cancelled")
    if outcome["failures"]:
        raise DocumentJobError("Saved document knowledge needs projection repair; resume the batch to retry")
    if outcome["pending"]["semantic"] or (outcome["wiki_enabled"] and outcome["pending"]["wiki"]):
        return False
    # Obsolete generation cleanup has its own truthful pending status. It does
    # not invalidate the complete current document knowledge projections.
    return None


def _notify_batch_complete(service: DocumentJobService, batch: DocumentBatch) -> None:
    try:
        from row_bot.notifications import notify

        jobs = service.list_jobs(batch.id)
        completed = sum(job.status == "completed" for job in jobs)
        failed = sum(job.status in {"failed", "cancelled"} for job in jobs)
        duplicates = sum(job.status == "skipped_duplicate" for job in jobs)
        notify(
            "Document Ingestion",
            f"{completed} complete, {failed} failed or cancelled, {duplicates} duplicate skipped",
            icon="📄",
        )
    except Exception:
        logger.debug("Document batch completion notification skipped", exc_info=True)


_supervisor_lock = threading.Lock()
_supervisor: DocumentSupervisor | None = None


def ensure_document_supervisor(
    service: DocumentJobService | None = None,
) -> DocumentSupervisor:
    """Return the one process-local supervisor, starting it when necessary."""
    global _supervisor
    with _supervisor_lock:
        if _supervisor is None:
            _supervisor = DocumentSupervisor(service or DocumentJobService())
            _supervisor.start()
        elif not _supervisor.running:
            _supervisor.start()
        return _supervisor


def _wake_supervisor() -> None:
    supervisor = _supervisor
    if supervisor is not None:
        supervisor.wake()


def get_document_job_service() -> DocumentJobService:
    """Create a lightweight service facade over the active data directory."""
    return DocumentJobService()
