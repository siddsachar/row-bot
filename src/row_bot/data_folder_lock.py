"""The data folder's lock: every Row-Bot server holds it shared while it runs; compacting chats takes it alone (W6).

``launcher.lock`` can't be this lock. It is one launcher's, held exclusively, while the server is another process
(``app.py``) that the launcher starts, that may outlive it, and that also runs without one: ``python app.py``, a
sandbox's ``python -m row_bot.app``. So the server itself holds this one, shared (a launcher's server and the next
one it starts on a restart may overlap), and the system drops it when the process ends. Compacting takes it
exclusively, so it can't start while any server runs, and no server starts while it runs; so do
``--reset-db``, ``--reset-tasks-db`` and ``--restore-data``, which replace databases (``alone``).

Plain standard library only: ``chat_compact`` imports it and must not import anything that opens a database.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

LOCK_FILENAME = "data-folder.lock"
IN_USE_MESSAGE = "Quit Row-Bot (or stop the server) before compacting chats."
RESET_IN_USE_MESSAGE = "Quit Row-Bot (or stop the server) before resetting or restoring data."
_held: list["DataFolderLock"] = []  # The server's, for the life of the process.


def lock_path(data_dir: Path) -> Path:
    return Path(data_dir) / LOCK_FILENAME


class DataFolderLock:
    """An OS file lock on the data folder, shared or exclusive, never waited for."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.handle = None

    def acquire(self, *, exclusive: bool) -> bool:
        """True when this process now holds it; False when another holds it in a way that excludes this one.

        OSError when the lock file can't be opened at all."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            _lock(handle.fileno(), exclusive=exclusive)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            _unlock(handle.fileno())
        except OSError:
            pass
        handle.close()


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel32.LockFileEx.restype = wintypes.BOOL
    _kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(_Overlapped)]
    _kernel32.UnlockFileEx.restype = wintypes.BOOL
    _FAIL_IMMEDIATELY, _EXCLUSIVE = 0x1, 0x2

    def _lock(fileno: int, *, exclusive: bool) -> None:
        # msvcrt.locking has no shared mode; LockFileEx does. One byte at the start, held by this handle.
        flags = _FAIL_IMMEDIATELY | (_EXCLUSIVE if exclusive else 0)
        if not _kernel32.LockFileEx(msvcrt.get_osfhandle(fileno), flags, 0, 1, 0, ctypes.byref(_Overlapped())):
            raise ctypes.WinError(ctypes.get_last_error())

    def _unlock(fileno: int) -> None:
        if not _kernel32.UnlockFileEx(msvcrt.get_osfhandle(fileno), 0, 1, 0, ctypes.byref(_Overlapped())):
            raise ctypes.WinError(ctypes.get_last_error())
else:
    import fcntl

    def _lock(fileno: int, *, exclusive: bool) -> None:
        fcntl.flock(fileno, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)

    def _unlock(fileno: int) -> None:
        fcntl.flock(fileno, fcntl.LOCK_UN)


def hold_for_server(data_dir: Path | None = None) -> None:
    """A server's start, before it opens anything in the data folder: hold the lock shared until the process ends.

    Raises SystemExit, with nothing opened, while the folder's data is being compacted, reset or restored.
    """
    if data_dir is None:
        from row_bot.data_paths import get_row_bot_data_dir

        data_dir = get_row_bot_data_dir()
    lock = DataFolderLock(lock_path(data_dir))
    try:
        held = lock.acquire(exclusive=False)
    except OSError as exc:
        raise SystemExit(f"Row-Bot didn't start: it can't open {lock.path} ({exc.strerror or exc}).") from None
    if not held:
        raise SystemExit("Row-Bot didn't start: its data is being compacted, reset or restored. Start it again "
                         "when that's done.")
    _held.append(lock)


class FolderInUse(RuntimeError):
    """Row-Bot, or something else, has the data folder open: nothing was changed."""


def still_open(database: Path) -> bool:
    """True when another connection has *database* open: its ``-wal`` stays after this process's own closes.

    An idle connection in WAL mode holds no lock ``BEGIN EXCLUSIVE`` would see, but SQLite deletes the WAL only
    when the last connection closes, so this sees a server that holds no data-folder lock (an older Row-Bot,
    another program). The probe's close may fold a WAL left by a crash into the file, as any start would. A file
    that isn't a SQLite database isn't opened (the reset is for broken files too), and only the lock guards it.
    """
    database = Path(database)
    try:
        with database.open("rb") as file:
            if file.read(16) != b"SQLite format 3\x00":
                return False
    except OSError:
        return False
    try:
        with closing(sqlite3.connect(database, timeout=0)) as probe:
            probe.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        return False
    return database.with_name(database.name + "-wal").exists()


@contextmanager
def alone(data_dir: Path, databases: Iterable[Path] = (), *, message: str) -> Iterator[None]:
    """Hold the data folder alone while the body changes its databases; FolderInUse(*message*), with nothing
    changed, while a server holds the folder or another connection still has one of *databases* open."""
    lock = DataFolderLock(lock_path(data_dir))
    try:
        held = lock.acquire(exclusive=True)
    except OSError as exc:
        raise FolderInUse(f"{message} ({lock.path.name} can't be opened: {exc.strerror or exc})") from None
    if not held:
        raise FolderInUse(message)
    try:
        if any(still_open(database) for database in databases):
            raise FolderInUse(message)
        yield
    finally:
        lock.release()
