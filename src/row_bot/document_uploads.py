"""Streaming, disk-first upload staging for durable document jobs."""

from __future__ import annotations

import hashlib
import asyncio
import inspect
import os
import pathlib
import shutil
import stat
from collections.abc import Callable
from typing import Any

from row_bot.document_jobs import (
    MAX_UPLOAD_BYTES,
    MIN_STAGING_FREE_BYTES,
    UPLOAD_CHUNK_BYTES,
    DocumentJob,
    DocumentJobError,
    DocumentJobService,
)


class UploadRejected(DocumentJobError):
    """A per-file staging rejection safe to display to the user."""


def _default_disk_free(path: pathlib.Path) -> int:
    return int(shutil.disk_usage(str(path)).free)


async def _read_bounded(stream: Any, size: int) -> bytes:
    read = getattr(stream, "read", None)
    if not callable(read):
        raise UploadRejected("This upload source does not support streaming reads.")
    try:
        value = read(size)
    except TypeError as exc:
        raise UploadRejected(
            "This upload source cannot be streamed safely; the file was not accepted."
        ) from exc
    if inspect.isawaitable(value):
        value = await value
    if value is None:
        return b""
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise UploadRejected("The upload source returned invalid binary data.")
    data = bytes(value)
    if len(data) > size:
        raise UploadRejected("The upload source exceeded the bounded read size.")
    return data


async def _iter_bounded_chunks(stream: Any, size: int):
    iterate = getattr(stream, "iterate", None)
    if callable(iterate):
        try:
            iterator = iterate(chunk_size=size)
        except TypeError as exc:
            raise UploadRejected(
                "This upload source cannot provide bounded streaming chunks."
            ) from exc
        async for value in iterator:
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise UploadRejected("The upload source returned invalid binary data.")
            data = bytes(value)
            if len(data) > size:
                raise UploadRejected("The upload source exceeded the bounded read size.")
            if data:
                yield data
        return
    while True:
        data = await _read_bounded(stream, size)
        if not data:
            return
        yield data


async def stage_upload(
    service: DocumentJobService,
    batch_id: str,
    sequence: int,
    original_name: str,
    stream: Any,
    *,
    declared_size: int | None = None,
    disk_free: Callable[[pathlib.Path], int] = _default_disk_free,
    max_bytes: int = MAX_UPLOAD_BYTES,
    reserve_bytes: int = MIN_STAGING_FREE_BYTES,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
    job_id: str | None = None,
    validate: Callable[[], None] | None = None,
    checkpoint: Callable[[str, dict], None] | None = None,
) -> DocumentJob:
    """Stream one upload into its collision-safe job directory.

    Backend size and disk checks remain authoritative even when the client
    supplies a size hint.
    """
    if job_id is not None:
        async with asyncio.timeout(300):
            return await _stage_reviewed_upload(service,batch_id,sequence,original_name,stream,
                job_id=job_id,declared_size=declared_size,disk_free=disk_free,max_bytes=max_bytes,
                reserve_bytes=reserve_bytes,chunk_bytes=chunk_bytes,validate=validate,checkpoint=checkpoint)
    chunk_bytes = min(max(1, int(chunk_bytes)), UPLOAD_CHUNK_BYTES)
    max_bytes = int(max_bytes)
    reserve_bytes = int(reserve_bytes)
    if declared_size is not None and int(declared_size) > max_bytes:
        raise UploadRejected("The file exceeds the 256 MiB upload limit.")

    job = service.create_staging_job(batch_id, sequence, original_name)
    final_path = pathlib.Path(job.staged_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(f".{final_path.name}.uploading")
    digest = hashlib.sha256()
    size = 0

    try:
        available = int(disk_free(final_path.parent))
        expected = max(0, int(declared_size or 0))
        if available - expected < reserve_bytes:
            raise UploadRejected(
                "Not enough free disk space to stage this file while keeping the 2 GiB safety reserve."
            )

        with temp_path.open("xb") as output:
            async for data in _iter_bounded_chunks(stream, chunk_bytes):
                next_size = size + len(data)
                if next_size > max_bytes:
                    raise UploadRejected("The file exceeds the 256 MiB upload limit.")
                if int(disk_free(final_path.parent)) - len(data) < reserve_bytes:
                    raise UploadRejected(
                        "Staging stopped because the 2 GiB free-space safety reserve would be crossed."
                    )
                output.write(data)
                digest.update(data)
                size = next_size
            if size == 0:
                raise UploadRejected("The uploaded file is empty.")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, final_path)
        return service.complete_staging(job.id, digest.hexdigest(), size, final_path)
    except Exception as exc:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        if service.get_job(job.id).status == "staging":
            code = "upload_rejected" if isinstance(exc, UploadRejected) else "upload_failed"
            service.fail_staging(job.id, code, str(exc))
        raise


async def _stage_reviewed_upload(service, batch_id, sequence, original_name, stream, *, job_id,
        declared_size, disk_free, max_bytes, reserve_bytes, chunk_bytes, validate, checkpoint):
    """Retain interrupted bytes, with every leaf effect under an owned handle."""
    from row_bot.file_ownership import directory_identity, guard_directory
    from row_bot.developer.edits import _rename_edit_no_replace

    if (not callable(validate) or not callable(checkpoint) or type(declared_size) is not int
            or not 0 < declared_size <= min(int(max_bytes), MAX_UPLOAD_BYTES)
            or not batch_id.startswith("client_") or not job_id.startswith("upload_")):
        raise UploadRejected("Invalid reviewed upload")
    validate()
    chunk_bytes = min(max(1,int(chunk_bytes)),UPLOAD_CHUNK_BYTES)
    reserve_bytes = max(0,int(reserve_bytes))
    max_bytes = min(int(max_bytes),MAX_UPLOAD_BYTES)
    job = service.create_staging_job(batch_id,sequence,original_name,job_id=job_id,validate=validate)
    validate_authority = validate
    def validate():
        validate_authority()
        service.raise_if_cancelled(job.id)
    checkpoint("job_created",{"job_id":job.id,"batch_id":batch_id})
    staging = service.staging_root.absolute()
    with guard_directory(staging,directory_identity(staging,parent=True)) as parent:
        validate()
        os.mkdir(job.id if parent is not None else staging / job.id,dir_fd=parent)
        directory = staging / job.id
        created_identity = directory_identity(directory,parent=True)
        checkpoint("directory_created",{"job_id":job.id})
        with guard_directory(directory,created_identity) as descriptor:
            name = job.stored_name
            temporary = f".{name}.uploading"
            leaf = temporary if descriptor is not None else directory / temporary
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,"O_NOFOLLOW",0)
            validate()
            fd = os.open(leaf,flags,0o600,dir_fd=descriptor)
            digest,size = hashlib.sha256(),0
            try:
                original = os.fstat(fd)
                if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
                    raise UploadRejected("Upload candidate is not an owned file")
                if int(disk_free(directory)) - declared_size < reserve_bytes:
                    raise UploadRejected("Not enough staging space for the upload and safety reserve")
                async for data in _iter_bounded_chunks(stream,chunk_bytes):
                    validate()
                    if size + len(data) > max_bytes or size + len(data) > declared_size:
                        raise UploadRejected("The stream exceeds the reviewed upload size")
                    if int(disk_free(directory)) - len(data) < reserve_bytes:
                        raise UploadRejected("The staging safety reserve would be crossed")
                    offset = 0
                    while offset < len(data):
                        validate()
                        written = os.write(fd,data[offset:])
                        if written <= 0:
                            raise OSError("Upload write made no progress")
                        offset += written
                    digest.update(data)
                    size += len(data)
                validate()
                if size != declared_size:
                    raise UploadRejected("The stream size differs from the reviewed upload")
                os.fsync(fd)
                completed = os.fstat(fd)
                named = os.stat(leaf,dir_fd=descriptor,follow_symlinks=False)
                identity = (completed.st_dev,completed.st_ino)
                if (identity != (original.st_dev,original.st_ino) or identity != (named.st_dev,named.st_ino)
                        or completed.st_size != size or completed.st_nlink != 1
                        or not stat.S_ISREG(named.st_mode)):
                    raise UploadRejected("Upload candidate changed; retained for recovery")
                proof = {"job_id":job.id,"size_bytes":size,"sha256":digest.hexdigest(),
                    "device":completed.st_dev,"inode":completed.st_ino}
                checkpoint("candidate_saved",proof)
                destination = name if descriptor is not None else directory / name
                if os.name == "nt":
                    os.close(fd)
                    fd = None
                validate()
                _rename_edit_no_replace(leaf,destination,src_dir_fd=descriptor,dst_dir_fd=descriptor)
                published = os.stat(destination,dir_fd=descriptor,follow_symlinks=False)
                if (published.st_dev,published.st_ino) != identity or published.st_nlink != 1:
                    raise UploadRejected("Published upload changed; retained for recovery")
                checkpoint("source_published",proof)
                validate()
                result = service.complete_staging(job.id,digest.hexdigest(),size,directory / name,
                    validate=validate,retain_duplicate=True,expected_identity=identity)
                checkpoint("source_recorded",{"job_id":job.id,"status":result.status})
                return result
            finally:
                if fd is not None:
                    os.close(fd)
