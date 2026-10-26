"""Fact validity for memories: when each fact held, what replaced it, and an "as of" view (stale skills, on only).

A memory's facts are its description, its own properties (not Row-Bot's bookkeeping: status, tier, evidence, review
notes, timestamps) and its relations. Each value is a row of ``stale_fact_validity`` in the knowledge graph's database
with ``valid_from``, ``valid_to`` (empty while it holds) and ``superseded_by`` (the row that replaced it). A row is
written when a memory is saved, closed and replaced when that fact is updated, and closed when the memory is
deleted; a memory superseded by another (``memory_evolution.mark_superseded``) closes its description with the new
memory's as ``superseded_by``.

The table is created, and the existing memories and relations get their rows (the additive migration:
``valid_from`` = the memory's last update, cause ``migrate``), the first time the setting is on in a process. Nothing
here runs while it is off. Every row carries the write's cause and its origin (where it came from:
:func:`row_bot.stale_skills.current_origin`); Dream's own merges, enrichment and inferences (:data:`HOUSEKEEPING`)
are never treated as changes in the world.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from row_bot import stale_skills

logger = logging.getLogger(__name__)

TABLE = "stale_fact_validity"
DESCRIPTION = "description"
#: Row-Bot's own bookkeeping in a memory's properties: not facts about the world.
BOOKKEEPING = frozenset({
    "status", "memory_tier", "confidence", "evidence", "evidence_count", "source_context", "last_user_modified_at",
    "last_evolved_at", "review_reason", "review_candidate", "evolution_reason", "supersedes", "superseded_by",
    "aliases", "tags", "recalled_at", "category",
})
#: Provenance keys (where and how a memory was saved), by prefix: not facts either.
BOOKKEEPING_PREFIXES = ("_", "source_", "extraction_", "learning_")
#: Causes that are Row-Bot tidying what it knows, not the world changing.
HOUSEKEEPING = frozenset({"merge", "enrich", "infer", "decay", "migrate"})
_migrated: set[str] = set()


def _connection(writing: str = "") -> sqlite3.Connection:
    """The graph's database, migrated once per process (``writing``: a memory being saved now, not migrated)."""

    import row_bot.knowledge_graph as kg

    conn = kg._get_conn()  # noqa: SLF001 - the graph's own database
    path = str(kg.DB_PATH)
    if path not in _migrated:
        migrate(conn, skip=writing)
        _migrated.add(path)
    return conn


def _iso(moment: datetime | None = None) -> str:
    return (moment or stale_skills.now()).isoformat(timespec="seconds")


def tracked(entity: dict[str, Any]) -> dict[str, str]:
    """A memory's facts: ``{"description": text, "property:<key>": json}``."""

    found = {DESCRIPTION: str(entity.get("description") or "")}
    raw = entity.get("properties") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            raw = {}
    for key, value in (raw if isinstance(raw, dict) else {}).items():
        if key in BOOKKEEPING or key.startswith(BOOKKEEPING_PREFIXES) or key.endswith("_at"):
            continue
        found[f"property:{key}"] = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return found


def migrate(conn: sqlite3.Connection, *, skip: str = "") -> int:
    """Create the table and give every memory and relation without rows its current values (idempotent, additive).

    Returns the rows added."""

    conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, fact TEXT NOT NULL, target_id TEXT NOT NULL DEFAULT '',
        value TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT, superseded_by TEXT,
        cause TEXT NOT NULL DEFAULT '', ended_cause TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT '')""")
    if "origin" not in {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")}:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN origin TEXT NOT NULL DEFAULT ''")  # a table from before origins
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_entity ON {TABLE}(entity_id, fact)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_ended ON {TABLE}(valid_to)")
    added = 0
    recorded = _iso()
    rows = conn.execute(f"SELECT * FROM entities e WHERE NOT EXISTS (SELECT 1 FROM {TABLE} v "
                        "WHERE v.entity_id = e.id)").fetchall()
    for row in rows:
        if row["id"] == skip:
            continue
        for fact, value in tracked(dict(row)).items():
            conn.execute(f"INSERT INTO {TABLE}(id,entity_id,fact,value,valid_from,cause,recorded_at) "
                         "VALUES(?,?,?,?,?,?,?)", (_new_id(), row["id"], fact, value,
                                                   row["updated_at"] or row["created_at"], "migrate", recorded))
            added += 1
    relations = conn.execute(f"SELECT * FROM relations r WHERE NOT EXISTS (SELECT 1 FROM {TABLE} v WHERE "
                             "v.entity_id = r.source_id AND v.fact = 'relation:' || r.relation_type "
                             "AND v.target_id = r.target_id)").fetchall()
    for row in relations:
        conn.execute(f"INSERT INTO {TABLE}(id,entity_id,fact,target_id,value,valid_from,cause,recorded_at) "
                     "VALUES(?,?,?,?,?,?,?,?)", (_new_id(), row["source_id"], f"relation:{row['relation_type']}",
                                                 row["target_id"], row["target_id"], row["created_at"], "migrate",
                                                 recorded))
        added += 1
    conn.commit()
    if added:
        logger.info("Stale skills: %d fact validity rows added for existing memories.", added)
    return added


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _open_rows(conn: sqlite3.Connection, entity_id: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(f"SELECT * FROM {TABLE} WHERE entity_id = ? AND valid_to IS NULL AND fact NOT LIKE "
                        "'relation:%'", (entity_id,)).fetchall()
    return {row["fact"]: row for row in rows}


# ── Writes (called by the knowledge graph while the setting is on) ───────────


def entity_written(before: dict[str, Any] | None, after: dict[str, Any]) -> list[dict[str, Any]]:
    """Record a memory saved or updated: new facts open, changed facts close and are replaced. Returns the changes."""

    cause = stale_skills.current_cause()
    origin = stale_skills.current_origin()
    at = _iso()
    changes: list[dict[str, Any]] = []
    opened: list[str] = []
    conn = _connection(writing=str(after["id"]))
    try:
        current = _open_rows(conn, after["id"])
        if not current and before:
            # A memory saved before the setting was on: its old values first (as the migration would have).
            for fact, value in tracked(before).items():
                conn.execute(f"INSERT INTO {TABLE}(id,entity_id,fact,value,valid_from,cause,recorded_at) "
                             "VALUES(?,?,?,?,?,?,?)", (_new_id(), after["id"], fact, value,
                                                       before.get("updated_at") or before.get("created_at") or at,
                                                       "migrate", at))
            current = _open_rows(conn, after["id"])
        new = not current
        for fact, value in tracked(after).items():
            row = current.pop(fact, None)
            if row is not None and row["value"] == value:
                continue
            row_id = _new_id()
            conn.execute(f"INSERT INTO {TABLE}(id,entity_id,fact,value,valid_from,cause,recorded_at,origin) "
                         "VALUES(?,?,?,?,?,?,?,?)", (row_id, after["id"], fact, value, at, cause, at, origin))
            opened.append(row_id)
            if row is not None:
                conn.execute(f"UPDATE {TABLE} SET valid_to = ?, superseded_by = ?, ended_cause = ? WHERE id = ?",
                             (at, row_id, cause, row["id"]))
                changes.append({"entity_id": after["id"], "fact": fact, "old": row["value"], "new": value,
                                "old_validity": row["id"], "new_validity": row_id})
        for fact, row in current.items():
            conn.execute(f"UPDATE {TABLE} SET valid_to = ?, ended_cause = ? WHERE id = ?", (at, cause or "removed",
                                                                                         row["id"]))
        conn.commit()
    finally:
        conn.close()
    subject = str(after.get("subject") or "")
    if new:
        stale_skills.log("write", entity_id=after["id"], subject=subject, entity_type=after.get("entity_type"),
                         text=str(after.get("description") or ""), origin=origin, validity=opened)
    for change in changes:
        stale_skills.log("supersede", subject=subject, origin=origin, **change)
    candidate = _review_candidate(after)
    if candidate and candidate != _review_candidate(before or {}):
        stale_skills.log("conflict", entity_id=after["id"], subject=subject, incoming=candidate)
    return changes


def _review_candidate(entity: dict[str, Any]) -> str:
    raw = entity.get("properties") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return ""
    found = (raw or {}).get("review_candidate") if isinstance(raw, dict) else None
    return str((found or {}).get("content") or (found or {}).get("description") or "") if isinstance(found, dict) else ""


def entity_deleted(entity: dict[str, Any]) -> None:
    cause = stale_skills.current_cause() or "delete"
    at = _iso()
    conn = _connection()
    try:
        conn.execute(f"UPDATE {TABLE} SET valid_to = ?, ended_cause = ? WHERE (entity_id = ? OR target_id = ?) "
                     "AND valid_to IS NULL", (at, cause, entity["id"], entity["id"]))
        conn.commit()
    finally:
        conn.close()
    stale_skills.log("merge" if cause == "merge" else "delete", entity_id=entity["id"],
                     subject=str(entity.get("subject") or ""))


def entity_superseded(old: dict[str, Any], new: dict[str, Any]) -> None:
    """``old`` was superseded by ``new``: its description closes, replaced by ``new``'s."""

    at = _iso()
    conn = _connection()
    try:
        current = _open_rows(conn, old["id"]).get(DESCRIPTION)
        successor = _open_rows(conn, new["id"]).get(DESCRIPTION)
        if current is None or successor is None:
            return
        conn.execute(f"UPDATE {TABLE} SET valid_to = ?, superseded_by = ?, ended_cause = 'supersede' WHERE id = ?",
                     (at, successor["id"], current["id"]))
        conn.commit()
    finally:
        conn.close()
    stale_skills.log("supersede", entity_id=old["id"], subject=str(old.get("subject") or ""), fact=DESCRIPTION,
                     old=current["value"], new=successor["value"], new_entity_id=new["id"], origin=successor["origin"],
                     old_validity=current["id"], new_validity=successor["id"])


def relation_added(relation: dict[str, Any]) -> None:
    at = _iso()
    row_id = _new_id()
    conn = _connection()
    try:
        conn.execute(f"INSERT INTO {TABLE}(id,entity_id,fact,target_id,value,valid_from,cause,recorded_at,origin) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (row_id, relation["source_id"],
                                                   f"relation:{relation['relation_type']}", relation["target_id"],
                                                   relation["target_id"], at, stale_skills.current_cause(), at,
                                                   stale_skills.current_origin()))
        conn.commit()
    finally:
        conn.close()
    stale_skills.log("write", relation_id=relation.get("id"), source_id=relation["source_id"],
                     target_id=relation["target_id"], relation_type=relation["relation_type"], validity=[row_id])


def relation_deleted(relation: dict[str, Any]) -> None:
    conn = _connection()
    try:
        conn.execute(f"UPDATE {TABLE} SET valid_to = ?, ended_cause = ? WHERE entity_id = ? AND fact = ? AND "
                     "target_id = ? AND valid_to IS NULL", (_iso(), stale_skills.current_cause() or "delete",
                                                            relation["source_id"],
                                                            f"relation:{relation['relation_type']}",
                                                            relation["target_id"]))
        conn.commit()
    finally:
        conn.close()
    stale_skills.log("delete", relation_id=relation.get("id"), relation_type=relation.get("relation_type"))


# ── Reads ────────────────────────────────────────────────────────────────────


def as_of(entity_id: str, when: datetime) -> dict[str, str]:
    """The memory's facts as they stood at ``when`` (empty: it didn't exist yet, or was gone)."""

    moment = _iso(when)
    conn = _connection()
    try:
        rows = conn.execute(f"SELECT fact, target_id, value FROM {TABLE} WHERE entity_id = ? AND valid_from <= ? AND "
                            "(valid_to IS NULL OR valid_to > ?) ORDER BY valid_from", (entity_id, moment, moment)).fetchall()
    finally:
        conn.close()
    found: dict[str, str] = {}
    for row in rows:
        key = row["fact"] if not row["fact"].startswith("relation:") else f"{row['fact']}:{row['target_id']}"
        found[key] = row["value"]
    return found


def apply_as_of(candidates: list[dict[str, Any]], when: datetime) -> list[dict[str, Any]]:
    """Recall candidates as of ``when``: a memory that didn't exist then is left out, the rest say what they said
    then."""

    found = []
    for candidate in candidates:
        values = as_of(str(candidate.get("id") or ""), when)
        if DESCRIPTION not in values:
            continue
        found.append({**candidate, "description": values[DESCRIPTION], "content": values[DESCRIPTION],
                      "as_of": _iso(when)})
    return found


def history(entity_id: str) -> list[dict[str, Any]]:
    conn = _connection()
    try:
        rows = conn.execute(f"SELECT * FROM {TABLE} WHERE entity_id = ? ORDER BY valid_from, recorded_at",
                            (entity_id,)).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def changes(since: str = "", until: str = "") -> list[dict[str, Any]]:
    """Changes in the world after ``since`` (and up to ``until``): a memory's description replaced (``update``,
    ``supersede``), and new memories (``write``), each with the ``origin`` of its new value. Dream's own tidying is
    left out. Oldest first."""

    until = until or "9999"
    conn = _connection()
    try:
        replaced = conn.execute(
            f"SELECT old.entity_id, old.value AS old, new.value AS new, new.entity_id AS new_entity_id, "
            f"old.valid_to AS at, old.ended_cause AS cause, old.id AS old_validity, new.id AS new_validity, "
            f"new.origin AS origin "
            f"FROM {TABLE} old JOIN {TABLE} new ON new.id = old.superseded_by WHERE old.fact = ? AND "
            "old.valid_to > ? AND old.valid_to <= ?", (DESCRIPTION, since, until)).fetchall()
        written = conn.execute(
            f"SELECT v.entity_id, v.value AS new, v.valid_from AS at, v.cause, v.id AS new_validity, v.origin "
            f"FROM {TABLE} v "
            f"WHERE v.fact = ? AND v.valid_from > ? AND v.valid_from <= ? AND NOT EXISTS (SELECT 1 FROM {TABLE} p "
            "WHERE p.superseded_by = v.id) AND NOT EXISTS (SELECT 1 FROM "
            f"{TABLE} e WHERE e.entity_id = v.entity_id AND e.fact = ? AND e.valid_from < v.valid_from)",
            (DESCRIPTION, since, until, DESCRIPTION)).fetchall()
    finally:
        conn.close()
    found = [{**dict(row), "kind": "supersede" if row["cause"] == "supersede" else "update"} for row in replaced
             if row["cause"] not in HOUSEKEEPING]
    found += [{**dict(row), "kind": "write", "old": None, "new_entity_id": row["entity_id"]} for row in written
              if row["cause"] not in HOUSEKEEPING]
    return sorted(found, key=lambda item: str(item["at"]))


__all__ = ["BOOKKEEPING", "DESCRIPTION", "HOUSEKEEPING", "TABLE", "apply_as_of", "as_of", "changes",
           "entity_deleted", "entity_superseded", "entity_written", "history", "migrate", "relation_added",
           "relation_deleted", "tracked"]
