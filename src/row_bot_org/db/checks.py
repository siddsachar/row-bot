"""What ``serve``, ``/readyz``, the pool and ``check-config`` ask the database about the role they use.

The server runs only as a role row-level security holds back: not a superuser, without BYPASSRLS, unable
to SET ROLE to either, and not the owner of the schema or its tables (the migrating role is a second role).
"""

from __future__ import annotations

import psycopg
from psycopg_pool import ConnectionPool
from starlette.concurrency import run_in_threadpool

from row_bot_org.db.migrations import SCHEMA, Migration, migration_status
from row_bot_org.health import ReadinessCheck

READINESS_TIMEOUT = 5.0

# Roles the connection's role is, or may SET ROLE to, that row-level security doesn't hold back.
_PRIVILEGED = """
SELECT r.rolname = current_user, r.rolsuper, r.rolbypassrls, r.rolname FROM pg_catalog.pg_roles r
WHERE (r.rolsuper OR r.rolbypassrls) AND pg_catalog.pg_has_role(current_user, r.oid, 'SET')
ORDER BY r.rolname
"""


class PrivilegedRoleError(RuntimeError):
    """The server's database role could get past row-level security; the server doesn't use it."""


# The schema, or a table in it, belongs to the role or to a role it is a member of. The owner matches
# the "_definer" policies (every row) and can turn row-level security off, so it never serves.
_OWNS = """
SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname = %(schema)s
               AND pg_catalog.pg_has_role(current_user, n.nspowner, 'MEMBER'))
    OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = %(schema)s AND pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER'))
"""


def privileged_role_problems(conn: psycopg.Connection) -> list[str]:
    """Why the role on ``conn`` must not run the server: it is, or can become, a role RLS doesn't hold back,
    or it owns (or is a member of the owner of) the schema or its tables.

    The role's own name is part of the database URL, so it is never named; other roles are.
    """
    rows = conn.execute(_PRIVILEGED).fetchall()
    own = [row for row in rows if row[0]]
    if own:
        _, superuser, bypass_rls, _ = own[0]
        problems = [problem for flag, problem in (
            (superuser, "the database role is a superuser"),
            (bypass_rls, "the database role has BYPASSRLS"),
        ) if flag]
    else:
        problems = [
            f"the database role can SET ROLE to {name}, which "
            + ("is a superuser" if superuser else "has BYPASSRLS")
            for _, superuser, _, name in rows
        ]
    # A superuser counts as a member of every role: its own problem says enough.
    is_superuser = bool(own and own[0][1])
    (owns,) = conn.execute(_OWNS, {"schema": SCHEMA}).fetchone()
    if owns and not is_superuser:
        problems.append(f"the database role owns (or is a member of the owner of) the {SCHEMA} schema or its tables")
    return problems


def refuse_privileged_role(conn: psycopg.Connection) -> None:
    """``PrivilegedRoleError`` when the role on ``conn`` is unfit (the pool runs this on each new connection)."""
    if privileged_role_problems(conn):
        raise PrivilegedRoleError("the database role could get past row-level security")


def database_readiness(pool: ConnectionPool, migrations: tuple[Migration, ...]) -> ReadinessCheck:
    """Ready when the pool reaches the database as an unprivileged role and no migration is pending."""

    def check() -> bool:
        with pool.connection(timeout=READINESS_TIMEOUT) as conn:
            # Again here: a role made a superuser after its connections opened is caught too.
            refuse_privileged_role(conn)
            return not migration_status(conn, migrations).pending

    async def ready() -> bool:
        return await run_in_threadpool(check)

    return ready


__all__ = ["READINESS_TIMEOUT", "PrivilegedRoleError", "database_readiness", "privileged_role_problems",
           "refuse_privileged_role"]
