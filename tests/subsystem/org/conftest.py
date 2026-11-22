"""Org server fixtures: the throwaway Postgres cluster (tests/fixtures/postgres.py) and databases in it.

The session's cluster gets the two roles a deployment has (deploy/org/README.md): ``row_bot_org_owner``
migrates and owns the tables; ``row_bot_org_app`` is the runtime login, a member of the database's
runtime group (``row_bot_org.runtime_role()``). A template database is migrated once; each test gets a
fresh copy of it (whose runtime group is the template's), or an empty database that hasn't been migrated.
"""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from row_bot_org import cli
from tests.fixtures.postgres import PostgresCluster, postgres_cluster  # noqa: F401 - the session fixture

OWNER = "row_bot_org_owner"
APP = "row_bot_org_app"
_TEMPLATE = "row_bot_org_template"
_DATABASES = itertools.count(1)


@pytest.fixture(autouse=True)
def private_environment(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """``main`` makes its process non-dumpable on Linux; the test process stays as it is."""
    calls: list[bool] = []
    monkeypatch.setattr(cli, "keep_environment_private", lambda: calls.append(True))
    return calls


@dataclass(frozen=True)
class OrgRoles:
    owner_password: str
    app_password: str


@dataclass(frozen=True)
class OrgDatabase:
    cluster: PostgresCluster
    roles: OrgRoles
    name: str

    @property
    def owner_url(self) -> str:
        return self.cluster.url(self.name, OWNER, self.roles.owner_password)

    @property
    def runtime_url(self) -> str:
        return self.cluster.url(self.name, APP, self.roles.app_password)

    @property
    def superuser_url(self) -> str:
        return self.cluster.url(self.name)

    def runtime_group(self) -> str:
        import psycopg

        with psycopg.connect(self.superuser_url) as conn:
            return conn.execute("SELECT row_bot_org.runtime_role()").fetchone()[0]

    def migrate_and_admit(self) -> None:
        """Migrate as the owner, then make the app role a member of the runtime group, as an operator would."""
        from row_bot_org.db.migrations import migrate

        migrate(self.owner_url)
        _admin(self.cluster, f'GRANT "{self.runtime_group()}" TO {APP}')


def _admin(cluster: PostgresCluster, sql: str, dbname: str = "postgres") -> None:
    import psycopg

    with psycopg.connect(cluster.url(dbname), autocommit=True) as conn:
        conn.execute(sql)


@pytest.fixture(scope="session")
def org_roles(postgres_cluster: PostgresCluster) -> OrgRoles:  # noqa: F811 - pytest's fixture injection
    roles = OrgRoles(secrets.token_urlsafe(18), secrets.token_urlsafe(18))
    _admin(postgres_cluster, f"CREATE ROLE {OWNER} LOGIN CREATEROLE PASSWORD '{roles.owner_password}'")
    _admin(postgres_cluster, f"CREATE ROLE {APP} LOGIN PASSWORD '{roles.app_password}'")
    return roles


def _empty_database(cluster: PostgresCluster, roles: OrgRoles, name: str) -> OrgDatabase:
    # As an administrator prepares one: owned by the migrating role, pgvector (not a trusted extension) made.
    _admin(cluster, f"CREATE DATABASE {name} OWNER {OWNER}")
    _admin(cluster, "CREATE EXTENSION vector", dbname=name)
    return OrgDatabase(cluster, roles, name)


@pytest.fixture(scope="session")
def org_template(postgres_cluster: PostgresCluster, org_roles: OrgRoles) -> str:  # noqa: F811
    _empty_database(postgres_cluster, org_roles, _TEMPLATE).migrate_and_admit()
    return _TEMPLATE


@pytest.fixture
def org_database(postgres_cluster: PostgresCluster, org_roles: OrgRoles,  # noqa: F811
                 org_template: str) -> Iterator[OrgDatabase]:
    """A migrated database of the test's own."""
    name = f"row_bot_org_test_{next(_DATABASES)}"
    _admin(postgres_cluster, f"CREATE DATABASE {name} TEMPLATE {org_template} OWNER {OWNER}")
    yield OrgDatabase(postgres_cluster, org_roles, name)
    _admin(postgres_cluster, f"DROP DATABASE {name} WITH (FORCE)")


@pytest.fixture
def empty_org_database(postgres_cluster: PostgresCluster, org_roles: OrgRoles,  # noqa: F811
                       org_template: str) -> Iterator[OrgDatabase]:
    """A database of the test's own that hasn't been migrated (the roles exist: the template made them)."""
    name = f"row_bot_org_test_{next(_DATABASES)}"
    yield _empty_database(postgres_cluster, org_roles, name)
    _admin(postgres_cluster, f"DROP DATABASE {name} WITH (FORCE)")
