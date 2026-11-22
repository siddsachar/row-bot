"""Forward-only SQL migrations: numbered ``NNNN_name.sql`` files shipped in ``migrations/``.

``migrate`` takes a Postgres advisory lock (so two runners never interleave), keeps a ledger in
``row_bot_org.schema_migrations`` with each file's SHA-256 (of its text with LF line endings), and applies each pending file in its own
transaction together with its ledger row. It refuses to go on when an applied file has changed or the
database records a migration this server doesn't know (a newer server migrated it). There is no
"down": a mistake is fixed by a new migration.
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
_FILE_NAME = re.compile(r"^(\d{4})_([a-z0-9]+(?:_[a-z0-9]+)*)\.sql$")


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


def migration_status(conn: psycopg.Connection, migrations: tuple[Migration, ...]) -> MigrationStatus:
    """What the database has applied and what is pending; ``MigrationError`` when they don't match."""
    exists = conn.execute(
        "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = %s AND c.relname = 'schema_migrations'",
        (SCHEMA,),
    ).fetchone()
    recorded: list[tuple[int, str, str]] = []
    if exists:
        try:
            recorded = conn.execute(f"SELECT version, name, checksum FROM {LEDGER} ORDER BY version").fetchall()
        except psycopg.errors.InsufficientPrivilege:
            raise MigrationError(
                f"this role can't read {LEDGER}: run row-bot-org migrate, and make the server's role "
                "a member of row_bot_org_runtime"
            ) from None
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
    return MigrationStatus(applied, tuple(migration for migration in migrations if migration.version not in applied))


def migrate(conninfo: str, migrations: tuple[Migration, ...] | None = None) -> tuple[Migration, ...]:
    """Apply the pending migrations (the shipped ones by default) as the migrating role; returns them."""
    chosen = load_migrations() if migrations is None else migrations
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            # The ledger lives in the server's schema, so a first run makes both.
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {LEDGER} (version integer PRIMARY KEY, name text NOT NULL,"
                " checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            pending = migration_status(conn, chosen).pending
            for migration in pending:
                try:
                    with conn.transaction():
                        conn.execute(migration.sql.encode("utf-8"))
                        conn.execute(
                            f"INSERT INTO {LEDGER} (version, name, checksum) VALUES (%s, %s, %s)",
                            (migration.version, migration.name, migration.checksum),
                        )
                except psycopg.Error as error:
                    # The server's message about the SQL (never the connection's details), or the type.
                    detail = error.diag.message_primary or type(error).__name__
                    raise MigrationError(f"migration {migration.file_name} failed: {detail}") from None
            return pending
        finally:
            if not conn.closed:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


__all__ = ["LEDGER", "LOCK_KEY", "SCHEMA", "Migration", "MigrationError", "MigrationStatus", "load_migrations",
           "migrate", "migration_status"]
