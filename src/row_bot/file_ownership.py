"""Stdlib directory identity and guarded handle ownership for contained files.

A held Windows ancestor prevents path replacement; POSIX users must perform
subsequent operations relative to the yielded directory descriptor.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
import hashlib
import json
from pathlib import Path
import stat


class DirectoryOwnershipError(ValueError):
    """The selected directory could not remain exclusively identified."""


def directory_identity(path: Path, *, parent: bool = False) -> str:
    info = path.lstat()
    # Parent timestamps change when we create its child. Child birth time is
    # stable on Windows/macOS; ctime is deliberately conservative elsewhere.
    stamp = 0 if parent else getattr(info, "st_birthtime_ns", info.st_ctime_ns)
    return f"{info.st_dev}:{info.st_ino}:{stamp}"


@contextmanager
def guard_directory(parent: Path, identity: str) -> Iterator[int | None]:
    """Keep pathname replacement from redirecting the directory creation."""
    handles: list[int] = []
    close: Callable[[int], object] = os.close
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class FileInformation(ctypes.Structure):
                _fields_ = [("attributes", wintypes.DWORD),
                            ("created", wintypes.FILETIME), ("accessed", wintypes.FILETIME),
                            ("written", wintypes.FILETIME), ("volume", wintypes.DWORD),
                            ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                            ("links", wintypes.DWORD), ("index_high", wintypes.DWORD),
                            ("index_low", wintypes.DWORD)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create.restype = wintypes.HANDLE
            inspect = kernel.GetFileInformationByHandle
            inspect.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
            inspect.restype = wintypes.BOOL
            close = kernel.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            close.restype = wintypes.BOOL
            # No FILE_SHARE_DELETE: an ancestor cannot be renamed/replaced
            # while the pathname-based Windows mkdir/save is in progress.
            for directory in [*reversed(parent.parents), parent]:
                handle = create(str(directory), 0x81, 0x3, None, 3, 0x02200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    raise OSError
                handles.append(handle)
                info = FileInformation()
                if not inspect(handle, ctypes.byref(info)) or info.attributes & 0x400 or not info.attributes & 0x10:
                    raise OSError
            descriptor = None
        else:
            # Walk from the anchor with relative no-follow opens; later mkdir
            # stays bound to the selected directory even if its name changes.
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(parent.anchor, flags)
            handles.append(descriptor)
            for component in parent.parts[1:]:
                descriptor = os.open(component, flags, dir_fd=descriptor)
                handles.append(descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or f"{info.st_dev}:{info.st_ino}:0" != identity:
                raise OSError
        if directory_identity(parent, parent=True) != identity:
            raise OSError
    except (OSError, ValueError):
        for handle in reversed(handles):
            close(handle)
        raise DirectoryOwnershipError("directory_unavailable") from None
    try:
        yield descriptor
    finally:
        for handle in reversed(handles):
            close(handle)


def confirmed_edit_publication(path: Path, saved_digest: str, saved_identity: str,
                               publication: dict, *, owner_id: str, key: str,
                               command_id: str, max_bytes: int) -> bool:
    """Confirm retained JSON publication from current guarded bytes, not a snapshot.

    Private receipts supply the candidate inode, bytes and metadata proof. This
    never publishes, repairs or adopts another same-content file. Imports of
    effectful file owners remain limited to explicit receipt reconciliation.
    """
    from row_bot.developer.edits import FileEditRecovery, file_edit_metadata_digest
    try:
        if type(max_bytes) is not int or not 0 < max_bytes <= 8 * 1024 * 1024:
            return False
        proof = FileEditRecovery(**publication)
        path = path.absolute()
        if (proof.command_id != command_id or proof.relative_path != path.name
                or saved_digest != proof.after_digest or saved_identity != proof.candidate_identity):
            return False
        info = path.parent.stat()
        if proof.root_identity != f"{info.st_dev}:{info.st_ino}":
            return False
        with guard_directory(path.parent, directory_identity(path.parent, parent=True)) as directory:
            leaf = path.name if directory is not None else path
            descriptor = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
            try:
                current = os.fstat(descriptor)
                if (not stat.S_ISREG(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400
                        or f"{current.st_dev}:{current.st_ino}" != proof.candidate_identity or current.st_size > max_bytes):
                    return False
                data = bytearray()
                while chunk := os.read(descriptor, min(65536, max_bytes + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        return False
                metadata = file_edit_metadata_digest(descriptor if directory is not None else path)
                final = os.fstat(descriptor)
                named = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
                def identity(info):
                    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                            info.st_ctime_ns if os.name != "nt" else None)
                if (hashlib.sha256(data).hexdigest() != proof.after_digest or metadata != proof.metadata_digest
                        or identity(current) != identity(final) or identity(final) != identity(named)):
                    return False
                document = json.loads(data)
                return (type(document) is dict and document.get("_client_publication") ==
                        {"owner_id": owner_id, "key": key, "command_id": command_id})
            finally:
                os.close(descriptor)
    except (OSError, TypeError, ValueError, RecursionError):
        return False
