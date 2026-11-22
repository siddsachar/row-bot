"""The server's connection pool.

Each connection is autocommit (every transaction is explicit, so transaction-local settings end with
it), has statement, lock and idle-in-transaction limits, is refused when its role could get past
row-level security, and is reset when it comes back (settings, role, session authorization), so nothing
a borrower set for its session reaches the next one.

psycopg's own log records name the host, port, user and database: ``row_bot_org.logging`` keeps only
their logger, level and exception type.
"""

from __future__ import annotations

import psycopg
from psycopg_pool import ConnectionPool

from row_bot_org.db.checks import refuse_privileged_role

POOL_NAME = "row_bot_org"


def _reset(conn: psycopg.Connection) -> None:
    # Not DISCARD ALL: it drops the server's prepared statements behind psycopg's back. RESET ALL leaves
    # out the role and the session authorization, so those are reset by name.
    conn.execute("RESET ALL; RESET ROLE; RESET SESSION AUTHORIZATION")


def create_pool(
    conninfo: str,
    *,
    max_size: int = 10,
    statement_timeout: int = 30,
    lock_timeout: int = 10,
    idle_in_transaction_timeout: int = 60,
) -> ConnectionPool:
    """A closed pool for ``conninfo`` (the app opens it at start); the timeouts are in seconds.

    Transactions are READ COMMITTED whatever the database's default: a check made after taking a lock
    (the policy writes' version check) must see what was committed before the lock was granted.
    """
    options = (
        f"-c statement_timeout={statement_timeout}s -c lock_timeout={lock_timeout}s"
        f" -c idle_in_transaction_session_timeout={idle_in_transaction_timeout}s"
        r" -c default_transaction_isolation=read\ committed"
    )
    return ConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        open=False,
        name=POOL_NAME,
        kwargs={"autocommit": True, "options": options},
        configure=refuse_privileged_role,
        reset=_reset,
    )


__all__ = ["POOL_NAME", "create_pool"]
