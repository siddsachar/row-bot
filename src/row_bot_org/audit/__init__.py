"""The audit store: the server's one hash chain of audit events (``row_bot.audit``, migration 0006).

``emit(conn, action, actor=..., ...)`` builds a contract ``AuditEvent`` (metadata level, layer ``org`` unless
given) and appends its link in ``conn``'s transaction, which must be the transaction of the change it records:
the change and its event are stored together, or neither is. ``record(conn, event)`` appends an event built
elsewhere (a device's, through ``POST /api/audit/events``). Appends go through ``append_audit_event``, which
serialises them with a transaction-scoped advisory lock and computes each link's hash from the chain's head, so
an audited transaction holds that lock from its first event until it ends: emit last.

``find`` and ``verify`` read the chain for admins (row-level security lets only an admin's transaction, or the
owner, read it). Retention: every event is kept (v1).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import pq, sql

from row_bot.audit import (
    Action, Actor, ActorKind, AuditEvent, AuditLayer, ChainReport, Outcome, Target, canonical_bytes, genesis_hash,
    new_event_id, verify_chain,
)

# The organisation every event names: one company per server. A setting can name it when events leave the
# server (SIEM export, later).
ORG_ID = "company"
# The chain's name, from which its genesis hash follows (migration 0006 computes the same one).
CHAIN_ID = "company-server"
GENESIS = genesis_hash(CHAIN_ID)
# The operator's commands (row-bot-org principals ...): they run as the database owner, with no principal.
OPERATOR = Actor(ActorKind.OPERATOR, "cli")


def person(principal_id: str) -> Actor:
    return Actor(ActorKind.PERSON, principal_id)


def admin(principal_id: str) -> Actor:
    return Actor(ActorKind.ADMIN, principal_id)


def record(conn: psycopg.Connection, event: AuditEvent) -> bool:
    """Append ``event`` in ``conn``'s open transaction. False when an event with its id is already stored."""
    if conn.info.transaction_status != pq.TransactionStatus.INTRANS:
        raise RuntimeError("an audit event is stored in the transaction of the change it records")
    return conn.execute("SELECT row_bot_org.append_audit_event(%s)", (canonical_bytes(event),)).fetchone()[0]


def emit(conn: psycopg.Connection, action: Action, *, actor: Actor, target: Target | None = None,
         outcome: Outcome = Outcome.SUCCEEDED, layer: AuditLayer = AuditLayer.ORG,
         details: Mapping[str, object] | None = None, device_id: str | None = None) -> None:
    """Record that ``actor`` did ``action``, in ``conn``'s transaction (the change's own)."""
    now = datetime.now(timezone.utc)
    record(conn, AuditEvent(id=new_event_id(), occurred_at=now, recorded_at=now, org_id=ORG_ID, actor=actor,
                            action=action, outcome=outcome, layer=layer, target=target, device_id=device_id,
                            details=details or {}))


_FILTERS = {
    "action": "action = %(action)s",
    "actor": "actor_id = %(actor)s",
    "target": "target_ref = %(target)s",
    "start": "recorded_at >= %(start)s",
    "end": "recorded_at < %(end)s",
    "before": "seq < %(before)s",
    "after": "seq > %(after)s",
}


def find(conn: psycopg.Connection, *, limit: int, newest_first: bool = True,
         **filters: Any) -> list[dict[str, Any]]:
    """Stored links (``{"seq", "prev_hash", "hash", "event"}``) matching ``filters`` (``action``, ``actor``,
    ``target``, ``start`` and ``end`` on the time recorded, ``before`` and ``after`` a seq), at most ``limit``."""
    given = {name: value for name, value in filters.items() if value is not None}
    where = sql.SQL(" AND ").join(sql.SQL(_FILTERS[name]) for name in given) if given else sql.SQL("true")
    query = sql.SQL("SELECT seq, prev_hash, hash, event FROM row_bot_org.audit_events WHERE {} "
                    "ORDER BY seq {} LIMIT %(limit)s").format(where, sql.SQL("DESC" if newest_first else "ASC"))
    return [{"seq": seq, "prev_hash": prev, "hash": digest, "event": event}
            for seq, prev, digest, event in conn.execute(query, {**given, "limit": limit}).fetchall()]


def verify(conn: psycopg.Connection) -> ChainReport:
    """``verify_chain`` over the whole stored chain, from its genesis, read in batches."""
    with conn.cursor(name="audit_verify") as cursor:
        cursor.itersize = 1000
        cursor.execute("SELECT seq, prev_hash, hash, event FROM row_bot_org.audit_events ORDER BY seq")
        return verify_chain(({"seq": seq, "prev_hash": prev, "hash": digest, "event": event}
                             for seq, prev, digest, event in cursor), start_hash=GENESIS)


__all__ = ["CHAIN_ID", "GENESIS", "OPERATOR", "ORG_ID", "admin", "emit", "find", "person", "record", "verify"]
