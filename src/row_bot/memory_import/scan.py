"""Scanning picked files or a pasted answer into an :class:`ImportScan`.

A scan has no side effects: nothing is written, no model is called and no
network is used. Links that an export only points to are listed, never fetched.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

from row_bot.memory_import import chatgpt, claude, copilot, deepseek, gemini, grok, letta, mem0, pasted, perplexity
from row_bot.memory_import.archive import Member, expand, memory_member, normal_name
from row_bot.memory_import.detect import classify, shard_number
from row_bot.memory_import.models import ImportedConversation, ImportScan
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonFormatError


logger = logging.getLogger(__name__)

MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024

ConversationReader = Callable[[Member, Sink], Iterator[ImportedConversation]]
FactReader = Callable[[Member, Sink], None]

_CONVERSATIONS: dict[str, tuple[str, ConversationReader]] = {
    "claude_conversations": ("claude", claude.iter_conversations),
    "chatgpt": ("chatgpt", chatgpt.iter_conversations),
    "gemini": ("gemini", gemini.iter_conversations),
    "copilot": ("copilot", copilot.iter_conversations),
    "perplexity": ("perplexity", perplexity.iter_conversations),
    "grok": ("grok", grok.iter_conversations),
    "deepseek": ("deepseek", deepseek.iter_conversations),
    "letta": ("letta", letta.iter_conversations),
}
_FACTS: dict[str, tuple[str, FactReader]] = {
    "claude_memory": ("claude", claude.read_memories),
    "claude_manifest": ("claude", claude.note_manifest),
    "mem0": ("mem0", mem0.read_items),
    "openmemory": ("mem0", mem0.read_openmemory),
    "openmemory_lines": ("mem0", mem0.read_openmemory_lines),
    "letta": ("letta", letta.read_blocks),
}
_SKIP_REASONS = {
    "account": "Account details (name, email, logins or billing) are never imported.",
    "claude_projects": "Claude projects hold project instructions and documents, not memories about you.",
    "chatgpt_single": "A single ChatGPT chat on its own is not an export; add the whole export zip.",
    "empty": "The file holds no records.",
    "unknown": "Not a file Row-Bot knows how to import.",
}


def _guarded(reader: ConversationReader, member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    """The reader's chats; a file broken part-way ends with a warning, not an error."""
    try:
        yield from reader(member, sink)
    except (JsonFormatError, UnicodeError, OSError, EOFError, ValueError) as exc:
        logger.info("memory import stopped reading a file early: %s", exc.__class__.__name__)
        sink.warn(f"{member.display} could not be read to the end; the chats before the damaged part were kept.")


def _fresh(reader: ConversationReader, member: Member) -> Iterator[ImportedConversation]:
    return _guarded(reader, member, Sink())


def _note_expansion(path: Path, sink: Sink) -> list[Member]:
    expansion = expand(path)
    for warning in expansion.warnings:
        sink.warn(warning)
    if expansion.ignored:
        sink.skip(path.name, f"{len(expansion.ignored)} attachments and other files in the archive were not read.")
    for name in expansion.inner_zips:
        sink.skip(f"{path.name} › {name}", "An archive inside the export, such as attachments; it was not opened.")
    return expansion.members


def _read_text_member(member: Member, sink: Sink) -> bool:
    with member.open() as raw:
        data = raw.read(MAX_TEXT_FILE_BYTES + 1)
    if len(data) > MAX_TEXT_FILE_BYTES:
        sink.skip(member.display, "The text file is too large to read as a list of memories.")
        return False
    text = data.decode("utf-8-sig", "replace")
    return pasted.parse(text, sink, source="pasted", label="Text file", ref=f"file:{normal_name(member.name)}")


def _check_chatgpt_shards(manifests: list[Member], shards: list[Member], sink: Sink) -> None:
    present = {normal_name(member.name) for member in shards}
    for manifest in manifests:
        try:
            names, count = chatgpt.manifest_shards(manifest)
        except (JsonFormatError, UnicodeError, OSError):
            continue
        missing = sorted({normal_name(name) for name in names} - present)
        for name in missing:
            sink.warn(f"The ChatGPT export lists {name}, but it is not among the files you added. "
                      "Add every part of the export.")
        if count is not None and count != len(shards) and not missing:
            sink.warn(f"The ChatGPT export says it has {count} conversation files; {len(shards)} were found.")


def _run(members: list[Member], sink: Sink, scan: ImportScan) -> None:
    kinds = [(member, classify(member)) for member in members]
    by_kind: dict[str, list[Member]] = {}
    more_remain = False
    for member, kind in kinds:
        by_kind.setdefault(kind, []).append(member)
        if kind in _SKIP_REASONS:
            if kind != "unknown" or member.archive is None:
                sink.skip(member.display, _SKIP_REASONS[kind])
        elif kind == "broken_json":
            sink.warn(f"{member.display} is not valid JSON; it may be damaged or cut short.")
        elif kind == "html" and member.archive is None:
            sink.warn(f"{member.display} is a web page. If it is Google's MyActivity.html, export Gemini again "
                      "with the JSON format chosen under My Activity.")
        elif kind == "text" and member.archive is None:
            more_remain = _read_text_member(member, sink) or more_remain
    if by_kind.get("html") and not any(kind in _CONVERSATIONS or kind in _FACTS for _, kind in kinds):
        sink.warn("This export only has web pages (HTML). Export it again with the JSON format if you can.")

    # OpenMemory zips hold the same memories twice; the JSON is read and the lines file is not.
    archives_with_json = {member.archive for member in by_kind.get("openmemory", [])}
    for member in list(by_kind.get("openmemory_lines", [])):
        if member.archive is not None and member.archive in archives_with_json:
            by_kind["openmemory_lines"].remove(member)
            sink.skip(member.display, "The same memories as memories.json in this backup.")

    for kind, (provider, read) in _FACTS.items():
        for member in by_kind.get(kind, []):
            try:
                read(member, sink)
            except (JsonFormatError, UnicodeError, OSError, EOFError, ValueError) as exc:
                logger.info("memory import could not read a memory file: %s", exc.__class__.__name__)
                sink.warn(f"{member.display} could not be read; it may be damaged.")

    shards = sorted(by_kind.get("chatgpt", []), key=lambda m: (m.archive or "", shard_number(m), m.name))
    by_kind["chatgpt"] = shards
    _check_chatgpt_shards(by_kind.get("chatgpt_manifest", []), shards, sink)

    seen: set[tuple[str, str]] = set()
    for kind, (provider, reader) in _CONVERSATIONS.items():
        for member in by_kind.get(kind, []):
            counts = sink.count(provider)
            for conversation in _guarded(reader, member, sink):
                key = (conversation.provider, conversation.id)
                if key not in seen:  # the same chat in two parts of an export counts once
                    seen.add(key)
                    counts.conversations += 1
                    counts.messages += len(conversation.messages)
            scan._streams.append(partial(_fresh, reader, member))
    chatgpt.add_instruction_facts(sink)
    scan.more_remain = more_remain


def _finish(sink: Sink, scan: ImportScan) -> ImportScan:
    if sink.secrets_dropped:
        noun = "line" if sink.secrets_dropped == 1 else "lines"
        sink.warn(f"{sink.secrets_dropped} {noun} that looked like a password, key or account number "
                  f"{'was' if sink.secrets_dropped == 1 else 'were'} left out.")
    if sink.broken_text:
        sink.warn("Some text had broken characters; they were replaced so the rest could be read.")
    if scan.more_remain:
        sink.warn("The answer says more memories remain. Ask your old assistant to continue, then paste the rest.")
    scan.facts = sink.facts
    scan.counts = {name: count for name, count in sink.counts.items()
                   if count.facts or count.conversations}
    scan.warnings = sink.warnings
    scan.skipped = sink.skipped
    return scan


def scan_files(paths: list[Path]) -> ImportScan:
    """Read the picked files (several parts of one export merge) without changing anything."""
    sink = Sink()
    scan = ImportScan()
    members: list[Member] = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            sink.warn(f"{path.name} is not a file that can be read.")
            continue
        members.extend(_note_expansion(path, sink))
    _run(members, sink, scan)
    return _finish(sink, scan)


def scan_text(text: str) -> ImportScan:
    """Read a pasted answer (or pasted export JSON) without changing anything."""
    sink = Sink()
    scan = ImportScan()
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped[:1] in {"[", "{"}:
        member = memory_member("Pasted text", text.encode("utf-8", "surrogatepass"))
        kind = classify(member)
        if kind in _FACTS or kind in _CONVERSATIONS:
            _run([member], sink, scan)
            return _finish(sink, scan)
    scan.more_remain = pasted.parse(text, sink)
    return _finish(sink, scan)
