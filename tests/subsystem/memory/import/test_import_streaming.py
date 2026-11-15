from __future__ import annotations

import io
import json

import pytest

from row_bot.memory_import import iter_json_array, scan_files
from tests.fixtures.memory_exports import chatgpt_simple, write_zip


pytestmark = pytest.mark.subsystem


class CountingReader(io.RawIOBase):
    """A byte stream that records how much has been read from it."""

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._inner.read(len(buffer))
        buffer[:len(data)] = data
        self.consumed += len(data)
        return len(data)


def test_array_items_come_one_at_a_time_before_the_file_is_read_to_the_end() -> None:
    items = [{"id": n, "text": "x" * 200} for n in range(5000)]
    data = json.dumps(items).encode("utf-8")
    reader = CountingReader(data)

    stream = iter_json_array(io.BufferedReader(reader, buffer_size=8192), chunk_chars=4096)
    first = next(stream)

    assert first == items[0]
    assert reader.consumed < len(data) // 20
    assert list(stream) == items[1:]
    assert reader.consumed == len(data)


@pytest.mark.parametrize("chunk_chars", [1, 2, 3, 7, 64])
def test_values_split_across_chunk_boundaries_decode_exactly(chunk_chars: int) -> None:
    items = [12345678901234567890, -0.5e-7, True, None, "naïve café ☕ \"quoted\" \\ 😀",
             {"nested": [1, [2, {"a": "b"}]], "empty": {}}, [], "", 7]
    data = ("﻿  " + json.dumps(items, ensure_ascii=False) + "\n").encode("utf-8")

    assert list(iter_json_array(io.BytesIO(data), chunk_chars=chunk_chars)) == items


def test_escaped_lone_surrogates_and_bad_bytes_do_not_stop_the_read() -> None:
    data = b'[{"text": "broken \\ud83d here"}, {"text": "bad \xff byte"}, {"text": "fine"}]'

    texts = [item["text"] for item in iter_json_array(io.BytesIO(data))]

    assert texts[0].startswith("broken ") and texts[2] == "fine"
    assert texts[1] == "bad � byte"


def test_a_large_generated_chatgpt_export_is_scanned_and_read_again_lazily(tmp_path) -> None:
    total = 3000
    conversations = [chatgpt_simple(f"c{n:05d}", 1762000000.0 + n, f"question number {n}") for n in range(total)]
    export = write_zip(tmp_path / "export.zip", {"conversations.json": json.dumps(conversations)})

    scan = scan_files([export])

    assert scan.counts["chatgpt"].conversations == total
    assert scan.counts["chatgpt"].messages == total * 2
    lazy = scan.iter_conversations()
    assert next(lazy).id == "c00000"
    ids = [conversation.id for conversation in scan.iter_conversations()]
    assert len(ids) == total and ids[-1] == f"c{total - 1:05d}"
