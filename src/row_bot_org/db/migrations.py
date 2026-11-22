"""Forward-only SQL migrations: numbered ``NNNN_name.sql`` files shipped in ``migrations/``.

``migrate`` takes a Postgres advisory lock (so two runners never interleave), keeps a ledger in
``row_bot_org.schema_migrations`` with each file's SHA-256 (of its text with LF line endings), and
applies each pending file in its own transaction together with its ledger row. It refuses to go on when
an applied file has changed, the database records a migration this server doesn't know (a newer server
migrated it), a pending file is older than the newest applied one, the schema or ledger belongs to a
role the migrating role isn't a member of, or the migrating role is a superuser. A migration that leaves any table in the schema readable by
another role without forced row-level security is rolled back. There is no "down": a mistake is fixed
by a new migration.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import psycopg

SCHEMA = "row_bot_org"
LEDGER = f"{SCHEMA}.schema_migrations"
# pg_advisory_lock key, shared by every runner on a database: the bytes of "RBOrgMig".
LOCK_KEY = int.from_bytes(b"RBOrgMig", "big")
CONNECT_TIMEOUT = 10
LOCK_WAIT = 120.0
_FILE_NAME = re.compile(r"^(\d{4})_([a-z0-9]+(?:_[a-z0-9]+)*)\.sql$")

# Relations in the schema that some role other than the owner may use without forced row-level
# security (tables and partitioned tables), as invoker (views), or at all (materialized and foreign).
_UNPROTECTED = """
SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname <> 'schema_migrations' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND (EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a WHERE a.grantee <> c.relowner)
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute att, pg_catalog.aclexplode(att.attacl) a
                  WHERE att.attrelid = c.oid AND a.grantee <> c.relowner))
  AND NOT CASE WHEN c.relkind IN ('r', 'p') THEN c.relrowsecurity AND c.relforcerowsecurity
               WHEN c.relkind = 'v' THEN COALESCE(c.reloptions && ARRAY['security_invoker=true',
                                                  'security_invoker=on', 'security_invoker=1'], false)
               ELSE false END
ORDER BY 1
"""


class MigrationError(RuntimeError):
    """The migrations can't be applied, or the database doesn't match them. Never carries a URL."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str

    @property
    def file_name(self) -> str:
        return f"{self.version:04d}_{self.name}.sql"


@dataclass(frozen=True)
class MigrationStatus:
    applied: tuple[int, ...]
    pending: tuple[Migration, ...]


def load_migrations(folder: Traversable | Path | None = None) -> tuple[Migration, ...]:
    """The migrations in ``folder`` (the shipped ones by default), in order."""
    source = resources.files("row_bot_org.db") / "migrations" if folder is None else folder
    found: dict[int, Migration] = {}
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".sql"):
            continue
        match = _FILE_NAME.match(entry.name)
        if not match:
            raise MigrationError(f"{entry.name} is not named NNNN_name.sql")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"{entry.name} has the same number as {found[version].file_name}")
        # Line endings don't count: a Windows checkout (CRLF) and the image (LF) hold the same migration.
        raw = entry.read_bytes().replace(b"\r\n", b"\n")
        found[version] = Migration(version, match.group(2), raw.decode("utf-8"), hashlib.sha256(raw).hexdigest())
    return tuple(found[version] for version in sorted(found))


def _read_ledger(conn: psycopg.Connection) -> list[tuple[int, str, str]]:
    exists = conn.execute(
        "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = %s AND c.relname = 'schema_migrations'",
        (SCHEMA,),
    ).fetchone()
    if not exists:
        return []
    try:
        return conn.execute(f"SELECT version, name, checksum FROM {LEDGER} ORDER BY version").fetchall()
    except psycopg.errors.InsufficientPrivilege:
        pass
    # Raised outside the except, so the database error isn't kept as its context.
    raise MigrationError(
        f"this role can't read {LEDGER}: run row-bot-org migrate, and make the server's role a member of "
        "the database's runtime group (SELECT row_bot_org.runtime_role(), as the migrating role)"
    )


def migration_status(conn: psycopg.Connection, migrations: tuple[Migration, ...]) -> MigrationStatus:
    """What the database has applied and what is pending; ``MigrationError`` when they don't match."""
    recorded = _read_ledger(conn)
    known = {migration.version: migration for migration in migrations}
    for version, name, checksum in recorded:
        migration = known.get(version)
        if migration is None:
            raise MigrationError(
                f"the database has migration {version:04d}_{name}, which this server doesn't have: "
                "a newer server migrated it"
            )
        if migration.checksum != checksum:
            raise MigrationError(f"migration {migration.file_name} has changed since it was applied")
    applied = tuple(version for version, _, _ in recorded)
    pending = tuple(migration for migration in migrations if migration.version not in applied)
    if applied and pending and pending[0].version < max(applied):
        raise MigrationError(
            f"migration {pending[0].file_name} is older than the newest applied one ({max(applied):04d}): "
            "migrations only go forward, so give it a higher number"
        )
    return MigrationStatus(applied, pending)


def _check_ownership(conn: psycopg.Connection) -> None:
    owned = conn.execute(
        "SELECT pg_catalog.pg_has_role(current_user, n.nspowner, 'MEMBER'),"
        " pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER')"
        " FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_class c"
        " ON c.relnamespace = n.oid AND c.relname = 'schema_migrations' WHERE n.nspname = %s",
        (SCHEMA,),
    ).fetchone()
    if owned != (True, True):
        raise MigrationError(
            f"the {SCHEMA} schema or its ledger belongs to a role the migrating role isn't a member of, "
            "so row-bot-org migrate didn't make it"
        )


def _apply(conn: psycopg.Connection, migration: Migration) -> None:
    detail = None
    try:
        with conn.transaction():
            conn.execute(migration.sql.encode("utf-8"))
            conn.execute(
                f"INSERT INTO {LEDGER} (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
            unprotected = [name for (name,) in conn.execute(_UNPROTECTED, {"schema": SCHEMA}).fetchall()]
            if unprotected:
                detail = (
                    f"it leaves {', '.join(unprotected)} open to other roles without forced row-level security "
                    "(use CALL row_bot_org.grant_runtime_access)"
                )
                raise _RollBack
    except _RollBack:
        pass
    except psycopg.Error as error:
        # The server's message about the SQL (never the connection's details), or the type.
        detail = error.diag.message_primary or type(error).__name__
    if detail is not None:
        raise MigrationError(f"migration {migration.file_name} failed: {detail}")


class _RollBack(Exception):
    """Leaves a migration's transaction so that it rolls back."""


def migrate(
    conninfo: str, migrations: tuple[Migration, ...] | None = None, *, lock_wait: float = LOCK_WAIT
) -> tuple[Migration, ...]:
    """Apply the pending migrations (the shipped ones by default) as the migrating role; returns them."""
    chosen = load_migrations() if migrations is None else migrations
    with psycopg.connect(conninfo, autocommit=True, connect_timeout=CONNECT_TIMEOUT) as conn:
        # The migrating role owns the SECURITY DEFINER functions the runtime role calls before sign-in:
        # owned by a superuser, they would run past every check in the database.
        (superuser,) = conn.execute(
            "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user").fetchone()
        if superuser:
            raise MigrationError(
                "the migrating role is a superuser, so the sign-in functions it would own would run as one: "
                "migrate as the owner role deploy/org/README.md describes"
            )
        waited = False
        try:
            conn.execute(f"SET lock_timeout = '{int(lock_wait * 1000)}ms'")
            conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            conn.execute("RESET lock_timeout")
        except psycopg.errors.LockNotAvailable:
            waited = True
        if waited:
            raise MigrationError(f"another row-bot-org migrate held the migration lock for {lock_wait:g} s")
        try:
            # The ledger lives in the server's schema, so a first run makes both.
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {LEDGER} (version integer PRIMARY KEY, name text NOT NULL,"
                " checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            _check_ownership(conn)
            pending = migration_status(conn, chosen).pending
            for migration in pending:
                _apply(conn, migration)
            return pending
        finally:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            except psycopg.Error:
                pass  # closing the connection releases the lock; the first error is the one to report


__all__ = ["CONNECT_TIMEOUT", "LEDGER", "LOCK_KEY", "LOCK_WAIT", "SCHEMA", "Migration", "MigrationError",
           "MigrationStatus", "load_migrations", "migrate", "migration_status"]
