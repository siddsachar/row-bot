"""Passive, bounded projections of the canonical knowledge and document stores.

These reads never import initializing owners or reconcile their data. Each page
is one SQLite snapshot; pages from different stores are not a joint snapshot.
Search uses SQLite's literal substring matching (ASCII case insensitive).
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import stat
import re
import sqlite3
import time
from typing import Literal

from row_bot.data_paths import get_memory_db_path, get_row_bot_data_dir


class KnowledgeViewError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EntitySummary:
    id: str
    entity_type: str
    subject: str
    description: str
    updated_at: str
    truncated: bool
    saved_state: Literal["saved"] = "saved"
    semantic_state: Literal["unknown"] = "unknown"


@dataclass(frozen=True)
class DocumentSummary:
    id: str
    name: str
    status: str
    stage: str
    record_state: Literal["saved", "job_only", "record_only", "partial", "removed"]
    index_current: int | None
    index_total: int | None
    extraction_current: int | None
    extraction_total: int | None
    updated_at: str
    truncated: bool
    searchability: Literal["unknown"] = "unknown"


@dataclass(frozen=True)
class EntitySummaryPage:
    schema_version: int
    revision: str
    items: tuple[EntitySummary, ...]
    total: int | None
    next_cursor: str | None
    availability: Literal["available", "missing", "unavailable"]


@dataclass(frozen=True)
class DocumentSummaryPage:
    schema_version: int
    revision: str
    items: tuple[DocumentSummary, ...]
    total: int | None
    next_cursor: str | None
    availability: Literal["available", "missing", "unavailable"]


_STATUSES = {
    "staging",
    "queued",
    "indexing",
    "searchable",
    "extracting",
    "completed",
    "failed",
    "cancelled",
    "skipped_duplicate",
}
_STAGES = {
    "upload",
    "parse",
    "embed",
    "index_commit",
    "knowledge_map",
    "knowledge_reduce",
    "knowledge_commit",
    "finalize",
}


_SQLITE_VALUE_LIMIT = 16 * 1024 * 1024
_SQLITE_STEP_LIMIT = 10_000_000
_QUERY_SECONDS = 2.0


def _parameters(kind, query, selected, cursor, limit):
    if (
        not isinstance(query, str)
        or len(query) > 256
        or type(limit) is not int
        or not 1 <= limit <= 100
        or (
            selected is not None
            and (not isinstance(selected, str) or not 1 <= len(selected) <= 64)
        )
    ):
        raise KnowledgeViewError("invalid_knowledge_query")
    term = query.strip()
    key = hashlib.sha256(json.dumps([kind, term, selected, limit]).encode()).hexdigest()
    offset, expected = 0, None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not re.fullmatch(
                r"[A-Za-z0-9_=-]{1,1024}", cursor
            ):
                raise ValueError
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (
                not isinstance(value, dict)
                or set(value) != {"v", "key", "revision", "offset"}
                or type(value["v"]) is not int
                or value["v"] != 1
                or value["key"] != key
                or type(value["offset"]) is not int
                or not 0 < value["offset"] <= 10**12
                or not isinstance(value["revision"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["revision"])
            ):
                raise ValueError
            offset, expected = value["offset"], value["revision"]
        except (ValueError, TypeError, UnicodeError):
            raise KnowledgeViewError("cursor_expired") from None
    return term, key, offset, expected


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise ValueError("Invalid saved identity")
    return value


def _number(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _text(value):
    if not isinstance(value, str):
        raise ValueError("Invalid saved text")
    return value


def _entity(row):
    return EntitySummary(
        _identity(row["id"]),
        _text(row["entity_type"]),
        _text(row["subject"]),
        _text(row["description"]),
        _text(row["updated_at"]),
        bool(row["truncated"]),
    )


def _document(row):
    status = row["status"] if row["status"] in _STATUSES else "unknown"
    stage = row["stage"] if row["stage"] in _STAGES else "unknown"
    state = (
        "saved"
        if row["has_job"] and row["has_record"]
        else ("job_only" if row["has_job"] else "record_only")
    )
    if row["has_job"] and status == "completed" and not row["consistent"]:
        state = "partial"
    if row["has_job"] and not row["has_record"] and row["removed"]:
        state = "removed"
    name = (
        _text(row["name"]).replace("\\", "/").rsplit("/", 1)[-1] or "Untitled document"
    )
    return DocumentSummary(
        _identity(row["id"]),
        name,
        status,
        stage,
        state,
        *(
            _number(row[key])
            for key in (
                "index_current",
                "index_total",
                "extraction_current",
                "extraction_total",
            )
        ),
        _text(row["updated_at"]),
        bool(row["truncated"]),
    )


def _read(path, required, sql, params, build, page, key, offset, expected, limit):
    availability = "available"
    digest = hashlib.sha256()
    items, total = [], 0
    try:
        root = get_row_bot_data_dir(create=False).absolute()
        # The configured root is authoritative; child links cannot redirect a
        # canonical store or its SQLite coordination files into another owner.
        for target in (
            path,
            path.with_name(path.name + "-wal"),
            path.with_name(path.name + "-shm"),
        ):
            candidate = root
            for component in target.absolute().relative_to(root).parts:
                candidate = candidate / component
                try:
                    metadata = candidate.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(metadata.st_mode) or getattr(
                    metadata, "st_file_attributes", 0
                ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                    raise ValueError("Linked saved store")
        if not path.is_file():
            availability = "missing"
        else:
            # mode=ro prevents database creation; SQLite may still create WAL
            # coordination sidecars. query_only rejects SQL data/schema effects.
            conn = sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1
            )
            try:
                deadline = time.monotonic() + _QUERY_SECONDS
                steps = 0

                def interrupted():
                    nonlocal steps
                    steps += 1000
                    return steps >= _SQLITE_STEP_LIMIT or time.monotonic() >= deadline

                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _SQLITE_VALUE_LIMIT)
                conn.set_progress_handler(interrupted, 1000)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                ordinary_tables = {
                    row[1]
                    for row in conn.execute("PRAGMA table_list")
                    if row[0] == "main" and row[2] == "table"
                }
                for table, columns in required.items():
                    if table not in ordinary_tables:
                        raise ValueError("Unsupported saved schema")
                    if any(
                        row[6] != 0
                        for row in conn.execute(f'PRAGMA table_xinfo("{table}")')
                    ):
                        raise ValueError("Computed saved schema")
                    found = {
                        row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
                    }
                    if not set(columns.split()).issubset(found):
                        raise ValueError("Unsupported saved schema")
                rows = conn.execute(sql(conn, ordinary_tables) if callable(sql) else sql, params)
                while batch := rows.fetchmany(128):
                    if time.monotonic() >= deadline:
                        raise ValueError("Saved query budget exceeded")
                    for row in batch:
                        item = build(row)
                        digest.update(
                            json.dumps(
                                [asdict(item), bool(row["matched"])],
                                sort_keys=True,
                                ensure_ascii=True,
                            ).encode()
                        )
                        digest.update(b"\n")
                        if row["matched"]:
                            if offset <= total < offset + limit:
                                items.append(item)
                            total += 1
                if time.monotonic() >= deadline:
                    raise ValueError("Saved query budget exceeded")
            finally:
                conn.close()
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        availability = "unavailable"
    if availability != "available":
        if expected is not None:
            raise KnowledgeViewError("cursor_expired")
        return page(
            1,
            hashlib.sha256(availability.encode()).hexdigest(),
            (),
            None,
            None,
            availability,
        )
    revision = digest.hexdigest()
    if expected is not None and (expected != revision or offset >= total):
        raise KnowledgeViewError("cursor_expired")
    next_cursor = None
    if offset + len(items) < total:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "key": key,
                    "revision": revision,
                    "offset": offset + len(items),
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
    return page(1, revision, tuple(items), total, next_cursor, availability)


def list_saved_entities(
    *,
    query: str = "",
    entity_type: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> EntitySummaryPage:
    """Search the entire saved subject/description/alias/tag library, literally."""
    term, key, offset, expected = _parameters(
        "entities", query, entity_type, cursor, limit
    )
    sql = """
        SELECT substr(id,1,129) id, substr(entity_type,1,64) entity_type,
            substr(subject,1,256) subject, substr(description,1,1000) description,
            substr(updated_at,1,64) updated_at,
            (length(subject)>256 OR length(description)>1000 OR length(entity_type)>64) truncated,
            ((? IS NULL OR entity_type=?) AND (?='' OR instr(lower(subject),lower(?))>0
              OR instr(lower(description),lower(?))>0 OR instr(lower(aliases),lower(?))>0
              OR instr(lower(tags),lower(?))>0)) matched
        FROM entities ORDER BY id
    """
    return _read(
        get_memory_db_path(create_parent=False),
        {"entities": "id entity_type subject description aliases tags updated_at"},
        sql,
        (entity_type, entity_type, term, term, term, term, term),
        _entity,
        EntitySummaryPage,
        key,
        offset,
        expected,
        limit,
    )


def list_saved_documents(
    *,
    query: str = "",
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> DocumentSummaryPage:
    """Read saved jobs and orphan records without checking paths or live indexes."""
    term, key, offset, expected = _parameters("documents", query, status, cursor, limit)
    if status is not None and status not in _STATUSES | {"unknown"}:
        raise KnowledgeViewError("invalid_knowledge_query")
    sql = """
        WITH saved AS (
            SELECT j.id, j.original_name name, j.status, j.stage,
                j.index_progress_current index_current, j.index_progress_total index_total,
                j.extraction_progress_current extraction_current,
                j.extraction_progress_total extraction_total, j.updated_at,
                1 has_job, (r.document_id IS NOT NULL) has_record, 0 removed,
                (r.document_id IS NOT NULL AND j.completed_at!='' AND r.completed_at!=''
                 AND j.completed_at=r.completed_at AND j.staged_path=r.staged_path
                 AND j.content_sha256=r.content_sha256 AND j.size_bytes=r.size_bytes) consistent
            FROM document_jobs j LEFT JOIN document_records r ON r.document_id=j.id
            UNION ALL
            SELECT r.document_id, r.original_name, 'unknown', 'unknown',
                NULL,NULL,NULL,NULL,r.completed_at,0,1,0,0
            FROM document_records r LEFT JOIN document_jobs j ON j.id=r.document_id
            WHERE j.id IS NULL
        )
        SELECT substr(id,1,129) id, substr(name,-256) name, substr(status,1,64) status,
            substr(stage,1,64) stage, index_current,index_total,extraction_current,extraction_total,
            substr(updated_at,1,64) updated_at,has_job,has_record,consistent,removed,
            length(name)>256 truncated,
            ((? IS NULL OR (CASE WHEN status IN
              ('staging','queued','indexing','searchable','extracting','completed','failed','cancelled','skipped_duplicate')
              THEN status ELSE 'unknown' END)=?)
              AND (?='' OR instr(lower(name),lower(?))>0)) matched
        FROM saved ORDER BY id
    """
    required = {
        "document_jobs": "id original_name status stage index_progress_current index_progress_total extraction_progress_current extraction_progress_total updated_at completed_at staged_path content_sha256 size_bytes",
        "document_records": "document_id original_name completed_at staged_path content_sha256 size_bytes",
    }
    def removal_projection(conn, ordinary_tables):
        # Legacy stores without the K07 table remain readable. When present,
        # consult its canonical snapshot within this same read transaction.
        if "document_removals" not in ordinary_tables:
            return sql
        columns = list(conn.execute('PRAGMA table_xinfo("document_removals")'))
        if any(row[6] != 0 for row in columns) or not {"target", "snapshot", "result"}.issubset({row[1] for row in columns}):
            raise ValueError("Unsupported saved removal schema")
        removed = """EXISTS(SELECT 1 FROM document_removals d
            WHERE d.target=j.id AND json_extract(d.result,'$.document_id')=j.id
            AND json_extract(d.result,'$.status')='complete'
            AND json_extract(d.snapshot,'$.record.content_sha256')=j.content_sha256
            AND json_extract(d.snapshot,'$.record.size_bytes')=j.size_bytes
            AND json_extract(d.snapshot,'$.record.staged_path')=j.staged_path
            AND json_extract(d.snapshot,'$.record.original_name')=j.original_name) removed"""
        return sql.replace("0 removed", removed, 1)
    return _read(
        get_row_bot_data_dir(create=False) / "document_ingestion" / "jobs.db",
        required,
        removal_projection,
        (status, status, term, term),
        _document,
        DocumentSummaryPage,
        key,
        offset,
        expected,
        limit,
    )
