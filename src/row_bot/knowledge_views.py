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
from typing import Any, Literal

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
class RelationPreview:
    relation_type: str
    direction: Literal["incoming", "outgoing"]
    peer_id: str
    peer_subject: str


@dataclass(frozen=True)
class EntityDetail:
    schema_version: int
    availability: Literal["available", "missing", "unavailable"]
    id: str
    revision: str
    entity_type: str
    subject: str
    description: str
    status: Literal["active", "needs_review", "superseded", "archived"]
    tier: Literal["core", "semantic", "episodic", "resource"]
    source: str
    source_bucket: Literal["manual", "extraction", "document", "wiki", "other"]
    confidence: float | None
    aliases: tuple[str, ...]
    alias_count: int
    tags: tuple[str, ...]
    tag_count: int
    created_at: str
    updated_at: str
    last_user_modified_at: str
    last_evolved_at: str
    last_recalled_at: str
    recall_count: int | None
    review_reason: str
    superseded_by: str
    supersedes: tuple[str, ...]
    source_context: tuple[str, ...]
    evidence: tuple[str, ...]
    evidence_count: int
    relations: tuple[RelationPreview, ...]
    relation_count: int
    can_archive: bool
    can_restore: bool
    can_resolve: bool


@dataclass(frozen=True)
class RecallCandidate:
    subject: str
    score: float | None


@dataclass(frozen=True)
class RecallDecision:
    timestamp: str
    outcome: Literal["used", "skipped"]
    reason: str
    candidate_count: int
    selected_count: int
    context_characters: int
    candidates: tuple[RecallCandidate, ...]
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RecallDecisionPage:
    schema_version: int
    availability: Literal["available", "missing", "unavailable", "corrupt"]
    items: tuple[RecallDecision, ...]


@dataclass(frozen=True)
class MemoryChange:
    timestamp: str
    action: str
    actor: str
    old_status: str
    new_status: str
    subjects: tuple[str, ...]
    additional_subjects: int
    reason: str


@dataclass(frozen=True)
class MemoryChangePage:
    schema_version: int
    availability: Literal["available", "missing", "unavailable", "corrupt"]
    items: tuple[MemoryChange, ...]


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
_LEGACY_MARKER_BYTES = 1024 * 1024
_LEGACY_MARKER_ITEMS = 4096
_AUDIT_FILE_BYTES = 512 * 1024
_ENTITY_STATUSES = {"active", "needs_review", "superseded", "archived"}
_ENTITY_SOURCES = {"manual", "extraction", "document", "wiki", "other"}
_ENTITY_TIERS = {"core", "semantic", "episodic", "resource"}


def _parameters(kind, query, selected, cursor, limit):
    selections = selected if isinstance(selected, (tuple, list)) else (selected,)
    if (
        not isinstance(query, str)
        or len(query) > 256
        or type(limit) is not int
        or not 1 <= limit <= 100
        or any(
            item is not None
            and (not isinstance(item, str) or not 1 <= len(item) <= 64)
            for item in selections
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


def _legacy_document_markers(root):
    """Read the legacy name catalog without importing its initializing owner."""

    path = root / "processed_files.json"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing", ()
    except OSError:
        return "unavailable", ()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or metadata.st_size > _LEGACY_MARKER_BYTES
    ):
        return "unavailable", ()
    try:
        with path.open("rb") as handle:
            encoded = handle.read(_LEGACY_MARKER_BYTES + 1)
        if len(encoded) > _LEGACY_MARKER_BYTES:
            return "unavailable", ()
        value = json.loads(encoded.decode("utf-8"))
        if (
            not isinstance(value, list)
            or len(value) > _LEGACY_MARKER_ITEMS
            or any(not isinstance(item, str) for item in value)
        ):
            return "unavailable", ()
        markers = tuple(
            {
                "id": "legacy:" + hashlib.sha256(name.encode("utf-8")).hexdigest(),
                "name": name,
                "order": order,
            }
            for order, name in enumerate(sorted(set(value)))
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return "unavailable", ()
    return "available", markers


def _legacy_document_page(markers, term, status, key, offset, expected, limit):
    digest = hashlib.sha256()
    items, total = [], 0
    for marker in markers:
        raw_name = marker["name"]
        item = DocumentSummary(
            marker["id"],
            raw_name[-256:].replace("\\", "/").rsplit("/", 1)[-1]
            or "Untitled document",
            "unknown",
            "unknown",
            "record_only",
            None,
            None,
            None,
            None,
            "",
            len(raw_name) > 256,
        )
        matched = (status is None or status == "unknown") and (
            not term or term.casefold() in raw_name.casefold()
        )
        digest.update(
            json.dumps(
                [asdict(item), matched], sort_keys=True, ensure_ascii=True
            ).encode()
        )
        digest.update(b"\n")
        if matched:
            if offset <= total < offset + limit:
                items.append(item)
            total += 1
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
    return DocumentSummaryPage(
        1, revision, tuple(items), total, next_cursor, "available"
    )


def _document_page_unavailable(expected):
    if expected is not None:
        raise KnowledgeViewError("cursor_expired")
    availability = "unavailable"
    return DocumentSummaryPage(
        1,
        hashlib.sha256(availability.encode()).hexdigest(),
        (),
        None,
        None,
        availability,
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
                rows = conn.execute(
                    sql(conn, ordinary_tables) if callable(sql) else sql, params
                )
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
    status: str | None = None,
    source: str | None = None,
    tier: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> EntitySummaryPage:
    """Search the entire saved subject/description/alias/tag library, literally."""
    if (
        status is not None
        and status not in _ENTITY_STATUSES
        or source is not None
        and source not in _ENTITY_SOURCES
        or tier is not None
        and tier not in _ENTITY_TIERS
    ):
        raise KnowledgeViewError("invalid_knowledge_query")
    term, key, offset, expected = _parameters(
        "entities", query, (entity_type, status, source, tier), cursor, limit
    )
    sql = """
        WITH normalized AS (
          SELECT *,
            CASE WHEN json_valid(properties) THEN
              CASE lower(COALESCE(json_extract(properties,'$.status'),'active'))
                WHEN 'needs_review' THEN 'needs_review' WHEN 'superseded' THEN 'superseded'
                WHEN 'archived' THEN 'archived' ELSE 'active' END
              ELSE 'active' END normalized_status,
            CASE WHEN json_valid(properties) AND lower(COALESCE(json_extract(properties,'$.memory_tier'),''))
              IN ('core','semantic','episodic','resource')
              THEN lower(json_extract(properties,'$.memory_tier'))
              WHEN source LIKE 'document:%' OR entity_type='media' THEN 'resource'
              ELSE 'semantic' END normalized_tier,
            CASE
              WHEN source LIKE 'document:%' OR (json_valid(properties) AND lower(COALESCE(json_extract(properties,'$.source_context.kind'),''))='document') THEN 'document'
              WHEN source LIKE 'wiki%' OR source LIKE 'dream%' OR (json_valid(properties) AND lower(COALESCE(json_extract(properties,'$.source_context.actor'),''))='wiki') THEN 'wiki'
              WHEN source IN ('extraction','background_extraction') OR (json_valid(properties) AND lower(COALESCE(json_extract(properties,'$.source_context.actor'),''))='extraction') THEN 'extraction'
              WHEN source IN ('','live','manual','chat') OR (json_valid(properties) AND lower(COALESCE(json_extract(properties,'$.source_context.actor'),''))='manual') THEN 'manual'
              ELSE 'other' END normalized_source
          FROM entities
        )
        SELECT substr(id,1,129) id, substr(entity_type,1,64) entity_type,
            substr(subject,1,256) subject, substr(description,1,1000) description,
            substr(updated_at,1,64) updated_at,
            (length(subject)>256 OR length(description)>1000 OR length(entity_type)>64) truncated,
            ((? IS NULL OR entity_type=?) AND (? IS NULL OR normalized_status=?)
              AND (? IS NULL OR normalized_source=?) AND (? IS NULL OR normalized_tier=?)
              AND (?='' OR instr(lower(subject),lower(?))>0
              OR instr(lower(description),lower(?))>0 OR instr(lower(aliases),lower(?))>0
              OR instr(lower(tags),lower(?))>0)) matched
        FROM normalized ORDER BY id
    """
    return _read(
        get_memory_db_path(create_parent=False),
        {"entities": "id entity_type subject description aliases tags properties source updated_at"},
        sql,
        (
            entity_type,
            entity_type,
            status,
            status,
            source,
            source,
            tier,
            tier,
            term,
            term,
            term,
            term,
            term,
        ),
        _entity,
        EntitySummaryPage,
        key,
        offset,
        expected,
        limit,
    )


@dataclass(frozen=True)
class _RawValue:
    value: str


@dataclass(frozen=True)
class _RawPage:
    schema_version: int
    revision: str
    items: tuple[_RawValue, ...]
    total: int | None
    next_cursor: str | None
    availability: str


def _bounded_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\0", "")[:limit]


def _string_items(value: Any, *, limit: int = 12, item_limit: int = 128) -> tuple[tuple[str, ...], int]:
    values = value.split(",") if isinstance(value, str) else value if isinstance(value, list) else []
    items: list[str] = []
    for raw in values[:256]:
        item = _bounded_text(raw, item_limit).strip()
        if item and item not in items:
            items.append(item)
    return tuple(items[:limit]), len(items)


def _safe_properties(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError, RecursionError):
        return {}


def _source_bucket(source: str, props: dict[str, Any]) -> str:
    context = props.get("source_context") if isinstance(props.get("source_context"), dict) else {}
    actor = str(context.get("actor") or "").lower()
    kind = str(context.get("kind") or "").lower()
    lowered = source.lower()
    if lowered.startswith("document:") or kind == "document":
        return "document"
    if actor == "wiki" or lowered.startswith(("wiki", "dream")):
        return "wiki"
    if actor == "extraction" or lowered in {"extraction", "background_extraction"}:
        return "extraction"
    if actor == "manual" or lowered in {"", "live", "manual", "chat"}:
        return "manual"
    return "other"


def _empty_detail(availability: str) -> EntityDetail:
    return EntityDetail(1, availability, "", "", "", "", "", "active", "semantic", "", "manual", None,
                        (), 0, (), 0, "", "", "", "", "", None, "", "", (), (), (), 0, (), 0,
                        False, False, False)


def read_saved_entity_detail(entity_id: str) -> EntityDetail:
    """Return one bounded saved entity and relation preview without initializing owners."""
    try:
        entity_id = _identity(entity_id)
    except ValueError:
        raise KnowledgeViewError("invalid_knowledge_query") from None
    columns = {
        "id": 128, "entity_type": 64, "subject": 256, "description": 32768,
        "aliases": 4096, "tags": 4096, "properties": 65536, "source": 4096,
        "created_at": 128, "updated_at": 128,
    }

    def build(row):
        value = {key: row[key] for key in columns}
        if any(not isinstance(value[key], str) or len(value[key]) > columns[key] for key in columns):
            raise ValueError("Invalid saved detail")
        return _RawValue(json.dumps(value, ensure_ascii=True, separators=(",", ":")))

    selected = ",".join(f'substr("{key}",1,{limit + 1}) "{key}"' for key, limit in columns.items())
    page = _read(
        get_memory_db_path(create_parent=False), {"entities": " ".join(columns)},
        f"SELECT {selected},1 matched FROM entities WHERE id=?", (entity_id,), build,
        _RawPage, "entity-detail:" + entity_id, 0, None, 1,
    )
    if page.availability != "available":
        return _empty_detail(page.availability)
    if not page.items:
        return _empty_detail("missing")
    entity = json.loads(page.items[0].value)
    props = _safe_properties(entity["properties"])
    status = str(props.get("status") or "active").lower()
    if status not in _ENTITY_STATUSES:
        status = "active"
    tier = str(props.get("memory_tier") or "").lower()
    if tier not in _ENTITY_TIERS:
        tier = "resource" if entity["source"].startswith("document:") or entity["entity_type"] == "media" else "semantic"
    confidence = props.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    aliases, alias_count = _string_items(entity["aliases"])
    tags, tag_count = _string_items(entity["tags"])
    supersedes, _ = _string_items(props.get("supersedes", []), limit=4)
    evidence_values = props.get("evidence")
    if not isinstance(evidence_values, list):
        evidence_values = [evidence_values] if evidence_values else []
    evidence: list[str] = []
    for item in evidence_values[:32]:
        if isinstance(item, dict):
            item = item.get("quote") or item.get("text") or item.get("content") or item.get("summary")
        text = _bounded_text(item, 256).strip()
        if text and text not in evidence:
            evidence.append(text)
    context = props.get("source_context") if isinstance(props.get("source_context"), dict) else {}
    context_lines = []
    for key in ("actor", "kind", "thread_name", "thread_id", "display_name", "document_title", "window_count", "chunk", "page"):
        value = context.get(key)
        if value not in (None, "", []):
            context_lines.append(f"{key.replace('_', ' ')}: {_bounded_text(str(value), 256)}")
    relation_items: tuple[RelationPreview, ...] = ()
    relation_count = 0
    relation_sql = """
      SELECT substr(r.id,1,129) relation_id, substr(r.relation_type,1,65) relation_type,
        CASE WHEN r.source_id=? THEN 'outgoing' ELSE 'incoming' END direction,
        substr(CASE WHEN r.source_id=? THEN r.target_id ELSE r.source_id END,1,129) peer_id,
        substr(e.subject,1,257) peer_subject, 1 matched
      FROM relations r JOIN entities e ON e.id=CASE WHEN r.source_id=? THEN r.target_id ELSE r.source_id END
      WHERE r.source_id=? OR r.target_id=? ORDER BY r.updated_at DESC,r.id
    """
    def relation(row):
        if row["direction"] not in {"incoming", "outgoing"}:
            raise ValueError
        return _RawValue(json.dumps({key: row[key] for key in ("relation_type", "direction", "peer_id", "peer_subject")}))
    relations = _read(
        get_memory_db_path(create_parent=False),
        {"entities": "id subject", "relations": "id source_id target_id relation_type updated_at"},
        relation_sql, (entity_id, entity_id, entity_id, entity_id, entity_id), relation,
        _RawPage, "entity-relations:" + entity_id, 0, None, 5,
    )
    if relations.availability == "available":
        relation_count = relations.total or 0
        relation_items = tuple(RelationPreview(**json.loads(item.value)) for item in relations.items)
    recall_count = props.get("recall_count")
    recall_count = recall_count if type(recall_count) is int and 0 <= recall_count <= 2**53 - 1 else None
    evidence_count = props.get("evidence_count")
    evidence_count = evidence_count if type(evidence_count) is int and 0 <= evidence_count <= 2**53 - 1 else len(evidence_values)
    revision = hashlib.sha256(json.dumps(entity, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return EntityDetail(
        1, "available", entity["id"], revision, entity["entity_type"], entity["subject"], entity["description"],
        status, tier, _bounded_text(entity["source"], 4096), _source_bucket(entity["source"], props), confidence,
        aliases, alias_count, tags, tag_count, entity["created_at"], entity["updated_at"],
        _bounded_text(props.get("last_user_modified_at"), 128), _bounded_text(props.get("last_evolved_at"), 128),
        _bounded_text(props.get("recalled_at"), 128), recall_count, _bounded_text(props.get("review_reason"), 1024),
        _bounded_text(props.get("superseded_by"), 128), supersedes, tuple(context_lines[:10]), tuple(evidence[:3]),
        max(evidence_count, len(evidence)), relation_items, relation_count, status != "archived", status == "archived",
        status == "needs_review",
    )


def _audit_rows(filename: str) -> tuple[str, list[dict[str, Any]]]:
    root = get_row_bot_data_dir(create=False).absolute()
    path = root / filename
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing", []
    except OSError:
        return "unavailable", []
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or metadata.st_size > _AUDIT_FILE_BYTES
    ):
        return "unavailable", []
    try:
        if path.resolve().parent != root.resolve():
            return "unavailable", []
        raw = path.read_bytes()
        if len(raw) > _AUDIT_FILE_BYTES:
            return "unavailable", []
        value = json.loads(raw.decode("utf-8") or "[]")
        if not isinstance(value, list) or len(value) > 1000:
            return "corrupt", []
        return "available", [item for item in value if isinstance(item, dict)]
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        return "corrupt", []


def _small_number(value: Any) -> int:
    return value if type(value) is int and 0 <= value <= 2**31 - 1 else 0


def read_recent_recall_decisions() -> RecallDecisionPage:
    availability, rows = _audit_rows("memory_recall_trace.json")
    if availability != "available":
        return RecallDecisionPage(1, availability, ())
    identifiers: list[str] = []
    for row in rows[-10:]:
        values = row.get("selected_ids") if isinstance(row.get("selected_ids"), list) else []
        scores = row.get("top_scores") if isinstance(row.get("top_scores"), list) else []
        values = [*values, *(item.get("id") for item in scores if isinstance(item, dict))]
        for value in values[:16]:
            if (
                isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value)
                and value not in identifiers
            ):
                identifiers.append(value)
    subjects: dict[str, str] = {}
    for identifier in identifiers[:60]:
        detail = read_saved_entity_detail(identifier)
        if detail.availability == "available":
            subjects[identifier] = detail.subject
    items: list[RecallDecision] = []
    for row in reversed(rows[-10:]):
        selected = row.get("selected") if isinstance(row.get("selected"), list) else []
        selected_ids = row.get("selected_ids") if isinstance(row.get("selected_ids"), list) else []
        top_scores = row.get("top_scores") if isinstance(row.get("top_scores"), list) else []
        candidates: list[RecallCandidate] = []
        raw_candidates = selected or top_scores
        for candidate in raw_candidates[:3]:
            if not isinstance(candidate, dict):
                continue
            identifier = _bounded_text(candidate.get("id"), 128).strip()
            subject = _bounded_text(
                candidate.get("subject") or subjects.get(identifier), 256
            ).strip()
            if not subject:
                subject = "Saved memory"
            score = candidate.get("score", candidate.get("final"))
            try:
                score = max(0.0, min(1.0, float(score))) if score is not None else None
            except (TypeError, ValueError):
                score = None
            candidates.append(RecallCandidate(subject, score))
        if not candidates:
            candidates = [
                RecallCandidate(subjects.get(str(identifier), "Saved memory"), None)
                for identifier in selected_ids[:3]
            ]
        rejected = row.get("rejections") if isinstance(row.get("rejections"), list) else row.get("rejected")
        if not isinstance(rejected, list):
            rejected = []
        reasons = []
        for value in rejected[:3]:
            if isinstance(value, dict):
                value = value.get("reason") or value.get("code")
            text = _bounded_text(value, 256).strip()
            if text:
                reasons.append(text)
        selected_count = _small_number(row.get("selected_count")) or len(selected) or len(selected_ids)
        items.append(RecallDecision(
            _bounded_text(row.get("timestamp") or row.get("ts"), 128),
            "used" if bool(row.get("allowed")) and selected_count else "skipped",
            _bounded_text(row.get("reason"), 512), _small_number(row.get("candidates_seen") or row.get("candidate_count")),
            selected_count, _small_number(row.get("context_chars") or row.get("block_chars")), tuple(candidates), tuple(reasons),
        ))
    return RecallDecisionPage(1, "available", tuple(items))


def read_memory_change_log() -> MemoryChangePage:
    availability, rows = _audit_rows("memory_evolution_journal.json")
    if availability != "available":
        return MemoryChangePage(1, availability, ())
    identifiers: list[str] = []
    for row in rows[-20:]:
        values = row.get("entity_ids") if isinstance(row.get("entity_ids"), list) else []
        if row.get("entity_id"):
            values = [row["entity_id"], *values]
        for value in values[:16]:
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value) and value not in identifiers:
                identifiers.append(value)
    subjects: dict[str, str] = {}
    for identifier in identifiers[:60]:
        detail = read_saved_entity_detail(identifier)
        if detail.availability == "available":
            subjects[identifier] = detail.subject
    items: list[MemoryChange] = []
    for row in reversed(rows[-20:]):
        values = row.get("entity_ids") if isinstance(row.get("entity_ids"), list) else []
        if row.get("entity_id"):
            values = [row["entity_id"], *values]
        values = list(dict.fromkeys(value for value in values if isinstance(value, str)))[:100]
        labels = tuple(_bounded_text(subjects.get(value) or "Saved memory", 256) for value in values[:3])
        items.append(MemoryChange(
            _bounded_text(row.get("timestamp"), 128), _bounded_text(row.get("action"), 128),
            _bounded_text(row.get("actor"), 128), _bounded_text(row.get("old_status"), 64),
            _bounded_text(row.get("new_status"), 64), labels, max(0, len(values) - len(labels)),
            _bounded_text(row.get("reason"), 512),
        ))
    return MemoryChangePage(1, "available", tuple(items))


def list_saved_documents(
    *,
    query: str = "",
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> DocumentSummaryPage:
    """Read saved jobs, records, and legacy markers without probing live indexes."""
    term, key, offset, expected = _parameters("documents", query, status, cursor, limit)
    if status is not None and status not in _STATUSES | {"unknown"}:
        raise KnowledgeViewError("invalid_knowledge_query")
    root = get_row_bot_data_dir(create=False)
    marker_availability, markers = _legacy_document_markers(root)
    if marker_availability == "unavailable":
        return _document_page_unavailable(expected)
    database = root / "document_ingestion" / "jobs.db"
    if not database.is_file() and marker_availability == "available":
        return _legacy_document_page(
            markers, term, status, key, offset, expected, limit
        )
    marker_json = json.dumps(markers, ensure_ascii=True, separators=(",", ":"))
    sql = """
        WITH legacy AS (
            SELECT json_extract(value,'$.id') id, json_extract(value,'$.name') name,
                json_extract(value,'$.order') catalog_order
            FROM json_each(?)
        ), saved AS (
            SELECT j.id, j.original_name name, j.status, j.stage,
                j.index_progress_current index_current, j.index_progress_total index_total,
                j.extraction_progress_current extraction_current,
                j.extraction_progress_total extraction_total, j.updated_at,
                1 has_job, (r.document_id IS NOT NULL) has_record, 0 removed,
                (r.document_id IS NOT NULL AND j.completed_at!='' AND r.completed_at!=''
                 AND j.completed_at=r.completed_at AND j.staged_path=r.staged_path
                 AND j.content_sha256=r.content_sha256 AND j.size_bytes=r.size_bytes) consistent,
                0 legacy_order, j.id catalog_order
            FROM document_jobs j LEFT JOIN document_records r ON r.document_id=j.id
            UNION ALL
            SELECT r.document_id, r.original_name, 'unknown', 'unknown',
                NULL,NULL,NULL,NULL,r.completed_at,0,1,0,0,0,r.document_id
            FROM document_records r LEFT JOIN document_jobs j ON j.id=r.document_id
            WHERE j.id IS NULL
        ), catalog AS (
            SELECT * FROM saved
            UNION ALL
            SELECT legacy.id, legacy.name, 'unknown', 'unknown',
                NULL,NULL,NULL,NULL,'',0,0,0,0,1,legacy.catalog_order
            FROM legacy
            WHERE NOT EXISTS (
                SELECT 1 FROM document_records r WHERE r.original_name=legacy.name
            )
        )
        SELECT substr(id,1,129) id, substr(name,-256) name, substr(status,1,64) status,
            substr(stage,1,64) stage, index_current,index_total,extraction_current,extraction_total,
            substr(updated_at,1,64) updated_at,has_job,has_record,consistent,removed,
            length(name)>256 truncated,
            ((? IS NULL OR (CASE WHEN status IN
              ('staging','queued','indexing','searchable','extracting','completed','failed','cancelled','skipped_duplicate')
              THEN status ELSE 'unknown' END)=?)
              AND (?='' OR instr(lower(name),lower(?))>0)) matched
        FROM catalog ORDER BY legacy_order, catalog_order
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
        if any(row[6] != 0 for row in columns) or not {
            "target",
            "snapshot",
            "result",
        }.issubset({row[1] for row in columns}):
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
        database,
        required,
        removal_projection,
        (marker_json, status, status, term, term),
        _document,
        DocumentSummaryPage,
        key,
        offset,
        expected,
        limit,
    )
