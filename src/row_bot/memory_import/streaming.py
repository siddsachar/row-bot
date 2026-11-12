"""Streaming JSON: one array item at a time from files that can be a gigabyte.

The standard library's :meth:`json.JSONDecoder.raw_decode` decodes each value
from a growing text buffer; consumed text is dropped, so memory holds about one
item. Exports are often a single line, so nothing here reads line by line.
"""
from __future__ import annotations

import codecs
import io
import json
from collections.abc import Iterator
from typing import IO, Any


CHUNK_CHARS = 1 << 16
MAX_VALUE_CHARS = 256 * 1024 * 1024
_WHITESPACE = " \t\r\n"
_NUMBER_CHARS = frozenset("0123456789+-.eE")
_DECODER = json.JSONDecoder()


class JsonFormatError(ValueError):
    """The text is not the JSON shape it should be."""


def text_reader(raw: IO[bytes]) -> io.TextIOWrapper:
    """UTF-8 text from bytes, an optional BOM dropped, broken bytes replaced."""
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")


class JsonStream:
    """A cursor over JSON text that decodes one value at a time."""

    def __init__(self, reader: IO[str], *, chunk_chars: int = CHUNK_CHARS,
                 max_value_chars: int = MAX_VALUE_CHARS) -> None:
        self._reader = reader
        self._chunk = max(1, chunk_chars)
        self._max = max_value_chars
        self._buf = ""
        self._pos = 0
        self._base = 0
        self._eof = False

    @property
    def offset(self) -> int:
        """Characters consumed so far."""
        return self._base + self._pos

    @classmethod
    def from_bytes(cls, raw: IO[bytes], **kwargs: Any) -> JsonStream:
        return cls(text_reader(raw), **kwargs)

    def _fill(self, want: int = 0) -> bool:
        if self._eof:
            return False
        if self._pos:
            self._base += self._pos
            self._buf = self._buf[self._pos:]
            self._pos = 0
        chunk = self._reader.read(max(self._chunk, want))
        if not chunk:
            self._eof = True
            return False
        self._buf += chunk
        return True

    def peek(self) -> str:
        """The next character that is not whitespace, or ``""`` at the end."""
        while True:
            while self._pos < len(self._buf) and self._buf[self._pos] in _WHITESPACE:
                self._pos += 1
            if self._pos < len(self._buf):
                return self._buf[self._pos]
            if not self._fill():
                return ""

    def _expect(self, char: str) -> None:
        found = self.peek()
        if found != char:
            raise JsonFormatError(f"expected {char!r}, found {found[:1]!r}")
        self._pos += 1

    def value(self) -> Any:
        """Decode the next whole value."""
        if not self.peek():
            raise JsonFormatError("unexpected end of the file")
        while True:
            try:
                result, end = _DECODER.raw_decode(self._buf, self._pos)
            except json.JSONDecodeError as exc:
                pending = len(self._buf) - self._pos
                if pending > self._max:
                    raise JsonFormatError("one item is too large to read") from exc
                if not self._fill(pending):
                    raise JsonFormatError(f"the JSON is broken ({exc.msg})") from exc
                continue
            # A number may go on in the next chunk ("-5" of "-5e-08"): read on while only its tail is buffered.
            if (isinstance(result, (int, float)) and not isinstance(result, bool) and not self._eof
                    and len(self._buf) - end < 32 and set(self._buf[end:]) <= _NUMBER_CHARS and self._fill()):
                continue
            self._pos = end
            return result

    def skip_value(self) -> None:
        """Pass over the next value without holding a large array or object."""
        char = self.peek()
        if char == "[":
            for _ in self.iter_array():
                pass
        elif char == "{":
            for _ in self.iter_object():
                pass
        else:
            self.value()

    def iter_array(self) -> Iterator[Any]:
        """Each item of the array that starts here."""
        self._expect("[")
        if self.peek() == "]":
            self._pos += 1
            return
        while True:
            yield self.value()
            char = self.peek()
            self._pos += 1
            if char == "]":
                return
            if char != ",":
                raise JsonFormatError(f"expected ',' or ']', found {char[:1]!r}")

    def iter_object(self) -> Iterator[str]:
        """Each key of the object that starts here.

        After a key is yielded the caller may read its value with :meth:`value`,
        :meth:`iter_array` (to the end) or :meth:`iter_object`; a value left
        unread is skipped.
        """
        self._expect("{")
        if self.peek() == "}":
            self._pos += 1
            return
        while True:
            key = self.value()
            if not isinstance(key, str):
                raise JsonFormatError("an object key is not text")
            self._expect(":")
            self.peek()
            before = self.offset
            yield key
            if self.offset == before:
                self.skip_value()
            char = self.peek()
            self._pos += 1
            if char == "}":
                return
            if char != ",":
                raise JsonFormatError(f"expected ',' or '}}', found {char[:1]!r}")

    def iter_values(self) -> Iterator[Any]:
        """Each top-level value, for JSON Lines or concatenated JSON."""
        while self.peek():
            yield self.value()


def iter_json_array(raw: IO[bytes], *, chunk_chars: int = CHUNK_CHARS) -> Iterator[Any]:
    """Each item of a top-level JSON array, read lazily from a byte stream."""
    yield from JsonStream.from_bytes(raw, chunk_chars=chunk_chars).iter_array()


def decode_utf8_head(head: bytes) -> str:
    """The start of a file as text, ignoring a character cut at the end."""
    return codecs.getincrementaldecoder("utf-8-sig")(errors="replace").decode(head, final=False)
