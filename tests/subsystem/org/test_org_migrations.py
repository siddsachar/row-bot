"""Forward-only migrations on a real (throwaway) Postgres: once each, one runner at a time, and a
database that doesn't match the files is refused."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()

from row_bot_org import cli  # noqa: E402
from row_bot_org.db.migrations import (  # noqa: E402
    LOCK_KEY,
    MigrationError,
    load_migrations,
    migrate,
    migration_status,
)

pytestmark = pytest.mark.postgres

SHIPPED = load_migrations()


def _folder(tmp_path: Path, **files: str) -> Path:
    folder = tmp_path / "migrations"
    folder.mkdir()
    for name, sql in files.items():
        (folder / name).write_text(sql, encoding="utf-8", newline="\n")
    return folder


def _ledger(url: str) -> list[tuple[int, str, str]]:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT version, name, checksum FROM row_bot_org.schema_migrations ORDER BY 1").fetchall()


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'row_bot_org'").fetchall()
    return {name for (name,) in rows}


def test_the_shipped_migrations_apply_once(empty_org_database) -> None:
    url = empty_org_database.owner_url

    assert [m.file_name for m in migrate(url)] == ["0001_foundation.sql"]
    assert migrate(url) == ()
    assert _ledger(url) == [(1, "foundation", SHIPPED[0].checksum)]


def test_a_runner_waits_while_another_holds_the_lock(empty_org_database) -> None:
    url = empty_org_database.owner_url
    results: list[object] = []
    with psycopg.connect(url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        runner = threading.Thread(target=lambda: results.append(migrate(url)))
        runner.start()
        deadline = time.monotonic() + 30
        while not holder.execute(
            "SELECT 1 FROM pg_catalog.pg_locks WHERE locktype = 'advisory' AND NOT granted"
            " AND database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())"
        ).fetchone():
            assert time.monotonic() < deadline, "the runner never asked for the lock"
            time.sleep(0.02)
        assert runner.is_alive() and results == []
        assert "schema_migrations" not in _tables(url)
        holder.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        runner.join(timeout=60)

    assert [[m.file_name for m in applied] for applied in results] == [["0001_foundation.sql"]]


def test_two_runners_at_once_apply_each_migration_once(empty_org_database, tmp_path: Path) -> None:
    url = empty_org_database.owner_url
    migrations = load_migrations(_folder(
        tmp_path,
        **{
            "0001_first.sql": "CREATE TABLE row_bot_org.first (id integer);",
            "0002_second.sql": "CREATE TABLE row_bot_org.second (id integer);",
        },
    ))
    start = threading.Barrier(2)
    results: list[tuple[str, ...]] = []
    errors: list[BaseException] = []

    def run() -> None:
        start.wait()
        try:
            results.append(tuple(m.file_name for m in migrate(url, migrations)))
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    runners = [threading.Thread(target=run) for _ in range(2)]
    for runner in runners:
        runner.start()
    for runner in runners:
        runner.join(timeout=60)

    assert errors == []
    assert sorted(results) == [(), ("0001_first.sql", "0002_second.sql")]
    assert [version for version, _, _ in _ledger(url)] == [1, 2]


def test_a_changed_migration_is_refused(org_database, tmp_path: Path) -> None:
    changed = load_migrations(_folder(tmp_path, **{"0001_foundation.sql": SHIPPED[0].sql + "\n-- edited\n"}))

    with pytest.raises(MigrationError, match="migration 0001_foundation.sql has changed since it was applied"):
        migrate(org_database.owner_url, changed)
    with psycopg.connect(org_database.runtime_url) as conn, pytest.raises(MigrationError, match="has changed"):
        migration_status(conn, changed)


def test_a_migration_newer_than_the_server_is_refused(org_database) -> None:
    with psycopg.connect(org_database.owner_url) as conn:
        conn.execute("INSERT INTO row_bot_org.schema_migrations (version, name, checksum) VALUES (2, 'later', 'x')")

    with pytest.raises(MigrationError, match="has migration 0002_later, which this server doesn't have"):
        migrate(org_database.owner_url)


def test_a_failing_migration_leaves_nothing_of_itself_behind(empty_org_database, tmp_path: Path) -> None:
    url = empty_org_database.owner_url
    migrations = load_migrations(_folder(
        tmp_path,
        **{
            "0001_fine.sql": "CREATE TABLE row_bot_org.fine (id integer);",
            "0002_broken.sql": "CREATE TABLE row_bot_org.half (id integer); SELECT * FROM no_such_table;",
        },
    ))

    with pytest.raises(MigrationError, match='0002_broken.sql failed: relation "no_such_table" does not exist'):
        migrate(url, migrations)

    assert _tables(url) == {"schema_migrations", "fine"}
    assert [version for version, _, _ in _ledger(url)] == [1]


@pytest.mark.parametrize("name", ["1_short.sql", "0001-dash.sql", "0001_Upper.sql", "0001_.sql"])
def test_a_badly_named_migration_file_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(MigrationError, match="is not named NNNN_name.sql"):
        load_migrations(_folder(tmp_path, **{name: "SELECT 1;"}))


def test_two_migration_files_cant_share_a_number(tmp_path: Path) -> None:
    folder = _folder(tmp_path, **{"0001_one.sql": "SELECT 1;", "0001_two.sql": "SELECT 2;"})

    with pytest.raises(MigrationError, match="0001_two.sql has the same number as 0001_one.sql"):
        load_migrations(folder)


def test_a_checkout_with_crlf_line_endings_holds_the_same_migrations(tmp_path: Path) -> None:
    folder = tmp_path / "crlf"
    folder.mkdir()
    (folder / "0001_foundation.sql").write_bytes(SHIPPED[0].sql.replace("\n", "\r\n").encode("utf-8"))

    assert load_migrations(folder) == SHIPPED[:1]


def test_the_migrate_command_applies_then_reports_nothing_pending(
    empty_org_database, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_MIGRATION_DATABASE_URL", empty_org_database.owner_url)
    assert cli.main(["migrate"]) == 0
    assert capsys.readouterr().out == "Applied 0001_foundation.sql.\n"
    assert "ROW_BOT_ORG_MIGRATION_DATABASE_URL" not in os.environ

    monkeypatch.setenv("ROW_BOT_ORG_MIGRATION_DATABASE_URL", empty_org_database.owner_url)
    assert cli.main(["migrate"]) == 0
    assert capsys.readouterr().out == "The database is up to date: no migration was pending.\n"


def test_a_migration_older_than_the_newest_applied_is_refused(empty_org_database, tmp_path: Path) -> None:
    url = empty_org_database.owner_url
    first = _folder(tmp_path, **{"0002_second.sql": "CREATE TABLE row_bot_org.second (id integer);"})
    migrate(url, load_migrations(first))
    (first / "0001_slipped_in.sql").write_text("CREATE TABLE row_bot_org.first (id integer);", encoding="utf-8")

    with pytest.raises(MigrationError, match=r"0001_slipped_in.sql is older than the newest applied one \(0002\)"):
        migrate(url, load_migrations(first))
    assert "first" not in _tables(url)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE row_bot_org.open (body text); GRANT SELECT ON row_bot_org.open TO PUBLIC;",
        "CREATE TABLE row_bot_org.open (body text); GRANT SELECT (body) ON row_bot_org.open TO PUBLIC;",
        "CREATE TABLE row_bot_org.open (body text); ALTER TABLE row_bot_org.open ENABLE ROW LEVEL SECURITY;"
        " GRANT SELECT ON row_bot_org.open TO PUBLIC;",
        "CREATE VIEW row_bot_org.open AS SELECT 1 AS one; GRANT SELECT ON row_bot_org.open TO PUBLIC;",
    ],
    ids=["no-rls", "column-grant", "enabled-not-forced", "definer-view"],
)
def test_a_migration_that_opens_an_unprotected_table_is_rolled_back(
    empty_org_database, tmp_path: Path, sql: str
) -> None:
    url = empty_org_database.owner_url
    migrations = load_migrations(_folder(tmp_path, **{"0001_open.sql": sql}))

    with pytest.raises(MigrationError, match="0001_open.sql failed: it leaves open open to other roles without forced"):
        migrate(url, migrations)
    assert "open" not in _tables(url) and _ledger(url) == []


def test_the_shipped_migrations_leave_no_table_open_without_forced_row_level_security(empty_org_database) -> None:
    url = empty_org_database.owner_url
    migrate(url)

    with psycopg.connect(url) as conn:
        open_tables = conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'row_bot_org' AND c.relkind IN ('r', 'p') AND c.relname <> 'schema_migrations'"
            " AND has_table_privilege(row_bot_org.runtime_role(), c.oid, 'SELECT, INSERT, UPDATE, DELETE')"
            " AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
        ).fetchall()
        ledger = conn.execute(
            "SELECT has_table_privilege(row_bot_org.runtime_role(), 'row_bot_org.schema_migrations', 'INSERT'),"
            " has_table_privilege(row_bot_org.runtime_role(), 'row_bot_org.schema_migrations', 'SELECT')"
        ).fetchone()
    assert open_tables == []
    assert ledger == (False, True)


def test_a_schema_someone_else_made_first_is_refused(empty_org_database) -> None:
    database = empty_org_database
    with psycopg.connect(database.superuser_url, autocommit=True) as admin:
        admin.execute("CREATE ROLE squatter LOGIN PASSWORD 'only-in-this-test'")
        admin.execute(f"GRANT CREATE ON DATABASE {database.name} TO squatter")
    try:
        with psycopg.connect(database.cluster.url(database.name, "squatter", "only-in-this-test"),
                             autocommit=True) as squatter:
            squatter.execute("CREATE SCHEMA row_bot_org")
            squatter.execute(
                "CREATE TABLE row_bot_org.schema_migrations (version integer PRIMARY KEY, name text NOT NULL,"
                " checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            squatter.execute("INSERT INTO row_bot_org.schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                             (SHIPPED[0].version, SHIPPED[0].name, SHIPPED[0].checksum))
            squatter.execute("GRANT USAGE, CREATE ON SCHEMA row_bot_org TO row_bot_org_owner")
            squatter.execute("GRANT SELECT, INSERT ON row_bot_org.schema_migrations TO row_bot_org_owner")

        with pytest.raises(MigrationError, match="belongs to a role the migrating role isn't a member of"):
            migrate(database.owner_url)
    finally:
        with psycopg.connect(database.superuser_url, autocommit=True) as admin:
            admin.execute("DROP SCHEMA IF EXISTS row_bot_org CASCADE")
            admin.execute(f"REVOKE ALL ON DATABASE {database.name} FROM squatter")
            admin.execute("DROP ROLE squatter")


def test_a_runner_gives_up_waiting_for_the_lock(empty_org_database) -> None:
    url = empty_org_database.owner_url
    with psycopg.connect(url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        with pytest.raises(MigrationError, match="another row-bot-org migrate held the migration lock for 0.5 s"):
            migrate(url, lock_wait=0.5)
    assert "schema_migrations" not in _tables(url)
