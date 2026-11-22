"""Row-level security on a real (throwaway) Postgres, as the runtime role: one principal never reads or
writes another's rows, even when the query forgets its WHERE, and the runtime role can't turn it off.

Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()

from psycopg_pool import ConnectionPool  # noqa: E402

from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402

pytestmark = pytest.mark.postgres

# A principal-scoped table as a later migration makes one (the pattern in 0001_foundation.sql), made by
# the migrating role so it gets the runtime role's default grants.
SCRATCH_TABLE = """
CREATE TABLE row_bot_org.notes (
    id serial PRIMARY KEY,
    owner text NOT NULL DEFAULT row_bot_org.row_bot_principal(),
    team text,
    body text NOT NULL
);
ALTER TABLE row_bot_org.notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.notes FORCE ROW LEVEL SECURITY;
CREATE POLICY notes_owner ON row_bot_org.notes
    USING (owner = row_bot_org.row_bot_principal())
    WITH CHECK (owner = row_bot_org.row_bot_principal());
CREATE POLICY notes_team_reads ON row_bot_org.notes FOR SELECT
    USING (team IS NOT NULL AND row_bot_org.row_bot_in_group(team));
"""


@pytest.fixture
def pool(org_database) -> Iterator[ConnectionPool]:
    """The runtime role's pool, one connection: every transaction reuses the same session."""
    with psycopg.connect(org_database.owner_url) as conn:
        conn.execute(SCRATCH_TABLE)
    runtime = create_pool(org_database.runtime_url, max_size=1)
    runtime.open(wait=True, timeout=30)
    yield runtime
    runtime.close()


def _add(pool: ConnectionPool, principal: str, body: str, team: str | None = None) -> None:
    with principal_transaction(pool, principal) as conn:
        conn.execute("INSERT INTO row_bot_org.notes (team, body) VALUES (%s, %s)", (team, body))


def _bodies(pool: ConnectionPool, principal: str, groups: tuple[str, ...] = ()) -> list[str]:
    with principal_transaction(pool, principal, groups) as conn:
        return [body for (body,) in conn.execute("SELECT body FROM row_bot_org.notes ORDER BY id").fetchall()]


def test_a_principal_reads_and_changes_only_their_own_rows(pool: ConnectionPool) -> None:
    _add(pool, "alice", "alice's note")
    _add(pool, "bob", "bob's note")

    assert _bodies(pool, "alice") == ["alice's note"]
    assert _bodies(pool, "bob") == ["bob's note"]
    with principal_transaction(pool, "bob") as conn:
        # No WHERE: row-level security still keeps bob to his own row.
        assert conn.execute("UPDATE row_bot_org.notes SET body = 'overwritten'").rowcount == 1
        assert conn.execute("SELECT count(*) FROM row_bot_org.notes WHERE owner = 'alice'").fetchone() == (0,)
    with principal_transaction(pool, "bob") as conn:
        assert conn.execute("DELETE FROM row_bot_org.notes").rowcount == 1
    assert _bodies(pool, "alice") == ["alice's note"]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO row_bot_org.notes (owner, body) VALUES ('alice', 'written as alice')",
        "UPDATE row_bot_org.notes SET owner = 'alice'",
    ],
    ids=["insert", "update"],
)
def test_a_principal_cant_write_a_row_as_someone_else(pool: ConnectionPool, statement: str) -> None:
    _add(pool, "bob", "bob's note")

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        with principal_transaction(pool, "bob") as conn:
            conn.execute(statement)
    assert _bodies(pool, "alice") == []
    assert _bodies(pool, "bob") == ["bob's note"]


def test_a_group_member_reads_the_groups_rows_but_cant_change_them(pool: ConnectionPool) -> None:
    _add(pool, "alice", "for the team", team="eng")
    _add(pool, "alice", "private")

    assert _bodies(pool, "carol", ("eng", "design")) == ["for the team"]
    assert _bodies(pool, "dave", ("sales",)) == []
    with principal_transaction(pool, "carol", ("eng",)) as conn:
        assert conn.execute("UPDATE row_bot_org.notes SET body = 'changed'").rowcount == 0
        assert conn.execute("DELETE FROM row_bot_org.notes").rowcount == 0
    assert _bodies(pool, "alice") == ["for the team", "private"]


def test_a_reused_connection_carries_no_principal_out_of_its_transaction(pool: ConnectionPool) -> None:
    _add(pool, "alice", "alice's note")
    with principal_transaction(pool, "alice", ("eng",)) as conn:
        backend = conn.info.backend_pid
        assert conn.execute("SELECT count(*) FROM row_bot_org.notes").fetchone() == (1,)

    with pool.connection() as conn:
        assert conn.info.backend_pid == backend
        assert conn.execute(
            "SELECT row_bot_org.row_bot_principal(), row_bot_org.row_bot_in_group('eng')"
        ).fetchone() == (None, False)
        # Outside a principal transaction, no scoped row is visible, and none can be written.
        assert conn.execute("SELECT count(*) FROM row_bot_org.notes").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute("INSERT INTO row_bot_org.notes (owner, body) VALUES ('alice', 'unowned')")
    with principal_transaction(pool, "bob") as conn:
        assert conn.info.backend_pid == backend
        assert conn.execute("SELECT count(*) FROM row_bot_org.notes").fetchone() == (0,)


def test_a_principal_transaction_that_raises_rolls_back(pool: ConnectionPool) -> None:
    with pytest.raises(RuntimeError, match="stop"):
        with principal_transaction(pool, "alice") as conn:
            conn.execute("INSERT INTO row_bot_org.notes (body) VALUES ('half done')")
            raise RuntimeError("stop")

    assert _bodies(pool, "alice") == []


@pytest.mark.parametrize(("principal", "groups"), [("", ()), ("  ", ()), ("alice", ("eng", "")), ("alice", (None,))],
                         ids=["empty", "blank", "empty-group", "not-a-name"])
def test_a_principal_needs_an_id_and_named_groups(pool: ConnectionPool, principal: str, groups: tuple) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        with principal_transaction(pool, principal, groups):
            pass


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE row_bot_org.notes DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE row_bot_org.notes NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY notes_owner ON row_bot_org.notes",
        "CREATE POLICY everything ON row_bot_org.notes USING (true)",
        "CREATE TABLE row_bot_org.own_table (id integer)",
    ],
    ids=["disable", "no-force", "drop-policy", "add-policy", "create-table"],
)
def test_the_runtime_role_cant_turn_row_level_security_off(pool: ConnectionPool, statement: str) -> None:
    with pool.connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


def test_the_runtime_role_cant_read_past_row_level_security_by_switching_it_off(pool: ConnectionPool) -> None:
    _add(pool, "alice", "alice's note")

    with pool.connection() as conn:
        conn.execute("SET row_security = off")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
                conn.execute("SELECT body FROM row_bot_org.notes")
        finally:
            conn.execute("RESET row_security")


def test_forced_row_level_security_holds_the_tables_owner_too(org_database, pool: ConnectionPool) -> None:
    _add(pool, "alice", "alice's note")

    with psycopg.connect(org_database.owner_url) as owner:
        assert owner.execute("SELECT count(*) FROM row_bot_org.notes").fetchone() == (0,)
