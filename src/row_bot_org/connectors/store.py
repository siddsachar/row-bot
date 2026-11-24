"""The connectors' database calls (migration 0011).

Each takes a connection inside an admin's transaction (``admin_connection``, or the sync's own as the connector's
``run_as`` admin) or, for taking a connector that is due and letting it go, the brain worker's
(``brain.store.worker_transaction``); row-level security decides what it sees. Column lists are fixed here; values
always go as parameters.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from row_bot_org.connectors.mirror import Mirrored
from row_bot_org.connectors.upstream import Item

# A connector taken this long ago whose sync never let it go belonged to a worker that stopped.
STALE_CLAIM = "15 minutes"
# What a connector's warning lists at most.
MOST_UNMAPPED = 20

_COLUMNS = ("c.id, c.kind, c.space_id, c.name, c.drive_id, c.folder_id, c.tenant_id, c.client_id, c.subject,"
            " c.secret_sealed IS NOT NULL, c.principal_map, c.state, c.state_reason, c.root_id, c.feed_cursor,"
            " c.listing, c.generation, c.auth_failures, c.failures, c.next_sync_at, c.last_sync_at, c.run_as,"
            " c.created_by, c.created_at, c.updated_at")


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


@dataclass(frozen=True)
class ConnectorRow:
    id: str
    kind: str
    space_id: str
    name: str
    drive_id: str | None
    folder_id: str | None
    tenant_id: str | None
    client_id: str
    subject: str | None
    has_secret: bool
    principal_map: dict[str, str]
    state: str
    state_reason: str | None
    root_id: str | None
    feed_cursor: str | None
    listing: bool
    generation: int
    auth_failures: int
    failures: int
    next_sync_at: datetime
    last_sync_at: datetime | None
    run_as: str
    created_by: str
    created_at: datetime
    updated_at: datetime


def _connector(row: tuple) -> ConnectorRow:
    return ConnectorRow(str(row[0]), row[1], str(row[2]), row[3], row[4], row[5], row[6], row[7], row[8], row[9],
                        dict(row[10]), row[11], row[12], row[13], row[14], row[15], row[16], row[17], row[18],
                        row[19], row[20], str(row[21]), str(row[22]), row[23], row[24])


def get_connector(conn: psycopg.Connection, connector_id: str) -> ConnectorRow | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM row_bot_org.connectors c WHERE c.id = %s",
                       (connector_id,)).fetchone()
    return None if row is None else _connector(row)


def list_connectors(conn: psycopg.Connection) -> list[ConnectorRow]:
    return [_connector(row) for row in conn.execute(
        f"SELECT {_COLUMNS} FROM row_bot_org.connectors c ORDER BY lower(c.name), c.id").fetchall()]


def sealed_secret(conn: psycopg.Connection, connector_id: str) -> bytes | None:
    row = conn.execute("SELECT secret_sealed FROM row_bot_org.connectors WHERE id = %s", (connector_id,)).fetchone()
    return None if row is None or row[0] is None else bytes(row[0])


def counts(conn: psycopg.Connection, connector_id: str) -> dict[str, Any]:
    """What a connector holds: its files, their documents, the files skipped, the files only admins read because
    a principal of theirs can't be mapped, and those principals (the most shared first)."""
    files, documents, skipped, unmapped_files = conn.execute(
        "SELECT count(*) FILTER (WHERE NOT is_folder), count(document_id), count(skipped),"
        " count(*) FILTER (WHERE document_id IS NOT NULL AND jsonb_array_length(unmapped) > 0)"
        " FROM row_bot_org.connector_items WHERE connector_id = %s", (connector_id,)).fetchone()
    principals = conn.execute(
        "SELECT u ->> 'key', max(u ->> 'name'), count(*) FROM row_bot_org.connector_items i,"
        " jsonb_array_elements(i.unmapped) AS u WHERE i.connector_id = %s AND i.document_id IS NOT NULL"
        " GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT %s", (connector_id, MOST_UNMAPPED)).fetchall()
    return {"files": files, "documents": documents, "skipped": skipped, "unmapped_files": unmapped_files,
            "unmapped": [{"key": key, "name": name, "files": count} for key, name, count in principals]}


def to_json(row: ConnectorRow, held: dict[str, Any]) -> dict[str, Any]:
    """A connector as admins see it: never its secret (only whether one is saved)."""
    unmapped = held["unmapped_files"]
    warning = None if not unmapped else (
        f"{unmapped:,} {'file is' if unmapped == 1 else 'files are'} shared with people or groups Row-Bot can't"
        " match, so only admins can read them. Map them to Row-Bot groups in the connector's principal map.")
    return {"id": row.id, "kind": row.kind, "space_id": row.space_id, "name": row.name, "drive_id": row.drive_id,
            "folder_id": row.folder_id, "tenant_id": row.tenant_id, "client_id": row.client_id,
            "subject": row.subject, "has_secret": row.has_secret, "principal_map": row.principal_map,
            "state": row.state, "state_reason": row.state_reason, "last_sync_at": _time(row.last_sync_at),
            "next_sync_at": _time(row.next_sync_at), "run_as": row.run_as, "created_by": row.created_by,
            "created_at": _time(row.created_at), "updated_at": _time(row.updated_at), **held, "warning": warning}


def create_connector(conn: psycopg.Connection, *, connector_id: str, kind: str, space_id: str, name: str,
                     drive_id: str | None, folder_id: str | None, tenant_id: str | None, client_id: str,
                     subject: str | None, secret_sealed: bytes, principal_map: dict[str, str]) -> ConnectorRow:
    conn.execute(
        "INSERT INTO row_bot_org.connectors (id, kind, space_id, name, drive_id, folder_id, tenant_id, client_id,"
        " subject, secret_sealed, principal_map) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (connector_id, kind, space_id, name, drive_id, folder_id, tenant_id, client_id, subject, secret_sealed,
         Jsonb(principal_map)))
    return get_connector(conn, connector_id)


def update_connector(conn: psycopg.Connection, connector_id: str, *, name: str, tenant_id: str | None,
                     client_id: str, subject: str | None, secret_sealed: bytes | None, principal_map: dict[str, str],
                     run_as: str, credentials_changed: bool, map_changed: bool) -> ConnectorRow | None:
    """Save an admin's change: they take the connector over (``run_as``). New credentials clear its refusals and,
    when it stopped with an error, start it again; a new principal map re-reads every file's permissions."""
    row = conn.execute(
        "UPDATE row_bot_org.connectors SET name = %(name)s, tenant_id = %(tenant)s, client_id = %(client)s,"
        " subject = %(subject)s, secret_sealed = coalesce(%(sealed)s::bytea, secret_sealed), principal_map = %(map)s,"
        " run_as = %(run_as)s, updated_at = now(),"
        " auth_failures = CASE WHEN %(credentials)s THEN 0 ELSE auth_failures END,"
        " state = CASE WHEN %(credentials)s AND state = 'error' THEN 'active' ELSE state END,"
        " state_reason = CASE WHEN %(credentials)s AND state = 'error' THEN NULL ELSE state_reason END,"
        " next_sync_at = CASE WHEN %(credentials)s OR %(remap)s THEN now() ELSE next_sync_at END"
        " WHERE id = %(id)s RETURNING id",
        {"name": name, "tenant": tenant_id, "client": client_id, "subject": subject, "sealed": secret_sealed,
         "map": Jsonb(principal_map), "run_as": run_as, "credentials": credentials_changed, "remap": map_changed,
         "id": connector_id}).fetchone()
    if row is None:
        return None
    if map_changed:
        conn.execute("UPDATE row_bot_org.connector_items SET readers_due = true WHERE connector_id = %s"
                     " AND NOT is_folder AND document_id IS NOT NULL", (connector_id,))
    return get_connector(conn, connector_id)


def set_state(conn: psycopg.Connection, connector_id: str, state: str, *, run_as: str | None = None) -> bool:
    """Pause or resume (``active``: again at once, with no failures counted, as ``run_as``). Whether it changed."""
    row = conn.execute(
        "UPDATE row_bot_org.connectors SET state = %(state)s, state_reason = NULL, updated_at = now(),"
        " run_as = coalesce(%(run_as)s::uuid, run_as),"
        " auth_failures = CASE WHEN %(state)s = 'active' THEN 0 ELSE auth_failures END,"
        " failures = CASE WHEN %(state)s = 'active' THEN 0 ELSE failures END,"
        " next_sync_at = CASE WHEN %(state)s = 'active' THEN now() ELSE next_sync_at END"
        " WHERE id = %(id)s AND state <> %(state)s RETURNING id",
        {"state": state, "run_as": run_as, "id": connector_id}).fetchone()
    return row is not None


def sync_now(conn: psycopg.Connection, connector_id: str) -> None:
    conn.execute("UPDATE row_bot_org.connectors SET next_sync_at = now() WHERE id = %s", (connector_id,))


def delete_connector(conn: psycopg.Connection, connector_id: str) -> int | None:
    """Delete a connector and its documents (with their chunks): how many documents went; None when it's gone."""
    removed = conn.execute("DELETE FROM row_bot_org.brain_documents WHERE connector_id = %s",
                           (connector_id,)).rowcount
    if conn.execute("DELETE FROM row_bot_org.connectors WHERE id = %s", (connector_id,)).rowcount != 1:
        return None
    return removed


# -- the worker: taking a connector that is due -------------------------------------------------------

@dataclass(frozen=True)
class Due:
    id: str
    run_as: str


def claim_due(conn: psycopg.Connection, *, skip: Sequence[str] = ()) -> Due | None:
    """The active connector that has waited longest past its time (or whose sync stopped), now taken; None when
    none is due. ``skip``: those this pass already took."""
    row = conn.execute(
        "UPDATE row_bot_org.connectors SET claimed_at = now()"
        " WHERE id = (SELECT id FROM row_bot_org.connectors"
        "             WHERE state = 'active' AND next_sync_at <= now() AND id <> ALL(%s::uuid[])"
        f"              AND (claimed_at IS NULL OR claimed_at < now() - interval '{STALE_CLAIM}')"
        "             ORDER BY next_sync_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
        " RETURNING id, run_as", (list(skip),)).fetchone()
    return None if row is None else Due(str(row[0]), str(row[1]))


def release(conn: psycopg.Connection, connector_id: str) -> None:
    """Let a connector go without a sync (it is no longer active, or its turn failed): its time is unchanged."""
    conn.execute("UPDATE row_bot_org.connectors SET claimed_at = NULL WHERE id = %s", (connector_id,))


def stop_without_admin(conn: psycopg.Connection, connector_id: str, reason: str) -> None:
    """The worker's: the admin it syncs as can't any more; it stops with ``reason`` until an admin saves or
    resumes it."""
    conn.execute("UPDATE row_bot_org.connectors SET claimed_at = NULL, state = 'error', state_reason = %s"
                 " WHERE id = %s AND state = 'active'", (reason, connector_id))


def finish_turn(conn: psycopg.Connection, connector_id: str, *, caught_up: bool, wait_seconds: float) -> None:
    """A sync turn that went well: again in ``wait_seconds`` (at once when it stopped at a limit)."""
    conn.execute(
        "UPDATE row_bot_org.connectors SET claimed_at = NULL, failures = 0, auth_failures = 0,"
        " next_sync_at = now() + make_interval(secs => %s),"
        " last_sync_at = CASE WHEN %s THEN now() ELSE last_sync_at END WHERE id = %s",
        (wait_seconds, caught_up, connector_id))


def fail_turn(conn: psycopg.Connection, connector_id: str, *, wait_seconds: float) -> None:
    """The source couldn't answer: one more failure, and again in ``wait_seconds``."""
    conn.execute("UPDATE row_bot_org.connectors SET claimed_at = NULL, failures = failures + 1,"
                 " next_sync_at = now() + make_interval(secs => %s) WHERE id = %s", (wait_seconds, connector_id))


def refuse_turn(conn: psycopg.Connection, connector_id: str, *, reason: str, most: int,
                wait_seconds: float) -> tuple[int, bool]:
    """The source refused the connector: one more refusal in a row; at ``most`` it stops with ``reason``. The
    refusals so far, and whether it stopped."""
    row = conn.execute(
        "UPDATE row_bot_org.connectors SET claimed_at = NULL, auth_failures = auth_failures + 1,"
        " next_sync_at = now() + make_interval(secs => %(wait)s),"
        " state = CASE WHEN state = 'active' AND auth_failures + 1 >= %(most)s THEN 'error' ELSE state END,"
        " state_reason = CASE WHEN state = 'active' AND auth_failures + 1 >= %(most)s THEN %(reason)s::text"
        "                ELSE state_reason END"
        " WHERE id = %(id)s RETURNING auth_failures, state = 'error'",
        {"wait": wait_seconds, "most": most, "reason": reason, "id": connector_id}).fetchone()
    return (0, False) if row is None else (row[0], row[1])


# -- the sync: what it knows of the source --------------------------------------------------------------

def start_listing(conn: psycopg.Connection, row: ConnectorRow, *, root_id: str, cursor: str) -> int:
    """List everything (again) from ``root_id``, the feed to read from ``cursor`` once that is done; what the
    listing doesn't see is gone once it ends. The listing's generation."""
    (generation,) = conn.execute(
        "UPDATE row_bot_org.connectors SET root_id = %s, feed_cursor = %s, listing = true,"
        " generation = generation + 1 WHERE id = %s RETURNING generation", (root_id, cursor, row.id)).fetchone()
    conn.execute("UPDATE row_bot_org.connector_items SET listed = false, list_page = NULL"
                 " WHERE connector_id = %s AND is_folder", (row.id,))
    conn.execute(
        "INSERT INTO row_bot_org.connector_items (connector_id, upstream_id, parent_id, is_folder, name, listed, seen)"
        " VALUES (%s, %s, NULL, true, '', false, %s)"
        " ON CONFLICT (connector_id, upstream_id) DO UPDATE SET parent_id = NULL, is_folder = true, listed = false,"
        " seen = EXCLUDED.seen", (row.id, root_id, generation))
    return generation


def next_unlisted(conn: psycopg.Connection, connector_id: str) -> tuple[str, str | None] | None:
    """A folder in scope whose children are still to list, and where its listing stopped."""
    row = conn.execute("SELECT upstream_id, list_page FROM row_bot_org.connector_items"
                       " WHERE connector_id = %s AND is_folder AND NOT listed ORDER BY updated_at, upstream_id LIMIT 1",
                       (connector_id,)).fetchone()
    return None if row is None else (row[0], row[1])


def _upsert(conn: psycopg.Connection, connector_id: str, item: Item, parent_id: str | None,
            generation: int) -> bool:
    """``item`` (in scope) as the sync knows it now: a new or changed file's content is to read, a file's
    permissions are read again whenever it shows up, and a new folder's children are to list. Whether it is new."""
    (inserted,) = conn.execute(
        "INSERT INTO row_bot_org.connector_items AS i (connector_id, upstream_id, parent_id, is_folder, name,"
        " media_type, size, version, listed, seen, content_due, readers_due)"
        " VALUES (%(c)s, %(id)s, %(parent)s, %(folder)s, %(name)s, %(media)s, %(size)s, %(version)s, NOT %(folder)s,"
        " %(seen)s, NOT %(folder)s, NOT %(folder)s)"
        " ON CONFLICT (connector_id, upstream_id) DO UPDATE SET parent_id = EXCLUDED.parent_id,"
        " is_folder = EXCLUDED.is_folder, name = EXCLUDED.name, media_type = EXCLUDED.media_type,"
        " size = EXCLUDED.size, version = EXCLUDED.version, seen = EXCLUDED.seen,"
        " content_due = i.content_due OR (NOT EXCLUDED.is_folder"
        "               AND (EXCLUDED.version IS DISTINCT FROM i.synced_version OR EXCLUDED.name IS DISTINCT FROM i.name)),"
        " readers_due = i.readers_due OR NOT EXCLUDED.is_folder, updated_at = now()"
        " RETURNING (xmax = 0)",
        {"c": connector_id, "id": item.id, "parent": parent_id, "folder": item.is_folder, "name": item.name,
         "media": item.media_type, "size": item.size, "version": item.version, "seen": generation}).fetchone()
    return bool(inserted)


def store_children(conn: psycopg.Connection, row: ConnectorRow, folder_id: str, items: Sequence[Item],
                   next_page: str | None) -> None:
    """One page of a folder's children, and where its listing stands (listed after its last page)."""
    for item in sorted(items, key=lambda found: not found.is_folder):
        if not item.removed:
            _upsert(conn, row.id, item, folder_id, row.generation)
    conn.execute("UPDATE row_bot_org.connector_items SET listed = %s, list_page = %s, updated_at = now()"
                 " WHERE connector_id = %s AND upstream_id = %s",
                 (next_page is None, next_page, row.id, folder_id))


def _in_scope(conn: psycopg.Connection, connector_id: str, folder_id: str | None) -> bool:
    return folder_id is not None and conn.execute(
        "SELECT 1 FROM row_bot_org.connector_items WHERE connector_id = %s AND upstream_id = %s AND is_folder",
        (connector_id, folder_id)).fetchone() is not None


def remove_items(conn: psycopg.Connection, connector_id: str, upstream_ids: Sequence[str]) -> int:
    """Forget these items and everything under them, and delete their documents (with their chunks): how many
    documents went."""
    if not upstream_ids:
        return 0
    documents = [row[0] for row in conn.execute(
        "WITH RECURSIVE gone (upstream_id) AS ("
        "   SELECT upstream_id FROM row_bot_org.connector_items WHERE connector_id = %(c)s"
        "     AND upstream_id = ANY(%(ids)s::text[])"
        "   UNION SELECT i.upstream_id FROM row_bot_org.connector_items i JOIN gone g ON i.parent_id = g.upstream_id"
        "     WHERE i.connector_id = %(c)s)"
        " DELETE FROM row_bot_org.connector_items i USING gone WHERE i.connector_id = %(c)s"
        " AND i.upstream_id = gone.upstream_id RETURNING i.document_id",
        {"c": connector_id, "ids": list(upstream_ids)}).fetchall() if row[0] is not None]
    if not documents:
        return 0
    return conn.execute("DELETE FROM row_bot_org.brain_documents WHERE id = ANY(%s::uuid[]) AND connector_id = %s",
                        (documents, connector_id)).rowcount


def _readers_again_under(conn: psycopg.Connection, connector_id: str, folder_id: str) -> None:
    """A folder changed (its sharing may have): every file under it has its permissions read again."""
    conn.execute(
        "WITH RECURSIVE under (upstream_id) AS ("
        "   SELECT upstream_id FROM row_bot_org.connector_items WHERE connector_id = %(c)s AND parent_id = %(f)s"
        "   UNION SELECT i.upstream_id FROM row_bot_org.connector_items i JOIN under u ON i.parent_id = u.upstream_id"
        "     WHERE i.connector_id = %(c)s)"
        " UPDATE row_bot_org.connector_items i SET readers_due = true FROM under"
        " WHERE i.connector_id = %(c)s AND i.upstream_id = under.upstream_id AND NOT i.is_folder",
        {"c": connector_id, "f": folder_id})


def apply_changes(conn: psycopg.Connection, row: ConnectorRow, items: Sequence[Item], cursor: str) -> int:
    """One page of the change feed, and the cursor to read from next. A removed item, or one whose folder is no
    longer in scope (moved out), goes with everything under it; one whose folder is in scope is kept (moved in, a
    new folder's children are to list); a changed folder has the permissions under it read again. How many
    documents went."""
    removed = 0
    for item in sorted(items, key=lambda found: not found.is_folder):
        if item.id == row.root_id:
            continue
        if item.removed or not _in_scope(conn, row.id, item.parent_id):
            removed += remove_items(conn, row.id, [item.id])
            continue
        if not _upsert(conn, row.id, item, item.parent_id, row.generation) and item.is_folder:
            _readers_again_under(conn, row.id, item.id)
    conn.execute("UPDATE row_bot_org.connectors SET feed_cursor = %s WHERE id = %s", (cursor, row.id))
    return removed


def end_listing(conn: psycopg.Connection, row: ConnectorRow) -> int:
    """The listing saw everything: what it didn't see again is gone (with its documents). How many went."""
    stale = [found for (found,) in conn.execute(
        "SELECT upstream_id FROM row_bot_org.connector_items WHERE connector_id = %s AND seen < %s",
        (row.id, row.generation)).fetchall()]
    removed = remove_items(conn, row.id, stale)
    conn.execute("UPDATE row_bot_org.connectors SET listing = false WHERE id = %s", (row.id,))
    return removed


@dataclass(frozen=True)
class DueItem:
    """A file whose content or permissions are to read."""

    item: Item
    document_id: str | None
    content_due: bool
    readers_due: bool


def next_due(conn: psycopg.Connection, connector_id: str) -> DueItem | None:
    row = conn.execute(
        "SELECT upstream_id, parent_id, name, media_type, size, version, document_id, content_due, readers_due"
        " FROM row_bot_org.connector_items WHERE connector_id = %s AND NOT is_folder AND (content_due OR readers_due)"
        " ORDER BY updated_at, upstream_id LIMIT 1", (connector_id,)).fetchone()
    if row is None:
        return None
    return DueItem(Item(row[0], row[1], row[2], False, media_type=row[3], size=row[4], version=row[5]),
                   None if row[6] is None else str(row[6]), row[7], row[8])


def _done(conn: psycopg.Connection, connector_id: str, due: DueItem, *, document_id: str | None,
          mirrored: Mirrored | None, skipped: str | None) -> None:
    conn.execute(
        "UPDATE row_bot_org.connector_items SET content_due = false, readers_due = false, document_id = %s,"
        " synced_version = CASE WHEN %s THEN %s::text ELSE synced_version END, readers = %s::text[], unmapped = %s,"
        " skipped = %s,"
        " updated_at = now() WHERE connector_id = %s AND upstream_id = %s",
        (document_id, due.content_due or skipped is not None, due.item.version,
         None if mirrored is None else list(mirrored.readers),
         Jsonb([] if mirrored is None else list(mirrored.unmapped)), skipped, connector_id, due.item.id))


def skip_item(conn: psycopg.Connection, connector_id: str, due: DueItem, reason: str) -> int:
    """A file that isn't a document (its type, its size, it couldn't be fetched), until it changes upstream: its
    document, if it had one, goes. How many documents went."""
    removed = 0
    if due.document_id is not None:
        removed = conn.execute("DELETE FROM row_bot_org.brain_documents WHERE id = %s AND connector_id = %s",
                               (due.document_id, connector_id)).rowcount
    _done(conn, connector_id, due, document_id=None, mirrored=None, skipped=reason)
    return removed


def item_settled(conn: psycopg.Connection, connector_id: str, due: DueItem, *, document_id: str | None,
                 mirrored: Mirrored | None) -> None:
    """A file's document (and readers) stored, or nothing to do for it."""
    _done(conn, connector_id, due, document_id=document_id, mirrored=mirrored, skipped=None)


def set_readers(conn: psycopg.Connection, connector_id: str, document_id: str, readers: Sequence[str]) -> bool:
    """A connector document's readers, mirrored again. Whether they changed."""
    return conn.execute(
        "UPDATE row_bot_org.brain_documents SET readers = %s WHERE id = %s AND connector_id = %s"
        " AND readers IS DISTINCT FROM %s::text[]",
        (list(readers), document_id, connector_id, list(readers))).rowcount == 1


def document_digest(conn: psycopg.Connection, connector_id: str, document_id: str) -> tuple[str, str] | None:
    """A connector document's SHA-256 and file name; None when it is gone."""
    row = conn.execute("SELECT sha256, file_name FROM row_bot_org.brain_documents WHERE id = %s AND connector_id = %s",
                       (document_id, connector_id)).fetchone()
    return None if row is None else (row[0], row[1])


__all__ = ["MOST_UNMAPPED", "STALE_CLAIM", "ConnectorRow", "Due", "DueItem", "apply_changes", "claim_due", "counts",
           "create_connector", "delete_connector", "document_digest", "end_listing", "fail_turn", "finish_turn",
           "get_connector", "item_settled", "list_connectors", "next_due", "next_unlisted", "refuse_turn",
           "release", "remove_items", "sealed_secret", "set_readers", "set_state", "skip_item", "start_listing",
           "stop_without_admin", "store_children", "sync_now", "to_json", "update_connector"]
