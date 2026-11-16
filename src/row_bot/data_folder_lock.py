"""The data folder's lock: every Row-Bot server holds it shared while it runs; compacting chats takes it alone (W6).

``launcher.lock`` can't be this lock. It is one launcher's, held exclusively, while the server is another process
(``app.py``) that the launcher starts, that may outlive it, and that also runs without one: ``python app.py``, a
sandbox's ``python -m row_bot.app``. So the server itself holds this one, shared (a launcher's server and the next
one it starts on a restart may overlap), and the system drops it when the process ends. Compacting takes it
exclusively, so it can't start while any server runs, and no server starts while it runs.

Plain standard library only: ``chat_compact`` imports it and must not import anything that opens a database.
"""
from __future__ import annotations

import os
from pathlib import Path

LOCK_FILENAME = "data-folder.lock"
IN_USE_MESSAGE = "Quit Row-Bot (or stop the server) before compacting chats."
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

    Raises SystemExit, with nothing opened, while chats are being compacted in the folder.
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
        raise SystemExit("Row-Bot didn't start: its chats are being compacted. Start it again when that's done.")
    _held.append(lock)
