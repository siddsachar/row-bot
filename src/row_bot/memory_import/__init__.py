"""Import memory: reading what another assistant knows about the person.

This package only parses. :func:`scan_files` and :func:`scan_text` turn picked
files or a pasted answer into an :class:`ImportScan` of candidate facts and past
conversations, with no side effects: no database write, no model call, no
network and nothing written to disk. Every string read is data, never an
instruction. Writing reviewed facts into memory belongs to the caller.

Formats follow the export research in the memory-534 plan; files are
recognised by their bytes and structure, never by name alone.
"""
from __future__ import annotations

from row_bot.memory_import.catalogue import CONTINUE_PROMPT, SOURCES, UNIVERSAL_PROMPT, SourceInfo
from row_bot.memory_import.models import (
    ENTITY_TYPES,
    ImportedConversation,
    ImportedFact,
    ImportedMessage,
    ImportScan,
    SkippedItem,
    SourceCounts,
)
from row_bot.memory_import.scan import scan_files, scan_text
from row_bot.memory_import.streaming import iter_json_array


__all__ = [
    "CONTINUE_PROMPT",
    "ENTITY_TYPES",
    "SOURCES",
    "UNIVERSAL_PROMPT",
    "ImportScan",
    "ImportedConversation",
    "ImportedFact",
    "ImportedMessage",
    "SkippedItem",
    "SourceCounts",
    "SourceInfo",
    "iter_json_array",
    "scan_files",
    "scan_text",
]
