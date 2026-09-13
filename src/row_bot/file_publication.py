"""Bounded single-link publication with private retained recovery proof.

Callers retain their canonical domain lock, policy and command ownership.
This module neither initializes a data store nor decides authority.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import stat
from typing import Any
import uuid

from row_bot.file_ownership import guard_directory, directory_identity

MAX_BYTES = 8 * 1024 * 1024


def _limit(max_bytes: int) -> None:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("invalid_publication_limit")


def _leaf(filename: str) -> None:
    if (not isinstance(filename, str) or not filename or Path(filename).name != filename
            or filename in {'.', '..'} or '/' in filename or '\\' in filename or ':' in filename
            or any(ord(char) < 32 for char in filename)):
        raise ValueError('invalid_publication_target')


def read_bytes(directory: int | None, path: Path, name: str, *, max_bytes: int = 65536,
               unavailable_code: str = "file_unavailable") -> tuple[bytes | None, str, str, int, str]:
    """One bounded, single-link opened snapshot under a held parent guard."""
    _limit(max_bytes)
    _leaf(name)
    from row_bot.developer.edits import FileEditError, file_edit_metadata_digest
    target = path / name
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False) if directory is not None else target.lstat()
    except FileNotFoundError:
        return None, "missing", "", 0o600, ""
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_bytes
            or getattr(before, "st_file_attributes", 0) & 0x400):
        raise FileEditError(unavailable_code)
    descriptor = os.open(name if directory is not None else target,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not os.path.samestat(before, opened) or opened.st_nlink != 1:
            raise FileEditError("file_revision_conflict")
        value = handle.read(max_bytes + 1)
        metadata = file_edit_metadata_digest(handle.fileno() if directory is not None else target)
        finished = os.fstat(handle.fileno())
    named = os.stat(name, dir_fd=directory, follow_symlinks=False) if directory is not None else target.lstat()
    if (not os.path.samestat(opened, named) or named.st_nlink != 1 or len(value) > max_bytes
            or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) !=
               (finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns)
            or (named.st_size, named.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
        raise FileEditError("file_revision_conflict")
    return value, hashlib.sha256(value).hexdigest(), f"{opened.st_dev}:{opened.st_ino}", stat.S_IMODE(opened.st_mode), metadata


def publish_bytes(root_path: Path, filename: str, data: bytes | None, *, expected_revision: str,
                           command_id: str, validate: Callable[[], None], checkpoint: Callable[[Any], None], max_bytes: int = 65536,
                  unavailable_code: str = "file_unavailable") -> str:
    """Publish bytes after exact revision review; retain originals and candidates."""
    _limit(max_bytes)
    _leaf(filename)
    from row_bot.developer.edits import FileEditError, FileEditRecovery, _rename_edit_no_replace
    if str(uuid.UUID(command_id)) != command_id:
        raise FileEditError("invalid_edit")
    if data is not None and (type(data) is not bytes or len(data) > max_bytes):
        raise FileEditError(unavailable_code)
    def read(directory, path, name):
        return read_bytes(directory, path, name, max_bytes=max_bytes, unavailable_code=unavailable_code)
    with ExitStack() as stack:
        validate()
        root = stack.enter_context(guard_directory(root_path, directory_identity(root_path, parent=True)))
        root_stat = root_path.lstat()
        original = read(root, root_path, filename)
        if original[1] != expected_revision:
            raise FileEditError("file_revision_conflict")
        if original[0] == data:
            return original[1]
        def child(parent_fd, parent_path, name, *, exclusive=False):
            path = parent_path / name
            if parent_fd is None:
                path.mkdir(exist_ok=not exclusive)
                stack.enter_context(guard_directory(path, directory_identity(path, parent=True)))
                return None, path
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                if exclusive:
                    raise
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            stack.callback(os.close, fd)
            return fd, path
        retained_root, retained_path = child(root, root_path, ".row-bot-edit-recovery")
        attempt, attempt_path = child(retained_root, retained_path, command_id, exclusive=True)
        if data is not None:
            fd = os.open("candidate" if attempt is not None else attempt_path / "candidate",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), original[3], dir_fd=attempt)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                if attempt is not None:
                    os.fchmod(handle.fileno(), original[3])
                os.fsync(handle.fileno())
            if attempt is None:
                os.chmod(attempt_path / "candidate", original[3])
        candidate = read(attempt, attempt_path, "candidate")
        if original[0] is not None and data is not None and original[4] != candidate[4]:
            raise FileEditError("file_metadata_unavailable")
        proof = FileEditRecovery(command_id, filename, f"{root_stat.st_dev}:{root_stat.st_ino}",
            original[1], candidate[1], original[2], candidate[2], candidate[4] or original[4])
        checkpoint(proof)
        def rename(source_fd, source_path, source, destination_fd, destination_path, destination):
            _rename_edit_no_replace(source if source_fd is not None else source_path / source,
                destination if destination_fd is not None else destination_path / destination,
                src_dir_fd=source_fd, dst_dir_fd=destination_fd)
        try:
            validate()
            if not os.path.samestat(root_stat, root_path.lstat()) or read(root, root_path, filename) != original:
                raise FileEditError("file_revision_conflict", proof)
            if original[0] is not None:
                rename(root, root_path, filename, attempt, attempt_path, "previous")
                if read(attempt, attempt_path, "previous") != original:
                    raise FileEditError("file_revision_conflict", proof)
            validate()
            if read(attempt, attempt_path, "candidate") != candidate:
                raise FileEditError("file_revision_conflict", proof)
            if data is not None:
                rename(attempt, attempt_path, "candidate", root, root_path, filename)
            if (read(root, root_path, filename) != candidate
                    or not os.path.samestat(root_stat, root_path.lstat())):
                raise FileEditError("file_revision_conflict", proof)
        except Exception:
            if (read(root, root_path, filename)[0] is None
                    and read(attempt, attempt_path, "previous")[0] is not None):
                try:
                    rename(attempt, attempt_path, "previous", root, root_path, filename)
                except OSError:
                    pass
            raise
        return candidate[1]


def read_recovery(root_path: Path, filename: str, proof: Any, *, max_bytes: int = 65536,
                  unavailable_code: str = "file_unavailable") -> str:
    """Inspect exact publication proof without writing or adopting foreign bytes."""
    _limit(max_bytes)
    _leaf(filename)
    from row_bot.developer.edits import FileEditRecovery
    def read(directory, path, name):
        return read_bytes(directory, path, name, max_bytes=max_bytes, unavailable_code=unavailable_code)
    try:
        if (not isinstance(proof, FileEditRecovery) or proof.relative_path != filename
                or str(uuid.UUID(proof.command_id)) != proof.command_id):
            return "conflict"
        with ExitStack() as stack:
            root = stack.enter_context(guard_directory(root_path, directory_identity(root_path, parent=True)))
            info = root_path.lstat()
            if proof.root_identity != f"{info.st_dev}:{info.st_ino}":
                return "conflict"
            current = read(root, root_path, filename)
            if proof.after_digest == 'missing' and current[0] is None:
                path = root_path / '.row-bot-edit-recovery' / proof.command_id
                attempt = stack.enter_context(guard_directory(path, directory_identity(path, parent=True)))
                previous = read(attempt, path, 'previous')
                if previous[1:3] == (proof.before_digest, proof.original_identity) and previous[4] == proof.metadata_digest:
                    return 'applied'
                return 'conflict'
            if current[1:3] == (proof.after_digest, proof.candidate_identity) and current[4] == proof.metadata_digest:
                return "applied"
            if current[1:3] != (proof.before_digest, proof.original_identity):
                return "conflict"
            path = root_path / ".row-bot-edit-recovery" / proof.command_id
            attempt = stack.enter_context(guard_directory(path, directory_identity(path, parent=True)))
            candidate = read(attempt, path, "candidate")
            if candidate[1:3] == (proof.after_digest, proof.candidate_identity) and candidate[4] == proof.metadata_digest:
                return "not_applied"
            return "conflict"
    except (OSError, ValueError):
        return "conflict"
