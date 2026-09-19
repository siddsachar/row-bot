"""Bounded request framing for reviewed uploads; no staging or provider effects."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
import json
import math
import struct
from typing import Any

MAX_CHUNK_BYTES = 1024 * 1024
MAX_COMMAND_BYTES = 16 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024


class DocumentUploadStreamError(ValueError):
    """Closed, content-free framing failure suitable for the protocol adapter."""

    def __init__(self, code: str = "invalid_document_upload") -> None:
        self.code = code
        super().__init__(code)


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DocumentUploadStreamError()
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise DocumentUploadStreamError()


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise DocumentUploadStreamError()
    return result


class _FileReader:
    def __init__(self, owner: DocumentUploadStream, index: int) -> None:
        self._owner = owner
        self._index = index

    async def read(self, size: int) -> bytes:
        return await self._owner._dispatch("file", self._index, size)


class DocumentUploadStream:
    """Read a command then exact declared files on the originating ASGI loop.

    Instantiate on the request loop. Validate the returned closed Command and
    its review before constructing a job service or consuming file readers.
    Call close in the route's finally block, including command replay/denial.
    """

    def __init__(self, stream: AsyncIterator[bytes], *, max_chunk_bytes: int = MAX_CHUNK_BYTES,
                 timeout_seconds: float = 300) -> None:
        if (type(max_chunk_bytes) is not int or not 1 <= max_chunk_bytes <= MAX_CHUNK_BYTES
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300):
            raise DocumentUploadStreamError()
        self._loop = asyncio.get_running_loop()
        self._stream = aiter(stream)
        self._chunk_limit = max_chunk_bytes
        self._deadline = self._loop.time() + timeout_seconds
        self._buffer = b""
        self._offset = 0
        self._eof = False
        self._closed = False
        self._closing: asyncio.Task | None = None
        self._failed: str | None = None
        self._active: asyncio.Task | None = None
        self._command_read = False
        self._sizes: list[int] | None = None
        self._index = 0
        self._remaining = 0

    async def _dispatch(self, operation: str, *args: Any) -> Any:
        if asyncio.get_running_loop() is self._loop:
            return await self._operate(operation, *args)
        if self._loop.is_closed() or not self._loop.is_running():
            raise DocumentUploadStreamError("document_upload_closed")
        coroutine = self._operate(operation, *args)
        try:
            pending = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            raise DocumentUploadStreamError("document_upload_closed") from None
        try:
            return await asyncio.wrap_future(pending)
        except asyncio.CancelledError:
            pending.cancel()
            raise

    def _check(self) -> None:
        if self._closed:
            raise DocumentUploadStreamError("document_upload_closed")
        if self._failed:
            raise DocumentUploadStreamError(self._failed)
        if self._loop.time() >= self._deadline:
            raise DocumentUploadStreamError("document_upload_timeout")

    async def _operate(self, operation: str, *args: Any) -> Any:
        if operation == "close":
            return await self._close()
        self._check()
        if self._active is not None:
            raise DocumentUploadStreamError("invalid_document_upload")
        self._active = asyncio.current_task()
        try:
            if operation == "command":
                return await self._read_command()
            return await self._read_file(*args)
        except DocumentUploadStreamError as error:
            self._failed = error.code
            self._buffer = b""
            self._offset = 0
            raise
        except asyncio.CancelledError:
            self._failed = "document_upload_closed"
            self._buffer = b""
            self._offset = 0
            raise
        finally:
            self._active = None

    async def _receive(self) -> bool:
        self._check()
        if self._offset < len(self._buffer):
            return True
        self._buffer = b""
        self._offset = 0
        if self._eof:
            return False
        for _ in range(1024):
            self._check()
            try:
                chunk = await asyncio.wait_for(anext(self._stream), min(30, self._deadline - self._loop.time()))
            except StopAsyncIteration:
                self._eof = True
                return False
            except TimeoutError:
                raise DocumentUploadStreamError("document_upload_timeout") from None
            except asyncio.CancelledError:
                raise
            except Exception:
                raise DocumentUploadStreamError("document_upload_closed") from None
            self._check()
            if not isinstance(chunk, bytes):
                raise DocumentUploadStreamError()
            if len(chunk) > self._chunk_limit:
                raise DocumentUploadStreamError("payload_too_large")
            if chunk:
                self._buffer = chunk
                return True
        raise DocumentUploadStreamError()

    async def _exact(self, size: int) -> bytes:
        # Called only for <=16KiB command fields or <=1MiB requested file reads.
        chunks = bytearray()
        while len(chunks) < size:
            if not await self._receive():
                raise DocumentUploadStreamError()
            count = min(size - len(chunks), len(self._buffer) - self._offset)
            chunks.extend(self._buffer[self._offset:self._offset + count])
            self._offset += count
        return bytes(chunks)

    async def read_command(self) -> dict:
        return await self._dispatch("command")

    async def _read_command(self) -> dict:
        if self._command_read:
            raise DocumentUploadStreamError()
        size = struct.unpack(">I", await self._exact(4))[0]
        if not 1 <= size <= MAX_COMMAND_BYTES:
            raise DocumentUploadStreamError("payload_too_large" if size > MAX_COMMAND_BYTES else "invalid_document_upload")
        data = await self._exact(size)
        try:
            command = json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant,
                                 parse_float=_float)
        except (ValueError, UnicodeError, RecursionError):
            raise DocumentUploadStreamError() from None
        if not isinstance(command, dict):
            raise DocumentUploadStreamError()
        self._command_read = True
        return command

    def files(self, metadata: Sequence[dict]) -> tuple[_FileReader, ...]:
        self._check()
        if (asyncio.get_running_loop() is not self._loop or self._active is not None
                or not self._command_read or self._sizes is not None or not isinstance(metadata, (list, tuple))
                or not 1 <= len(metadata) <= 50):
            raise DocumentUploadStreamError()
        sizes = []
        for item in metadata:
            if (not isinstance(item, dict) or item.keys() != {"name", "size_bytes"}
                    or not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 256
                    or type(item["size_bytes"]) is not int or not 1 <= item["size_bytes"] <= MAX_FILE_BYTES):
                raise DocumentUploadStreamError()
            sizes.append(item["size_bytes"])
        self._sizes = sizes
        self._remaining = sizes[0]
        return tuple(_FileReader(self, index) for index in range(len(sizes)))

    async def _read_file(self, index: int, size: int) -> bytes:
        if type(size) is not int or not 1 <= size <= MAX_CHUNK_BYTES or self._sizes is None:
            raise DocumentUploadStreamError()
        if index < self._index:
            return b""  # Canonical upload staging can check a completed source again.
        if index != self._index:
            raise DocumentUploadStreamError()
        if self._remaining == 0:
            self._index += 1
            if self._index < len(self._sizes):
                self._remaining = self._sizes[self._index]
            return b""
        result = await self._exact(min(size, self._remaining))
        self._remaining -= len(result)
        if self._remaining == 0 and index == len(self._sizes) - 1:
            # Prove exact framing before the domain can publish this final file
            # and finish the paused batch, not on some later optional read.
            if await self._receive():
                raise DocumentUploadStreamError()
        return result

    async def close(self) -> None:
        await self._dispatch("close")

    async def _close(self) -> None:
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._finish_close())
        # One cancelled/disconnected caller cannot make another caller mistake
        # cleanup in progress for a fully drained request stream.
        await asyncio.shield(self._closing)

    async def _finish_close(self) -> None:
        active = self._active
        if active is not None and active is not asyncio.current_task():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        self._buffer = b""
        self._offset = 0
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            try:
                await asyncio.wait_for(close(), 1)
            except (Exception, asyncio.CancelledError):
                # No bytes or effects survive close; a transport close error is
                # not permission to consume or stage another request body.
                pass
