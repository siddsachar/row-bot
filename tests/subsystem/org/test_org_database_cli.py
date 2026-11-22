"""``row-bot-org`` and ``/readyz`` against a real (throwaway) Postgres: serve refuses a pending migration,
readiness follows the migrations, check-config judges the role, and the URL never shows."""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()

from psycopg_pool import PoolTimeout  # noqa: E402

from row_bot_org import cli  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.db import checks  # noqa: E402
from row_bot_org.db.migrations import migrate  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.settings import OrgSettings  # noqa: E402

pytestmark = pytest.mark.postgres

MARKER = "marker7f3a"


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def root_logging():
    """``serve`` takes over the root logger, as it should in its own process; put pytest's back."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}

    def run(app, **options):
        calls["app"] = app
        calls.update(options)

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def _applied(url: str) -> list[int]:
    with psycopg.connect(url) as conn:
        return [version for (version,) in conn.execute("SELECT version FROM row_bot_org.schema_migrations")]


def test_serve_refuses_to_start_while_a_migration_is_pending(
    empty_org_database, monkeypatch: pytest.MonkeyPatch, served, capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", empty_org_database.runtime_url)

    assert cli.main(["serve"]) == 1

    assert served == {}
    assert capsys.readouterr().err == (
        "Row-Bot Org didn't start: 1 migration pending (0001_foundation.sql). "
        "Run row-bot-org migrate, or set ROW_BOT_ORG_AUTO_MIGRATE=1.\n"
    )


def test_serve_with_auto_migrate_applies_the_pending_migrations_first(
    empty_org_database, monkeypatch: pytest.MonkeyPatch, served
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", empty_org_database.owner_url)
    monkeypatch.setenv("ROW_BOT_ORG_AUTO_MIGRATE", "1")

    assert cli.main(["serve"]) == 0

    assert _applied(empty_org_database.owner_url) == [1]
    assert "app" in served


def test_serve_listens_on_loopback_by_default_and_is_ready(
    org_database, monkeypatch: pytest.MonkeyPatch, served
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", org_database.runtime_url)

    assert cli.main(["serve"]) == 0

    assert "ROW_BOT_ORG_DATABASE_URL" not in os.environ
    assert (served["host"], served["port"]) == ("127.0.0.1", 8100)
    assert served["access_log"] is False and served["log_config"] is None and served["server_header"] is False
    with TestClient(served["app"]) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_serve_options_override_the_variables(org_database, monkeypatch: pytest.MonkeyPatch, served) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", org_database.runtime_url)
    monkeypatch.setenv("ROW_BOT_ORG_PORT", "9000")

    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9001"]) == 0
    assert (served["host"], served["port"]) == ("0.0.0.0", 9001)


def test_readyz_turns_ready_once_the_database_is_migrated(empty_org_database) -> None:
    app = create_app(OrgSettings(), create_pool(empty_org_database.runtime_url))

    with TestClient(app) as client:
        before = client.get("/readyz")
        migrate(empty_org_database.owner_url)
        after = client.get("/readyz")

    assert (before.status_code, before.json()) == (503, {"status": "not_ready", "checks": {"database": "failing"}})
    assert (after.status_code, after.json()) == (200, {"status": "ready", "checks": {"database": "ok"}})


def test_readyz_is_not_ready_and_quiet_when_the_database_cant_be_reached(
    org_database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(checks, "READINESS_TIMEOUT", 1.0)
    url = org_database.cluster.url(org_database.name, f"user_{MARKER}", f"password_{MARKER}")
    app = create_app(OrgSettings(), create_pool(url))

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = client.get("/readyz")

    assert (response.status_code, response.json()) == (503, {"status": "not_ready", "checks": {"database": "failing"}})
    assert "readiness check database failed: PoolTimeout" in caplog.text
    assert "error connecting in 'row_bot_org': OperationalError" in caplog.text
    assert MARKER not in caplog.text and org_database.name not in caplog.text
    assert MARKER not in response.text


def test_the_pool_logs_no_part_of_the_url(org_database, caplog: pytest.LogCaptureFixture) -> None:
    url = org_database.cluster.url(org_database.name, f"user_{MARKER}", f"password_{MARKER}")
    pool = create_pool(url)

    with caplog.at_level(logging.INFO, logger="psycopg"):
        pool.open(wait=False)
        try:
            with pytest.raises(PoolTimeout), pool.connection(timeout=1.0):
                pass
        finally:
            pool.close()

    assert "OperationalError" in caplog.text
    assert MARKER not in caplog.text
    assert org_database.name not in caplog.text and str(org_database.cluster.port) not in caplog.text


@pytest.mark.parametrize("command", ["serve", "migrate", "check-config"])
def test_a_refused_connection_never_shows_the_url(
    org_database, monkeypatch: pytest.MonkeyPatch, served, capsys, command: str
) -> None:
    url = org_database.cluster.url(org_database.name, f"user_{MARKER}", f"password_{MARKER}")
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", url)

    assert cli.main([command]) == 1

    output = capsys.readouterr()
    assert output.err == "Row-Bot Org can't use the database (OperationalError).\n"
    assert MARKER not in output.out + output.err
    assert served == {}


def test_check_config_accepts_the_runtime_role_without_a_warning(
    org_database, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", org_database.runtime_url)
    monkeypatch.setenv("ROW_BOT_ORG_PORT", "9000")

    assert cli.main(["check-config"]) == 0

    output = capsys.readouterr()
    assert output.out == (
        "Row-Bot Org settings are valid: host 127.0.0.1, port 9000, log level INFO, OpenAPI off, "
        "auto-migrate off; the database is reachable.\n"
    )
    assert output.err == ""


def test_check_config_says_when_migrations_are_pending(
    empty_org_database, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", empty_org_database.runtime_url)

    assert cli.main(["check-config"]) == 0
    assert capsys.readouterr().out.endswith("the database is reachable, 1 migration pending (row-bot-org migrate).\n")


@pytest.mark.parametrize(
    ("role", "warnings"),
    [
        ("owner", ["owns (or is a member of the owner of) the row_bot_org schema"]),
        ("superuser", ["is a superuser", "has BYPASSRLS", "owns (or is a member of the owner of) the row_bot_org schema"]),
    ],
)
def test_check_config_warns_when_the_role_could_get_past_row_level_security(
    org_database, monkeypatch: pytest.MonkeyPatch, capsys, role: str, warnings: list[str]
) -> None:
    url = org_database.owner_url if role == "owner" else org_database.superuser_url
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", url)

    assert cli.main(["check-config"]) == 0

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == len(warnings)
    assert all(line.startswith("Warning: the database role ") for line in lines)
    assert all(expected in line for expected, line in zip(warnings, lines, strict=True))


def test_check_config_warns_about_bypassrls(org_database, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    with psycopg.connect(org_database.superuser_url, autocommit=True) as admin:
        admin.execute("CREATE ROLE row_bot_org_bypass LOGIN BYPASSRLS PASSWORD 'bypass-only-in-this-test'"
                      " IN ROLE row_bot_org_runtime")
    try:
        monkeypatch.setenv(
            "ROW_BOT_ORG_DATABASE_URL",
            org_database.cluster.url(org_database.name, "row_bot_org_bypass", "bypass-only-in-this-test"),
        )
        assert cli.main(["check-config"]) == 0
    finally:
        with psycopg.connect(org_database.superuser_url, autocommit=True) as admin:
            admin.execute("DROP ROLE row_bot_org_bypass")

    assert capsys.readouterr().err.splitlines() == [
        "Warning: the database role has BYPASSRLS, so row-level security doesn't apply to it. "
        "Serve with a role like deploy/org/README.md describes."
    ]


def _free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.slow
def test_the_installed_command_serves_on_loopback_and_is_ready(org_database, tmp_path: Path) -> None:
    command = shutil.which("row-bot-org", path=str(Path(sys.executable).parent))
    assert command, "uv sync installs the row-bot-org command next to the interpreter"
    port = _free_loopback_port()
    env = {
        **{name: value for name, value in os.environ.items() if not name.startswith("ROW_BOT_ORG_")},
        "ROW_BOT_DATA_DIR": str(tmp_path / "data"),
        "PYTHONUNBUFFERED": "1",
        "ROW_BOT_ORG_DATABASE_URL": org_database.runtime_url,
    }
    server = subprocess.Popen([command, "serve", "--port", str(port)], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env)
    try:
        lines = []
        for line in server.stdout:
            lines.append(json.loads(line))
            if lines[-1]["msg"].startswith("Uvicorn running on"):
                break
        assert lines and f"http://127.0.0.1:{port}" in lines[-1]["msg"], lines
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(f"http://127.0.0.1:{port}/healthz", timeout=10) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers.get("Server") is None
        with direct.open(f"http://127.0.0.1:{port}/readyz", timeout=10) as response:
            assert json.loads(response.read()) == {"status": "ready", "checks": {"database": "ok"}}
    finally:
        server.terminate()
        server.wait(timeout=30)
        output = server.stdout.read()
        server.stdout.close()
    assert org_database.roles.app_password not in output
