"""What ``/readyz`` and ``check-config`` ask the database."""

from __future__ import annotations

import psycopg
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from row_bot_org.db.migrations import SCHEMA, Migration, migration_status
from row_bot_org.health import ReadinessCheck

READINESS_TIMEOUT = 5.0


def database_readiness(pool: ConnectionPool, migrations: tuple[Migration, ...]) -> ReadinessCheck:
    """Ready when the pool reaches the database and no migration is pending."""

    def check() -> bool:
        with pool.connection(timeout=READINESS_TIMEOUT) as conn:
            return not migration_status(conn, migrations).pending

    async def ready() -> bool:
        return await run_in_threadpool(check)

    return ready


def runtime_role_warnings(conn: psycopg.Connection) -> list[str]:
    """Why the role on ``conn`` is unfit to run the server, if it is: each would let it past RLS."""
    superuser, bypass_rls, owns = conn.execute(
        "SELECT r.rolsuper, r.rolbypassrls,"
        " EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname = %(schema)s"
        "         AND pg_catalog.pg_has_role(current_user, n.nspowner, 'USAGE'))"
        " OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c"
        "            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        "            WHERE n.nspname = %(schema)s AND pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE'))"
        " FROM pg_catalog.pg_roles r WHERE r.rolname = current_user",
        {"schema": SCHEMA},
    ).fetchone()
    warnings = []
    if superuser:
        warnings.append("the database role is a superuser, which row-level security doesn't hold back")
    if bypass_rls:
        warnings.append("the database role has BYPASSRLS, so row-level security doesn't apply to it")
    if owns:
        warnings.append(
            f"the database role owns (or is a member of the owner of) the {SCHEMA} schema or its tables, "
            "so it could turn row-level security off"
        )
    return warnings


__all__ = ["READINESS_TIMEOUT", "database_readiness", "runtime_role_warnings"]
