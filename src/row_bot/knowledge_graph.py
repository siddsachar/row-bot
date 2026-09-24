"""Personal Knowledge Graph — entity-relation graph with SQLite + NetworkX.

Replaces the flat ``memories`` table with a connected graph of **entities**
(people, places, facts, preferences, events, projects, …) and **relations**
(edges like ``father_of``, ``lives_in``, ``works_on``).

Architecture
~~~~~~~~~~~~
* **SQLite** is the durable store (WAL mode, same ``~/.row-bot/memory.db``).
* **NetworkX** ``MultiDiGraph`` is a process-local mirror refreshed against
  SQLite's source revision when graph traversal is requested.
* **FAISS** flat numeric generations serve semantic recall only after safe
  decoding, embedding fingerprint and complete source coverage checks. SQLite
  records pending projection work and atomically selects each generation.

Migration
~~~~~~~~~
On first import the module checks for a legacy ``memories`` table and
migrates every row into an ``entities`` row, preserving IDs, timestamps,
and all content.  The old table is renamed to ``memories_v35_backup`` so
data is never lost.

Public API is consumed by ``memory.py`` (thin backward-compatible wrapper),
``tools/memory_tool.py``, ``memory_extraction.py``, and ``agent.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import threading

import networkx as nx
import numpy as np

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# Lock protecting the in-memory NetworkX graph.  Multiple threads
# (live saves, extraction timer, dream daemon) mutate the graph
# concurrently.  RLock is used because nested calls exist (e.g.
# _dedup_and_save → save_memory → save_entity → add_relation).
_graph_lock = threading.RLock()

# Legacy compatibility override for callers/tests that explicitly own repair.
# Shipped extraction callers use context-local projection_batch instead.
_skip_reindex = False

# PDF extraction and web scraping can introduce lone UTF-16 surrogates
# (U+D800–U+DFFF) into text.  These are valid in Python str but
# invalid in strict UTF-8, causing orjson (NiceGUI) to crash on encode.
_SURROGATE_RE = re.compile('[\ud800-\udfff]')


def _sanitize_text(s: str) -> str:
    """Strip lone UTF-16 surrogates that are invalid in strict UTF-8."""
    return _SURROGATE_RE.sub('', s) if s else s


def extract_json_block(text: str, bracket: str = "[") -> str | None:
    """Extract the first balanced JSON array or object from *text*.

    Uses bracket-counting instead of greedy regex so nested structures
    (e.g. ``[[1,2],[3,4]]``) are matched correctly and stray brackets
    in surrounding prose are ignored.

    Parameters
    ----------
    text : str
        Raw LLM response that may contain a JSON block.
    bracket : str
        ``'['`` to find an array, ``'{'`` to find an object.

    Returns the matched substring (valid for ``json.loads``) or ``None``.
    """
    open_br = bracket
    close_br = "]" if bracket == "[" else "}"
    start = text.find(open_br)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(text)):
        ch = text[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            if in_string:
                escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_br:
            depth += 1
        elif ch == close_br:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None  # unbalanced brackets

# ── Data directory ───────────────────────────────────────────────────────────
_DATA_DIR = get_row_bot_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_DATA_DIR / "memory.db")
_VECTOR_DIR = _DATA_DIR / "memory_vectors"

# Entity types — superset of the old memory categories.  Open string: the
# LLM can use any of these, but we guide it toward the canonical set.
VALID_ENTITY_TYPES = {
    "person", "preference", "fact", "event", "place", "project",
    "organisation", "concept", "skill", "media", "self_knowledge",
}

# Keep backward compat alias
VALID_CATEGORIES = VALID_ENTITY_TYPES

# Controlled vocabulary of allowed relation types.
# Any relation created with a type NOT in this set will be logged as a
# warning (but still accepted to avoid breaking existing data).
# Dream inference uses this to reject vague types.
VALID_RELATION_TYPES = {
    # Family / social
    "knows", "friend_of", "colleague_of", "boss_of", "mentor_of",
    "mother_of", "father_of", "sibling_of", "married_to", "child_of",
    "partner_of", "parent_of", "family_member_of", "cousin_of",
    # Location
    "lives_in", "works_at", "located_in", "born_in", "visits",
    "based_in", "headquarters_in",
    # Work / organisational
    "works_on", "manages", "member_of", "part_of", "employed_by",
    "founded", "leads", "reports_to", "manager_of",
    # Preference / interest
    "prefers", "enjoys", "dislikes", "interested_in", "has_hobby",
    # Temporal
    "deadline_for", "scheduled_for", "started_on", "completed_on",
    # Knowledge / skill
    "studies", "proficient_in", "certified_in", "learning",
    "has_skill", "teaches",
    # Media
    "reading", "watching", "recommends", "authored", "listening_to",
    # General / ownership
    "uses", "created_by", "owns", "has_pet", "pet_of",
    "treats", "attends", "participates_in",
    # Auto-link system types
    "related_to", "associated_with", "has_event",
    # Document extraction types
    "extracted_from", "uploaded",
    "builds_on", "cites", "extends", "contradicts",
}

# Aliases map common LLM-produced variants to their canonical form.
# Checked *before* the VALID_RELATION_TYPES warning so normalised types
# never produce a warning.
_RELATION_ALIASES: dict[str, str] = {
    # is_X_of → X_of
    "is_father_of": "father_of",
    "is_mother_of": "mother_of",
    "is_sibling_of": "sibling_of",
    "is_child_of": "child_of",
    "is_parent_of": "parent_of",
    "is_friend_of": "friend_of",
    "is_colleague_of": "colleague_of",
    "is_boss_of": "boss_of",
    "is_mentor_of": "mentor_of",
    "is_member_of": "member_of",
    "is_part_of": "part_of",
    "is_pet_of": "pet_of",
    # Synonym mapping
    "works_for": "employed_by",
    "employed_at": "employed_by",
    "resides_in": "lives_in",
    "living_in": "lives_in",
    "likes": "enjoys",
    "hates": "dislikes",
    "wrote": "authored",
    "written_by": "authored",
    "reads": "reading",
    "watches": "watching",
    "listens_to": "listening_to",
    "skilled_in": "proficient_in",
    "expert_in": "proficient_in",
    "located_at": "located_in",
    "situated_in": "located_in",
    "managed_by": "reports_to",
    "supervised_by": "reports_to",
    "supervises": "manages",
    "head_of": "leads",
    "leading": "leads",
    "belongs_to": "part_of",
    "affiliated_with": "member_of",
    "lover_of": "partner_of",
    "spouse_of": "married_to",
    "husband_of": "married_to",
    "wife_of": "married_to",
    "visited": "visits",
    "visiting": "visits",
    "founded_by": "founded",
    "created": "created_by",
    "made_by": "created_by",
    "participates": "participates_in",
    "attending": "attends",
    "attended": "attends",
    "studying": "studies",
    "studied": "studies",
    "teaching": "teaches",
    "taught": "teaches",
    "owns_pet": "has_pet",
    "interested": "interested_in",
    "hobby": "has_hobby",
    "has_interest": "interested_in",
    # Document extraction types
    "extracted_from": "extracted_from",
    "uploaded": "uploaded",
    "published_by": "authored",
    "implements": "uses",
    "used_by": "uses",
    "references": "cites",
}


def normalize_relation_type(relation_type: str) -> str:
    """Map *relation_type* to its canonical form.

    1. Exact match in ``_RELATION_ALIASES`` → return mapped value.
    2. Strip ``is_`` or ``has_`` prefix and check ``VALID_RELATION_TYPES``.
    3. Otherwise return the original (unchanged).
    """
    rt = relation_type.lower().strip().replace(" ", "_")
    # 1. Explicit alias
    if rt in _RELATION_ALIASES:
        return _RELATION_ALIASES[rt]
    # Already valid — no change needed
    if rt in VALID_RELATION_TYPES:
        return rt
    # 2. Strip is_ / has_ prefix
    for prefix in ("is_", "has_"):
        if rt.startswith(prefix):
            stripped = rt[len(prefix):]
            if stripped in VALID_RELATION_TYPES:
                return stripped
    return rt





# ═════════════════════════════════════════════════════════════════════════════
# Wiki vault hooks (fire-and-forget — never block graph operations)
# ═════════════════════════════════════════════════════════════════════════════

def _wiki_export_entity(entity: dict) -> None:
    """Best-effort immediate projection; unacknowledged work remains durable."""
    if _skip_reindex or _projection_batch.get() is not None:
        return
    try:
        from row_bot import wiki_vault
        if not wiki_vault.is_enabled():
            return
        conn = _get_conn()
        try:
            item = conn.execute("SELECT revision FROM knowledge_projection_work WHERE entity_id=?",
                                (entity["id"],)).fetchone()
        finally:
            conn.close()
        current = get_entity(entity["id"])
        if current is None:
            return
        result = wiki_vault.export_entity_projection(current)
        if not result.complete:
            return
        if item is not None:
            _ack_wiki_work(entity["id"], item[0])
    except Exception:
        logger.debug("Wiki projection remains pending", exc_info=True)


def _wiki_delete_entity(entity: dict) -> None:
    """Synchronous ownership-aware retirement; callers can retry failures."""
    from row_bot import wiki_vault
    if not wiki_vault.is_enabled():
        return
    conn = _get_conn()
    try:
        item = conn.execute("SELECT revision FROM knowledge_projection_work WHERE entity_id=?",
                            (entity["id"],)).fetchone()
    finally:
        conn.close()
    wiki_vault.delete_entity_md(entity, only_if_entity_absent=True)
    if item is not None:
        _ack_wiki_work(entity["id"], item[0])


# ═════════════════════════════════════════════════════════════════════════════
# SQLite schema & connection
# ═════════════════════════════════════════════════════════════════════════════

def _get_conn() -> sqlite3.Connection:
    """Return a connection with WAL mode and row-factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_FTS_TABLE = "entities_fts"


def _ensure_fts(conn: sqlite3.Connection | None = None) -> bool:
    """Ensure the optional FTS5 lexical index exists."""
    own_conn = conn is None
    conn = conn or _get_conn()
    try:
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(
                entity_id UNINDEXED,
                subject,
                aliases,
                tags,
                description,
                tokenize = 'unicode61 remove_diacritics 2'
            )
        """)
        if own_conn:
            conn.commit()
        return True
    except sqlite3.OperationalError as exc:
        logger.debug("Memory FTS unavailable: %s", exc)
        return False
    finally:
        if own_conn:
            conn.close()


def _delete_fts_rows(conn: sqlite3.Connection, entity_id: str) -> None:
    rows = conn.execute(
        f"SELECT rowid FROM {_FTS_TABLE} WHERE entity_id = ?",
        (entity_id,),
    ).fetchall()
    for row in rows:
        conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE rowid = ?", (row[0],))


def _upsert_fts_entity(entity: dict) -> None:
    """Compatibility hook: canonical entity triggers publish lexical rows."""


def _delete_fts_entity(entity_id: str) -> None:
    """Compatibility hook: deletion and lexical retirement share a transaction."""


def _clear_fts_index() -> None:
    conn = _get_conn()
    try:
        if not _ensure_fts(conn):
            return
        conn.execute(f"DELETE FROM {_FTS_TABLE}")
        conn.commit()
    except Exception as exc:
        logger.debug("Memory FTS clear skipped: %s", exc)
    finally:
        conn.close()


def rebuild_fts_index(*, validate: Callable[[], None] | None = None) -> int:
    """Reconcile lexical rows in one source transaction, or report failure."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        if not _ensure_fts(conn):
            raise KnowledgeProjectionIncomplete("Memory lexical indexing is unavailable")
        conn.execute(f"DELETE FROM {_FTS_TABLE}")
        conn.execute(f"""INSERT INTO {_FTS_TABLE}(rowid,entity_id,subject,aliases,tags,description)
            SELECT rowid,id,subject,aliases,tags,description FROM entities""")
        count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        conn.execute("UPDATE knowledge_projection_state SET lexical_error=NULL WHERE singleton=1")
        if validate is not None:
            validate()
        conn.commit()
        return count
    finally:
        conn.close()


def _lexical_projection_current() -> bool:
    """Validate source/lexical correspondence without rewriting current rows."""
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        if _projection_state(conn)["lexical_error"] is not None:
            return False
        mismatch = conn.execute("""SELECT EXISTS(
            SELECT 1 FROM entities e LEFT JOIN entities_fts f ON f.rowid=e.rowid
            WHERE f.entity_id IS NOT e.id OR f.subject IS NOT e.subject
                OR f.aliases IS NOT e.aliases OR f.tags IS NOT e.tags
                OR f.description IS NOT e.description
        ) OR (SELECT COUNT(*) FROM entities)!=(SELECT COUNT(*) FROM entities_fts)""").fetchone()[0]
        return not mismatch
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _ensure_fts_populated() -> None:
    conn = _get_conn()
    try:
        if not _ensure_fts(conn):
            return
        entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        fts_count = conn.execute(f"SELECT COUNT(*) FROM {_FTS_TABLE}").fetchone()[0]
    except Exception as exc:
        logger.debug("Memory FTS population check skipped: %s", exc)
        return
    finally:
        conn.close()

    if entity_count and not fts_count:
        rebuild_fts_index()


def _fts_match_query(query: str) -> str:
    terms = []
    try:
        terms = _keyword_terms(query)
    except NameError:
        terms = [
            term.strip("'-_").lower()
            for term in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_'-]*", query or "")
            if len(term.strip("'-_")) >= 3
        ]
    unique_terms = list(dict.fromkeys(terms))[:8]
    return " OR ".join(f'"{term}"' for term in unique_terms)


def fts_search_entities(query: str, limit: int = 20) -> list[dict]:
    """Return no-touch lexical candidates from the optional FTS5 index."""
    match_query = _fts_match_query(query)
    if not match_query:
        return []

    conn = _get_conn()
    try:
        if not _ensure_fts(conn):
            return []
        rows = conn.execute(
            f"""
            SELECT entity_id, bm25({_FTS_TABLE}, 0.0, 8.0, 5.0, 3.0, 1.0) AS bm25_score
            FROM {_FTS_TABLE}
            WHERE {_FTS_TABLE} MATCH ?
            ORDER BY bm25_score
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.debug("Memory FTS search skipped: %s", exc)
        return []
    finally:
        conn.close()

    hits: list[dict] = []
    total = max(1, len(rows))
    for rank, row in enumerate(rows):
        entity = get_entity(row["entity_id"])
        if not entity:
            continue
        bm25_norm = 1.0 - (rank / total)
        entity["bm25_raw"] = float(row["bm25_score"] or 0.0)
        entity["bm25_score"] = round(max(0.0, min(1.0, bm25_norm)), 4)
        hits.append(entity)
    return hits


# Canonical SQLite records pending projection work and the one selected vector
# generation. Projection files are immutable, rebuildable derived data.
_projection_lock = threading.RLock()
_PROJECTION_METADATA_BYTES = 32 * 1024 * 1024
_PROJECTION_ENTITY_BYTES = 2 * 1024 * 1024
_PROJECTION_BATCH_BYTES = 8 * 1024 * 1024
_SEMANTIC_TEXT_VERSION = 2
_generation_read_state = threading.local()


def _lock_generation_handle(handle, *, exclusive: bool, blocking: bool):
    """Return an unlock callback, or None for a contended nonblocking lease."""
    if os.name != "nt":
        import errno
        import fcntl

        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(handle.fileno(), flags | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            if not blocking and exc.errno in {errno.EAGAIN, errno.EACCES}:
                return None
            raise
        return lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    native = msvcrt.get_osfhandle(handle.fileno())
    overlap = Overlapped()
    flags = (2 if exclusive else 0) | (0 if blocking else 1)
    if not kernel.LockFileEx(native, flags, 0, 1, 0, ctypes.byref(overlap)):
        error = ctypes.get_last_error()
        if not blocking and error == 33:  # ERROR_LOCK_VIOLATION
            return None
        raise ctypes.WinError(error)

    def unlock():
        if not kernel.UnlockFileEx(native, 0, 1, 0, ctypes.byref(overlap)):
            raise ctypes.WinError(ctypes.get_last_error())

    return unlock


@contextmanager
def _generation_access(*, exclusive: bool = False, blocking: bool = True):
    """Protect immutable readers/builders across processes; never delete the lock."""
    depth = getattr(_generation_read_state, "depth", 0)
    if depth:
        # A writer cannot retire a generation still used by its own call stack.
        yield not exclusive
        return
    _VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _VECTOR_DIR / ".generation-readers.lock"
    if lock_path.is_symlink():
        raise KnowledgeProjectionIncomplete("Knowledge generation lock requires recovery")
    with lock_path.open("a+b") as handle:
        if os.fstat(handle.fileno()).st_nlink != 1:
            raise KnowledgeProjectionIncomplete("Knowledge generation lock ownership is ambiguous")
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        unlock = _lock_generation_handle(handle, exclusive=exclusive, blocking=blocking)
        if unlock is None:
            yield False
            return
        _generation_read_state.depth = 0 if exclusive else 1
        try:
            yield True
        finally:
            _generation_read_state.depth = depth
            unlock()


@dataclass
class ProjectionBatch:
    """Outcome of one explicitly bounded, context-local projection drain."""

    result: dict[str, object] | None = None


_projection_batch: ContextVar[ProjectionBatch | None] = ContextVar("knowledge_projection_batch", default=None)


@contextmanager
def projection_batch(*, max_entities: int = 256, cancelled: Callable[[], bool] | None = None,
                     drain_on_exit: bool = True) -> Iterator[ProjectionBatch]:
    """Coalesce upsert/wiki attempts; source commits and removals remain immediate."""
    if type(max_entities) is not int or not 1 <= max_entities <= 1000:
        raise ValueError("max_entities must be between 1 and 1000")
    if type(drain_on_exit) is not bool:
        raise ValueError("drain_on_exit must be a boolean")
    existing = _projection_batch.get()
    if existing is not None:
        yield existing
        return
    batch = ProjectionBatch()
    token = _projection_batch.set(batch)
    try:
        yield batch
    except BaseException:
        # Source work was admitted transactionally and remains available to retry.
        raise
    else:
        _projection_batch.reset(token)
        token = None
        if drain_on_exit:
            batch.result = repair_projections(max_entities=max_entities, cancelled=cancelled)
    finally:
        if token is not None:
            _projection_batch.reset(token)


class KnowledgeProjectionIncomplete(RuntimeError):
    """Canonical knowledge is saved but a required projection is incomplete."""


def _initialize_projections(conn: sqlite3.Connection, *, lexical: bool) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_projection_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL DEFAULT 0,
        generation TEXT,
        vector_revision INTEGER NOT NULL DEFAULT -1,
        vector_error TEXT,
        lexical_error TEXT
    )""")
    fresh = conn.execute("INSERT OR IGNORE INTO knowledge_projection_state(singleton) VALUES(1)").rowcount == 1
    conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_vector_generations (
        generation TEXT PRIMARY KEY,
        previous_generation TEXT,
        manifest_hash TEXT,
        retiring INTEGER NOT NULL DEFAULT 0,
        validated_segments INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_projection_work (
        entity_id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        semantic_pending INTEGER NOT NULL DEFAULT 1,
        wiki_pending INTEGER NOT NULL DEFAULT 1,
        deleted_type TEXT,
        deleted_source TEXT
    )""")
    # Initial installed-data admission is inert: no embedding/model/wiki work.
    conn.execute("""INSERT OR IGNORE INTO knowledge_projection_work(entity_id,revision)
        SELECT id,(SELECT revision FROM knowledge_projection_state WHERE singleton=1)
        FROM entities WHERE (SELECT generation FROM knowledge_projection_state WHERE singleton=1) IS NULL""")
    for operation, row in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        deleted_type = "OLD.entity_type" if operation == "DELETE" else "NULL"
        deleted_source = "OLD.source" if operation == "DELETE" else "NULL"
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS knowledge_entity_{operation.lower()}
            AFTER {operation} ON entities BEGIN
            UPDATE knowledge_projection_state SET revision=revision+1 WHERE singleton=1;
            INSERT INTO knowledge_projection_work(entity_id,revision,semantic_pending,wiki_pending,deleted_type,deleted_source)
            VALUES({row}.id,(SELECT revision FROM knowledge_projection_state WHERE singleton=1),1,1,{deleted_type},{deleted_source})
            ON CONFLICT(entity_id) DO UPDATE SET revision=excluded.revision,
                semantic_pending=1,wiki_pending=1,deleted_type=excluded.deleted_type,deleted_source=excluded.deleted_source;
            END""")
        endpoints = (("OLD", "NEW") if operation == "UPDATE" else (row,))
        work = ""
        for endpoint in endpoints:
            for field in ("source_id", "target_id"):
                work += f"""INSERT INTO knowledge_projection_work(entity_id,revision,semantic_pending,wiki_pending)
                    VALUES({endpoint}.{field},(SELECT revision FROM knowledge_projection_state WHERE singleton=1),0,1)
                    ON CONFLICT(entity_id) DO UPDATE SET revision=excluded.revision,wiki_pending=1;\n"""
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS knowledge_relation_{operation.lower()}
            AFTER {operation} ON relations BEGIN
            UPDATE knowledge_projection_state SET revision=revision+1 WHERE singleton=1;
            {work} END""")
    if lexical:
        if fresh:
            conn.execute("DELETE FROM entities_fts")
            conn.execute("""INSERT INTO entities_fts(rowid,entity_id,subject,aliases,tags,description)
                SELECT rowid,id,subject,aliases,tags,description FROM entities""")
        fields = "rowid,entity_id,subject,aliases,tags,description"
        values = "NEW.rowid,NEW.id,NEW.subject,NEW.aliases,NEW.tags,NEW.description"
        for operation in ("INSERT", "UPDATE", "DELETE"):
            deletion = "" if operation == "INSERT" else "DELETE FROM entities_fts WHERE rowid=OLD.rowid;"
            insertion = "" if operation == "DELETE" else f"INSERT INTO entities_fts({fields}) VALUES({values});"
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS knowledge_fts_{operation.lower()}
                AFTER {operation} ON entities BEGIN
                {deletion}
                {insertion} END""")
    conn.execute("UPDATE knowledge_projection_state SET lexical_error=? WHERE singleton=1",
                 (None if lexical else "fts_unavailable",))


def _projection_state(conn: sqlite3.Connection | None = None) -> dict:
    owned = conn is None
    conn = conn or _get_conn()
    try:
        row = conn.execute("SELECT * FROM knowledge_projection_state WHERE singleton=1").fetchone()
        if row is None:
            raise KnowledgeProjectionIncomplete("Knowledge projection state is unavailable")
        return dict(row)
    finally:
        if owned:
            conn.close()


def _semantic_hash(entity: dict) -> str:
    return hashlib.sha256(_entity_text(entity).encode("utf-8")).hexdigest()


def _projection_file(directory: pathlib.Path, name: str, limit: int) -> bytes:
    resolved = (directory / name).resolve(strict=True)
    if resolved.parent != directory.resolve(strict=True):
        raise ValueError("Knowledge projection file escapes generation")
    with resolved.open("rb") as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Knowledge projection exceeds publication budget")
    return value


def _read_generation_segment(directory: pathlib.Path, segment: dict, dimension: int):
    from row_bot.flat_vector_storage import MAX_VECTOR_BYTES, decode_flat_vectors

    data = _projection_file(directory, segment["name"], MAX_VECTOR_BYTES)
    if hashlib.sha256(data).hexdigest() != segment["sha256"]:
        raise ValueError("Knowledge vector and metadata publication disagree")
    return decode_flat_vectors(data, expected_dimension=dimension,
                               expected_count=segment["count"], expected_metric="inner_product")


def _load_vector_generation(state: dict, *, fingerprint: dict | None = None, validate_vectors: bool = True,
                            retiring: bool = False):
    generation = state.get("generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
        raise ValueError("No validated knowledge vector generation")
    root = _VECTOR_DIR / "generations"
    directory = root / (f".retiring-{generation}" if retiring else generation)
    if directory.resolve(strict=True).parent != root.resolve(strict=True):
        raise ValueError("Knowledge generation escapes index owner")
    metadata = json.loads(_projection_file(directory, "manifest.json", _PROJECTION_METADATA_BYTES))
    if (type(metadata) is not dict or metadata.get("version") != 1
            or metadata.get("text_version") != _SEMANTIC_TEXT_VERSION
            or metadata.get("generation") != generation):
        raise ValueError("Knowledge generation metadata is incompatible")
    stored = metadata.get("embedding")
    if type(stored) is not dict or (fingerprint is not None and stored != fingerprint):
        raise ValueError("Knowledge generation embedding fingerprint changed")
    identifiers, hashes, segments = metadata.get("ids"), metadata.get("source_hashes"), metadata.get("segments")
    if (type(identifiers) is not list or type(hashes) is not list or type(segments) is not list
            or len(identifiers) != len(hashes)
            or any(type(value) is not str or not value for value in identifiers)
            or len(set(identifiers)) != len(identifiers) or identifiers != sorted(identifiers)
            or any(type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes)):
        raise ValueError("Knowledge generation coverage is invalid")
    offset = 0
    for number, segment in enumerate(segments):
        if (type(segment) is not dict or segment.get("name") != f"segment-{number:06d}.faiss"
                or type(segment.get("count")) is not int or not 0 < segment["count"] <= 2000
                or type(segment.get("start")) is not int or segment["start"] != offset
                or type(segment.get("sha256")) is not str or not re.fullmatch(r"[a-f0-9]{64}", segment["sha256"])):
            raise ValueError("Knowledge vector segment coverage is invalid")
        if validate_vectors:
            _read_generation_segment(directory, segment, stored.get("dimension"))
        offset += segment["count"]
    if offset != len(identifiers):
        raise ValueError("Knowledge segment and identity counts disagree")
    return metadata, directory


def _retirement_protected(conn: sqlite3.Connection) -> tuple[str, str]:
    selected = _projection_state(conn)["generation"] or ""
    row = conn.execute("SELECT previous_generation FROM knowledge_vector_generations WHERE generation=?",
                       (selected,)).fetchone()
    previous = (row[0] if row else None) or ""
    return selected, previous


def _retirement_candidates(conn: sqlite3.Connection, limit: int | None = None):
    selected, previous = _retirement_protected(conn)
    suffix = " LIMIT ?" if limit is not None else ""
    params = (selected, previous, limit) if limit is not None else (selected, previous)
    return conn.execute("""SELECT * FROM knowledge_vector_generations
        WHERE generation!=? AND generation!=? ORDER BY attempts,rowid""" + suffix, params)


def _retirement_pending() -> int:
    conn = _get_conn()
    try:
        return conn.execute("""SELECT COUNT(*) FROM knowledge_vector_generations
            WHERE generation!=? AND generation!=?""", _retirement_protected(conn)).fetchone()[0]
    finally:
        conn.close()


def _retire_vector_generations(*, max_generations: int = 8, cancelled=None) -> dict[str, object]:
    """Bounded retirement of registered obsolete projections, with reader leases.

    Unknown, legacy, modified and malformed bytes stay in place. Retirement
    first captures the whole obsolete directory with an exclusive rename; its
    durable registry record and unchanged manifest support interrupted retries.
    """
    from row_bot.document_jobs import _rename_source_no_replace
    from row_bot.flat_vector_storage import MAX_VECTOR_BYTES

    if type(max_generations) is not int or not 1 <= max_generations <= 32:
        raise ValueError("Generation retirement limit must be between 1 and 32")
    retired, failures, byte_count, file_count = 0, [], 0, 0
    with _generation_access(exclusive=True, blocking=False) as acquired:
        if not acquired:
            return {"retired": 0, "pending": _retirement_pending(), "deferred": "readers_active", "failures": []}
        conn = _get_conn()
        try:
            work = [dict(row) for row in _retirement_candidates(conn, max_generations)]
        finally:
            conn.close()
        for item in work:
            if byte_count >= MAX_VECTOR_BYTES or file_count >= 16:
                break
            generation = item["generation"]
            conn = _get_conn()
            try:
                _cancel_projection(cancelled)
                conn.execute("UPDATE knowledge_vector_generations SET attempts=attempts+1 WHERE generation=?", (generation,))
                _cancel_projection(cancelled)
                conn.commit()
            finally:
                conn.close()
            if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
                failures.append("unverified_generation")
                continue
            try:
                # Recheck canonical selection before marking retirement. No SQL
                # lock is held while waiting for the process-wide reader lease.
                conn = _get_conn()
                try:
                    _cancel_projection(cancelled)
                    conn.execute("BEGIN IMMEDIATE")
                    if generation in _retirement_protected(conn):
                        continue
                    _cancel_projection(cancelled)
                    conn.execute("UPDATE knowledge_vector_generations SET retiring=MAX(retiring,1) WHERE generation=?", (generation,))
                    _cancel_projection(cancelled)
                    conn.commit()
                finally:
                    conn.close()
                root = (_VECTOR_DIR / "generations").resolve(strict=True)
                original = root / generation
                captured = root / f".retiring-{generation}"
                if (original.is_symlink() or original.is_junction()
                        or captured.is_symlink() or captured.is_junction()):
                    raise ValueError("Generation ownership is ambiguous")
                if original.exists():
                    if captured.exists():
                        raise ValueError("Generation retirement names conflict")
                    if original.resolve(strict=True).parent != root:
                        raise ValueError("Generation retirement escapes its owner")
                directory = original if original.exists() else captured
                if not directory.exists():
                    if item["retiring"] == 3:
                        conn = _get_conn()
                        try:
                            _cancel_projection(cancelled)
                            conn.execute("DELETE FROM knowledge_vector_generations WHERE generation=?", (generation,))
                            _cancel_projection(cancelled)
                            conn.commit()
                        finally:
                            conn.close()
                        retired += 1
                        continue
                    raise ValueError("Registered generation is missing")
                if directory.resolve(strict=True).parent != root:
                    raise ValueError("Generation retirement escapes its owner")
                manifest_path = directory / "manifest.json"
                if item["retiring"] == 3 and not manifest_path.exists():
                    _cancel_projection(cancelled)
                    directory.rmdir()
                    conn = _get_conn()
                    try:
                        _cancel_projection(cancelled)
                        conn.execute("DELETE FROM knowledge_vector_generations WHERE generation=?", (generation,))
                        _cancel_projection(cancelled)
                        conn.commit()
                    finally:
                        conn.close()
                    retired += 1
                    continue
                data = _projection_file(directory, "manifest.json", _PROJECTION_METADATA_BYTES)
                if hashlib.sha256(data).hexdigest() != item["manifest_hash"]:
                    raise ValueError("Modified generation retained")
                metadata, _ = _load_vector_generation({"generation": generation}, validate_vectors=False,
                                                       retiring=directory == captured)
                expected = {"manifest.json", *(segment["name"] for segment in metadata["segments"])}
                actual = {path.name for path in directory.iterdir()}
                if actual - expected:
                    raise ValueError("Unrecognized generation files retained")
                if (item["retiring"] < 2 or directory == original) and actual != expected:
                    raise ValueError("Incomplete generation retained")
                if item["retiring"] < 2:
                    validated = item["validated_segments"]
                    if type(validated) is not int or not 0 <= validated <= len(metadata["segments"]):
                        raise ValueError("Generation validation progress is invalid")
                    for segment in metadata["segments"][validated:]:
                        path = directory / segment["name"]
                        if path.is_symlink() or path.stat().st_nlink != 1:
                            raise ValueError("Generation file ownership is ambiguous")
                        size = path.stat().st_size
                        if file_count >= 16 or byte_count + size > MAX_VECTOR_BYTES:
                            break
                        _read_generation_segment(directory, segment, metadata["embedding"].get("dimension"))
                        byte_count += size
                        file_count += 1
                        validated += 1
                        conn = _get_conn()
                        try:
                            _cancel_projection(cancelled)
                            conn.execute("UPDATE knowledge_vector_generations SET validated_segments=? WHERE generation=?",
                                         (validated, generation))
                            _cancel_projection(cancelled)
                            conn.commit()
                        finally:
                            conn.close()
                    if validated != len(metadata["segments"]):
                        break
                    conn = _get_conn()
                    try:
                        _cancel_projection(cancelled)
                        conn.execute("UPDATE knowledge_vector_generations SET retiring=2 WHERE generation=?", (generation,))
                        _cancel_projection(cancelled)
                        conn.commit()
                    finally:
                        conn.close()
                if directory == original:
                    _cancel_projection(cancelled)
                    _rename_source_no_replace(original, captured)
                    directory = captured
                    manifest_path = captured / "manifest.json"
                    if (captured.is_symlink() or captured.is_junction() or captured.resolve(strict=True).parent != root
                            or {path.name for path in captured.iterdir()} != expected
                            or hashlib.sha256(_projection_file(captured, "manifest.json", _PROJECTION_METADATA_BYTES)).hexdigest()
                            != item["manifest_hash"]):
                        raise ValueError("Generation changed during retirement capture")
                remaining = False
                for segment in metadata["segments"]:
                    path = captured / segment["name"]
                    if not path.exists():
                        continue
                    if path.is_symlink() or path.stat().st_nlink != 1:
                        raise ValueError("Generation file ownership is ambiguous")
                    size = path.stat().st_size
                    if file_count >= 16 or byte_count + size > MAX_VECTOR_BYTES:
                        remaining = True
                        break
                    before = path.stat()
                    _read_generation_segment(captured, segment, metadata["embedding"].get("dimension"))
                    after = path.stat()
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise ValueError("Generation changed during retirement")
                    _cancel_projection(cancelled)
                    path.unlink()
                    byte_count += size
                    file_count += 1
                if remaining:
                    break
                if manifest_path.is_symlink() or manifest_path.stat().st_nlink != 1:
                    raise ValueError("Generation manifest ownership is ambiguous")
                if hashlib.sha256(_projection_file(captured, "manifest.json", _PROJECTION_METADATA_BYTES)).hexdigest() != item["manifest_hash"]:
                    raise ValueError("Generation manifest changed during retirement")
                conn = _get_conn()
                try:
                    _cancel_projection(cancelled)
                    conn.execute("UPDATE knowledge_vector_generations SET retiring=3 WHERE generation=?", (generation,))
                    _cancel_projection(cancelled)
                    conn.commit()
                finally:
                    conn.close()
                _cancel_projection(cancelled)
                manifest_path.unlink()
                _cancel_projection(cancelled)
                captured.rmdir()  # Never recursively remove unknown content.
                conn = _get_conn()
                try:
                    _cancel_projection(cancelled)
                    conn.execute("DELETE FROM knowledge_vector_generations WHERE generation=?", (generation,))
                    _cancel_projection(cancelled)
                    conn.commit()
                finally:
                    conn.close()
                retired += 1
            except (OSError, ValueError, TypeError, KeyError):
                failures.append("generation_retirement_incomplete")
    return {"retired": retired, "pending": _retirement_pending(), "deferred": None, "failures": failures}


def _cancel_projection(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise KnowledgeProjectionIncomplete("Knowledge projection cancelled")


def _persist_projection_failure(code: str) -> None:
    conn = _get_conn()
    try:
        conn.execute("UPDATE knowledge_projection_state SET vector_error=? WHERE singleton=1", (code,))
        conn.commit()
    finally:
        conn.close()


def _projection_source_batches(conn: sqlite3.Connection, batch_size: int) -> Iterator[list[sqlite3.Row]]:
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError("Projection batch size must be between 1 and 256")
    # Check sizes in SQLite before materializing saved text in Python. Large
    # canonical records remain intact and explicitly require projection repair.
    fields = "id entity_type subject description aliases tags properties source created_at updated_at".split()
    size = "+".join(f"COALESCE(length(CAST({field} AS BLOB)),0)" for field in fields)
    cursor = conn.execute(f"SELECT rowid,({size}) AS byte_size FROM entities ORDER BY id")
    rows, byte_count = [], 0
    for item in cursor:
        if item["byte_size"] > _PROJECTION_ENTITY_BYTES:
            raise KnowledgeProjectionIncomplete("Saved knowledge exceeds the projection text budget")
        if rows and (len(rows) == batch_size or byte_count + item["byte_size"] > _PROJECTION_BATCH_BYTES):
            yield rows
            rows, byte_count = [], 0
        rows.append(conn.execute("SELECT * FROM entities WHERE rowid=?", (item["rowid"],)).fetchone())
        byte_count += item["byte_size"]
    if rows:
        yield rows


def _publish_vector_projection(*, cancelled=None, allow_embedding: bool = True,
                               max_embeddings: int | None = None,
                               entity_ids: set[str] | None = None) -> None:
    with _generation_access():
        if not _reuse_current_vector_projection(cancelled=cancelled):
            _build_vector_projection(cancelled=cancelled, allow_embedding=allow_embedding,
                                     max_embeddings=max_embeddings, entity_ids=entity_ids)
    _retire_vector_generations(**({"cancelled":cancelled} if cancelled is not None else {}))


def _reuse_current_vector_projection(*, cancelled=None) -> bool:
    """Acknowledge a verified semantic no-op under current source admission."""
    from row_bot.embedding_config import active_embedding_metadata

    _cancel_projection(cancelled)
    captured = _projection_state()
    status, loaded = _read_vector_readiness()
    if not status["ready"] or loaded is None:
        return False
    metadata, _directory = loaded
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _cancel_projection(cancelled)
        current = _projection_state(conn)
        if (current["revision"] != captured["revision"] or current["generation"] != captured["generation"]
                or metadata["embedding"] != active_embedding_metadata()):
            return False
        # Full-row/wiki changes (including recalled_at) keep their own pending
        # work; only validated complete semantic coverage can be acknowledged.
        conn.execute("UPDATE knowledge_projection_work SET semantic_pending=0 WHERE revision<=?",
                     (captured["revision"],))
        conn.execute("DELETE FROM knowledge_projection_work WHERE semantic_pending=0 AND wiki_pending=0")
        conn.execute("UPDATE knowledge_projection_state SET vector_revision=?,vector_error=NULL WHERE singleton=1",
                     (captured["revision"],))
        _cancel_projection(cancelled)
        conn.commit()
        return True
    finally:
        conn.close()


def _build_vector_projection(*, cancelled=None, allow_embedding: bool = True,
                               max_embeddings: int | None = None,
                               entity_ids: set[str] | None = None) -> None:
    """Build one immutable source cut and select it with SQLite write admission."""
    import faiss
    from row_bot.embedding_config import active_embedding_metadata, get_embedding_config
    from row_bot.flat_vector_storage import MAX_VECTOR_BYTES, MAX_VECTOR_DIMENSION

    with _projection_lock:
        _cancel_projection(cancelled)
        config = deepcopy(get_embedding_config())
        fingerprint = active_embedding_metadata(config)
        dimension = fingerprint["dimension"]
        if type(dimension) is not int or not 1 <= dimension <= MAX_VECTOR_DIMENSION:
            raise ValueError("Invalid knowledge embedding dimension")
        batch_size = config["batch_size"]
        previous_state = _projection_state()
        previous = None
        previous_rows = {}
        if previous_state["generation"]:
            try:
                old_metadata, previous = _load_vector_generation(previous_state, fingerprint=fingerprint, validate_vectors=False)
                previous_rows = {identifier: (index, old_metadata["source_hashes"][index])
                                 for index, identifier in enumerate(old_metadata["ids"])}
            except (OSError, ValueError, TypeError, KeyError):
                if not allow_embedding:
                    raise KnowledgeProjectionIncomplete("Existing knowledge vectors require recovery before removal")
                previous = None
        elif not allow_embedding:
            # No selected semantic projection ever exposed these entities.
            return

        generation = uuid.uuid4().hex
        directory = _VECTOR_DIR / "generations" / generation
        directory.mkdir(parents=True, exist_ok=False)
        segment_rows = min(2000, (MAX_VECTOR_BYTES - 64) // (dimension * 4))
        segments = []
        index = faiss.IndexFlatIP(dimension)
        previous_segment = None
        previous_segment_number = -1
        invalid_segments = set()
        old_starts = [item["start"] for item in old_metadata["segments"]] if previous is not None else []

        def prior_vector(position):
            nonlocal previous_segment, previous_segment_number
            import bisect
            number = bisect.bisect_right(old_starts, position) - 1
            if number in invalid_segments:
                return None
            if number != previous_segment_number:
                previous_segment = None
                try:
                    previous_segment = _read_generation_segment(previous, old_metadata["segments"][number], dimension)
                except (OSError, ValueError, TypeError, KeyError):
                    if not allow_embedding:
                        raise KnowledgeProjectionIncomplete("Existing knowledge vectors require recovery before removal") from None
                    invalid_segments.add(number)
                    return None
                previous_segment_number = number
            return previous_segment.vectors[position - old_starts[number]]

        def flush_segment():
            nonlocal index
            if not index.ntotal:
                return
            name = f"segment-{len(segments):06d}.faiss"
            faiss.write_index(index, str(directory / name))
            data = _projection_file(directory, name, MAX_VECTOR_BYTES)
            segment = {"name": name, "start": sum(item["count"] for item in segments),
                       "count": index.ntotal, "sha256": hashlib.sha256(data).hexdigest()}
            _read_generation_segment(directory, segment, dimension)
            segments.append(segment)
            index = faiss.IndexFlatIP(dimension)

        conn = _get_conn()
        ids, source_hashes = [], []
        coverage_bytes = 0
        embedding = None
        embedded_count = 0
        try:
            conn.execute("BEGIN")
            captured = _projection_state(conn)
            if captured["generation"] != previous_state["generation"]:
                raise KnowledgeProjectionIncomplete("Knowledge projection changed before rebuild")
            for rows in _projection_source_batches(conn, batch_size):
                _cancel_projection(cancelled)
                batch = []
                pending_texts = []
                for row in rows:
                    entity = dict(row)
                    digest = _semantic_hash(entity)
                    prior = previous_rows.get(entity["id"])
                    retained = prior_vector(prior[0]) if prior is not None and prior[1] == digest else None
                    if retained is not None:
                        batch.append((entity["id"], digest, retained, None))
                    elif (allow_embedding and (entity_ids is None or entity["id"] in entity_ids)
                          and (max_embeddings is None or embedded_count < max_embeddings)):
                        batch.append((entity["id"], digest, None, len(pending_texts)))
                        pending_texts.append(_entity_text(entity))
                        embedded_count += 1
                vectors = None
                if pending_texts:
                    embedding = embedding or _get_embedding_model(config=config)
                    raw = embedding.embed_documents(pending_texts)
                    _cancel_projection(cancelled)
                    vectors = np.asarray(raw, dtype=np.float32)
                    if vectors.shape != (len(pending_texts), dimension) or not np.isfinite(vectors).all():
                        raise ValueError("Knowledge embedding batch shape or values are invalid")
                    norms = np.linalg.norm(vectors.astype(np.float64), axis=1, keepdims=True)
                    norms[norms == 0] = 1
                    vectors = (vectors / norms).astype(np.float32)
                if batch:
                    added = np.empty((len(batch), dimension), dtype=np.float32)
                    for offset, (identifier, digest, old, pending) in enumerate(batch):
                        added[offset] = old if pending is None else vectors[pending]
                        ids.append(identifier)
                        source_hashes.append(digest)
                    coverage_bytes += sum(len(json.dumps(item[0], ensure_ascii=False).encode("utf-8")) + 70 for item in batch)
                    if coverage_bytes > _PROJECTION_METADATA_BYTES:
                        raise ValueError("Knowledge coverage metadata exceeds publication budget")
                    offset = 0
                    while offset < len(added):
                        remaining = segment_rows - index.ntotal
                        count = min(remaining, len(added) - offset)
                        index.add(added[offset:offset + count])
                        offset += count
                        if index.ntotal == segment_rows:
                            flush_segment()
        finally:
            conn.close()

        _cancel_projection(cancelled)
        flush_segment()
        metadata = {
            "version": 1, "text_version": _SEMANTIC_TEXT_VERSION,
            "generation": generation, "source_revision": captured["revision"],
            "embedding": fingerprint, "ids": ids, "source_hashes": source_hashes,
            "segments": segments,
        }
        encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > _PROJECTION_METADATA_BYTES:
            raise ValueError("Knowledge metadata exceeds publication budget")
        with (directory / "manifest.json").open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _load_vector_generation({"generation": generation}, fingerprint=fingerprint)
        _cancel_projection(cancelled)
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _cancel_projection(cancelled)
            current = _projection_state(conn)
            if (current["revision"] != captured["revision"]
                    or current["generation"] != captured["generation"]
                    or active_embedding_metadata() != fingerprint):
                raise KnowledgeProjectionIncomplete("Knowledge changed during projection; retry required")
            conn.execute("""UPDATE knowledge_projection_state SET generation=?,vector_revision=?,vector_error=NULL
                WHERE singleton=1""", (generation, captured["revision"]))
            conn.execute("""INSERT INTO knowledge_vector_generations(generation,previous_generation,manifest_hash)
                VALUES(?,?,?)""", (generation, captured["generation"], hashlib.sha256(encoded).hexdigest()))
            conn.execute("""UPDATE knowledge_projection_work SET semantic_pending=0
                WHERE revision<=? AND NOT EXISTS(SELECT 1 FROM entities WHERE id=entity_id)""",
                         (captured["revision"],))
            conn.executemany("""UPDATE knowledge_projection_work SET semantic_pending=0
                WHERE entity_id=? AND revision<=?""",
                             ((identifier, captured["revision"]) for identifier in ids))
            conn.execute("DELETE FROM knowledge_projection_work WHERE semantic_pending=0 AND wiki_pending=0")
            _cancel_projection(cancelled)
            conn.commit()
        finally:
            conn.close()


def rebuild_index(*, cancelled: Callable[[], bool] | None = None) -> None:
    try:
        _publish_vector_projection(cancelled=cancelled)
    except Exception:
        _persist_projection_failure("rebuild_incomplete")
        raise


def _upsert_index(entity_id: str) -> None:
    # Durable work is admitted by SQLite, even if this synchronous attempt fails.
    if _projection_batch.get() is not None:
        return
    try:
        _publish_vector_projection(entity_ids={entity_id}, max_embeddings=1)
    except Exception:
        _persist_projection_failure("upsert_incomplete")
        raise


def _remove_from_index(entity_id: str, *, cancelled: Callable[[], bool] | None = None) -> None:
    try:
        _publish_vector_projection(allow_embedding=False, **({"cancelled": cancelled} if cancelled is not None else {}))
    except Exception:
        _persist_projection_failure("removal_incomplete")
        raise


def _vector_readiness():
    with _generation_access():
        return _read_vector_readiness()


def _read_vector_readiness():
    from row_bot.embedding_config import active_embedding_metadata

    state = _projection_state()
    if not state["generation"]:
        return {"state": "missing", "ready": False, "detail": "The memory vector index needs a complete rebuild."}, None
    try:
        metadata, vectors = _load_vector_generation(state)
    except (OSError, ValueError, TypeError, KeyError):
        return {"state": "failed", "ready": False, "detail": "The memory vector generation could not be validated."}, None
    if metadata["embedding"] != active_embedding_metadata():
        return {"state": "stale", "ready": False, "detail": "The memory vector index does not match the selected embedding model."}, None
    coverage = dict(zip(metadata["ids"], metadata["source_hashes"]))
    count = 0
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        for batch in _projection_source_batches(conn, 256):
            for row in batch:
                entity = dict(row)
                count += 1
                if coverage.get(entity["id"]) != _semantic_hash(entity):
                    return {"state": "pending", "ready": False, "detail": "Saved knowledge has pending semantic projection work."}, None
    except (KnowledgeProjectionIncomplete, ValueError, TypeError, RecursionError):
        return {"state": "failed", "ready": False, "detail": "Saved knowledge exceeds safe projection bounds."}, None
    finally:
        conn.close()
    if count != len(coverage):
        return {"state": "pending", "ready": False, "detail": "Knowledge vector coverage does not match current entities."}, None
    if _projection_state()["revision"] != state["revision"]:
        return {"state": "pending", "ready": False, "detail": "Knowledge changed while vector coverage was checked."}, None
    return {"state": "ready", "ready": True, "detail": "The memory vector index completely covers current knowledge."}, (metadata, vectors)


def memory_vector_status() -> dict[str, object]:
    return dict(_vector_readiness()[0], retirement_pending=_retirement_pending())


def _ack_wiki_work(entity_id: str, revision: int, *, validate=None) -> None:
    conn = _get_conn()
    try:
        if validate is not None:
            conn.execute("BEGIN IMMEDIATE")
            validate()
        conn.execute("UPDATE knowledge_projection_work SET wiki_pending=0 WHERE entity_id=? AND revision=?",
                     (entity_id, revision))
        conn.execute("DELETE FROM knowledge_projection_work WHERE semantic_pending=0 AND wiki_pending=0")
        if validate is not None:
            validate()
        conn.commit()
    finally:
        conn.close()


def _repair_wiki_work(*, max_entities: int, cancelled=None, validate=None) -> tuple[int, bool]:
    from row_bot import wiki_vault

    if not wiki_vault.is_enabled():
        return 0, True
    conn = _get_conn()
    try:
        work = [dict(row) for row in conn.execute("""SELECT * FROM knowledge_projection_work
            WHERE wiki_pending=1 ORDER BY revision,entity_id LIMIT ?""", (max_entities,))]
    finally:
        conn.close()
    live, deleted = [], []

    def captured_entities():
        # Stream rows rather than retaining a max_entities-sized collection of
        # full descriptions. The wiki owner checks each exact row under its
        # one writer transaction; work is acknowledged after it releases that.
        for item in work:
            _cancel_projection(cancelled)
            entity = get_entity(item["entity_id"])
            if entity is None:
                deleted.append(item)
            else:
                live.append(item)
                yield entity

    strict = {"validate":validate} if validate is not None else {}
    outcomes = wiki_vault.export_entities_projection(captured_entities(), cancelled=cancelled, **strict)
    completed = 0
    for item, outcome in zip(live, outcomes, strict=True):
        if not outcome.complete:
            continue
        _ack_wiki_work(item["entity_id"], item["revision"], **strict)
        completed += 1
    for item in deleted:
        _cancel_projection(cancelled)
        wiki_vault.delete_entity_md({"id": item["entity_id"], "entity_type": item["deleted_type"] or "fact",
                                     "source": item["deleted_source"] or ""}, only_if_entity_absent=True, **strict)
        _ack_wiki_work(item["entity_id"], item["revision"], **strict)
        completed += 1
    return completed, all(outcome.complete for outcome in outcomes)


def repair_projections(*, max_entities: int = 256, cancelled: Callable[[], bool] | None = None,
                       validate: Callable[[], None] | None = None) -> dict[str, object]:
    """Explicit bounded embedding/wiki repair; remaining source work stays durable."""
    if type(max_entities) is not int or not 1 <= max_entities <= 1000:
        raise ValueError("max_entities must be between 1 and 1000")
    if validate is not None:
        original_cancelled = cancelled
        def cancelled():
            validate()
            return bool(original_cancelled and original_cancelled())
    _cancel_projection(cancelled)
    _ensure_graph()
    # FTS has its own source transaction and no provider dependency.
    if not _lexical_projection_current():
        rebuild_fts_index(**({"validate":validate} if validate is not None else {}))
    failures = []
    try:
        _publish_vector_projection(cancelled=cancelled, max_embeddings=max_entities)
    except Exception:
        if validate is not None:
            raise
        _persist_projection_failure("repair_incomplete")
        failures.append("semantic_projection_incomplete")
    completed = 0
    try:
        completed, wiki_complete = _repair_wiki_work(max_entities=max_entities, cancelled=cancelled,
            **({"validate":validate} if validate is not None else {}))
        if not wiki_complete:
            failures.append("wiki_projection_incomplete")
    except Exception:
        if validate is not None:
            raise
        failures.append("wiki_projection_incomplete")
    conn = _get_conn()
    try:
        pending = dict(conn.execute("""SELECT COALESCE(SUM(semantic_pending),0) AS semantic,
            COALESCE(SUM(wiki_pending),0) AS wiki FROM knowledge_projection_work""").fetchone())
    finally:
        conn.close()
    from row_bot import wiki_vault
    wiki_enabled = wiki_vault.is_enabled()
    retirement_pending = _retirement_pending()
    return {"complete": not failures and not pending["semantic"] and (not wiki_enabled or not pending["wiki"])
            and not retirement_pending,
            "pending": pending, "wiki_enabled": wiki_enabled, "wiki_completed": completed,
            "retirement_pending": retirement_pending, "failures": failures}


def semantic_search(query: str, top_k: int = 5, threshold: float = 0.5, *, for_auto_recall: bool = False) -> list[dict]:
    with _generation_access():
        return _search_vector_generation(query, top_k, threshold, for_auto_recall=for_auto_recall)


def _search_vector_generation(query: str, top_k: int, threshold: float, *, for_auto_recall: bool) -> list[dict]:
    import faiss

    status, loaded = _vector_readiness()
    if not status["ready"]:
        if _projection_batch.get() is not None:
            # Internal dedup may reuse unchanged prior candidates while its own
            # new rows remain pending. It never repairs implicitly or advertises
            # this incomplete cut as ready to ordinary application recall.
            if status["state"] != "pending":
                raise MemorySemanticUnavailable(f"memory_index_{status['state']}", str(status["detail"]))
            from row_bot.embedding_config import active_embedding_metadata
            try:
                loaded = _load_vector_generation(_projection_state(), fingerprint=active_embedding_metadata())
            except (OSError, ValueError, TypeError, KeyError):
                raise MemorySemanticUnavailable("memory_index_failed", "Prior knowledge vectors could not be validated.") from None
        elif for_auto_recall:
            raise MemorySemanticUnavailable(f"memory_index_{status['state']}", str(status["detail"]))
        else:
            rebuild_index()
            status, loaded = _vector_readiness()
    if loaded is None:
        raise MemorySemanticUnavailable(f"memory_index_{status['state']}", str(status["detail"]))
    metadata, directory = loaded
    if not metadata["ids"]:
        return []
    dimension = metadata["embedding"]["dimension"]
    from row_bot.embedding_config import active_embedding_metadata, get_embedding_config
    config = deepcopy(get_embedding_config())
    if active_embedding_metadata(config) != metadata["embedding"]:
        raise MemorySemanticUnavailable("memory_index_stale", "The memory embedding model changed during recall.")
    embedding = _get_embedding_model(for_auto_recall=for_auto_recall, config=config)
    raw = embedding.embed_query(query)
    if active_embedding_metadata() != metadata["embedding"]:
        raise MemorySemanticUnavailable("memory_index_stale", "The memory embedding model changed during recall.")
    query_vector = np.asarray(raw, dtype=np.float32)
    if query_vector.shape != (dimension,) or not np.isfinite(query_vector).all():
        raise MemorySemanticUnavailable("memory_query_invalid", "The memory query vector is invalid.")
    norm = np.linalg.norm(query_vector.astype(np.float64)) or 1
    query_vector = (query_vector / norm).astype(np.float32).reshape(1, -1)
    limit = max(1, int(top_k))
    candidates = []
    for segment in metadata["segments"]:
        vectors = _read_generation_segment(directory, segment, dimension)
        index = faiss.IndexFlatIP(dimension)
        index.add(vectors.vectors)
        scores, positions = index.search(query_vector, min(limit, vectors.count))
        for score, position in zip(scores[0], positions[0]):
            if position < 0 or score < threshold:
                continue
            offset = segment["start"] + int(position)
            candidates.append((float(score), offset))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        del candidates[limit:]
        del index, vectors
    results = []
    for score, position in candidates:
        entity = get_entity(metadata["ids"][position])
        if entity is not None and _semantic_hash(entity) == metadata["source_hashes"][position]:
            entity["score"] = round(score, 4)
            results.append(entity)
    return results


def _init_db() -> None:
    """Create entities + relations tables (idempotent)."""
    conn = _get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id            TEXT PRIMARY KEY,
            entity_type   TEXT NOT NULL,
            subject       TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            aliases       TEXT NOT NULL DEFAULT '',
            tags          TEXT NOT NULL DEFAULT '',
            properties    TEXT NOT NULL DEFAULT '{}',
            source        TEXT NOT NULL DEFAULT 'live',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entities_subject ON entities(subject)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS relations (
            id              TEXT PRIMARY KEY,
            source_id       TEXT NOT NULL,
            target_id       TEXT NOT NULL,
            relation_type   TEXT NOT NULL,
            confidence      REAL NOT NULL DEFAULT 1.0,
            properties      TEXT NOT NULL DEFAULT '{}',
            source          TEXT NOT NULL DEFAULT 'live',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (source_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (target_id) REFERENCES entities(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type)"
    )
    # Prevent exact duplicate edges
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_unique
        ON relations(source_id, target_id, relation_type)
    """)
    _initialize_projections(conn, lexical=_ensure_fts(conn))

    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# Migration from legacy memories table
# ═════════════════════════════════════════════════════════════════════════════

def _migrate_from_memories() -> int:
    """Migrate rows from the legacy ``memories`` table into ``entities``.

    Preserves original IDs, timestamps, content, and all metadata.  The
    old table is renamed to ``memories_v35_backup`` so data is never lost.

    Returns the number of rows migrated.
    """
    conn = _get_conn()

    # Check if legacy table exists
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "memories" not in tables:
        conn.close()
        return 0

    # Check if already migrated (backup table exists)
    if "memories_v35_backup" in tables:
        conn.close()
        return 0

    logger.info("Migrating legacy memories table to knowledge graph entities…")

    rows = conn.execute("SELECT * FROM memories").fetchall()
    migrated = 0

    for row in rows:
        row = dict(row)
        # Map old columns to new schema
        entity_id = row["id"]
        entity_type = row.get("category", "fact").lower().strip()
        subject = row.get("subject", "").strip()
        description = row.get("content", "").strip()
        tags = row.get("tags", "").strip()
        source = row.get("source", "live").strip()
        created_at = row.get("created_at", datetime.now().isoformat())
        updated_at = row.get("updated_at", created_at)

        # Check for collisions (shouldn't happen, but be safe)
        existing = conn.execute(
            "SELECT id FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if existing:
            continue

        conn.execute(
            "INSERT INTO entities "
            "(id, entity_type, subject, description, aliases, tags, properties, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entity_id, entity_type, subject, description, "", tags, "{}", source, created_at, updated_at),
        )
        migrated += 1

    # Rename old table as backup
    conn.execute("ALTER TABLE memories RENAME TO memories_v35_backup")
    conn.commit()
    conn.close()

    logger.info("Migrated %d memories → entities. Backup at 'memories_v35_backup'.", migrated)
    return migrated


# ═════════════════════════════════════════════════════════════════════════════
# Initialise on import
# ═════════════════════════════════════════════════════════════════════════════

_init_db()
_migrated_count = _migrate_from_memories()
if _migrated_count:
    rebuild_fts_index()
else:
    _ensure_fts_populated()


def _scrub_surrogates() -> None:
    """One-time startup cleanup: strip surrogate chars from existing entities.

    PDF text extraction can inject lone UTF-16 surrogates into entity text.
    These are valid Python str but invalid in strict UTF-8, crashing orjson
    when NiceGUI serialises graph data for the browser.  This scan fixes
    existing rows so the data layer is clean going forward.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, subject, description, aliases, tags FROM entities"
    ).fetchall()
    updates = []
    for row in rows:
        r = dict(row)
        cleaned = {}
        dirty = False
        for col in ("subject", "description", "aliases", "tags"):
            val = r[col] or ""
            scrubbed = _sanitize_text(val)
            if scrubbed != val:
                dirty = True
            cleaned[col] = scrubbed
        if dirty:
            updates.append((
                cleaned["subject"], cleaned["description"],
                cleaned["aliases"], cleaned["tags"], r["id"],
            ))
    if updates:
        conn.executemany(
            "UPDATE entities SET subject=?, description=?, aliases=?, tags=? "
            "WHERE id=?",
            updates,
        )
        conn.commit()
        logger.info(
            "Scrubbed surrogate characters from %d entities.", len(updates)
        )
    conn.close()


_scrub_surrogates()


# ═════════════════════════════════════════════════════════════════════════════
# NetworkX in-memory graph
# ═════════════════════════════════════════════════════════════════════════════

_graph: nx.MultiDiGraph = nx.MultiDiGraph()
_graph_ready = False
_graph_revision = -1


def _load_graph() -> None:
    """Publish one complete SQLite snapshot into this process's graph mirror."""
    global _graph, _graph_ready, _graph_revision
    with _graph_lock:
        conn = _get_conn()
        try:
            conn.execute("BEGIN")
            revision = _projection_state(conn)["revision"]
            graph = nx.MultiDiGraph()
            for row in conn.execute("SELECT * FROM entities"):
                entity = dict(row)
                graph.add_node(entity["id"], **entity)
            for row in conn.execute("SELECT * FROM relations"):
                relation = dict(row)
                if relation["source_id"] in graph and relation["target_id"] in graph:
                    graph.add_edge(relation["source_id"], relation["target_id"], key=relation["id"], **relation)
            _graph, _graph_revision, _graph_ready = graph, revision, True
        finally:
            conn.close()


def _ensure_graph() -> nx.MultiDiGraph:
    """Use the canonical revision to invalidate this process's graph mirror."""
    with _graph_lock:
        if not _graph_ready or _graph_revision != _projection_state()["revision"]:
            _load_graph()
        return _graph


# ═════════════════════════════════════════════════════════════════════════════
# FAISS vector index (shared with documents.py embedding model)
# ═════════════════════════════════════════════════════════════════════════════

class MemorySemanticUnavailable(RuntimeError):
    """A display-safe reason why semantic memory recall is unavailable."""

    def __init__(self, code: str, detail: str):
        self.code = str(code or "semantic_unavailable")
        self.detail = str(detail or "Semantic memory recall is unavailable.")
        super().__init__(self.detail)


def _get_embedding_model(*, for_auto_recall: bool = False, config: dict | None = None):
    """Return the shared embedding model using the appropriate load path."""
    if for_auto_recall:
        from row_bot.documents import get_embedding_model_for_recall

        return get_embedding_model_for_recall(config=config) if config is not None else get_embedding_model_for_recall()
    from row_bot.documents import get_embedding_model

    return get_embedding_model(config=config) if config is not None else get_embedding_model()


def _entity_text(entity: dict) -> str:
    """Build the string that gets embedded for an entity."""
    parts = [
        entity.get("entity_type", ""),
        entity.get("subject", ""),
        entity.get("description", ""),
    ]
    aliases = entity.get("aliases", "")
    if aliases:
        parts.append(aliases)
    tags = entity.get("tags", "")
    if tags:
        parts.append(tags)
    # Include key properties in embedding
    props = entity.get("properties", "{}")
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except (json.JSONDecodeError, TypeError):
            props = {}
    if isinstance(props, dict):
        props = {key: value for key, value in props.items() if key != "recalled_at"}
    if props:
        parts.append(json.dumps(props, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return " | ".join(p for p in parts if p)








# ═════════════════════════════════════════════════════════════════════════════
# Entity CRUD
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_subject(s: str) -> str:
    """Lower-case, strip, collapse whitespace — for subject comparison."""
    return " ".join(s.lower().split())


def save_entity(
    entity_type: str,
    subject: str,
    description: str = "",
    *,
    aliases: str = "",
    tags: str = "",
    properties: dict | None = None,
    source: str = "live",
    entity_id: str | None = None,
    expected_user: dict | None = None,
    validate: Callable[[], None] | None = None,
) -> dict:
    """Create a new entity in the knowledge graph.

    Parameters
    ----------
    entity_type : str
        Category / type (e.g. person, fact, preference).
    subject : str
        Short identifier — a name, topic, or title.
    description : str
        Free-text detail about the entity.
    aliases : str
        Comma-separated alternative names for entity resolution
        (e.g. "Mom, Mother, Mama").
    tags : str
        Comma-separated tags for search.
    properties : dict, optional
        Structured metadata as JSON-serialisable dict
        (e.g. {"birthday": "1965-03-15", "phone": "+1-555-0199"}).
    source : str
        Origin: 'live' or 'extraction'.

    Returns
    -------
    dict  with all entity columns.
    """
    entity_type = entity_type.lower().strip()
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity type '{entity_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_ENTITY_TYPES))}"
        )

    strict_create = entity_id is not None or validate is not None
    canonical_user = _normalize_subject(subject) == "user"

    entity_id = entity_id or uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    props_json = json.dumps(properties or {})

    conn = _get_conn()
    _subject = _sanitize_text(subject.strip())
    _description = _sanitize_text(description.strip())
    _aliases = _sanitize_text(aliases.strip())
    _tags = _sanitize_text(tags.strip())

    row = None
    try:
        if strict_create or canonical_user:
            conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        if canonical_user:
            # Resolve both legacy and reviewed User creates under the same
            # SQLite writer admission. Never insert from an earlier read.
            def matches_user(subject_value, aliases_value):
                if strict_create and (len(subject_value or "") > 8192 or len(aliases_value or "") > 8192):
                    raise ValueError("Entity identity exceeds review budget")
                return int(any(_normalize_subject(value) == "user" for value in
                               [subject_value or "", *(aliases_value or "").split(",")]))
            conn.create_function("client_matches_user", 2, matches_user)
            if strict_create:
                deadline = time.monotonic() + 2.0
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
                conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            current = conn.execute(
                "SELECT * FROM entities WHERE client_matches_user(subject,aliases) "
                "ORDER BY (entity_type='person') DESC,updated_at DESC,id LIMIT 1"
            ).fetchone()
            if strict_create:
                conn.set_progress_handler(None, 0)
                if (dict(current) if current else None) != expected_user:
                    raise ValueError("Canonical User changed after review")
                if current is not None:
                    if validate is not None:
                        validate()
                    return dict(current)
            elif current is not None:
                old_desc = current["description"] or ""
                new_desc = description.strip()
                merged = old_desc
                if new_desc and new_desc.lower() not in old_desc.lower():
                    merged = f"{old_desc}. {new_desc}".strip(". ") if old_desc else new_desc
                entity_id = current["id"]
                conn.execute("UPDATE entities SET description=?,entity_type='person',updated_at=? WHERE id=?",
                             (_sanitize_text(merged.strip()), now, entity_id))
                row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO entities "
                "(id, entity_type, subject, description, aliases, tags, properties, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entity_id, entity_type, _subject, _description,
                 _aliases, _tags, props_json, source.strip(), now, now),
            )
        if validate is not None:
            validate()
        conn.commit()
    finally:
        conn.close()

    entity = {
        "id": entity_id,
        "entity_type": entity_type,
        "subject": _subject,
        "description": _description,
        "aliases": _aliases,
        "tags": _tags,
        "properties": props_json,
        "source": source.strip(),
        "created_at": now,
        "updated_at": now,
    }

    if row is not None:
        entity = dict(row)
    _upsert_fts_entity(entity)

    # Update NetworkX

    # Update FAISS (skipped during batch extraction)
    if not _skip_reindex:
        _upsert_index(entity_id)

    # Wiki vault export (non-blocking, fire-and-forget)
    _wiki_export_entity(entity)

    return entity


def get_entity(entity_id: str) -> dict | None:
    """Fetch a single entity by ID."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_entity(
    entity_id: str,
    description: str,
    *,
    subject: str | None = None,
    entity_type: str | None = None,
    aliases: str | None = None,
    tags: str | None = None,
    properties: dict | None = None,
    source: str | None = None,
    expected_updated_at: str | None = None,
    expected_entity: dict | None = None,
    validate: Callable[[], None] | None = None,
) -> dict | None:
    """Update an existing entity's fields.

    Only ``description`` is required.  Pass other kwargs to update those
    fields as well. Returns None if missing or an optional revision/snapshot
    changed. The full snapshot also protects properties updated without a timestamp.
    """
    now = datetime.now().isoformat()
    fields = ["description = ?", "updated_at = ?"]
    params: list = [_sanitize_text(description.strip()), now]

    if subject is not None:
        fields.append("subject = ?")
        params.append(_sanitize_text(subject.strip()))
    if entity_type is not None:
        et = entity_type.lower().strip()
        if et in VALID_ENTITY_TYPES:
            fields.append("entity_type = ?")
            params.append(et)
    if aliases is not None:
        fields.append("aliases = ?")
        params.append(_sanitize_text(aliases.strip()))
    if tags is not None:
        fields.append("tags = ?")
        params.append(_sanitize_text(tags.strip()))
    if properties is not None:
        fields.append("properties = ?")
        params.append(json.dumps(properties))
    if source is not None:
        fields.append("source = ?")
        params.append(source.strip())

    params.append(entity_id)
    revision_clause = ""
    if expected_updated_at is not None:
        revision_clause = " AND updated_at = ?"
        params.append(expected_updated_at)
    conn = _get_conn()
    try:
        if expected_entity is not None or validate is not None:
            conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        if expected_entity is not None:
            current = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            if current is None or dict(current) != expected_entity:
                return None
        cur = conn.execute(
            f"UPDATE entities SET {', '.join(fields)} WHERE id = ?{revision_clause}",
            params,
        )
        row = None
        if cur.rowcount:
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if validate is not None:
            validate()
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()

    if row:
        entity = dict(row)
        _upsert_fts_entity(entity)
        if not _skip_reindex:
            _upsert_index(entity_id)
        # Wiki vault export (non-blocking)
        _wiki_export_entity(entity)
        return entity
    return None


def update_entity_properties_pair(first: tuple[dict, dict], second: tuple[dict, dict], *,
                                  validate: Callable[[], None] | None = None) -> tuple[dict | None, dict | None]:
    """Publish two reviewed property changes atomically in the canonical store."""
    pairs = (first, second)
    if first[0]["id"] == second[0]["id"]:
        raise ValueError("Paired entity updates require distinct identities")
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        for expected, _props in pairs:
            current = conn.execute("SELECT * FROM entities WHERE id=?", (expected["id"],)).fetchone()
            if current is None or dict(current) != expected:
                return None, None
        now = datetime.now().isoformat()
        for expected, props in pairs:
            if validate is not None:
                validate()
            conn.execute("UPDATE entities SET properties=?,updated_at=? WHERE id=?",
                         (json.dumps(props), now, expected["id"]))
        rows = tuple(dict(conn.execute("SELECT * FROM entities WHERE id=?", (expected["id"],)).fetchone())
                     for expected, _props in pairs)
        if validate is not None:
            validate()
        conn.commit()
    finally:
        conn.close()
    if not _skip_reindex and _projection_batch.get() is None:
        # Both rows committed together: publish their bounded work together,
        # rather than attempting incomplete one-row coverage twice.
        _publish_vector_projection(entity_ids={entity["id"] for entity in rows}, max_embeddings=2)
    for entity in rows:
        _upsert_fts_entity(entity)
        _wiki_export_entity(entity)
    return rows


def delete_entity(entity_id: str) -> bool:
    """Delete an entity and its relations.  Returns True if deleted."""
    # Capture entity data before deletion for wiki cleanup
    entity_data = get_entity(entity_id)

    conn = _get_conn()
    # FK CASCADE handles relations
    cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.commit()
    conn.close()

    deleted = cur.rowcount > 0
    if deleted:
        _delete_fts_entity(entity_id)
        if not _skip_reindex:
            _remove_from_index(entity_id)
        # Wiki vault cleanup
        if entity_data:
            _wiki_delete_entity(entity_data)
    return deleted


def _reviewed_catalog_revision(conn: sqlite3.Connection) -> str:
    """Match the unfiltered passive catalog revision inside write admission."""
    digest = hashlib.sha256()
    for row in conn.execute(
        "SELECT id,entity_type,subject,description,updated_at FROM entities ORDER BY id"
    ):
        item = {
            "id": str(row["id"]),
            "entity_type": str(row["entity_type"])[:64],
            "subject": str(row["subject"])[:256],
            "description": str(row["description"])[:1000],
            "updated_at": str(row["updated_at"])[:64],
            "truncated": any(
                len(str(row[name])) > bound
                for name, bound in (("entity_type", 64), ("subject", 256), ("description", 1000))
            ),
            "saved_state": "saved",
            "semantic_state": "unknown",
        }
        digest.update(json.dumps([item, True], sort_keys=True, ensure_ascii=True).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def delete_reviewed_entities(
    expected_revisions: dict[str, str] | None = None,
    *,
    catalog_revision: str | None = None,
    delete_all: bool = False,
    validate: Callable[[], None],
) -> dict[str, Any]:
    """Delete exact reviewed rows atomically, then report derived cleanup truthfully."""
    expected_revisions = dict(expected_revisions or {})
    if delete_all == bool(expected_revisions) or len(expected_revisions) > 100:
        raise ValueError("invalid_knowledge_maintenance")
    conn = _get_conn()
    captured: list[dict[str, Any]] = []
    stale: list[str] = []
    missing: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        validate()
        if delete_all:
            if not isinstance(catalog_revision, str) or _reviewed_catalog_revision(conn) != catalog_revision:
                raise ValueError("knowledge_changed")
            captured = [dict(row) for row in conn.execute("SELECT * FROM entities ORDER BY id")]
            conn.execute("DELETE FROM relations")
            conn.execute("DELETE FROM entities")
        else:
            for identifier, revision in expected_revisions.items():
                row = conn.execute("SELECT * FROM entities WHERE id=?", (identifier,)).fetchone()
                if row is None:
                    missing.append(identifier)
                    continue
                entity = dict(row)
                current = hashlib.sha256(
                    json.dumps(entity, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
                ).hexdigest()
                if current != revision:
                    stale.append(identifier)
                    continue
                captured.append(entity)
            conn.executemany("DELETE FROM entities WHERE id=?", [(row["id"],) for row in captured])
        validate()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    global _graph_ready
    with _graph_lock:
        _graph_ready = False
    cleanup = {"lexical_index": "completed", "vector_index": "completed", "wiki": "completed"}
    if delete_all:
        try:
            rebuild_fts_index()
        except Exception:
            cleanup["lexical_index"] = "failed"
        try:
            if not _skip_reindex:
                rebuild_index()
            else:
                cleanup["vector_index"] = "skipped"
        except Exception:
            cleanup["vector_index"] = "failed"
        try:
            from row_bot import wiki_vault

            wiki_vault.clear_wiki_folder()
        except Exception:
            cleanup["wiki"] = "failed"
    else:
        for entity in captured:
            try:
                _delete_fts_entity(str(entity["id"]))
            except Exception:
                cleanup["lexical_index"] = "failed"
            try:
                if not _skip_reindex:
                    _remove_from_index(str(entity["id"]))
                else:
                    cleanup["vector_index"] = "skipped"
            except Exception:
                cleanup["vector_index"] = "failed"
            try:
                _wiki_delete_entity(entity)
            except Exception:
                cleanup["wiki"] = "failed"
    return {
        "deleted": [str(row["id"]) for row in captured],
        "stale": stale,
        "missing": missing,
        "cleanup": cleanup,
    }


def delete_entities_by_source(source: str, *, retry_entities: list[dict] | None = None,
                              validate: Callable[[], None] | None = None) -> int:
    """Delete all entities (and their relations via FK CASCADE) matching *source*.

    Re-syncs the NetworkX graph and rebuilds the FAISS index once at the end.
    Returns the number of entities deleted. A durable removal owner may supply
    previously captured entities to retry projection/wiki cleanup after commit.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        rows = conn.execute(
            "SELECT * FROM entities WHERE source = ?",
            (source,),
        ).fetchall()
        if not rows and retry_entities is None:
            return 0

        from row_bot import wiki_vault
        if retry_entities is not None:
            captured = {str(entity["id"]): entity for entity in retry_entities}
            for row in rows:
                expected = captured.get(str(row["id"]))
                if expected is None or expected.get("wiki_revision") != wiki_vault._source_revision(dict(row)):
                    raise ValueError("Derived knowledge changed after removal capture; preserve for review")

        current_ids = [row["id"] for row in rows]
        cleanup_entities = {}
        for entity in retry_entities or []:
            current = conn.execute("SELECT source FROM entities WHERE id=?", (str(entity["id"]),)).fetchone()
            if current is None or current[0] == source:
                cleanup_entities[str(entity["id"])] = entity
        cleanup_entities.update({str(row["id"]): dict(dict(row), wiki_revision=wiki_vault._source_revision(dict(row)))
                                 for row in rows})
        conn.execute(
            "DELETE FROM entities WHERE source = ?", (source,),
        )
        # Also delete relations that reference this source (orphaned by other docs)
        conn.execute("DELETE FROM relations WHERE source = ?", (source,))
        if validate is not None:
            validate()
        conn.commit()
    finally:
        conn.close()


    if validate is None:
        _remove_from_index("")
        rebuild_fts_index()
    else:
        def cancelled():
            validate()
            return False
        _remove_from_index("", cancelled=cancelled)
        rebuild_fts_index(validate=validate)

    # Wiki vault cleanup
    for entity in cleanup_entities.values():
        if validate is not None:
            validate()
        if retry_entities is None and validate is None:
            _wiki_delete_entity(entity)
        else:
            wiki_vault.delete_entity_md(entity, only_if_entity_absent=True,
                                       **({"validate": validate} if validate is not None else {}))

    return len(current_ids)


def delete_entities_by_source_prefix(prefix: str) -> int:
    """Delete all entities whose source starts with *prefix* (e.g. ``'document:'``).

    Returns total entities deleted.
    """
    conn = _get_conn()
    sources = conn.execute(
        "SELECT DISTINCT source FROM entities WHERE source LIKE ?",
        (prefix + "%",),
    ).fetchall()
    conn.close()

    total = 0
    for (src,) in sources:
        total += delete_entities_by_source(src)
    return total


def list_entities(
    entity_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List entities, optionally filtered by type."""
    conn = _get_conn()
    if entity_type:
        entity_type = entity_type.lower().strip()
        rows = conn.execute(
            "SELECT * FROM entities WHERE entity_type = ? ORDER BY updated_at DESC LIMIT ?",
            (entity_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def iter_entities_snapshot(
    entity_type: str | None = None, *, batch_size: int = 256,
) -> Iterator[dict]:
    """Yield a complete, consistent SQLite snapshot in bounded read batches."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        if entity_type:
            cursor = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? ORDER BY id",
                (entity_type.lower().strip(),),
            )
        else:
            cursor = conn.execute("SELECT * FROM entities ORDER BY id")
        while rows := cursor.fetchmany(batch_size):
            for row in rows:
                yield dict(row)
    finally:
        conn.close()


def list_entity_summaries(
    entity_type: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    description_chars: int = 500,
) -> list[dict]:
    """Return lightweight entity rows for list UIs.

    The summary keeps audit/status properties but avoids returning full
    descriptions by default, so Settings can render browse rows without
    building a large component tree or moving large text payloads.
    """

    conn = _get_conn()
    desc_len = max(0, int(description_chars or 0))
    if entity_type:
        entity_type = entity_type.lower().strip()
        rows = conn.execute(
            """
            SELECT id, entity_type, subject,
                   substr(description, 1, ?) AS description,
                   aliases, tags, source, created_at, updated_at, properties
            FROM entities
            WHERE entity_type = ?
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (desc_len, entity_type, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, entity_type, subject,
                   substr(description, 1, ?) AS description,
                   aliases, tags, source, created_at, updated_at, properties
            FROM entities
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (desc_len, limit, offset),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_entity_subjects(entity_ids: list[str] | tuple[str, ...] | set[str]) -> dict[str, str]:
    """Return subjects for the requested entity ids using one bounded lookup."""

    ids = [str(entity_id).strip() for entity_id in entity_ids if str(entity_id).strip()]
    ids = list(dict.fromkeys(ids))[:100]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT id, subject FROM entities WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    conn.close()
    return {str(row["id"]): str(row["subject"] or "") for row in rows}


def count_entities() -> int:
    """Return total number of stored entities."""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()
    return count


def search_entities(
    query: str,
    entity_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Keyword search across subject, description, aliases, and tags."""
    conn = _get_conn()
    sql = (
        "SELECT * FROM entities WHERE "
        "(subject LIKE ? OR description LIKE ? OR aliases LIKE ? OR tags LIKE ?)"
    )
    params: list = [f"%{query}%"] * 4

    if entity_type:
        entity_type = entity_type.lower().strip()
        sql += " AND entity_type = ?"
        params.append(entity_type)

    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_by_subject(
    entity_type: str | None,
    subject: str,
) -> dict | None:
    """Find an entity by normalised subject (and optionally type).

    Deterministic SQL lookup — no embedding similarity.  Also checks
    the ``aliases`` field for alternative name matches.

    Returns the most recently updated match, or None.
    """
    conn = _get_conn()
    if entity_type is not None:
        et = entity_type.lower().strip()
        rows = conn.execute(
            "SELECT * FROM entities WHERE entity_type = ? ORDER BY updated_at DESC",
            (et,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY updated_at DESC",
        ).fetchall()
    conn.close()

    norm = _normalize_subject(subject)

    matches = []
    for row in rows:
        row = dict(row)
        # Match on subject
        if _normalize_subject(row["subject"]) == norm:
            matches.append(row)
            continue
        # Match on aliases
        aliases = row.get("aliases", "")
        if aliases:
            for alias in aliases.split(","):
                if _normalize_subject(alias.strip()) == norm:
                    matches.append(row)
                    break

    if not matches:
        return None
    # For the canonical "User" entity, prefer person type
    if norm == "user":
        for m in matches:
            if m.get("entity_type") == "person":
                return m
    return matches[0]


# ── Auto-link helpers ────────────────────────────────────────────────────────

def _ensure_user_entity(*, validate: Callable[[], None] | None = None) -> str:
    """Return the ID of the canonical 'User' entity, creating it if needed."""
    if validate is not None:
        validate()
    existing = find_by_subject(None, "User")
    if existing:
        return existing["id"]
    entity = save_entity("person", "User", "The user of this system",
                         **({"validate":validate} if validate is not None else {}))
    return entity["id"]








def find_duplicate(
    entity_type: str,
    subject: str,
    description: str,
    threshold: float = 0.92,
) -> dict | None:
    """Find a near-duplicate entity by semantic similarity + subject match."""
    search_text = f"{entity_type} {subject} {description}"
    try:
        results = semantic_search(search_text, top_k=5, threshold=threshold)
    except Exception:
        return None
    norm_subj = _normalize_subject(subject)
    for e in results:
        if _normalize_subject(e.get("subject", "")) == norm_subj:
            return e
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Relation CRUD
# ═════════════════════════════════════════════════════════════════════════════

def add_relation(
    source_id: str,
    target_id: str,
    relation_type: str,
    *,
    confidence: float = 1.0,
    properties: dict | None = None,
    source: str = "live",
    relation_id: str | None = None,
    expected_entities: tuple[dict, dict] | None = None,
    validate: Callable[[], None] | None = None,
) -> dict | None:
    """Create a directed relation (edge) between two entities.

    Parameters
    ----------
    source_id, target_id : str
        Entity IDs.  Both must exist.
    relation_type : str
        Open label — e.g. ``'father_of'``, ``'lives_in'``, ``'works_on'``.
    confidence : float
        0.0–1.0 confidence score (1.0 = certain).
    properties : dict, optional
        Extra structured metadata on the relation.
    source : str
        ``'live'`` or ``'extraction'``.

    Returns
    -------
    dict  with all relation columns, or ``None`` if either entity is missing.
    """
    # Block self-loops (entity pointing to itself)
    if source_id == target_id:
        logger.debug("Rejected self-loop relation: %s --[%s]--> %s", source_id, relation_type, target_id)
        return None

    # Block vague/meaningless relation types
    _BANNED_RELATION_TYPES = {
        "related_to", "associated_with", "connected_to", "linked_to",
        "has_relation", "involves", "correlates_with",
    }
    _norm_check = normalize_relation_type(relation_type)
    if _norm_check in _BANNED_RELATION_TYPES:
        logger.debug(
            "Rejected vague relation type '%s': %s → %s",
            relation_type, source_id, target_id,
        )
        return None

    strict = expected_entities is not None or validate is not None
    conn = _get_conn()
    rel_id = relation_id or uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    props_json = json.dumps(properties or {})
    relation_type = normalize_relation_type(relation_type)
    confidence = max(0.0, min(1.0, confidence))

    # Warn on unknown relation types (but still accept to avoid breakage)
    if relation_type not in VALID_RELATION_TYPES:
        logger.warning(
            "Unknown relation type '%s' (%s → %s) — not in VALID_RELATION_TYPES",
            relation_type, source_id, target_id,
        )

    try:
        if strict:
            conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        endpoints = [conn.execute("SELECT * FROM entities WHERE id=?", (identifier,)).fetchone()
                     for identifier in (source_id, target_id)]
        if any(row is None for row in endpoints):
            return None
        if expected_entities is not None and tuple(dict(row) for row in endpoints) != expected_entities:
            return None
        if strict:
            existing = conn.execute("SELECT * FROM relations WHERE source_id=? AND target_id=? AND relation_type=?",
                                    (source_id, target_id, relation_type)).fetchone()
            if existing is not None:
                if validate is not None:
                    validate()
                return dict(existing)
        conn.execute(
            "INSERT INTO relations "
            "(id, source_id, target_id, relation_type, confidence, properties, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rel_id, source_id, target_id, relation_type, confidence,
             props_json, source, now, now),
        )
        if validate is not None:
            validate()
        conn.commit()
    except sqlite3.IntegrityError:
        if strict:
            raise
        # Legacy duplicate-edge contract is unchanged.
        return None
    finally:
        conn.close()

    rel = {
        "id": rel_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": relation_type,
        "confidence": confidence,
        "properties": props_json,
        "source": source,
        "created_at": now,
        "updated_at": now,
    }

    # Update NetworkX (use relation ID as edge key for deterministic removal)

    # Re-export both endpoints so .md Connections sections stay current
    for eid in (source_id, target_id):
        ent = get_entity(eid)
        if ent:
            _wiki_export_entity(ent)

    return rel


def get_relations(
    entity_id: str,
    direction: str = "both",
) -> list[dict]:
    """Get all relations involving an entity.

    Parameters
    ----------
    entity_id : str
        The entity to query.
    direction : str
        ``'outgoing'`` (entity is source), ``'incoming'`` (entity is target),
        or ``'both'`` (default).

    Returns
    -------
    list[dict]  — each dict has all relation columns plus ``peer_id`` and
    ``peer_subject`` for convenience.
    """
    conn = _get_conn()
    results = []

    if direction in ("outgoing", "both"):
        rows = conn.execute(
            "SELECT r.*, e.subject AS peer_subject FROM relations r "
            "JOIN entities e ON e.id = r.target_id "
            "WHERE r.source_id = ? ORDER BY r.updated_at DESC",
            (entity_id,),
        ).fetchall()
        for row in rows:
            d = dict(row)
            d["peer_id"] = d["target_id"]
            d["direction"] = "outgoing"
            results.append(d)

    if direction in ("incoming", "both"):
        rows = conn.execute(
            "SELECT r.*, e.subject AS peer_subject FROM relations r "
            "JOIN entities e ON e.id = r.source_id "
            "WHERE r.target_id = ? ORDER BY r.updated_at DESC",
            (entity_id,),
        ).fetchall()
        for row in rows:
            d = dict(row)
            d["peer_id"] = d["source_id"]
            d["direction"] = "incoming"
            results.append(d)

    conn.close()
    return results


def delete_relation(relation_id: str, *, expected_relation: dict | None = None,
                    expected_entities: tuple[dict, dict] | None = None,
                    validate: Callable[[], None] | None = None) -> bool:
    """Delete an exact relation, optionally guarded by edge/endpoint snapshots."""
    conn = _get_conn()
    try:
        if expected_relation is not None or expected_entities is not None or validate is not None:
            conn.execute("BEGIN IMMEDIATE")
        if validate is not None:
            validate()
        current = conn.execute("SELECT * FROM relations WHERE id=?", (relation_id,)).fetchone()
        if current is None:
            return False
        row = dict(current)
        if expected_relation is not None and row != expected_relation:
            return False
        if expected_entities is not None:
            endpoints = [conn.execute("SELECT * FROM entities WHERE id=?", (row[key],)).fetchone()
                         for key in ("source_id", "target_id")]
            if any(value is None for value in endpoints) or tuple(dict(value) for value in endpoints) != expected_entities:
                return False
        conn.execute("DELETE FROM relations WHERE id=?", (relation_id,))
        if validate is not None:
            validate()
        conn.commit()
    finally:
        conn.close()

    # Re-export both endpoints so .md Connections sections stay current
    for eid in (row["source_id"], row["target_id"]):
        ent = get_entity(eid)
        if ent:
            _wiki_export_entity(ent)

    return True


def count_relations() -> int:
    """Return total number of stored relations."""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    conn.close()
    return count


def list_relations(limit: int = 100) -> list[dict]:
    """List all relations with entity subjects for readability."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT r.*, "
        "  s.subject AS source_subject, "
        "  t.subject AS target_subject "
        "FROM relations r "
        "JOIN entities s ON s.id = r.source_id "
        "JOIN entities t ON t.id = r.target_id "
        "ORDER BY r.updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# Graph query helpers
# ═════════════════════════════════════════════════════════════════════════════

def get_neighbors(
    entity_id: str,
    hops: int = 1,
    direction: str = "both",
) -> list[dict]:
    """Return entities within *hops* of *entity_id* in the graph.

    Parameters
    ----------
    entity_id : str
    hops : int
        Number of edges to traverse (1 = immediate neighbors).
    direction : str
        ``'outgoing'``, ``'incoming'``, or ``'both'``.

    Returns list of entity dicts with an extra ``hop`` key.
    """
    g = _ensure_graph()
    if entity_id not in g:
        return []

    visited: dict[str, int] = {entity_id: 0}
    frontier = [entity_id]

    for depth in range(1, hops + 1):
        next_frontier = []
        for nid in frontier:
            neighbors = set()
            if direction in ("outgoing", "both"):
                neighbors.update(g.successors(nid))
            if direction in ("incoming", "both"):
                neighbors.update(g.predecessors(nid))
            for nbr in neighbors:
                if nbr not in visited:
                    visited[nbr] = depth
                    next_frontier.append(nbr)
        frontier = next_frontier

    results = []
    for nid, hop in visited.items():
        if nid == entity_id:
            continue
        node_data = g.nodes.get(nid, {})
        if node_data:
            entity = dict(node_data)
            entity["hop"] = hop
            results.append(entity)

    # Sort by hop distance, then by update time
    results.sort(key=lambda e: (e["hop"], e.get("updated_at", "")))
    return results


def get_shortest_path(
    source_id: str,
    target_id: str,
) -> list[dict] | None:
    """Return the shortest path between two entities as a list of entity dicts.

    Returns None if no path exists.  Uses the undirected view of the graph.
    """
    g = _ensure_graph()
    if source_id not in g or target_id not in g:
        return None

    try:
        path = nx.shortest_path(g.to_undirected(), source_id, target_id)
    except nx.NetworkXNoPath:
        return None

    return [dict(g.nodes[nid]) for nid in path if g.nodes.get(nid)]


def get_subgraph(entity_id: str, hops: int = 2) -> dict:
    """Extract a subgraph around an entity for visualisation.

    Returns
    -------
    dict with keys:
        ``nodes`` — list of entity dicts
        ``edges`` — list of relation dicts with source_subject/target_subject
    """
    g = _ensure_graph()
    if entity_id not in g:
        return {"nodes": [], "edges": []}

    neighbors = get_neighbors(entity_id, hops=hops)
    node_ids = {entity_id} | {n["id"] for n in neighbors}

    nodes = []
    center = g.nodes.get(entity_id)
    if center:
        nodes.append(dict(center))
    nodes.extend(neighbors)

    edges = []
    for u, v, data in g.edges(data=True):
        if u in node_ids and v in node_ids:
            edge = dict(data)
            edge["source_id"] = u
            edge["target_id"] = v
            edge["source_subject"] = g.nodes[u].get("subject", u)
            edge["target_subject"] = g.nodes[v].get("subject", v)
            edges.append(edge)

    return {"nodes": nodes, "edges": edges}


def get_connected_components() -> list[list[str]]:
    """Return connected components as lists of entity IDs (largest first)."""
    g = _ensure_graph()
    undirected = g.to_undirected()
    components = sorted(nx.connected_components(undirected), key=len, reverse=True)
    return [list(c) for c in components]


def get_graph_stats() -> dict:
    """Return summary statistics about the knowledge graph."""
    g = _ensure_graph()
    conn = _get_conn()

    # Entity type breakdown
    type_counts = {}
    for row in conn.execute(
        "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type"
    ).fetchall():
        type_counts[row[0]] = row[1]

    # Relation type breakdown
    rel_counts = {}
    for row in conn.execute(
        "SELECT relation_type, COUNT(*) as cnt FROM relations GROUP BY relation_type"
    ).fetchall():
        rel_counts[row[0]] = row[1]

    conn.close()

    components = get_connected_components()

    return {
        "total_entities": g.number_of_nodes(),
        "total_relations": g.number_of_edges(),
        "entity_types": type_counts,
        "relation_types": rel_counts,
        "connected_components": len(components),
        "largest_component": len(components[0]) if components else 0,
        "isolated_entities": sum(1 for c in components if len(c) == 1),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Mermaid export
# ═════════════════════════════════════════════════════════════════════════════

def _mermaid_safe(text: str) -> str:
    """Escape text for Mermaid labels."""
    return text.replace('"', "'").replace("\n", " ")[:50]


def to_mermaid(
    entity_id: str | None = None,
    hops: int = 2,
    max_nodes: int = 30,
) -> str:
    """Generate a Mermaid graph diagram.

    If *entity_id* is given, shows the local subgraph.  Otherwise shows
    the full graph (capped at *max_nodes* most-connected entities).

    Returns a Mermaid string like::

        graph LR
            a123["Mom (person)"] -->|mother_of| b456["User (person)"]
    """
    g = _ensure_graph()
    lines = ["graph LR"]

    if entity_id and entity_id in g:
        sub = get_subgraph(entity_id, hops=hops)
        nodes = sub["nodes"][:max_nodes]
        node_ids = {n["id"] for n in nodes}
        for n in nodes:
            label = _mermaid_safe(f"{n.get('subject', '?')} ({n.get('entity_type', '?')})")
            lines.append(f'    {n["id"]}["{label}"]')
        for e in sub["edges"]:
            if e.get("source_id") in node_ids and e.get("target_id") in node_ids:
                rel = _mermaid_safe(e.get("relation_type", "related"))
                lines.append(f'    {e["source_id"]} -->|{rel}| {e["target_id"]}')
    else:
        # Full graph, pick top N by degree
        degree_sorted = sorted(g.nodes, key=lambda n: g.degree(n), reverse=True)[:max_nodes]
        node_ids = set(degree_sorted)
        for nid in degree_sorted:
            data = g.nodes.get(nid, {})
            label = _mermaid_safe(f"{data.get('subject', '?')} ({data.get('entity_type', '?')})")
            lines.append(f'    {nid}["{label}"]')
        for u, v, data in g.edges(data=True):
            if u in node_ids and v in node_ids:
                rel = _mermaid_safe(data.get("relation_type", "related"))
                lines.append(f"    {u} -->|{rel}| {v}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# vis-network JSON serialization (used by the UI graph tab)
# ═════════════════════════════════════════════════════════════════════════════

# Color palette for entity types — muted, readable on dark backgrounds.
_VIS_TYPE_COLORS: dict[str, str] = {
    "person":       "#4FC3F7",   # light blue
    "preference":   "#FFD54F",   # amber
    "fact":         "#81C784",   # green
    "event":        "#FF8A65",   # deep orange
    "place":        "#BA68C8",   # purple
    "project":      "#4DB6AC",   # teal
    "organisation": "#A1887F",   # brown
    "concept":      "#90A4AE",   # blue-grey
    "skill":        "#F06292",   # pink
    "media":        "#AED581",   # light green
    "self_knowledge": "#7E57C2", # deep purple
}
_VIS_DEFAULT_COLOR = "#B0BEC5"  # grey fallback


def graph_to_vis_json(
    entity_id: str | None = None,
    hops: int = 2,
    max_nodes: int = 500,
) -> dict:
    """Serialize the graph (or a subgraph) into vis-network JSON format.

    Parameters
    ----------
    entity_id
        If given, returns the *hops*-hop neighborhood around this entity.
        If ``None``, returns the full graph (degree-sorted, capped at
        *max_nodes*).
    hops
        Neighborhood radius when *entity_id* is provided.
    max_nodes
        Hard cap on node count for the full-graph mode.

    Returns
    -------
    dict with keys:
        ``nodes`` — list of vis-network node objects
        ``edges`` — list of vis-network edge objects
        ``center`` — entity_id of the center node (or highest-degree node)
        ``stats`` — ``{total_entities, total_relations, shown_nodes, shown_edges}``
    """
    g = _ensure_graph()

    if entity_id and entity_id in g:
        # ── Local subgraph mode ──────────────────────────────────────────
        sub = get_subgraph(entity_id, hops=hops)
        raw_nodes = sub["nodes"]
        raw_edges = sub["edges"]
        center_id = entity_id
    else:
        # ── Full graph mode (degree-sorted, capped) ─────────────────────
        if g.number_of_nodes() == 0:
            return {
                "nodes": [], "edges": [], "center": None,
                "stats": {"total_entities": 0, "total_relations": 0,
                          "shown_nodes": 0, "shown_edges": 0},
            }
        degree_sorted = sorted(
            g.nodes, key=lambda n: g.degree(n), reverse=True,
        )[:max_nodes]
        node_ids = set(degree_sorted)

        raw_nodes = [dict(g.nodes[nid]) for nid in degree_sorted if g.nodes.get(nid)]
        raw_edges = []
        for u, v, data in g.edges(data=True):
            if u in node_ids and v in node_ids:
                edge = dict(data)
                edge["source_id"] = u
                edge["target_id"] = v
                edge["source_subject"] = g.nodes[u].get("subject", u)
                edge["target_subject"] = g.nodes[v].get("subject", v)
                raw_edges.append(edge)

        # Center = "User" entity if present, else highest-degree node
        center_id = degree_sorted[0]
        for nid in degree_sorted:
            subj = g.nodes[nid].get("subject", "")
            if subj.lower() == "user":
                center_id = nid
                break

    # ── Build vis-network nodes ──────────────────────────────────────────
    # Compute degree range for sizing
    node_ids_set = {n["id"] for n in raw_nodes}
    degrees = {n["id"]: g.degree(n["id"]) for n in raw_nodes if n["id"] in g}
    min_deg = min(degrees.values()) if degrees else 0
    max_deg = max(degrees.values()) if degrees else 0
    deg_range = max_deg - min_deg if max_deg > min_deg else 1

    vis_nodes = []
    for n in raw_nodes:
        nid = n["id"]
        etype = n.get("entity_type", "")
        subject = n.get("subject", "?")
        color = _VIS_TYPE_COLORS.get(etype, _VIS_DEFAULT_COLOR)

        # Size: 15–40 based on degree
        deg = degrees.get(nid, 0)
        size = 15 + int(25 * (deg - min_deg) / deg_range)

        desc = n.get("description", "") or ""
        aliases = n.get("aliases", "") or ""
        tags = n.get("tags", "") or ""
        source = n.get("source", "live") or "live"
        updated_at = n.get("updated_at", "") or ""
        props = n.get("properties", "{}")
        if isinstance(props, str):
            try:
                props = json.loads(props or "{}")
            except (json.JSONDecodeError, TypeError):
                props = {}
        props = props if isinstance(props, dict) else {}
        status = str(props.get("status") or "active").strip().lower()
        if status not in {"active", "archived", "superseded", "needs_review"}:
            status = "active"
        tier = str(props.get("memory_tier") or "").strip().lower()
        if tier not in {"core", "semantic", "episodic", "resource"}:
            tier = "resource" if source.startswith("document:") or etype == "media" else "semantic"

        vis_nodes.append({
            "id": nid,
            "label": subject,
            "color": color,
            "size": size,
            "font": {"color": "#ECEFF1"},
            "title": (
                f"{subject}\n"
                f"Type: {etype}\n"
                f"Connections: {deg}"
                + (f"\n{desc[:120]}" if desc else "")
            ),
            # Extra data for the detail card and filtering
            "_type": etype,
            "_description": desc,
            "_aliases": aliases,
            "_tags": tags,
            "_degree": deg,
            "_source": source,
            "_updated_at": updated_at,
            "_status": status,
            "_tier": tier,
            "_confidence": props.get("confidence"),
            "_review_reason": props.get("review_reason", ""),
            "_superseded_by": props.get("superseded_by", ""),
            "_recalled_at": props.get("recalled_at", ""),
        })

    # ── Build vis-network edges ──────────────────────────────────────────
    vis_edges = []
    for e in raw_edges:
        src = e.get("source_id", "")
        tgt = e.get("target_id", "")
        if src not in node_ids_set or tgt not in node_ids_set:
            continue
        rel = e.get("relation_type", "")
        vis_edges.append({
            "id": f"{src}__{tgt}__{rel}",
            "from": src,
            "to": tgt,
            "label": rel,
            "arrows": "to",
            "color": {"color": "#616161", "highlight": "#FFD54F"},
        })

    return {
        "nodes": vis_nodes,
        "edges": vis_edges,
        "center": center_id,
        "stats": {
            "total_entities": g.number_of_nodes(),
            "total_relations": g.number_of_edges(),
            "shown_nodes": len(vis_nodes),
            "shown_edges": len(vis_edges),
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# Memory decay & recall reinforcement
# ═════════════════════════════════════════════════════════════════════════════

def _decay_multiplier(entity: dict) -> float:
    """Return a decay factor (0.7–1.0) based on recency of access/update.

    Mimics human memory: recently accessed or updated memories stay vivid,
    while unused ones gradually fade.  Recalling a memory refreshes it
    (the *testing effect*).

    * Within 7 days  → 1.0 (no decay)
    * 7–90 days      → linear from 1.0 → 0.7
    * 90+ days       → 0.7 (floor — never fully forgotten)
    """
    props = entity.get("properties", "{}")
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except (json.JSONDecodeError, TypeError):
            props = {}

    recalled_at = props.get("recalled_at", "") if isinstance(props, dict) else ""
    updated_at = entity.get("updated_at", "")

    # Use the most recent of recalled_at and updated_at
    fresh_ts = max(recalled_at, updated_at) if recalled_at else updated_at
    if not fresh_ts:
        return 0.7

    try:
        fresh_dt = datetime.fromisoformat(fresh_ts)
        days_old = (datetime.now() - fresh_dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.85

    if days_old <= 7:
        return 1.0
    if days_old >= 90:
        return 0.7
    # Linear decay: 1.0 at day 7 → 0.7 at day 90
    return 1.0 - 0.3 * (days_old - 7) / 83


def _touch_recalled(entity_ids: list[str]) -> None:
    """Update ``recalled_at`` in properties for entities just recalled.

    This 'refreshes' memories in the decay system — mimicking how human
    memory strengthens through recall (the *testing effect*).
    """
    if not entity_ids:
        return
    now = datetime.now().isoformat()
    conn = _get_conn()
    for eid in entity_ids:
        row = conn.execute(
            "SELECT properties FROM entities WHERE id = ?", (eid,)
        ).fetchone()
        if row:
            try:
                props = json.loads(row[0] or "{}")
            except (json.JSONDecodeError, TypeError):
                props = {}
            props["recalled_at"] = now
            conn.execute(
                "UPDATE entities SET properties = ? WHERE id = ?",
                (json.dumps(props), eid),
            )
    conn.commit()
    conn.close()


def touch_recalled(entity_ids: list[str]) -> None:
    """Public wrapper for reinforcing memories that were actually used."""
    _touch_recalled(entity_ids)


# ═════════════════════════════════════════════════════════════════════════════
# Graph-enhanced recall (used by agent.py auto-recall)
# ═════════════════════════════════════════════════════════════════════════════

_KEYWORD_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "about", "be", "by", "can",
    "do", "does", "for", "from", "have", "how", "i", "in", "is", "it",
    "me", "my", "of", "on", "or", "please", "tell", "that", "the", "this",
    "to", "was", "what", "when", "where", "who", "why", "with", "you",
}


def _keyword_terms(text: str) -> list[str]:
    import re

    return [
        term
        for term in re.findall(r"[a-z0-9][a-z0-9_-]*", _normalize_subject(text))
        if term not in _KEYWORD_STOPWORDS and len(term) > 1
    ]


def _split_csv_terms(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _phrase_norm(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", (value or "").lower())
    return " ".join(tokens)


def _field_keyword_score(entity: dict, query: str) -> tuple[float, str]:
    """Return a field-aware lexical score and the strongest matched field."""
    query_norm = _normalize_subject(query)
    query_phrase = _phrase_norm(query)
    terms = _keyword_terms(query)
    if not query_norm and not query_phrase:
        return 0.0, ""

    subject = entity.get("subject", "") or ""
    subject_norm = _normalize_subject(subject)
    aliases = _split_csv_terms(entity.get("aliases", "") or "")
    tags = _split_csv_terms(entity.get("tags", "") or "")
    description_norm = _normalize_subject(entity.get("description", "") or "")

    names = [subject, *aliases]
    name_norms = [_normalize_subject(name) for name in names if name]
    name_phrases = [_phrase_norm(name) for name in names if name]

    if any(name and (name == query_norm or _phrase_norm(name) == query_phrase) for name in name_norms):
        return 0.98, "subject_or_alias_exact"
    in_query_lengths = [
        len(name.split())
        for name in name_phrases
        if name and f" {name} " in f" {query_phrase} "
    ]
    if in_query_lengths:
        return min(0.97, 0.90 + (0.02 * max(in_query_lengths))), "subject_or_alias_in_query"
    if subject_norm and query_norm in subject_norm:
        return 0.86, "subject_contains_query"

    tag_norms = [_phrase_norm(tag) for tag in tags]
    if any(tag and (tag == query_phrase or f" {tag} " in f" {query_phrase} ") for tag in tag_norms):
        return 0.78, "tag_exact"

    if terms:
        subject_terms = set(_keyword_terms(subject))
        alias_terms = set().union(*(set(_keyword_terms(alias)) for alias in aliases)) if aliases else set()
        tag_terms = set().union(*(set(_keyword_terms(tag)) for tag in tags)) if tags else set()
        desc_terms = set(_keyword_terms(entity.get("description", "") or ""))
        query_terms = set(terms)

        name_terms = subject_terms | alias_terms
        if name_terms and name_terms <= query_terms:
            return 0.82, "subject_or_alias_terms"
        if tag_terms and query_terms & tag_terms:
            return 0.68, "tag_terms"
        if name_terms:
            overlap = len(query_terms & name_terms) / max(1, len(name_terms))
            if overlap >= 0.5:
                return 0.50 + (0.15 * overlap), "subject_or_alias_partial"
        if desc_terms:
            overlap = len(query_terms & desc_terms) / max(1, len(query_terms))
            if overlap >= 0.75:
                return 0.36, "description_terms"
            if overlap >= 0.5:
                return 0.24, "description_partial"

    if query_norm and query_norm in description_norm:
        return 0.30, "description_phrase"
    return 0.0, ""


def _keyword_candidate_hits(query: str, limit: int = 20) -> list[dict]:
    """Return keyword candidates scored by matched field strength."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM entities ORDER BY updated_at DESC").fetchall()
    conn.close()

    hits: list[dict] = []
    for row in rows:
        entity = dict(row)
        lexical_score, matched_field = _field_keyword_score(entity, query)
        if lexical_score <= 0:
            continue
        decay = _decay_multiplier(entity)
        entity["score"] = round(lexical_score * decay, 4)
        entity["semantic_score"] = 0.0
        entity["lexical_score"] = round(lexical_score, 4)
        entity["decay_multiplier"] = round(decay, 4)
        entity["via"] = "keyword"
        entity["relations"] = []
        entity["retrieval_debug"] = {
            "matched_field": matched_field,
            "keyword_score": round(lexical_score, 4),
        }
        hits.append(entity)

    hits.sort(key=lambda m: m["score"], reverse=True)
    return hits[:limit]


def _merge_recall_candidate(result_by_id: dict[str, dict], candidate: dict) -> None:
    existing = result_by_id.get(candidate["id"])
    debug = candidate.setdefault("retrieval_debug", {})
    sources = debug.setdefault("sources", [])
    via = candidate.get("via")
    if via and via not in sources:
        sources.append(via)
    if existing is None:
        result_by_id[candidate["id"]] = candidate
        return

    existing_debug = existing.setdefault("retrieval_debug", {})
    existing_sources = existing_debug.setdefault("sources", [])
    for source in sources:
        if source not in existing_sources:
            existing_sources.append(source)

    if candidate.get("score", 0) > existing.get("score", 0):
        preserved_sources = list(existing_sources)
        preserved_relations = existing.get("relations", [])
        preserved_debug = dict(existing_debug)
        existing.clear()
        existing.update(candidate)
        existing_debug = existing.setdefault("retrieval_debug", {})
        existing_debug.update(preserved_debug)
        existing_debug.update(debug)
        existing_debug["sources"] = list(dict.fromkeys(preserved_sources + sources))
        if preserved_relations and not existing.get("relations"):
            existing["relations"] = preserved_relations

    for key in ("semantic_score", "lexical_score", "bm25_score", "field_score", "decay_multiplier"):
        existing[key] = max(
            float(existing.get(key, 0) or 0),
            float(candidate.get(key, 0) or 0),
        )

    if len(existing_debug.get("sources", [])) > 1:
        existing["via"] = "hybrid"


def _expand_graph_recall_candidates(
    result_by_id: dict[str, dict],
    seeds: list[dict],
    *,
    hops: int,
) -> None:
    if not seeds or hops <= 0:
        return

    g = _ensure_graph()
    for seed in seeds:
        for nbr in get_neighbors(seed["id"], hops=hops):
            if nbr["id"] == seed["id"]:
                continue

            connecting_rels = []
            relation_confidences: list[float] = []
            if g.has_edge(seed["id"], nbr["id"]):
                for _ekey, edata in g[seed["id"]][nbr["id"]].items():
                    conf = float(edata.get("confidence", 1.0) or 1.0)
                    relation_confidences.append(conf)
                    connecting_rels.append({
                        "from": seed.get("subject", ""),
                        "to": nbr.get("subject", ""),
                        "type": edata.get("relation_type", "related"),
                        "confidence": conf,
                    })
            if g.has_edge(nbr["id"], seed["id"]):
                for _ekey, edata in g[nbr["id"]][seed["id"]].items():
                    conf = float(edata.get("confidence", 1.0) or 1.0)
                    relation_confidences.append(conf)
                    connecting_rels.append({
                        "from": nbr.get("subject", ""),
                        "to": seed.get("subject", ""),
                        "type": edata.get("relation_type", "related"),
                        "confidence": conf,
                    })

            relation_confidence = max(relation_confidences) if relation_confidences else 0.8
            nbr["semantic_score"] = 0.0
            nbr["lexical_score"] = 0.0
            nbr["decay_multiplier"] = round(_decay_multiplier(nbr), 4)
            nbr["score"] = round(seed.get("score", 0) * 0.5 * relation_confidence, 4)
            nbr["via"] = "graph"
            nbr["relations"] = connecting_rels
            nbr["retrieval_debug"] = {
                "seed_id": seed["id"],
                "seed_score": seed.get("score", 0),
                "relation_confidence": round(relation_confidence, 4),
                "sources": ["graph"],
            }
            _merge_recall_candidate(result_by_id, nbr)


def _is_strong_lexical_seed(candidate: dict) -> bool:
    debug = candidate.get("retrieval_debug", {}) or {}
    matched_field = debug.get("matched_field", "")
    lexical_score = float(candidate.get("lexical_score", 0) or 0)
    if matched_field.startswith("subject_or_alias"):
        return lexical_score >= 0.65
    if matched_field.startswith("tag_"):
        return lexical_score >= 0.75
    return False


def retrieve_memory_candidates(
    query: str,
    *,
    top_k: int = 8,
    threshold: float = 0.30,
    hops: int = 1,
    max_results: int = 20,
    include_keyword: bool = True,
    diagnostics: dict[str, object] | None = None,
) -> list[dict]:
    """Retrieve recall candidates without mutating ``recalled_at``.

    This is the safe path for auto-recall policy: candidates can be inspected,
    validated, and rejected without reinforcing memories the model never sees.
    """
    query = (query or "").strip()
    if not query:
        return []

    if diagnostics is not None:
        diagnostics.update(
            {
                "semantic_status": "not_attempted",
                "semantic_fallback_code": "",
                "semantic_fallback_detail": "",
                "semantic_wait_ms": 0,
            }
        )
    result_by_id: dict[str, dict] = {}
    seeds: list[dict] = []
    semantic_started = time.perf_counter()
    try:
        seeds = semantic_search(
            query,
            top_k=top_k,
            threshold=threshold,
            for_auto_recall=True,
        )
        if diagnostics is not None:
            diagnostics["semantic_status"] = "used" if seeds else "no_matches"
    except Exception as exc:
        seeds = []
        if diagnostics is not None:
            diagnostics["semantic_status"] = "fallback"
            diagnostics["semantic_fallback_code"] = str(
                getattr(exc, "code", "semantic_unavailable")
            )
            diagnostics["semantic_fallback_detail"] = str(
                getattr(exc, "detail", "Semantic memory recall is unavailable.")
            )
    finally:
        if diagnostics is not None:
            diagnostics["semantic_wait_ms"] = round(
                (time.perf_counter() - semantic_started) * 1000
            )

    decay_floor = threshold * 0.7
    for seed in seeds:
        semantic_score = float(seed.get("score", 0) or 0)
        decay = _decay_multiplier(seed)
        seed["semantic_score"] = round(semantic_score, 4)
        seed["lexical_score"] = 0.0
        seed["decay_multiplier"] = round(decay, 4)
        seed["score"] = round(semantic_score * decay, 4)
        seed["via"] = "semantic"
        seed["relations"] = []
        seed["retrieval_debug"] = {
            "semantic_score": round(semantic_score, 4),
            "decay_multiplier": round(decay, 4),
        }
        if seed["score"] >= decay_floor:
            _merge_recall_candidate(result_by_id, seed)

    if include_keyword:
        try:
            for entity in fts_search_entities(query, limit=max(10, max_results)):
                field_score, matched_field = _field_keyword_score(entity, query)
                bm25_score = float(entity.get("bm25_score", 0) or 0)
                lexical_score = max(field_score, bm25_score * 0.65)
                decay = _decay_multiplier(entity)
                entity["semantic_score"] = 0.0
                entity["field_score"] = round(field_score, 4)
                entity["lexical_score"] = round(lexical_score, 4)
                entity["decay_multiplier"] = round(decay, 4)
                entity["score"] = round((lexical_score * 0.9 + bm25_score * 0.1) * decay, 4)
                entity["via"] = "fts"
                entity["relations"] = []
                entity["retrieval_debug"] = {
                    "matched_field": matched_field,
                    "field_score": round(field_score, 4),
                    "bm25_score": round(bm25_score, 4),
                    "sources": ["fts"],
                }
                _merge_recall_candidate(result_by_id, entity)
        except Exception:
            pass

        try:
            for entity in _keyword_candidate_hits(query, limit=max(10, max_results)):
                _merge_recall_candidate(result_by_id, entity)
        except Exception:
            pass

    expansion_seeds = [
        candidate
        for candidate in result_by_id.values()
        if candidate.get("via") in {"semantic", "hybrid"} or _is_strong_lexical_seed(candidate)
    ]
    expansion_seeds = sorted(
        expansion_seeds,
        key=lambda s: s.get("score", 0),
        reverse=True,
    )[:max(8, top_k)]
    _expand_graph_recall_candidates(result_by_id, expansion_seeds, hops=hops)

    result = sorted(result_by_id.values(), key=lambda m: m.get("score", 0), reverse=True)
    return result[:max_results]

def graph_enhanced_recall(
    query: str,
    top_k: int = 5,
    threshold: float = 0.35,
    hops: int = 1,
    max_results: int = 20,
) -> list[dict]:
    """Semantic search + memory decay + graph expansion + keyword fallback.

    1. FAISS semantic search for top-k seed entities.
    2. Apply memory decay multiplier (recent/recalled → higher score).
    3. Expand 1-hop neighbors from the graph (scored relative to seed).
    4. Wiki vault full-text fallback when FAISS returns few results.
    5. Cap total results to *max_results* to control token usage.
    6. Reinforce recalled memories (touch ``recalled_at``).

    Each returned entity has extra keys:
        ``score`` — semantic similarity × decay (seeds), or derived (graph/wiki)
        ``via`` — ``'semantic'``, ``'graph'``, or ``'wiki'``
        ``relations`` — list of relations connecting this entity to its seed
    """
    result = retrieve_memory_candidates(
        query,
        top_k=top_k,
        threshold=threshold,
        hops=hops,
        max_results=max_results,
        include_keyword=True,
    )

    # Reinforce recalled memories (touch recalled_at timestamp)
    try:
        _touch_recalled([m["id"] for m in result])
    except Exception:
        pass  # non-critical — don't break recall if touch fails

    return result


# ═════════════════════════════════════════════════════════════════════════════
# Bulk operations
# ═════════════════════════════════════════════════════════════════════════════

def delete_all_entities() -> int:
    """Delete every entity and relation.  Returns entity count deleted."""
    conn = _get_conn()
    conn.execute("DELETE FROM relations")
    cur = conn.execute("DELETE FROM entities")
    conn.commit()
    conn.close()
    count = cur.rowcount

    global _graph, _graph_ready
    with _graph_lock:
        _graph = nx.MultiDiGraph()
        _graph_ready = True

    if count:
        rebuild_index()
        rebuild_fts_index()

    # Clean wiki vault files
    try:
        import row_bot.wiki_vault as wiki_vault
        wiki_vault.clear_wiki_folder()
    except Exception as exc:
        logger.debug("Wiki cleanup skipped: %s", exc)

    return count


def consolidate_duplicates(threshold: float = 0.90) -> int:
    """Scan all entities and merge near-duplicates by subject.

    For each pair sharing the same normalised subject and a semantic
    similarity score >= *threshold*, the shorter/older entry is merged
    into the longer/newer one and then deleted.

    Returns the number of entities removed.
    """
    all_entities = list_entities(limit=100_000)
    if len(all_entities) < 2:
        return 0

    # Group by normalised subject
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in all_entities:
        key = _normalize_subject(e["subject"])
        groups[key].append(e)

    removed = 0
    for _subj, entities in groups.items():
        if len(entities) < 2:
            continue

        deleted_ids: set[str] = set()
        for i, e1 in enumerate(entities):
            if e1["id"] in deleted_ids:
                continue
            for e2 in entities[i + 1:]:
                if e2["id"] in deleted_ids:
                    continue

                text1 = f"{e1['entity_type']} {e1['subject']} {e1['description']}"
                try:
                    hits = semantic_search(text1, top_k=5, threshold=threshold)
                except Exception:
                    continue

                hit_ids = {h["id"] for h in hits}
                if e2["id"] not in hit_ids:
                    continue

                # Near-duplicates — keep the richer one
                keep, drop = (
                    (e1, e2)
                    if len(e1.get("description", "")) >= len(e2.get("description", ""))
                    else (e2, e1)
                )

                # Merge tags
                merged_tags = ", ".join(
                    dict.fromkeys(
                        t.strip()
                        for t in (keep.get("tags", "") + "," + drop.get("tags", "")).split(",")
                        if t.strip()
                    )
                )

                # Merge aliases
                merged_aliases = ", ".join(
                    dict.fromkeys(
                        a.strip()
                        for a in (keep.get("aliases", "") + "," + drop.get("aliases", "")).split(",")
                        if a.strip()
                    )
                )

                # Merge properties
                keep_props = json.loads(keep.get("properties", "{}")) if isinstance(keep.get("properties"), str) else keep.get("properties", {})
                drop_props = json.loads(drop.get("properties", "{}")) if isinstance(drop.get("properties"), str) else drop.get("properties", {})
                merged_props = {**drop_props, **keep_props}  # keep's values win

                update_entity(
                    keep["id"],
                    keep["description"],
                    tags=merged_tags,
                    aliases=merged_aliases,
                    properties=merged_props,
                )

                # Re-point drop's relations to keep
                conn = _get_conn()
                for rel in conn.execute(
                    "SELECT * FROM relations WHERE source_id = ?", (drop["id"],)
                ).fetchall():
                    rel = dict(rel)
                    try:
                        conn.execute(
                            "UPDATE relations SET source_id = ?, updated_at = ? WHERE id = ?",
                            (keep["id"], datetime.now().isoformat(), rel["id"]),
                        )
                    except sqlite3.IntegrityError:
                        conn.execute("DELETE FROM relations WHERE id = ?", (rel["id"],))
                for rel in conn.execute(
                    "SELECT * FROM relations WHERE target_id = ?", (drop["id"],)
                ).fetchall():
                    rel = dict(rel)
                    try:
                        conn.execute(
                            "UPDATE relations SET target_id = ?, updated_at = ? WHERE id = ?",
                            (keep["id"], datetime.now().isoformat(), rel["id"]),
                        )
                    except sqlite3.IntegrityError:
                        conn.execute("DELETE FROM relations WHERE id = ?", (rel["id"],))
                conn.commit()
                conn.close()

                delete_entity(drop["id"])
                deleted_ids.add(drop["id"])
                removed += 1
                logger.info(
                    "Consolidated duplicate: kept %s (%s), removed %s",
                    keep["id"], keep["subject"], drop["id"],
                )

    # Reload graph after bulk consolidation
    if removed:
        _load_graph()

    return removed





# ═════════════════════════════════════════════════════════════════════════════
# Load graph on import (but lazily — only when first accessed)
# ═════════════════════════════════════════════════════════════════════════════

# We defer _load_graph() to first access via _ensure_graph() — this avoids
# blocking import time when the embedding model or FAISS aren't needed yet.
