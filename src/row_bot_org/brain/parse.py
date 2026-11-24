"""Company documents' text, and its parts: what the brain can read, and the chunks it searches.

The types are the ones the server can read with what its image carries (the core dependencies and the ``org``
group, standard library here):

- **text** (``.txt``) and **Markdown** (``.md``, ``.markdown``): UTF-8 (a byte-order mark is dropped);
- **Word** (``.docx``): ``word/document.xml`` read from the zip, its paragraphs (tables' included, in order) one
  per line, read paragraph by paragraph (``iterparse``). A part larger than ``MAX_XML_BYTES`` (16 MiB)
  unpacked, one that isn't UTF-8, or one with a DTD is refused, so a zip bomb or an entity expansion never runs.

PDF is left out: the desktop app reads it with ``pypdf``, which is an extra, not in the server's image (see the
B1a handoff). The chunks follow the app's own document chunking (``row_bot.documents``): 1,500 characters with
150 overlapping.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

CHUNK_SIZE = 1_500
CHUNK_OVERLAP = 150
# A document gives at most this many chunks (about 6.8 million characters).
MAX_CHUNKS = 5_000
MAX_XML_BYTES = 16 * 1024 * 1024
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class FileType:
    name: str
    media_type: str


TEXT = FileType("text", "text/plain")
MARKDOWN = FileType("markdown", "text/markdown")
WORD = FileType("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
BY_EXTENSION = {".txt": TEXT, ".md": MARKDOWN, ".markdown": MARKDOWN, ".docx": WORD}
BY_MEDIA_TYPE = {kind.media_type: kind for kind in (TEXT, MARKDOWN, WORD)}


class ParseFailed(Exception):
    """The file can't be read as its type; ``reason`` says so, for a person to read."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def file_type(file_name: str) -> FileType | None:
    """The type a file name says, by its extension; None for a type the brain can't read."""
    stem, dot, extension = file_name.rpartition(".")
    return BY_EXTENSION.get(f"{dot}{extension}".lower()) if dot and stem else None


def _text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseFailed("This file isn't UTF-8 text.") from None


def _word(content: bytes) -> str:
    unreadable = ParseFailed("This Word document can't be read.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive, archive.open("word/document.xml") as part:
            xml = part.read(MAX_XML_BYTES + 1)
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, ValueError, NotImplementedError, EOFError):
        raise unreadable from None
    if len(xml) > MAX_XML_BYTES:
        raise ParseFailed("This Word document is too large to read.")
    # Word writes UTF-8. Anything else is refused before the DTD check, so another encoding can't hide one.
    try:
        decoded = xml.decode("utf-8")
    except UnicodeDecodeError:
        raise unreadable from None
    if "<!DOCTYPE" in decoded or "<!ENTITY" in decoded:
        raise unreadable
    del decoded
    lines = []
    try:
        # Paragraph by paragraph, each cleared once read, so the document's tree is never held whole.
        for _, node in ElementTree.iterparse(io.BytesIO(xml), events=("end",)):
            if node.tag != f"{_WORD}p":
                continue
            parts = []
            for inner in node.iter():
                if inner.tag == f"{_WORD}t" and inner.text:
                    parts.append(inner.text)
                elif inner.tag == f"{_WORD}tab":
                    parts.append("\t")
                elif inner.tag in (f"{_WORD}br", f"{_WORD}cr"):
                    parts.append("\n")
            lines.append("".join(parts))
            node.clear()
    except ElementTree.ParseError:
        raise unreadable from None
    return "\n".join(lines)


def extract_text(kind: FileType, content: bytes) -> str:
    """The text of ``content``, a file of type ``kind``; ``ParseFailed`` when it can't be read or holds none."""
    text = _word(content) if kind is WORD else _text(content)
    # Postgres text can't hold NUL.
    text = text.replace("\x00", "")
    if not text.strip():
        raise ParseFailed("This file has no text.")
    return text


def chunk_text(text: str) -> list[str]:
    """``text`` in overlapping parts, as the app chunks a document; ``ParseFailed`` past ``MAX_CHUNKS``."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if text[start:end].strip():
            chunks.append(text[start:end])
            if len(chunks) > MAX_CHUNKS:
                raise ParseFailed(f"This document has too much text (more than {MAX_CHUNKS:,} parts).")
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


__all__ = ["BY_EXTENSION", "BY_MEDIA_TYPE", "CHUNK_OVERLAP", "CHUNK_SIZE", "MARKDOWN", "MAX_CHUNKS", "TEXT", "WORD",
           "FileType", "ParseFailed", "chunk_text", "extract_text", "file_type"]
