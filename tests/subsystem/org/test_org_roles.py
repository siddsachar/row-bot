"""The server's database role on a real (throwaway) Postgres: it never runs as a role row-level security
doesn't hold back (a superuser, BYPASSRLS, or one that can SET ROLE to either), each Org database has
its own runtime group, and only that group may connect.

Security-sensitive: these run in the pull request lane's Org job.
"""

from __future__ import annotations

import io
import json
import logging
import os
from collections.abc import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()

from psycopg_pool import PoolTimeout  # noqa: E402

from row_bot_org import cli  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db.checks import runtime_role_warnings  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.logging import configure_logging  # noqa: E402
from row_bot_org.settings import OrgSettings  # noqa: E402
from tests.subsystem.org.conftest import APP, OWNER  # noqa: E402

pytestmark = pytest.mark.postgres

PASSWORD = "only-in-this-test"


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.update(app=app, **options))
    return calls


@pytest.fixture
def make_role(org_database) -> Iterator:
    """``make_role(name, attributes, *grants)``: a login role in the database's runtime group, dropped after."""
    made: list[str] = []
    group = org_database.runtime_group()

    def make(name: str, attributes: str = "", *grants: str) -> str:
        with psycopg.connect(org_database.superuser_url, autocommit=True) as admin:
            admin.execute(f"CREATE ROLE {name} LOGIN {attributes} PASSWORD '{PASSWORD}' IN ROLE \"{group}\"")
            made.append(name)
            for grant in grants:
                admin.execute(grant.format(role=name))
        return org_database.cluster.url(org_database.name, name, PASSWORD)

    yield make
    with psycopg.connect(org_database.superuser_url, autocommit=True) as admin:
        for name in reversed(made):
            admin.execute(f"DROP OWNED BY {name}")
            admin.execute(f"DROP ROLE {name}")
            admin.execute(f"DROP ROLE IF EXISTS {name}_bypass")


UNFIT = [
    ("superuser", "SUPERUSER", (), "the database role is a superuser"),
    ("bypassrls", "BYPASSRLS", (), "the database role has BYPASSRLS"),
    ("set_superuser", "", ("GRANT row_bot_test TO {role} WITH INHERIT FALSE, SET TRUE",),
     "the database role can SET ROLE to row_bot_test, which is a superuser"),
    ("set_bypass", "", ("CREATE ROLE {role}_bypass NOLOGIN BYPASSRLS", "GRANT {role}_bypass TO {role}"),
     "the database role can SET ROLE to set_bypass_bypass, which has BYPASSRLS"),
]


@pytest.mark.parametrize(("name", "attributes", "grants", "problem"), UNFIT, ids=[case[0] for case in UNFIT])
def test_serve_refuses_a_role_that_row_level_security_doesnt_hold_back(
    make_role, monkeypatch: pytest.MonkeyPatch, served, capsys, name, attributes, grants, problem
) -> None:
    url = make_role(name, attributes, *grants)
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", url)

    assert cli.main(["serve"]) == 1

    assert served == {}
    err = capsys.readouterr().err
    assert err.startswith(f"Row-Bot Org didn't start: {problem}, so row-level security wouldn't hold it back.")
    assert PASSWORD not in err


@pytest.mark.parametrize(("name", "attributes", "grants", "problem"), UNFIT, ids=[case[0] for case in UNFIT])
def test_check_config_refuses_the_same_roles(
    make_role, monkeypatch: pytest.MonkeyPatch, capsys, name, attributes, grants, problem
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", make_role(name, attributes, *grants))

    assert cli.main(["check-config"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert f"Error: {problem}, so row-level security wouldn't hold it back." in output.err
    assert output.err.endswith("Row-Bot Org wouldn't start with this database role.\n")


def test_a_role_made_a_superuser_after_start_makes_the_server_not_ready(org_database, make_role) -> None:
    url = make_role("promoted")
    stream = io.StringIO()
    configure_logging("INFO", stream)
    app = create_app(OrgSettings(), create_pool(url, max_size=1))

    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        with psycopg.connect(org_database.superuser_url, autocommit=True) as admin:
            admin.execute("ALTER ROLE promoted SUPERUSER")
        response = client.get("/readyz")
        with psycopg.connect(org_database.superuser_url, autocommit=True) as admin:
            admin.execute("ALTER ROLE promoted NOSUPERUSER")

    assert (response.status_code, response.json()) == (503, {"status": "not_ready", "checks": {"database": "failing"}})
    assert any(line["msg"] == "readiness check database failed: PrivilegedRoleError"
               for line in map(json.loads, stream.getvalue().splitlines()))


def test_the_pool_hands_out_no_connection_for_an_unfit_role(make_role) -> None:
    pool = create_pool(make_role("unfit", "BYPASSRLS"), max_size=1)
    pool.open(wait=False)
    try:
        with pytest.raises(PoolTimeout), pool.connection(timeout=1.0):
            pass
    finally:
        pool.close()


def test_a_role_that_can_become_the_owner_is_warned_about(make_role, monkeypatch: pytest.MonkeyPatch,
                                                         capsys) -> None:
    url = make_role("sneaky", "", f"GRANT {OWNER} TO {{role}} WITH INHERIT FALSE, SET TRUE")

    with psycopg.connect(url) as conn:
        assert runtime_role_warnings(conn) == [
            "the database role owns (or is a member of the owner of) the row_bot_org schema or its tables, "
            "so it could turn row-level security off"
        ]
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", url)
    assert cli.main(["check-config"]) == 0
    assert capsys.readouterr().err.startswith("Warning: the database role owns")


def test_each_database_has_its_own_runtime_group_and_only_it_may_connect(empty_org_database, org_database) -> None:
    empty_org_database.migrate_and_admit()

    assert empty_org_database.runtime_group() == f"row_bot_org_runtime_{empty_org_database.name}"
    assert empty_org_database.runtime_group() != org_database.runtime_group()
    with psycopg.connect(empty_org_database.runtime_url) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    with psycopg.connect(empty_org_database.superuser_url, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE outsider LOGIN PASSWORD '{PASSWORD}'")
    try:
        with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
            psycopg.connect(empty_org_database.cluster.url(empty_org_database.name, "outsider", PASSWORD))
    finally:
        with psycopg.connect(empty_org_database.superuser_url, autocommit=True) as admin:
            admin.execute("DROP ROLE outsider")


def test_a_long_database_name_gets_a_hashed_group_name(postgres_cluster, org_roles, org_template) -> None:
    from tests.subsystem.org.conftest import OrgDatabase, _admin, _empty_database

    name = "row_bot_org_" + "x" * 50
    database: OrgDatabase = _empty_database(postgres_cluster, org_roles, name)
    try:
        database.migrate_and_admit()
        group = database.runtime_group()
        assert group.startswith("row_bot_org_runtime_") and len(group) == len("row_bot_org_runtime_") + 32
        with psycopg.connect(database.runtime_url) as conn:
            assert conn.execute("SELECT current_user").fetchone() == (APP,)
    finally:
        _admin(postgres_cluster, f"DROP DATABASE {name} WITH (FORCE)")
