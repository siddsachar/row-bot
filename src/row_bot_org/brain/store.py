"""The company brain's database calls (migration 0008; the knowledge graph's are in ``graph_store``).

Each takes a connection already inside a principal's transaction (``principal_transaction``, perhaps acting as an
admin) or the worker's (``worker_transaction``); row-level security decides which rows it sees and may change.
Every column list is fixed here; values always go as parameters.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from row_bot_org.brain import graph_store

COMPANY = "company"
GROUP = "group"
# A document claimed this long ago and still processing belonged to a worker that stopped: it is claimed again.
STALE_CLAIM = "15 minutes"
# Claims without a finish after which a document is failed rather than tried again (it keeps stopping workers).
MAX_CLAIMS = 3

_SPACE_COLUMNS = "id, name, kind, reader_groups, owner_groups, owner_principals, created_by, created_at, updated_at"
_DOCUMENT_COLUMNS = ("d.id, d.space_id, d.title, d.file_name, d.media_type, d.byte_count, d.sha256, d.status,"
                     " d.status_reason, d.version, d.uploaded_by, d.uploaded_at, d.processed_at,"
                     " (SELECT count(*) FROM row_bot_org.brain_chunks c WHERE c.document_id = d.id),"
                     " d.graph_status, d.graph_reason")
_FACT_COLUMNS = "id, space_id, body, source_document_id, written_by, created_at, updated_at, retired_at"


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


# -- spaces -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SpaceRow:
    id: str
    name: str
    kind: str
    reader_groups: tuple[str, ...]
    owner_groups: tuple[str, ...]
    owner_principals: tuple[str, ...]
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "reader_groups": list(self.reader_groups),
                "owner_groups": list(self.owner_groups), "owner_principals": list(self.owner_principals),
                "created_by": self.created_by, "created_at": _time(self.created_at),
                "updated_at": _time(self.updated_at)}


def _space(row: tuple) -> SpaceRow:
    return SpaceRow(str(row[0]), row[1], row[2], tuple(row[3]), tuple(row[4]), tuple(str(p) for p in row[5]),
                    None if row[6] is None else str(row[6]), row[7], row[8])


def list_spaces(conn: psycopg.Connection) -> list[SpaceRow]:
    """The spaces this transaction may see, the company space first."""
    return [_space(r) for r in conn.execute(
        f"SELECT {_SPACE_COLUMNS} FROM row_bot_org.spaces ORDER BY kind <> 'company', lower(name), id").fetchall()]


def get_space(conn: psycopg.Connection, space_id: str) -> SpaceRow | None:
    row = conn.execute(f"SELECT {_SPACE_COLUMNS} FROM row_bot_org.spaces WHERE id = %s", (space_id,)).fetchone()
    return None if row is None else _space(row)


def get_spaces(conn: psycopg.Connection, space_ids: Sequence[str]) -> dict[str, SpaceRow]:
    rows = conn.execute(f"SELECT {_SPACE_COLUMNS} FROM row_bot_org.spaces WHERE id = ANY(%s::uuid[])",
                        (list(space_ids),)).fetchall()
    return {space.id: space for space in map(_space, rows)}


def create_space(conn: psycopg.Connection, *, name: str, reader_groups: Sequence[str], owner_groups: Sequence[str],
                 owner_principals: Sequence[str]) -> SpaceRow:
    return _space(conn.execute(
        "INSERT INTO row_bot_org.spaces (name, kind, reader_groups, owner_groups, owner_principals)"
        f" VALUES (%s, 'group', %s, %s, %s::uuid[]) RETURNING {_SPACE_COLUMNS}",
        (name, list(reader_groups), list(owner_groups), list(owner_principals))).fetchone())


_SPACE_CHANGES = {"name": "%s", "reader_groups": "%s", "owner_groups": "%s", "owner_principals": "%s::uuid[]"}


def update_space(conn: psycopg.Connection, space_id: str, changes: dict[str, Any]) -> SpaceRow | None:
    """Apply ``changes`` (``name``, ``reader_groups``, ``owner_groups``, ``owner_principals``) to one space."""
    assignments = [sql.SQL("{} = " + _SPACE_CHANGES[column]).format(sql.Identifier(column)) for column in changes]
    query = sql.SQL("UPDATE row_bot_org.spaces SET {} WHERE id = %s RETURNING " + _SPACE_COLUMNS).format(
        sql.SQL(", ").join([*assignments, sql.SQL("updated_at = now()")]))
    values = [list(value) if isinstance(value, (list, tuple)) else value for value in changes.values()]
    row = conn.execute(query, (*values, space_id)).fetchone()
    return None if row is None else _space(row)


def delete_space(conn: psycopg.Connection, space_id: str) -> bool:
    return conn.execute("DELETE FROM row_bot_org.spaces WHERE id = %s", (space_id,)).rowcount == 1


def known_principals(conn: psycopg.Connection, principal_ids: Sequence[str]) -> set[str]:
    """Which of ``principal_ids`` have signed in (an admin's transaction reads every principal)."""
    rows = conn.execute("SELECT id FROM row_bot_org.principals WHERE id = ANY(%s::uuid[])",
                        (list(principal_ids),)).fetchall()
    return {str(row[0]) for row in rows}


# -- documents --------------------------------------------------------------------------------------

def _document(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "space_id": str(row[1]), "title": row[2], "file_name": row[3], "media_type": row[4],
            "byte_count": row[5], "sha256": row[6], "status": row[7], "status_reason": row[8], "version": row[9],
            "uploaded_by": str(row[10]), "uploaded_at": _time(row[11]), "processed_at": _time(row[12]),
            "chunk_count": row[13], "graph_status": row[14], "graph_reason": row[15]}


def add_document(conn: psycopg.Connection, *, space_id: str, title: str, file_name: str, media_type: str,
                 content: bytes, sha256: str, uploader_groups: Sequence[str]) -> dict[str, Any]:
    (document_id,) = conn.execute(
        "INSERT INTO row_bot_org.brain_documents (space_id, title, file_name, media_type, byte_count, sha256, content,"
        " uploader_groups) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (space_id, title, file_name, media_type, len(content), sha256, content, list(uploader_groups))).fetchone()
    return get_document(conn, str(document_id))


def replace_document(conn: psycopg.Connection, document_id: str, *, title: str | None, file_name: str,
                     media_type: str, content: bytes, sha256: str, principal_id: str,
                     uploader_groups: Sequence[str]) -> dict[str, Any] | None:
    """A new file for a document, same id, next version, queued again; its chunks go at once, and with them its
    mentions in the graph (what that leaves with none is settled: ``graph_store.settle_orphans``); it is read for
    the graph again once it is ready."""
    mentioned = graph_store.mentioned_in_document(conn, document_id)
    row = conn.execute(
        "UPDATE row_bot_org.brain_documents SET title = coalesce(%s, title), file_name = %s, media_type = %s,"
        " byte_count = %s, sha256 = %s, content = %s, status = 'queued', status_reason = NULL,"
        " version = version + 1, chunked_version = NULL, claimed_at = NULL, uploaded_by = %s,"
        " uploader_groups = %s, uploaded_at = now(), processed_at = NULL, graph_status = 'pending',"
        " graph_reason = NULL, graph_claimed_at = NULL, graph_attempts = 0, graph_retry_at = NULL,"
        " graph_route = NULL"
        " WHERE id = %s RETURNING id",
        (title, file_name, media_type, len(content), sha256, content, principal_id, list(uploader_groups),
         document_id)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM row_bot_org.brain_chunks WHERE document_id = %s", (document_id,))
    graph_store.settle_orphans(conn, mentioned)
    return get_document(conn, document_id)


def get_document(conn: psycopg.Connection, document_id: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_DOCUMENT_COLUMNS} FROM row_bot_org.brain_documents d WHERE d.id = %s",
                       (document_id,)).fetchone()
    return None if row is None else _document(row)


def list_documents(conn: psycopg.Connection, space_id: str) -> list[dict[str, Any]]:
    return [_document(r) for r in conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM row_bot_org.brain_documents d WHERE d.space_id = %s"
        " ORDER BY d.uploaded_at DESC, d.id", (space_id,)).fetchall()]


def delete_document(conn: psycopg.Connection, document_id: str) -> bool:
    """Delete a document; its chunks go with it (the key cascades), and so do its mentions in the graph (what
    that leaves with none is settled), and its facts lose their source."""
    mentioned = graph_store.mentioned_in_document(conn, document_id)
    if conn.execute("DELETE FROM row_bot_org.brain_documents WHERE id = %s", (document_id,)).rowcount != 1:
        return False
    graph_store.settle_orphans(conn, mentioned)
    return True


# -- facts ------------------------------------------------------------------------------------------

def _fact(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "space_id": str(row[1]), "text": row[2],
            "source_document_id": None if row[3] is None else str(row[3]), "written_by": str(row[4]),
            "created_at": _time(row[5]), "updated_at": _time(row[6]), "retired_at": _time(row[7])}


def add_fact(conn: psycopg.Connection, *, space_id: str, text: str, source_document_id: str | None,
             writer_groups: Sequence[str]) -> dict[str, Any]:
    return _fact(conn.execute(
        "INSERT INTO row_bot_org.brain_facts (space_id, body, source_document_id, writer_groups)"
        f" VALUES (%s, %s, %s, %s) RETURNING {_FACT_COLUMNS}",
        (space_id, text, source_document_id, list(writer_groups))).fetchone())


def get_fact(conn: psycopg.Connection, fact_id: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_FACT_COLUMNS} FROM row_bot_org.brain_facts WHERE id = %s", (fact_id,)).fetchone()
    return None if row is None else _fact(row)


def list_facts(conn: psycopg.Connection, space_id: str, *, include_retired: bool) -> list[dict[str, Any]]:
    return [_fact(r) for r in conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM row_bot_org.brain_facts WHERE space_id = %s"
        " AND (%s OR retired_at IS NULL) ORDER BY created_at DESC, id", (space_id, include_retired)).fetchall()]


def update_fact(conn: psycopg.Connection, fact_id: str, *, text: str | None, source_document_id: str | None,
                clear_source: bool, principal_id: str, writer_groups: Sequence[str]) -> dict[str, Any] | None:
    """A fact's new wording or source, written by ``principal_id`` (who then answers for it); its embedding is
    made again, and new wording is read again for the graph (its old mentions go)."""
    if text is not None:
        graph_store.clear_fact_mentions(conn, fact_id)
    row = conn.execute(
        "UPDATE row_bot_org.brain_facts SET body = coalesce(%s, body),"
        " source_document_id = CASE WHEN %s THEN NULL ELSE coalesce(%s, source_document_id) END,"
        " written_by = %s, writer_groups = %s, updated_at = now(),"
        " embedding = NULL, embedding_model = NULL, embedding_dim = NULL,"
        " graph_done_at = CASE WHEN %s::text IS NULL THEN graph_done_at END, graph_retry_at = NULL"
        f" WHERE id = %s RETURNING {_FACT_COLUMNS}",
        (text, clear_source, source_document_id, principal_id, list(writer_groups), text, fact_id)).fetchone()
    return None if row is None else _fact(row)


def retire_fact(conn: psycopg.Connection, fact_id: str) -> tuple[dict[str, Any] | None, bool]:
    """Retire a fact (recall no longer finds it, and its mentions in the graph go): the fact, and whether this
    retired it."""
    row = conn.execute(f"UPDATE row_bot_org.brain_facts SET retired_at = now() WHERE id = %s AND retired_at IS NULL"
                       f" RETURNING {_FACT_COLUMNS}", (fact_id,)).fetchone()
    if row is None:
        return get_fact(conn, fact_id), False
    graph_store.clear_fact_mentions(conn, fact_id)
    return _fact(row), True


def delete_fact(conn: psycopg.Connection, fact_id: str) -> bool:
    """Delete a fact; its mentions in the graph go with it (what that leaves with none is settled)."""
    mentioned = graph_store.mentioned_in_fact(conn, fact_id)
    if conn.execute("DELETE FROM row_bot_org.brain_facts WHERE id = %s", (fact_id,)).rowcount != 1:
        return False
    graph_store.settle_orphans(conn, mentioned)
    return True


# -- the worker -------------------------------------------------------------------------------------

@contextmanager
def worker_transaction(pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    """A transaction for the brain's worker: no principal, ``row_bot.brain_worker`` on (migration 0008)."""
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('row_bot.brain_worker', 'on', true)")
        yield conn


def default_embeddings_model(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT name FROM row_bot_org.catalog_models WHERE kind = 'embeddings' AND is_default"
                       ).fetchone()
    return None if row is None else row[0]


@dataclass(frozen=True)
class Claim:
    """A document the worker took: the version it read, and what it needs to process it."""

    document_id: str
    space_id: str
    version: int
    media_type: str
    # The file, only when it still has to be parsed (not yet in chunks, and not past MAX_CLAIMS).
    content: bytes | None
    chunked: bool
    uploaded_by: str
    uploader_groups: tuple[str, ...]
    # Claims since the document was last finished, this one included.
    attempts: int

    @property
    def worn_out(self) -> bool:
        """Claimed more than ``MAX_CLAIMS`` times without a finish: its file keeps stopping the worker."""
        return self.attempts > MAX_CLAIMS


def claim_document(conn: psycopg.Connection, *, embeddings: bool, skip: Sequence[str] = ()) -> Claim | None:
    """The oldest queued document (or one whose worker stopped), now ``processing``; None when there is none.
    Without a usable embeddings model only documents not yet in chunks are taken: the rest wait queued for one.
    ``skip`` holds the documents this pass already took, so a pass takes each at most once."""
    row = conn.execute(
        "UPDATE row_bot_org.brain_documents SET status = 'processing', status_reason = NULL, claimed_at = now(),"
        " claim_attempts = claim_attempts + 1"
        " WHERE id = (SELECT id FROM row_bot_org.brain_documents"
        "             WHERE id <> ALL(%(skip)s::uuid[])"
        "               AND ((status = 'queued' AND (%(embeddings)s OR chunked_version IS DISTINCT FROM version))"
        f"                   OR (status = 'processing' AND claimed_at < now() - interval '{STALE_CLAIM}'))"
        "             ORDER BY uploaded_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
        " RETURNING id, space_id, version, media_type,"
        " CASE WHEN chunked_version IS DISTINCT FROM version AND claim_attempts <= %(most)s THEN content END,"
        " chunked_version IS NOT DISTINCT FROM version, uploaded_by, uploader_groups, claim_attempts",
        {"embeddings": embeddings, "skip": list(skip), "most": MAX_CLAIMS}).fetchone()
    if row is None:
        return None
    return Claim(str(row[0]), str(row[1]), row[2], row[3], None if row[4] is None else bytes(row[4]), row[5],
                 str(row[6]), tuple(row[7]), row[8])


def _current(conn: psycopg.Connection, claim: Claim) -> bool:
    """Whether ``claim`` is still the document's version (locked until the transaction ends): a replace or a
    delete since the claim leaves the worker's results unwritten."""
    row = conn.execute("SELECT version FROM row_bot_org.brain_documents WHERE id = %s FOR UPDATE",
                       (claim.document_id,)).fetchone()
    return row is not None and row[0] == claim.version


def store_chunks(conn: psycopg.Connection, claim: Claim, chunks: Sequence[str]) -> bool:
    """The claimed version's chunks, in place of any before; False when the document changed since the claim."""
    if not _current(conn, claim):
        return False
    conn.execute("DELETE FROM row_bot_org.brain_chunks WHERE document_id = %s", (claim.document_id,))
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO row_bot_org.brain_chunks (document_id, space_id, ordinal, body) VALUES (%s, %s, %s, %s)",
            [(claim.document_id, claim.space_id, ordinal, body) for ordinal, body in enumerate(chunks)])
    conn.execute("UPDATE row_bot_org.brain_documents SET chunked_version = version WHERE id = %s",
                 (claim.document_id,))
    return True


def chunks_to_embed(conn: psycopg.Connection, document_id: str, model: str) -> list[tuple[int, str]]:
    """The document's chunks without an embedding by ``model``: (id, text), in order."""
    return [(row[0], row[1]) for row in conn.execute(
        "SELECT id, body FROM row_bot_org.brain_chunks WHERE document_id = %s"
        " AND embedding_model IS DISTINCT FROM %s ORDER BY ordinal", (document_id, model)).fetchall()]


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def store_chunk_embeddings(conn: psycopg.Connection, claim: Claim, model: str,
                           embedded: Sequence[tuple[int, Sequence[float]]]) -> bool:
    if not _current(conn, claim):
        return False
    with conn.cursor() as cursor:
        cursor.executemany(
            "UPDATE row_bot_org.brain_chunks SET embedding = %s::vector, embedding_model = %s, embedding_dim = %s"
            " WHERE id = %s AND document_id = %s",
            [(vector_literal(vector), model, len(vector), chunk_id, claim.document_id) for chunk_id, vector in embedded])
    return True


def finish_document(conn: psycopg.Connection, claim: Claim, status: str, reason: str | None) -> bool:
    """The claimed version's outcome: ``ready``, ``failed`` or back to ``queued`` (with ``reason``)."""
    if not _current(conn, claim):
        return False
    conn.execute(
        "UPDATE row_bot_org.brain_documents SET status = %s, status_reason = %s, claimed_at = NULL,"
        " claim_attempts = 0, processed_at = CASE WHEN %s = 'ready' THEN now() ELSE processed_at END WHERE id = %s",
        (status, reason, status, claim.document_id))
    return True


@dataclass(frozen=True)
class FactToEmbed:
    id: str
    text: str
    updated_at: datetime
    written_by: str
    writer_groups: tuple[str, ...]


def facts_to_embed(conn: psycopg.Connection, limit: int, *, tried: Sequence[str] = (),
                   writers_failed: Sequence[str] = ()) -> list[FactToEmbed]:
    """Facts in force without an embedding, oldest first, leaving out the facts this pass ``tried`` and those of
    the writers whose calls failed in it."""
    return [FactToEmbed(str(r[0]), r[1], r[2], str(r[3]), tuple(r[4])) for r in conn.execute(
        "SELECT id, body, updated_at, written_by, writer_groups FROM row_bot_org.brain_facts"
        " WHERE embedding IS NULL AND retired_at IS NULL AND id <> ALL(%s::uuid[]) AND written_by <> ALL(%s::uuid[])"
        " ORDER BY created_at, id LIMIT %s", (list(tried), list(writers_failed), limit)).fetchall()]


def store_fact_embeddings(conn: psycopg.Connection, model: str,
                          embedded: Sequence[tuple[FactToEmbed, Sequence[float]]]) -> None:
    """Each fact's embedding, unless it was edited since it was read."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "UPDATE row_bot_org.brain_facts SET embedding = %s::vector, embedding_model = %s, embedding_dim = %s"
            " WHERE id = %s AND updated_at = %s",
            [(vector_literal(vector), model, len(vector), fact.id, fact.updated_at) for fact, vector in embedded])


# -- recall -----------------------------------------------------------------------------------------

# A query's words, any of which may match (plainto_tsquery joins them with AND).
_ANY_WORD = ("(SELECT replace(plainto_tsquery('pg_catalog.english'::regconfig, %(text)s)::text, ' & ', ' | ')"
             "::tsquery AS q)")
_IN_SPACES = "(%(spaces)s::uuid[] IS NULL OR {}.space_id = ANY(%(spaces)s::uuid[]))"


def text_candidates(conn: psycopg.Connection, text: str, spaces: Sequence[str] | None,
                    limit: int) -> list[tuple[str, str]]:
    """Chunks (of documents that aren't failed) and facts in force matching any word of ``text``, best text
    rank first: (``chunk`` or ``fact``, id)."""
    rows = conn.execute(
        f"WITH words AS {_ANY_WORD}"
        " SELECT 'chunk', c.id::text, ts_rank_cd(c.tsv, words.q) AS rank"
        " FROM row_bot_org.brain_chunks c JOIN row_bot_org.brain_documents d ON d.id = c.document_id, words"
        f" WHERE c.tsv @@ words.q AND d.status <> 'failed' AND {_IN_SPACES.format('c')}"
        " UNION ALL"
        " SELECT 'fact', f.id::text, ts_rank_cd(f.tsv, words.q) FROM row_bot_org.brain_facts f, words"
        f" WHERE f.tsv @@ words.q AND f.retired_at IS NULL AND {_IN_SPACES.format('f')}"
        " ORDER BY rank DESC, 1, 2 LIMIT %(limit)s",
        {"text": text, "spaces": None if spaces is None else list(spaces), "limit": limit}).fetchall()
    return [(row[0], row[1]) for row in rows]


def vector_candidates(conn: psycopg.Connection, vector: Sequence[float], model: str, spaces: Sequence[str] | None,
                      limit: int) -> list[tuple[str, str]]:
    """Chunks and facts embedded by ``model`` at this vector's dimension, nearest (cosine) first. Rows of another
    model or dimension are never compared."""
    rows = conn.execute(
        "SELECT 'chunk', c.id::text, c.embedding <=> %(vector)s::vector AS distance"
        " FROM row_bot_org.brain_chunks c JOIN row_bot_org.brain_documents d ON d.id = c.document_id"
        " WHERE c.embedding_model = %(model)s AND c.embedding_dim = %(dim)s AND d.status <> 'failed'"
        f" AND {_IN_SPACES.format('c')}"
        " UNION ALL"
        " SELECT 'fact', f.id::text, f.embedding <=> %(vector)s::vector FROM row_bot_org.brain_facts f"
        " WHERE f.embedding_model = %(model)s AND f.embedding_dim = %(dim)s AND f.retired_at IS NULL"
        f" AND {_IN_SPACES.format('f')}"
        " ORDER BY distance, 1, 2 LIMIT %(limit)s",
        {"vector": vector_literal(vector), "model": model, "dim": len(vector),
         "spaces": None if spaces is None else list(spaces), "limit": limit}).fetchall()
    return [(row[0], row[1]) for row in rows]


@dataclass(frozen=True)
class ChunkHit:
    id: str
    ordinal: int
    text: str
    document_id: str
    title: str
    file_name: str
    sha256: str
    uploaded_by: str
    uploaded_at: datetime
    processed_at: datetime | None
    space_id: str


@dataclass(frozen=True)
class FactHit:
    id: str
    text: str
    written_by: str
    created_at: datetime
    updated_at: datetime
    space_id: str
    source_document_id: str | None
    source_title: str | None
    source_file_name: str | None


def chunk_hits(conn: psycopg.Connection, chunk_ids: Sequence[str]) -> dict[str, ChunkHit]:
    rows = conn.execute(
        "SELECT c.id::text, c.ordinal, c.body, d.id, d.title, d.file_name, d.sha256, d.uploaded_by, d.uploaded_at,"
        " d.processed_at, c.space_id FROM row_bot_org.brain_chunks c"
        " JOIN row_bot_org.brain_documents d ON d.id = c.document_id WHERE c.id = ANY(%s::bigint[])",
        ([int(chunk_id) for chunk_id in chunk_ids],)).fetchall()
    return {row[0]: ChunkHit(row[0], row[1], row[2], str(row[3]), row[4], row[5], row[6], str(row[7]), row[8],
                             row[9], str(row[10])) for row in rows}


def fact_hits(conn: psycopg.Connection, fact_ids: Sequence[str]) -> dict[str, FactHit]:
    """The facts, each with its source document's title when the transaction may read that document."""
    rows = conn.execute(
        "SELECT f.id::text, f.body, f.written_by, f.created_at, f.updated_at, f.space_id, d.id, d.title, d.file_name"
        " FROM row_bot_org.brain_facts f LEFT JOIN row_bot_org.brain_documents d ON d.id = f.source_document_id"
        " WHERE f.id = ANY(%s::uuid[]) AND f.retired_at IS NULL", (list(fact_ids),)).fetchall()
    return {row[0]: FactHit(row[0], row[1], str(row[2]), row[3], row[4], str(row[5]),
                            None if row[6] is None else str(row[6]), row[7], row[8]) for row in rows}


__all__ = ["COMPANY", "GROUP", "MAX_CLAIMS", "STALE_CLAIM", "ChunkHit", "Claim", "FactHit", "FactToEmbed", "SpaceRow",
           "add_document", "add_fact", "chunk_hits", "chunks_to_embed", "claim_document", "create_space",
           "default_embeddings_model", "delete_document", "delete_fact", "delete_space", "fact_hits", "facts_to_embed",
           "finish_document", "get_document", "get_fact", "get_space", "get_spaces", "known_principals",
           "list_documents", "list_facts", "list_spaces", "replace_document", "retire_fact", "store_chunk_embeddings",
           "store_chunks", "store_fact_embeddings", "text_candidates", "update_fact", "update_space", "vector_candidates",
           "vector_literal"]
