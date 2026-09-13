"""Deterministic framing, receive-loop ownership and bounded upload readers."""
from __future__ import annotations

import asyncio
import json
import struct

import pytest

from row_bot.api.v1.document_upload_stream import DocumentUploadStream, DocumentUploadStreamError

pytestmark = pytest.mark.subsystem


def framed(command, payload=b""):
    value = json.dumps(command, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(value)) + value + payload


async def chunks(values):
    for value in values:
        yield value


def metadata(*sizes):
    return [{"name": f"document-{index}.txt", "size_bytes": size} for index, size in enumerate(sizes)]


def test_fragmented_unicode_command_and_files_preserve_exact_binary_bytes():
    async def scenario():
        command = {"type": "document.upload", "description": "資料"}
        body = framed(command, b"\xff\x00abcd")
        reader = DocumentUploadStream(chunks([body[index:index + 1] for index in range(len(body))]))
        try:
            assert await reader.read_command() == command
            first, second = reader.files(metadata(2, 4))
            assert await first.read(1) == b"\xff"
            assert await first.read(100) == b"\x00"
            assert await first.read(100) == b""
            assert await second.read(3) == b"abc"
            assert await second.read(3) == b"d"
            assert await second.read(3) == b""
            assert await first.read(3) == b""
        finally:
            await reader.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("raw", [b"", b"\x00", b"\x00\x00\x00\x02{", struct.pack(">I", 0),
    struct.pack(">I", 16385), b'\x00\x00\x00\x02[]', b'\x00\x00\x00\x02\xff\xff',
    struct.pack(">I", 13) + b'{"a":1,"a":2}', struct.pack(">I", 9) + b'{"x":NaN}',
    struct.pack(">I", 11) + b'{"x":1e309}'])
def test_invalid_or_truncated_command_never_offers_file_readers(raw):
    async def scenario():
        reader = DocumentUploadStream(chunks([raw]))
        try:
            with pytest.raises(DocumentUploadStreamError):
                await reader.read_command()
            with pytest.raises(DocumentUploadStreamError):
                reader.files(metadata(1))
        finally:
            await reader.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("payload,size", [(b"ab", 3), (b"abcd", 3)])
def test_truncation_and_trailing_data_fail_before_final_file_read_returns(payload, size):
    async def scenario():
        reader = DocumentUploadStream(chunks([framed({}, payload)]))
        await reader.read_command()
        source, = reader.files(metadata(size))
        try:
            with pytest.raises(DocumentUploadStreamError, match="invalid_document_upload"):
                await source.read(1024)
            with pytest.raises(DocumentUploadStreamError):
                await source.read(1024)
        finally:
            await reader.close()
    asyncio.run(scenario())


def test_final_file_waits_for_true_request_eof_before_returning_last_bytes():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def source():
            yield framed({}, b"abc")
            entered.set()
            await release.wait()
        reader = DocumentUploadStream(source())
        await reader.read_command()
        file, = reader.files(metadata(3))
        task = asyncio.create_task(file.read(3))
        await entered.wait()
        assert not task.done()
        release.set()
        assert await task == b"abc"
        await reader.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("values", [[], metadata(*([1] * 51)), metadata(0), metadata(256 * 1024**2 + 1),
    [{"name": "a.txt", "size_bytes": True}], [{"name": "a.txt", "size_bytes": 1, "path": "forbidden"}]])
def test_file_count_and_declared_byte_metadata_are_bounded(values):
    async def scenario():
        reader = DocumentUploadStream(chunks([framed({})]))
        await reader.read_command()
        with pytest.raises(DocumentUploadStreamError):
            reader.files(values)
        await reader.close()
    asyncio.run(scenario())


def test_maximum_metadata_is_accepted_without_reading_or_allocating_file_bodies():
    async def scenario():
        pulled = []
        async def source():
            yield framed({})
            pulled.append("files")
            raise AssertionError("Metadata registration consumed files")
        reader = DocumentUploadStream(source())
        await reader.read_command()
        assert len(reader.files(metadata(*([256 * 1024**2] * 50)))) == 50
        assert pulled == []
        await reader.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [-1, 0, True, 1024**2 + 1])
def test_read_size_has_no_unbounded_or_default_read_escape(size):
    async def scenario():
        reader = DocumentUploadStream(chunks([framed({}, b"a")]))
        await reader.read_command()
        source, = reader.files(metadata(1))
        with pytest.raises(DocumentUploadStreamError):
            await source.read(size)
        await reader.close()
    asyncio.run(scenario())


def test_oversized_asgi_chunk_rejected_without_copying_into_pending_buffer():
    async def scenario():
        reader = DocumentUploadStream(chunks([b"a" * (1024**2 + 1)]))
        with pytest.raises(DocumentUploadStreamError, match="payload_too_large"):
            await reader.read_command()
        assert reader._buffer == b""
        await reader.close()
    asyncio.run(scenario())


def test_out_of_order_read_is_terminal_and_does_not_adopt_another_file():
    async def scenario():
        reader = DocumentUploadStream(chunks([framed({}, b"ab")]))
        await reader.read_command()
        first, second = reader.files(metadata(1, 1))
        with pytest.raises(DocumentUploadStreamError):
            await second.read(1)
        with pytest.raises(DocumentUploadStreamError):
            await first.read(1)
        await reader.close()
    asyncio.run(scenario())


def test_worker_loop_reader_receives_only_on_origin_asgi_loop():
    async def scenario():
        origin = asyncio.get_running_loop()
        received = []
        async def source():
            for value in (framed({}), b"abc", b"d"):
                assert asyncio.get_running_loop() is origin
                received.append(value)
                yield value
        reader = DocumentUploadStream(source())
        await reader.read_command()
        file, = reader.files(metadata(4))
        async def worker():
            assert asyncio.get_running_loop() is not origin
            assert await file.read(2) == b"ab"
            assert await file.read(2) == b"cd"
            assert await file.read(2) == b""
        await asyncio.to_thread(lambda: asyncio.run(worker()))
        assert received == [framed({}), b"abc", b"d"]
        await reader.close()
    asyncio.run(scenario())


def test_close_cancels_pending_origin_receive_and_waits_for_actual_finally():
    async def scenario():
        entered, finalized = asyncio.Event(), asyncio.Event()
        async def source():
            yield framed({})
            try:
                entered.set()
                await asyncio.Event().wait()
                yield b"x"
            finally:
                finalized.set()
        reader = DocumentUploadStream(source())
        await reader.read_command()
        file, = reader.files(metadata(1))
        async def worker():
            with pytest.raises(asyncio.CancelledError):
                await file.read(1)
        pending = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(worker())))
        await entered.wait()
        await reader.close()
        assert finalized.is_set() and reader._active is None and reader._buffer == b""
        await pending
        await reader.close()
        with pytest.raises(DocumentUploadStreamError, match="document_upload_closed"):
            await file.read(1)
    asyncio.run(scenario())


def test_concurrent_file_read_rejected_without_a_second_receive():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def source():
            yield framed({})
            calls.append("receive")
            entered.set()
            await release.wait()
            yield b"a"
        reader = DocumentUploadStream(source())
        await reader.read_command()
        file, = reader.files(metadata(1))
        first = asyncio.create_task(file.read(1))
        await entered.wait()
        with pytest.raises(DocumentUploadStreamError):
            await file.read(1)
        release.set()
        assert await first == b"a" and calls == ["receive"]
        await reader.close()
    asyncio.run(scenario())


def test_total_deadline_checked_before_and_after_chunk_receipt():
    async def scenario(after):
        async def source():
            reader._deadline = asyncio.get_running_loop().time() - 1
            yield framed({})
        reader = DocumentUploadStream(source())
        if not after:
            reader._deadline = asyncio.get_running_loop().time() - 1
        with pytest.raises(DocumentUploadStreamError, match="document_upload_timeout"):
            await reader.read_command()
        await reader.close()
    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_stalled_receive_uses_bounded_remaining_timeout(monkeypatch):
    async def scenario():
        class Source:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise AssertionError("Fake timeout must intercept receive")
        values = []
        async def timeout(awaitable, seconds):
            values.append(seconds)
            awaitable.close()
            raise TimeoutError
        monkeypatch.setattr(asyncio, "wait_for", timeout)
        reader = DocumentUploadStream(Source(), timeout_seconds=12)
        with pytest.raises(DocumentUploadStreamError, match="document_upload_timeout"):
            await reader.read_command()
        assert len(values) == 1 and 0 < values[0] <= 12
        await reader.close()
    asyncio.run(scenario())


def test_close_before_file_approval_never_consumes_pending_body():
    async def scenario():
        pulled = []
        async def source():
            try:
                yield framed({})
                pulled.append("body")
                yield b"secret file contents"
            finally:
                pulled.append("closed")
        reader = DocumentUploadStream(source())
        await reader.read_command()
        await reader.close()
        assert pulled == ["closed"]
    asyncio.run(scenario())


def test_exact_maximum_command_and_small_chunk_bound():
    async def scenario():
        raw = b"{}" + b" " * (16384 - 2)
        body = struct.pack(">I", len(raw)) + raw + b"x"
        reader = DocumentUploadStream(chunks([body[index:index + 7] for index in range(0, len(body), 7)]), max_chunk_bytes=7)
        assert await reader.read_command() == {}
        file, = reader.files(metadata(1))
        assert await file.read(1) == b"x"
        await reader.close()
    asyncio.run(scenario())


def test_transport_disconnect_is_content_free_and_terminal():
    async def scenario():
        async def source():
            yield framed({})
            raise RuntimeError("synthetic private transport details")
        reader = DocumentUploadStream(source())
        await reader.read_command()
        file, = reader.files(metadata(1))
        with pytest.raises(DocumentUploadStreamError) as error:
            await file.read(1)
        assert str(error.value) == "document_upload_closed"
        with pytest.raises(DocumentUploadStreamError, match="document_upload_closed"):
            await file.read(1)
        await reader.close()
    asyncio.run(scenario())


def test_cancelled_close_caller_does_not_release_another_before_receive_cleanup():
    async def scenario():
        entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def source():
            yield framed({})
            try:
                entered.set()
                await asyncio.Event().wait()
                yield b"a"
            finally:
                closing.set()
                await release.wait()
        reader = DocumentUploadStream(source())
        await reader.read_command()
        file, = reader.files(metadata(1))
        pending = asyncio.create_task(file.read(1))
        await entered.wait()
        first = asyncio.create_task(reader.close())
        await closing.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(reader.close())
        # The shared close task is still blocked on actual receive cleanup.
        assert reader._closing is not None and not reader._closing.done()
        assert not second.done()
        release.set()
        await second
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert reader._active is None and reader._closing.done()
    asyncio.run(scenario())


def test_closed_origin_loop_cannot_be_reused_by_a_new_worker_loop():
    async def prepare():
        reader = DocumentUploadStream(chunks([framed({}, b"a")]))
        await reader.read_command()
        return reader.files(metadata(1))[0]
    file = asyncio.run(prepare())
    async def attempt():
        with pytest.raises(DocumentUploadStreamError, match="document_upload_closed"):
            await file.read(1)
    asyncio.run(attempt())
