"""Memory that self-hosted agents keep in files (Hermes Agent, OpenClaw) and Grok Bot's self-export.

Formats, from each project's documentation and source (Nov 2026):

* Hermes Agent (Nous Research) keeps what it learned in two files under its home folder (``~/.hermes``, or
  ``%LOCALAPPDATA%\\hermes`` on Windows): ``memories/USER.md`` about the person and ``memories/MEMORY.md``
  with its own notes. Entries are separated by a section sign on its own line (``"\\n§\\n"``), with no other
  markup. Chats are in ``state.db`` (SQLite: ``sessions`` and ``messages``) or a ``hermes sessions export``
  JSONL file, one session per line.
* OpenClaw keeps memory as plain Markdown in each agent's workspace (``~/.openclaw/workspace``):
  ``USER.md``, ``MEMORY.md`` and daily notes ``memory/YYYY-MM-DD[-slug].md``. Chats are JSONL transcripts
  (a ``session`` header, then events in a tree by ``parentId``), in ``sessions/<id>.jsonl`` or, in newer
  releases, the agent's ``openclaw-agent.sqlite`` (``transcript_events``, JSON or zstd-compressed JSON).
* Grok Bot keeps memory only in xAI's cloud, with no export. A Bot asked with the Hermes importer's prompt
  writes ``{"format": "grok-bot-export", "bots": [{"name", "instructions", "memories", "user_facts"}]}``,
  which is read here; any other answer goes through the pasted-answer reader.

Persona and operating files (SOUL.md, IDENTITY.md, AGENTS.md…) are not memories about the person and are
skipped; settings and key files are never read (``detect.ACCOUNT_NAMES``). A database is read only when the
picked file itself is one, opened read-only.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.markdown import heading_of
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink, category_for_heading
from row_bot.memory_import.values import as_dict, as_list, as_text, to_utc

HERMES, OPENCLAW, GROK_BOT = "hermes", "openclaw", "grok_bot"
MAX_MEMORY_FILE_BYTES = 2 * 1024 * 1024  # OpenClaw's own editor limit; Hermes files are a few KB
_HERMES_DELIMITER = "\n§\n"
_DAILY_NOTE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-[\w.-]+)?\.md$")
_MEMORY_FILES = frozenset({"user.md", "memory.md"})
PERSONA_FILES = frozenset({
    "soul.md", "identity.md", "agents.md", "tools.md", "heartbeat.md", "boot.md", "bootstrap.md", "dreams.md",
})
_DIRECTIVE = re.compile(r"^(?:always|never|prefers?|likes?|dislikes?|wants?|avoid|don't|do not)\b", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_TITLE = re.compile(r"^(?:user|memory)\.md\b|^\d{4}-\d{2}-\d{2}\b|^(?:user model|about your human|long-term memory)$",
                    re.IGNORECASE)


# ── Detection ────────────────────────────────────────────────────────────────

def memory_file_kind(member: Member) -> str | None:
    """``hermes_memory``, ``openclaw_memory`` or ``agent_persona`` for an agent's memory or persona file."""
    base = member.base
    if base in PERSONA_FILES:
        return "agent_persona"
    daily = _DAILY_NOTE.match(base)
    if base not in _MEMORY_FILES and not daily:
        return None
    head = member.head.decode("utf-8-sig", "replace").replace("\r\n", "\n")
    parent = PurePosixPath(member.name.replace("\\", "/")).parent.name.lower()
    if daily:
        return "openclaw_memory"
    if _HERMES_DELIMITER in head or parent == "memories":
        return "hermes_memory"
    if parent == "memory" or any(line.lstrip().startswith(("#", "- ", "* ")) for line in head.splitlines()):
        return "openclaw_memory"
    return "hermes_memory"  # a single plain entry, as Hermes writes it


def jsonl_kind(member: Member) -> str | None:
    """``hermes_sessions`` or ``openclaw_transcript`` for those JSONL files, from their first line."""
    first = member.head.decode("utf-8-sig", "replace").split("\n", 1)[0].strip()
    if not first.startswith("{"):
        return None
    try:
        record = json.loads(first)
    except ValueError:
        return None  # a first line longer than the sniffed head: not one of these
    if not isinstance(record, dict):
        return None
    if record.get("type") == "session" and "id" in record:
        return "openclaw_transcript"
    if isinstance(record.get("messages"), list) and ({"started_at", "source", "model"} & record.keys()):
        return "hermes_sessions"
    return None


def database_kind(member: Member) -> str:
    """``hermes_db``, ``openclaw_db``, ``database_in_archive`` or ``database_unknown`` for a SQLite file."""
    if member.archive is not None or member.path is None:
        return "database_in_archive"
    try:
        with _read_only(member.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return "database_unknown"
    if "transcript_events" in tables:
        return "openclaw_db"
    if {"sessions", "messages"} <= tables:
        return "hermes_db"
    return "database_unknown"


def is_grok_bot_export(data: Any) -> bool:
    return isinstance(data, dict) and data.get("format") == "grok-bot-export" and isinstance(data.get("bots"), list)


# ── Memory files ─────────────────────────────────────────────────────────────

def _text(member: Member, sink: Sink) -> str | None:
    with member.open() as raw:
        data = raw.read(MAX_MEMORY_FILE_BYTES + 1)
    if len(data) > MAX_MEMORY_FILE_BYTES:
        sink.skip(member.display, "The memory file is larger than an agent's memory file can be.")
        return None
    return data.decode("utf-8-sig", "replace").replace("\r\n", "\n")


def _category(text: str, heading: str | None = None) -> tuple[str, tuple[str, ...]]:
    if _DIRECTIVE.match(text):
        return "preference", ()
    return category_for_heading(heading)


def read_hermes_memory(member: Member, sink: Sink) -> None:
    text = _text(member, sink)
    if text is None:
        return
    about_person = member.base == "user.md"
    label = "Hermes Agent: about you" if about_person else "Hermes Agent: its notes"
    for entry in (part.strip() for part in text.split(_HERMES_DELIMITER)):
        if not entry:
            continue
        category, tags = _category(entry)
        sink.add_facts(entry, category=category, source=HERMES, source_label=label, ref=f"hermes:{member.base}",
                       subject_hint="User" if about_person else None,
                       tags=tags if about_person else (*tags, "agent_note"))


def markdown_entries(text: str) -> Iterator[tuple[str | None, str]]:
    """Each bullet or paragraph line with the heading it sits under; code blocks, tables and titles left out."""
    heading: str | None = None
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        stripped = line.strip()
        if fenced or not stripped or stripped.startswith(("|", "<!--", "---")):
            continue
        found = heading_of(stripped)
        if found is not None:
            heading = None if _TITLE.match(found) else found
            continue
        entry = _BOLD.sub(r"\2", stripped.lstrip("-*+> ").strip())
        entry = re.sub(r"^\[[ xX]\]\s*", "", entry)  # a task-list box
        if entry:
            yield heading, entry


def read_openclaw_memory(member: Member, sink: Sink) -> None:
    text = _text(member, sink)
    if text is None:
        return
    daily = _DAILY_NOTE.match(member.base)
    about_person = member.base == "user.md"
    label = ("OpenClaw: about you" if about_person
             else f"OpenClaw: notes of {daily.group(1)}" if daily else "OpenClaw: its memory")
    for heading, entry in markdown_entries(text):
        category, tags = _category(entry, heading)
        if daily:
            tags = (*tags, "daily_note")
        elif not about_person:
            tags = (*tags, "agent_note")
        sink.add_facts(entry, category=category, source=OPENCLAW, source_label=label,
                       ref=f"openclaw:{member.base}", date=daily.group(1) if daily else None,
                       subject_hint="User" if about_person else None, tags=tags)


# ── Grok Bot's self-export ───────────────────────────────────────────────────

def read_grok_bot_export(data: dict[str, Any], sink: Sink, *, ref: str) -> None:
    """Facts from a ``grok-bot-export`` document: each Bot's facts about the person, its memories and rules."""
    left_out = False
    for bot in as_list(data.get("bots")):
        bot = as_dict(bot)
        name = " ".join(as_text(bot.get("name")).split()) or "Bot"
        for fact in as_list(bot.get("user_facts")):
            category, tags = _category(as_text(fact))
            sink.add_facts(as_text(fact), category=category, source=GROK_BOT, source_label=f"Grok Bot {name}: about you",
                           ref=f"{ref}:{name}:user", subject_hint="User", tags=tags)
        for memory in as_list(bot.get("memories")):
            category, tags = _category(as_text(memory))
            sink.add_facts(as_text(memory), category=category, source=GROK_BOT, source_label=f"Grok Bot {name}",
                           ref=f"{ref}:{name}:memory", tags=(*tags, "agent_note"))
        instructions = as_text(bot.get("instructions"))
        if instructions.strip():
            sink.add_facts(instructions, category="preference", source=GROK_BOT,
                           source_label=f"Grok Bot {name}: its rules", ref=f"{ref}:{name}:rules",
                           tags=("instruction",))
        left_out = left_out or any(as_list(bot.get(key)) for key in ("skills", "routines", "apps"))
    if left_out:
        sink.skip("Grok Bot export", "Its skills, routines and apps are not memories, so they were left out.")
    sink.warn("A Grok Bot wrote this export itself: check each memory before importing it.")


def read_grok_bot_file(member: Member, sink: Sink) -> None:
    with member.open() as raw:
        data = json.loads(raw.read(MAX_MEMORY_FILE_BYTES).decode("utf-8-sig", "replace"))
    read_grok_bot_export(data, sink, ref=f"grok-bot:{member.base}")


# ── Chats ────────────────────────────────────────────────────────────────────

def _read_only(path: Path) -> closing[sqlite3.Connection]:
    """The picked database, read-only and treated as unchanging: nothing beside it is created or written."""
    return closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True))


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 (fixed names)


def iter_hermes_db(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    assert member.path is not None
    with _read_only(member.path) as conn:
        columns = _columns(conn, "messages")
        visible = " AND ".join(
            clause for column, clause in (("compacted", "COALESCE(compacted, 0) = 0"), ("active", "COALESCE(active, 1) = 1"))
            if column in columns
        ) or "1"
        sessions = conn.execute("SELECT id, title, started_at, ended_at FROM sessions ORDER BY started_at").fetchall()
        for session_id, title, started, ended in sessions:
            rows = conn.execute(
                f"SELECT role, content, timestamp FROM messages WHERE session_id = ? AND {visible} "  # noqa: S608
                "AND role IN ('user', 'assistant') ORDER BY timestamp, id", (session_id,),
            ).fetchall()
            messages = [ImportedMessage(role, as_text(content).strip(), to_utc(stamp)) for role, content, stamp in rows]
            conversation = build_conversation(HERMES, as_text(session_id), as_text(title) or None,
                                              to_utc(started), to_utc(ended), messages)
            if conversation is not None:
                yield conversation


def iter_hermes_sessions(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    for record in _jsonl(member):
        messages = [ImportedMessage(as_text(m.get("role")), _content(m.get("content")), to_utc(m.get("timestamp")))
                    for m in map(as_dict, as_list(record.get("messages")))
                    if as_text(m.get("role")) in {"user", "assistant"} and not m.get("compacted")]
        conversation = build_conversation(HERMES, as_text(record.get("id")), as_text(record.get("title")) or None,
                                          to_utc(record.get("started_at")), to_utc(record.get("ended_at")), messages)
        if conversation is not None:
            yield conversation


def _jsonl(member: Member) -> Iterator[dict[str, Any]]:
    with member.open() as raw:
        for line in raw:
            line = line.strip()
            if line:
                try:
                    record = json.loads(line.decode("utf-8-sig", "replace"))
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record


def _content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    return "\n".join(as_text(as_dict(block).get("text")) for block in as_list(content)
                     if as_text(as_dict(block).get("type")) in {"text", "input_text", "output_text", ""}).strip()


def _openclaw_conversation(session_id: str, events: list[dict[str, Any]]) -> ImportedConversation | None:
    """The chat on the newest branch of an OpenClaw transcript's event tree."""
    header = next((event for event in events if event.get("type") == "session"), {})
    by_id = {as_text(event.get("id")): event for event in events if event.get("id") is not None}
    leaf = next((event for event in reversed(events) if event.get("type") == "message"), None)
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    while leaf is not None and as_text(leaf.get("id")) not in seen:
        seen.add(as_text(leaf.get("id")))
        chain.append(leaf)
        leaf = by_id.get(as_text(leaf.get("parentId")))
    messages = []
    for event in reversed(chain):
        body = as_dict(event.get("message")) or event
        role = as_text(body.get("role"))
        if event.get("type") == "message" and role in {"user", "assistant"}:
            messages.append(ImportedMessage(role, _content(body.get("content")),
                                            to_utc(body.get("timestamp") or event.get("timestamp"))))
    return build_conversation(OPENCLAW, as_text(header.get("id")) or session_id, None,
                              to_utc(header.get("timestamp")), None, messages)


def iter_openclaw_transcript(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    conversation = _openclaw_conversation(PurePosixPath(member.name).stem, list(_jsonl(member)))
    if conversation is not None:
        yield conversation


def iter_openclaw_db(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    assert member.path is not None
    with _read_only(member.path) as conn:
        columns = _columns(conn, "transcript_events")
        session = next((name for name in ("session_id", "session_key", "session") if name in columns), None)
        if session is None or not ({"event_json", "event_zstd"} & columns):
            sink.warn(f"{member.display} is an OpenClaw database in a layout Row-Bot doesn't know; its chats were "
                      "not read. Add the agent's sessions .jsonl files instead.")
            return
        order = "seq" if "seq" in columns else "rowid"
        payload = ", ".join(name if name in columns else f"NULL AS {name}" for name in ("event_json", "event_zstd"))
        decompress = _zstd()
        skipped = 0
        current: str | None = None
        events: list[dict[str, Any]] = []
        rows = conn.execute(f"SELECT {session}, {payload} FROM transcript_events ORDER BY {session}, {order}")  # noqa: S608
        for key, text, packed in rows:
            if key != current:
                if current is not None:
                    found = _openclaw_conversation(as_text(current), events)
                    if found is not None:
                        yield found
                current, events = key, []
            if text is None and packed is not None:
                if decompress is None:
                    skipped += 1
                    continue
                try:
                    text = decompress(packed).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 (a damaged frame: that event is left out)
                    skipped += 1
                    continue
            try:
                event = json.loads(text or "")
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if current is not None:
            found = _openclaw_conversation(as_text(current), events)
            if found is not None:
                yield found
        if skipped:
            sink.warn(f"{skipped} compressed chat events in {member.display} could not be read on this computer.")


def _zstd():
    try:
        import zstandard
    except ImportError:
        return None
    return lambda packed: zstandard.ZstdDecompressor().decompressobj().decompress(packed)
