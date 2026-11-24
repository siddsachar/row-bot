"""The company knowledge graph's database calls (migration 0009).

Like ``store``, each takes a connection inside a principal's transaction (perhaps an admin's) or the worker's
(``store.worker_transaction``); row-level security decides what it sees and may change. Column lists are fixed
here and values always go as parameters.

- **The worker**: ``wait_for_model``, ``claim_graph``, ``start_graph``, ``next_chunk``, ``store_chunk_found``,
  ``finish_graph`` for documents; ``next_fact``, ``store_fact_found``, ``hold_fact`` for facts.
- **Orphans**: ``mentioned_in_document`` and ``mentioned_in_fact`` name what a source's mentions point at, before
  they go; ``retire_orphans`` then retires those left with none (``retired_auto``).
- **Curation**: ``update_entity``, ``merge_entity``, ``set_entity_status``, ``add_relation``,
  ``set_relation_status``.
- **Reading**: ``graph``, ``get_entity``, ``entity_relations``, ``entity_mentions``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from row_bot_org.brain import graph

ACTIVE, RETIRED, MERGED = "active", "retired", "merged_into"
EXTRACTED, MANUAL = "extracted", "manual"
# As store.STALE_CLAIM and store.MAX_CLAIMS, for the graph's own claims.
STALE_CLAIM = "15 minutes"
MAX_CLAIMS = 3
# How long a document or fact waits after a call that failed (a spent budget, the endpoint down).
RETRY_AFTER = "1 hour"
MAX_EDGES_PER_NODE = 3
MAX_DETAIL_RELATIONS = 200
MAX_DETAIL_MENTIONS = 50

_ENTITY_COLUMNS = ("id, space_id, name, entity_type, description, aliases, status, merged_into, retired_auto,"
                   " edited_by, edited_at, created_at, updated_at")
_RELATION_COLUMNS = ("id, space_id, source_id, target_id, relation_type, origin, status, retired_auto, edited_by,"
                     " edited_at, created_at, updated_at")
_EDITED = "edited_by = row_bot_org.row_bot_principal()::uuid, edited_at = now(), updated_at = now()"


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _id(value: Any) -> str | None:
    return None if value is None else str(value)


def _alias_keys(aliases: Sequence[str]) -> list[str]:
    return [graph.name_key(alias) for alias in aliases]


# -- rows -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class EntityRow:
    id: str
    space_id: str
    name: str
    entity_type: str
    description: str
    aliases: tuple[str, ...]
    status: str
    merged_into: str | None
    retired_auto: bool
    edited_by: str | None
    edited_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "space_id": self.space_id, "subject": self.name, "entity_type": self.entity_type,
                "description": self.description, "aliases": list(self.aliases), "status": self.status,
                "merged_into": self.merged_into, "edited_by": self.edited_by, "edited_at": _time(self.edited_at),
                "created_at": _time(self.created_at), "updated_at": _time(self.updated_at)}


def _entity(row: tuple) -> EntityRow:
    return EntityRow(str(row[0]), str(row[1]), row[2], row[3], row[4], tuple(row[5]), row[6], _id(row[7]), row[8],
                     _id(row[9]), row[10], row[11], row[12])


@dataclass(frozen=True)
class RelationRow:
    id: str
    space_id: str
    source_id: str
    target_id: str
    relation_type: str
    origin: str
    status: str
    retired_auto: bool
    edited_by: str | None
    edited_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "space_id": self.space_id, "source_id": self.source_id, "target_id": self.target_id,
                "relation_type": self.relation_type, "origin": self.origin, "status": self.status,
                "edited_by": self.edited_by, "edited_at": _time(self.edited_at), "created_at": _time(self.created_at),
                "updated_at": _time(self.updated_at)}


def _relation(row: tuple) -> RelationRow:
    return RelationRow(str(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4], row[5], row[6], row[7],
                       _id(row[8]), row[9], row[10], row[11])


def get_entity(conn: psycopg.Connection, entity_id: str, *, lock: bool = False) -> EntityRow | None:
    row = conn.execute(f"SELECT {_ENTITY_COLUMNS} FROM row_bot_org.brain_entities WHERE id = %s"
                       + (" FOR UPDATE" if lock else ""), (entity_id,)).fetchone()
    return None if row is None else _entity(row)


def get_relation(conn: psycopg.Connection, relation_id: str) -> RelationRow | None:
    row = conn.execute(f"SELECT {_RELATION_COLUMNS} FROM row_bot_org.brain_relations WHERE id = %s",
                       (relation_id,)).fetchone()
    return None if row is None else _relation(row)


def entity_by_name(conn: psycopg.Connection, space_id: str, name: str, entity_type: str, *,
                   lock: bool = False) -> EntityRow | None:
    """The entity ``name`` (compared by ``graph.name_key``) and ``entity_type`` have in ``space_id``."""
    row = conn.execute(
        f"SELECT {_ENTITY_COLUMNS} FROM row_bot_org.brain_entities"
        " WHERE space_id = %s AND name_key = %s AND entity_type = %s" + (" FOR UPDATE" if lock else ""),
        (space_id, graph.name_key(name), entity_type)).fetchone()
    return None if row is None else _entity(row)


def _entity_by_alias(conn: psycopg.Connection, space_id: str, name: str, entity_type: str) -> EntityRow | None:
    """The oldest entity of ``entity_type`` in ``space_id`` (not merged away) that has ``name`` as an alias."""
    row = conn.execute(
        f"SELECT {_ENTITY_COLUMNS} FROM row_bot_org.brain_entities"
        " WHERE space_id = %s AND entity_type = %s AND status <> 'merged_into' AND alias_keys @> ARRAY[%s::text]"
        " ORDER BY created_at, id LIMIT 1 FOR UPDATE", (space_id, entity_type, graph.name_key(name))).fetchone()
    return None if row is None else _entity(row)


# -- orphans ----------------------------------------------------------------------------------------

Mentioned = tuple[list[str], list[str]]


def _mentioned(rows: list[tuple]) -> Mentioned:
    return (sorted({str(entity) for entity, _ in rows if entity is not None}),
            sorted({str(relation) for _, relation in rows if relation is not None}))


def mentioned_in_document(conn: psycopg.Connection, document_id: str) -> Mentioned:
    """The entities and relations a document's mentions point at (read before they go)."""
    return _mentioned(conn.execute(
        "SELECT m.entity_id, m.relation_id FROM row_bot_org.brain_mentions m"
        " JOIN row_bot_org.brain_chunks c ON c.id = m.chunk_id WHERE c.document_id = %s", (document_id,)).fetchall())


def mentioned_in_fact(conn: psycopg.Connection, fact_id: str) -> Mentioned:
    return _mentioned(conn.execute(
        "SELECT entity_id, relation_id FROM row_bot_org.brain_mentions WHERE fact_id = %s", (fact_id,)).fetchall())


def clear_fact_mentions(conn: psycopg.Connection, fact_id: str) -> None:
    """A fact's mentions go (it was edited or retired), and what they leave with none is retired."""
    mentioned = mentioned_in_fact(conn, fact_id)
    conn.execute("DELETE FROM row_bot_org.brain_mentions WHERE fact_id = %s", (fact_id,))
    retire_orphans(conn, mentioned)


def retire_orphans(conn: psycopg.Connection, mentioned: Mentioned) -> None:
    """Of ``mentioned``, the active entities and extracted relations left with no mention are retired
    (``retired_auto``: found again, they are active again). Relations made by hand stay."""
    entities, relations = mentioned
    if entities:
        conn.execute(
            "UPDATE row_bot_org.brain_entities e SET status = 'retired', retired_auto = true, updated_at = now()"
            " WHERE e.id = ANY(%s::uuid[]) AND e.status = 'active'"
            " AND NOT EXISTS (SELECT 1 FROM row_bot_org.brain_mentions m WHERE m.entity_id = e.id)", (entities,))
    if relations:
        conn.execute(
            "UPDATE row_bot_org.brain_relations r SET status = 'retired', retired_auto = true, updated_at = now()"
            " WHERE r.id = ANY(%s::uuid[]) AND r.status = 'active' AND r.origin = 'extracted'"
            " AND NOT EXISTS (SELECT 1 FROM row_bot_org.brain_mentions m WHERE m.relation_id = r.id)", (relations,))


# -- the worker: what it found ----------------------------------------------------------------------

def _found_entity(conn: psycopg.Connection, space_id: str, found: graph.FoundEntity) -> str:
    """The entity ``found`` is in ``space_id``: the one its name and type have (or, merged away, the one it was
    merged into), else one that has its name as an alias, else a new one. A description fills an empty one,
    aliases are added, and one retired for losing its mentions is active again."""
    row = entity_by_name(conn, space_id, found.name, found.entity_type, lock=True)
    if row is None:
        row = _entity_by_alias(conn, space_id, found.name, found.entity_type)
    if row is None:
        aliases = list(found.aliases)
        added = conn.execute(
            "INSERT INTO row_bot_org.brain_entities (space_id, name, name_key, entity_type, description, aliases,"
            " alias_keys) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (space_id, name_key, entity_type) DO NOTHING RETURNING id",
            (space_id, found.name, found.key, found.entity_type, found.description, aliases,
             _alias_keys(aliases))).fetchone()
        if added is not None:
            return str(added[0])
        row = entity_by_name(conn, space_id, found.name, found.entity_type, lock=True)  # Another worker's, just now.
    if row.status == MERGED:
        row = get_entity(conn, row.merged_into, lock=True)
    aliases = graph.merge_aliases(row.name, list(row.aliases), [found.name], list(found.aliases))
    description = row.description or found.description
    if aliases != list(row.aliases) or description != row.description or row.retired_auto:
        conn.execute(
            "UPDATE row_bot_org.brain_entities SET aliases = %s, alias_keys = %s, description = %s,"
            " status = CASE WHEN retired_auto THEN 'active' ELSE status END, retired_auto = false,"
            " updated_at = now() WHERE id = %s", (aliases, _alias_keys(aliases), description, row.id))
    return row.id


def _store_found(conn: psycopg.Connection, space_id: str, found: graph.Found, *, chunk_id: int | None = None,
                 fact_id: str | None = None) -> None:
    """``found`` in ``space_id``'s graph, each entity and relation with a mention of the chunk or fact."""
    def mention(entity_id: str | None, relation_id: str | None) -> None:
        conn.execute("INSERT INTO row_bot_org.brain_mentions (space_id, entity_id, relation_id, chunk_id, fact_id)"
                     " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                     (space_id, entity_id, relation_id, chunk_id, fact_id))

    ids: dict[str, str] = {}
    for entity in found.entities:
        entity_id = _found_entity(conn, space_id, entity)
        ids.setdefault(entity.key, entity_id)
        mention(entity_id, None)
    for relation in found.relations:
        source, target = ids.get(relation.source), ids.get(relation.target)
        if source is None or target is None or source == target:
            continue  # Two names of one entity (an alias, a merge).
        (relation_id,) = conn.execute(
            "INSERT INTO row_bot_org.brain_relations (space_id, source_id, target_id, relation_type)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (space_id, source_id, target_id, relation_type) DO UPDATE SET"
            " status = CASE WHEN brain_relations.retired_auto THEN 'active' ELSE brain_relations.status END,"
            " updated_at = CASE WHEN brain_relations.retired_auto THEN now() ELSE brain_relations.updated_at END,"
            " retired_auto = false RETURNING id", (space_id, source, target, relation.relation_type)).fetchone()
        mention(None, str(relation_id))


# -- the worker: documents --------------------------------------------------------------------------

def wait_for_model(conn: psycopg.Connection, reason: str) -> None:
    """No usable chat model: ready documents still to read wait, saying ``reason`` (one statement, no claim).
    They are claimed once there is one."""
    conn.execute(
        "UPDATE row_bot_org.brain_documents SET graph_status = 'waiting', graph_reason = %(reason)s,"
        " graph_retry_at = NULL WHERE status = 'ready' AND (graph_status = 'pending'"
        " OR (graph_status = 'waiting' AND graph_retry_at IS NULL AND graph_reason IS DISTINCT FROM %(reason)s))",
        {"reason": reason})


@dataclass(frozen=True)
class GraphClaim:
    """A ready document the worker took to read for the graph."""

    document_id: str
    space_id: str
    title: str
    version: int
    # The version whose mentions are stored, and the next part to read in it.
    graph_version: int | None
    next: int
    uploaded_by: str
    uploader_groups: tuple[str, ...]
    attempts: int

    @property
    def fresh(self) -> bool:
        """This version hasn't been started: its reading begins at the first part."""
        return self.graph_version != self.version

    @property
    def worn_out(self) -> bool:
        return self.attempts > MAX_CLAIMS


def claim_graph(conn: psycopg.Connection, *, skip: Sequence[str] = ()) -> GraphClaim | None:
    """The oldest ready document whose graph is to read (pending, waiting with its wait over, or claimed by a
    worker that stopped), now ``extracting``; None when there is none. ``skip``: the documents this pass took."""
    row = conn.execute(
        "UPDATE row_bot_org.brain_documents SET graph_status = 'extracting', graph_claimed_at = now(),"
        " graph_attempts = graph_attempts + 1"
        " WHERE id = (SELECT id FROM row_bot_org.brain_documents"
        "             WHERE status = 'ready' AND id <> ALL(%(skip)s::uuid[])"
        "               AND (graph_status = 'pending'"
        "                    OR (graph_status = 'waiting' AND (graph_retry_at IS NULL OR graph_retry_at <= now()))"
        f"                   OR (graph_status = 'extracting' AND graph_claimed_at < now() - interval '{STALE_CLAIM}'))"
        "             ORDER BY uploaded_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
        " RETURNING id, space_id, title, version, graph_version, graph_next, uploaded_by, uploader_groups,"
        " graph_attempts", {"skip": list(skip)}).fetchone()
    if row is None:
        return None
    return GraphClaim(str(row[0]), str(row[1]), row[2], row[3], row[4], row[5], str(row[6]), tuple(row[7]), row[8])


def _current(conn: psycopg.Connection, claim: GraphClaim, *, started: bool = True) -> bool:
    """Whether ``claim`` still holds (the same version, still extracting; locked until the transaction ends)."""
    row = conn.execute("SELECT version, graph_version, graph_status FROM row_bot_org.brain_documents"
                       " WHERE id = %s FOR UPDATE", (claim.document_id,)).fetchone()
    return (row is not None and row[0] == claim.version and row[2] == "extracting"
            and (not started or row[1] == claim.version))


def start_graph(conn: psycopg.Connection, claim: GraphClaim) -> bool:
    """Begin reading the claimed version: mentions of any other go (orphans retired), from the first part."""
    if not _current(conn, claim, started=False):
        return False
    mentioned = mentioned_in_document(conn, claim.document_id)
    conn.execute("DELETE FROM row_bot_org.brain_mentions m USING row_bot_org.brain_chunks c"
                 " WHERE c.id = m.chunk_id AND c.document_id = %s", (claim.document_id,))
    retire_orphans(conn, mentioned)
    conn.execute("UPDATE row_bot_org.brain_documents SET graph_version = version, graph_next = 0 WHERE id = %s",
                 (claim.document_id,))
    return True


def next_chunk(conn: psycopg.Connection, document_id: str, start: int, most: int) -> tuple[int, int, str] | None:
    """The document's first part from ordinal ``start`` that is among its first ``most``: (id, ordinal, text)."""
    row = conn.execute("SELECT id, ordinal, body FROM row_bot_org.brain_chunks WHERE document_id = %s"
                       " AND ordinal >= %s AND ordinal < %s ORDER BY ordinal LIMIT 1",
                       (document_id, start, most)).fetchone()
    return None if row is None else (row[0], row[1], row[2])


def chunk_count(conn: psycopg.Connection, document_id: str) -> int:
    return conn.execute("SELECT count(*) FROM row_bot_org.brain_chunks WHERE document_id = %s",
                        (document_id,)).fetchone()[0]


def store_chunk_found(conn: psycopg.Connection, claim: GraphClaim, chunk_id: int, ordinal: int,
                      found: graph.Found) -> bool:
    """What one part gave, and the next part to read; False when the document changed since the claim."""
    if not _current(conn, claim):
        return False
    _store_found(conn, claim.space_id, found, chunk_id=chunk_id)
    conn.execute("UPDATE row_bot_org.brain_documents SET graph_next = %s, graph_claimed_at = now() WHERE id = %s",
                 (ordinal + 1, claim.document_id))
    return True


def finish_graph(conn: psycopg.Connection, claim: GraphClaim, status: str, reason: str | None, *,
                 retry: bool = False, reset_attempts: bool = True) -> bool:
    """The claim's outcome: ``done``, ``failed``, ``waiting`` (``retry``: again after ``RETRY_AFTER``) or back to
    ``pending`` (to carry on next pass). ``reset_attempts`` False keeps counting claims towards ``MAX_CLAIMS``."""
    if not _current(conn, claim, started=False):
        return False
    conn.execute(
        "UPDATE row_bot_org.brain_documents SET graph_status = %(status)s, graph_reason = %(reason)s,"
        " graph_claimed_at = NULL,"
        f" graph_retry_at = CASE WHEN %(retry)s THEN now() + interval '{RETRY_AFTER}' END,"
        " graph_attempts = CASE WHEN %(reset)s THEN 0 ELSE graph_attempts END WHERE id = %(id)s",
        {"status": status, "reason": reason, "retry": retry, "reset": reset_attempts, "id": claim.document_id})
    return True


# -- the worker: facts ------------------------------------------------------------------------------

@dataclass(frozen=True)
class FactToRead:
    id: str
    space_id: str
    text: str
    updated_at: datetime
    written_by: str
    writer_groups: tuple[str, ...]


def next_fact(conn: psycopg.Connection, *, tried: Sequence[str], writers_failed: Sequence[str]) -> FactToRead | None:
    """The oldest fact in force not yet read for the graph (nor held back), leaving out those this pass ``tried``
    and those of writers whose calls failed in it."""
    row = conn.execute(
        "SELECT id, space_id, body, updated_at, written_by, writer_groups FROM row_bot_org.brain_facts"
        " WHERE retired_at IS NULL AND graph_done_at IS NULL AND (graph_retry_at IS NULL OR graph_retry_at <= now())"
        " AND id <> ALL(%s::uuid[]) AND written_by <> ALL(%s::uuid[]) ORDER BY created_at, id LIMIT 1",
        (list(tried), list(writers_failed))).fetchone()
    if row is None:
        return None
    return FactToRead(str(row[0]), str(row[1]), row[2], row[3], str(row[4]), tuple(row[5]))


def _fact_unchanged(conn: psycopg.Connection, fact: FactToRead) -> bool:
    return conn.execute("SELECT 1 FROM row_bot_org.brain_facts WHERE id = %s AND updated_at = %s"
                        " AND retired_at IS NULL AND graph_done_at IS NULL FOR UPDATE",
                        (fact.id, fact.updated_at)).fetchone() is not None


def store_fact_found(conn: psycopg.Connection, fact: FactToRead, found: graph.Found) -> bool:
    """What a fact gave; False (nothing stored) when it was edited or retired since it was read."""
    if not _fact_unchanged(conn, fact):
        return False
    _store_found(conn, fact.space_id, found, fact_id=fact.id)
    conn.execute("UPDATE row_bot_org.brain_facts SET graph_done_at = now(), graph_retry_at = NULL WHERE id = %s",
                 (fact.id,))
    return True


def hold_fact(conn: psycopg.Connection, fact: FactToRead) -> None:
    """A fact whose call failed is read again after ``RETRY_AFTER``."""
    conn.execute(f"UPDATE row_bot_org.brain_facts SET graph_retry_at = now() + interval '{RETRY_AFTER}'"
                 " WHERE id = %s AND updated_at = %s", (fact.id, fact.updated_at))


# -- curation ---------------------------------------------------------------------------------------

def update_entity(conn: psycopg.Connection, entity_id: str, *, name: str, entity_type: str, description: str,
                  aliases: Sequence[str]) -> EntityRow | None:
    """An entity's new name, type, description and aliases, curated by the transaction's principal.
    ``psycopg.errors.UniqueViolation`` when another entity of the space has that name and type."""
    row = conn.execute(
        "UPDATE row_bot_org.brain_entities SET name = %s, name_key = %s, entity_type = %s, description = %s,"
        f" aliases = %s, alias_keys = %s, {_EDITED} WHERE id = %s RETURNING {_ENTITY_COLUMNS}",
        (name, graph.name_key(name), entity_type, description, list(aliases), _alias_keys(aliases),
         entity_id)).fetchone()
    return None if row is None else _entity(row)


def _move_relations(conn: psycopg.Connection, source: EntityRow, target: EntityRow) -> None:
    """``source``'s relations become ``target``'s: one that would join ``target`` to itself goes, and one that
    ``target`` already has gives it its mentions (and keeps it active, or made by hand) and goes."""
    rows = conn.execute(
        "SELECT id, source_id, target_id, relation_type, status, origin FROM row_bot_org.brain_relations"
        " WHERE source_id = %(a)s OR target_id = %(a)s ORDER BY id FOR UPDATE", {"a": source.id}).fetchall()
    for relation_id, start, end, label, status, origin in rows:
        start = target.id if str(start) == source.id else str(start)
        end = target.id if str(end) == source.id else str(end)
        if start == end:
            conn.execute("DELETE FROM row_bot_org.brain_relations WHERE id = %s", (relation_id,))
            continue
        same = conn.execute(
            "SELECT id FROM row_bot_org.brain_relations WHERE space_id = %s AND source_id = %s AND target_id = %s"
            " AND relation_type = %s FOR UPDATE", (source.space_id, start, end, label)).fetchone()
        if same is None:
            conn.execute("UPDATE row_bot_org.brain_relations SET source_id = %s, target_id = %s, updated_at = now()"
                         " WHERE id = %s", (start, end, relation_id))
            continue
        conn.execute(
            "DELETE FROM row_bot_org.brain_mentions m WHERE m.relation_id = %(old)s AND EXISTS (SELECT 1"
            " FROM row_bot_org.brain_mentions o WHERE o.relation_id = %(new)s"
            " AND o.chunk_id IS NOT DISTINCT FROM m.chunk_id AND o.fact_id IS NOT DISTINCT FROM m.fact_id)",
            {"old": relation_id, "new": same[0]})
        conn.execute("UPDATE row_bot_org.brain_mentions SET relation_id = %s WHERE relation_id = %s",
                     (same[0], relation_id))
        conn.execute(
            "UPDATE row_bot_org.brain_relations SET"
            " status = CASE WHEN %(active)s AND retired_auto THEN 'active' ELSE status END,"
            " origin = CASE WHEN %(manual)s THEN 'manual' ELSE origin END,"
            " retired_auto = retired_auto AND NOT %(active)s AND NOT %(manual)s, updated_at = now() WHERE id = %(id)s",
            {"active": status == ACTIVE, "manual": origin == MANUAL, "id": same[0]})
        conn.execute("DELETE FROM row_bot_org.brain_relations WHERE id = %s", (relation_id,))


def merge_entity(conn: psycopg.Connection, source: EntityRow, target: EntityRow) -> EntityRow:
    """Merge ``source`` into ``target`` (both of one space, locked, neither merged away): its relations and
    mentions move, its names become aliases, entities merged into it now point to ``target``, and it is
    ``merged_into`` ``target`` (its name keeps leading there). The target, as it is now."""
    _move_relations(conn, source, target)
    conn.execute(
        "DELETE FROM row_bot_org.brain_mentions m WHERE m.entity_id = %(old)s AND EXISTS (SELECT 1"
        " FROM row_bot_org.brain_mentions o WHERE o.entity_id = %(new)s"
        " AND o.chunk_id IS NOT DISTINCT FROM m.chunk_id AND o.fact_id IS NOT DISTINCT FROM m.fact_id)",
        {"old": source.id, "new": target.id})
    conn.execute("UPDATE row_bot_org.brain_mentions SET entity_id = %s WHERE entity_id = %s", (target.id, source.id))
    conn.execute(f"UPDATE row_bot_org.brain_entities SET merged_into = %s, {_EDITED} WHERE merged_into = %s",
                 (target.id, source.id))
    conn.execute(f"UPDATE row_bot_org.brain_entities SET status = 'merged_into', merged_into = %s,"
                 f" retired_auto = false, {_EDITED} WHERE id = %s", (target.id, source.id))
    aliases = graph.merge_aliases(target.name, list(target.aliases), [source.name], list(source.aliases))
    row = conn.execute(
        "UPDATE row_bot_org.brain_entities SET aliases = %(aliases)s, alias_keys = %(keys)s,"
        " description = CASE WHEN description = '' THEN %(description)s ELSE description END,"
        " status = CASE WHEN retired_auto AND %(active)s THEN 'active' ELSE status END,"
        f" retired_auto = retired_auto AND NOT %(active)s, {_EDITED} WHERE id = %(id)s RETURNING {_ENTITY_COLUMNS}",
        {"aliases": aliases, "keys": _alias_keys(aliases), "description": source.description,
         "active": source.status == ACTIVE, "id": target.id}).fetchone()
    return _entity(row)


def set_entity_status(conn: psycopg.Connection, entity_id: str, status: str) -> tuple[EntityRow | None, bool]:
    """Retire an active entity, or restore a retired one: the entity, and whether this changed it."""
    before = RETIRED if status == ACTIVE else ACTIVE
    row = conn.execute(f"UPDATE row_bot_org.brain_entities SET status = %s, retired_auto = false, {_EDITED}"
                       f" WHERE id = %s AND status = %s RETURNING {_ENTITY_COLUMNS}",
                       (status, entity_id, before)).fetchone()
    return (_entity(row), True) if row is not None else (get_entity(conn, entity_id), False)


def set_relation_status(conn: psycopg.Connection, relation_id: str, status: str) -> tuple[RelationRow | None, bool]:
    before = RETIRED if status == ACTIVE else ACTIVE
    row = conn.execute(f"UPDATE row_bot_org.brain_relations SET status = %s, retired_auto = false, {_EDITED}"
                       f" WHERE id = %s AND status = %s RETURNING {_RELATION_COLUMNS}",
                       (status, relation_id, before)).fetchone()
    return (_relation(row), True) if row is not None else (get_relation(conn, relation_id), False)


def add_relation(conn: psycopg.Connection, *, space_id: str, source_id: str, target_id: str,
                 relation_type: str) -> tuple[RelationRow, bool]:
    """A relation made by hand (never retired for having no mention): it, and False when the space already had it
    (then unchanged)."""
    row = conn.execute(
        "INSERT INTO row_bot_org.brain_relations (space_id, source_id, target_id, relation_type, origin, edited_by,"
        " edited_at) VALUES (%s, %s, %s, %s, 'manual', row_bot_org.row_bot_principal()::uuid, now())"
        f" ON CONFLICT (space_id, source_id, target_id, relation_type) DO NOTHING RETURNING {_RELATION_COLUMNS}",
        (space_id, source_id, target_id, relation_type)).fetchone()
    if row is not None:
        return _relation(row), True
    found = conn.execute(f"SELECT {_RELATION_COLUMNS} FROM row_bot_org.brain_relations WHERE space_id = %s"
                         " AND source_id = %s AND target_id = %s AND relation_type = %s",
                         (space_id, source_id, target_id, relation_type)).fetchone()
    return _relation(found), False


# -- reading ----------------------------------------------------------------------------------------

def graph_view(conn: psycopg.Connection, *, spaces: Sequence[str] | None, query: str | None, limit: int,
               retired: bool) -> dict[str, Any]:
    """The entities (active, and retired with ``retired``) this transaction reads, in ``spaces`` (None: all it
    reads), whose name or an alias holds ``query``: the ``limit`` most related first, and the relations among them.
    The answer's shape is the API's (``nodes``, ``edges``, counts); ``spaces`` there is filled by the caller."""
    where = {"spaces": None if spaces is None else list(spaces), "query": query, "retired": retired}
    matching = (
        "FROM row_bot_org.brain_entities e WHERE (e.status = 'active' OR (%(retired)s AND e.status = 'retired'))"
        " AND (%(spaces)s::uuid[] IS NULL OR e.space_id = ANY(%(spaces)s::uuid[]))"
        " AND (%(query)s::text IS NULL OR strpos(lower(e.name), lower(%(query)s::text)) > 0"
        "      OR EXISTS (SELECT 1 FROM unnest(e.aliases) AS a (alias)"
        "                 WHERE strpos(lower(a.alias), lower(%(query)s::text)) > 0))")
    shown_status = "(r.status = 'active' OR (%(retired)s AND r.status = 'retired'))"
    rows = conn.execute(
        "SELECT e.id, e.space_id, e.name, e.description, e.entity_type, e.aliases, e.status, e.updated_at,"
        " (SELECT count(*) FROM row_bot_org.brain_relations r WHERE r.status = 'active'"
        "  AND (r.source_id = e.id OR r.target_id = e.id)) AS related, count(*) OVER ()"
        f" {matching} ORDER BY related DESC, lower(e.name), e.id LIMIT %(limit)s",
        {**where, "limit": limit}).fetchall()
    nodes = [{"id": str(r[0]), "space_id": str(r[1]), "subject": r[2], "description": r[3], "entity_type": r[4],
              "aliases": list(r[5]), "status": r[6], "updated_at": _time(r[7]), "relation_count": r[8],
              "orphan": r[8] == 0} for r in rows]
    total_entities = rows[0][9] if rows else 0
    (total_relations,) = conn.execute(
        f"WITH matching AS (SELECT e.id {matching})"
        f" SELECT count(*) FROM row_bot_org.brain_relations r WHERE {shown_status}"
        " AND r.source_id IN (SELECT id FROM matching) AND r.target_id IN (SELECT id FROM matching)",
        where).fetchone()
    ids = [node["id"] for node in nodes]
    edge_rows = conn.execute(
        "SELECT r.id, r.space_id, r.source_id, r.target_id, r.relation_type, r.status, r.origin, r.updated_at"
        f" FROM row_bot_org.brain_relations r WHERE {shown_status}"
        " AND r.source_id = ANY(%(ids)s::uuid[]) AND r.target_id = ANY(%(ids)s::uuid[])"
        " ORDER BY r.updated_at DESC, r.id LIMIT %(edges)s",
        {"retired": retired, "ids": ids, "edges": MAX_EDGES_PER_NODE * limit}).fetchall() if ids else []
    edges = [{"id": str(r[0]), "space_id": str(r[1]), "source_id": str(r[2]), "target_id": str(r[3]),
              "relation_type": r[4], "status": r[5], "origin": r[6], "updated_at": _time(r[7])} for r in edge_rows]
    return {"nodes": nodes, "edges": edges, "total_entities": total_entities, "total_relations": total_relations,
            "shown_entities": len(nodes), "shown_relations": len(edges),
            "truncated": len(nodes) < total_entities or len(edges) < total_relations,
            "entity_types": sorted({node["entity_type"] for node in nodes})}


def entity_relations(conn: psycopg.Connection, entity: EntityRow) -> list[dict[str, Any]]:
    """An entity's relations (active first, at most ``MAX_DETAIL_RELATIONS``), each with the entity at its other
    end."""
    rows = conn.execute(
        "SELECT r.id, r.relation_type, r.source_id = %(id)s, p.id, p.name, p.entity_type, p.status, r.status,"
        " r.origin, r.updated_at FROM row_bot_org.brain_relations r"
        " JOIN row_bot_org.brain_entities p ON p.id = CASE WHEN r.source_id = %(id)s THEN r.target_id"
        " ELSE r.source_id END"
        " WHERE r.source_id = %(id)s OR r.target_id = %(id)s"
        " ORDER BY r.status <> 'active', r.relation_type, lower(p.name), r.id LIMIT %(most)s",
        {"id": entity.id, "most": MAX_DETAIL_RELATIONS}).fetchall()
    return [{"id": str(r[0]), "relation_type": r[1], "direction": "out" if r[2] else "in",
             "peer": {"id": str(r[3]), "subject": r[4], "entity_type": r[5], "status": r[6]}, "status": r[7],
             "origin": r[8], "updated_at": _time(r[9])} for r in rows]


@dataclass(frozen=True)
class MentionRow:
    chunk_ordinal: int | None
    chunk_text: str | None
    document: dict[str, str] | None
    fact: dict[str, str] | None
    fact_document: dict[str, str] | None


def _document_ref(row: tuple, at: int) -> dict[str, str] | None:
    return None if row[at] is None else {"id": str(row[at]), "title": row[at + 1], "file_name": row[at + 2]}


def entity_mentions(conn: psycopg.Connection, entity: EntityRow) -> tuple[list[MentionRow], int]:
    """Where an entity was found, newest first (at most ``MAX_DETAIL_MENTIONS``), and how many mentions it has."""
    rows = conn.execute(
        "SELECT c.ordinal, c.body, d.id, d.title, d.file_name, f.id, f.body, fd.id, fd.title, fd.file_name,"
        " count(*) OVER () FROM row_bot_org.brain_mentions m"
        " LEFT JOIN row_bot_org.brain_chunks c ON c.id = m.chunk_id"
        " LEFT JOIN row_bot_org.brain_documents d ON d.id = c.document_id"
        " LEFT JOIN row_bot_org.brain_facts f ON f.id = m.fact_id"
        " LEFT JOIN row_bot_org.brain_documents fd ON fd.id = f.source_document_id"
        " WHERE m.entity_id = %s ORDER BY m.id DESC LIMIT %s", (entity.id, MAX_DETAIL_MENTIONS)).fetchall()
    mentions = [MentionRow(r[0], r[1], _document_ref(r, 2),
                           None if r[5] is None else {"id": str(r[5]), "text": r[6]}, _document_ref(r, 7))
                for r in rows]
    return mentions, rows[0][10] if rows else 0


__all__ = ["ACTIVE", "EXTRACTED", "MANUAL", "MAX_CLAIMS", "MAX_DETAIL_MENTIONS", "MAX_DETAIL_RELATIONS",
           "MAX_EDGES_PER_NODE", "MERGED", "RETIRED", "RETRY_AFTER", "STALE_CLAIM", "EntityRow", "FactToRead",
           "GraphClaim", "Mentioned", "MentionRow", "RelationRow", "add_relation", "chunk_count", "claim_graph",
           "clear_fact_mentions", "entity_by_name", "entity_mentions", "entity_relations", "finish_graph",
           "get_entity", "get_relation", "graph_view", "hold_fact", "mentioned_in_document", "mentioned_in_fact",
           "merge_entity", "next_chunk", "next_fact", "retire_orphans", "set_entity_status", "set_relation_status",
           "start_graph", "store_chunk_found", "store_fact_found", "update_entity", "wait_for_model"]
