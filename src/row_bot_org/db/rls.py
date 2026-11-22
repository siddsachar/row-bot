"""Row-level security's "who is asking": ``principal_transaction``.

It sets ``row_bot.principal`` and ``row_bot.groups`` with ``set_config(..., true)``, which lasts only
until the transaction ends, so a pooled connection never carries one person's identity into the next
person's work. The SQL functions ``row_bot_org.row_bot_principal()`` and
``row_bot_org.row_bot_in_group(text)`` (migration 0001) read them for the tables' policies.

This guards against application code that forgets a ``WHERE``. It is not a boundary against SQL
the runtime role runs on its own: any role may set these settings.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool


@contextmanager
def principal_transaction(
    pool: ConnectionPool, principal_id: str, groups: Iterable[str] = ()
) -> Iterator[psycopg.Connection]:
    """A connection inside one transaction that acts for ``principal_id``, a member of ``groups``.

    The transaction commits when the block ends and rolls back when it raises.
    """
    members = list(groups)
    if not isinstance(principal_id, str) or not principal_id.strip():
        raise ValueError("a principal needs a non-empty id")
    if not all(isinstance(group, str) and group.strip() for group in members):
        raise ValueError("each group needs a non-empty name")
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "SELECT set_config('row_bot.principal', %s, true), set_config('row_bot.groups', %s, true)",
            (principal_id, json.dumps(sorted(set(members)))),
        )
        yield conn


__all__ = ["principal_transaction"]
