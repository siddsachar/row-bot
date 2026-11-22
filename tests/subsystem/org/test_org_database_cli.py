"""``row-bot-org`` and ``/readyz`` against a real (throwaway) Postgres: serve refuses a pending migration,
readiness follows the migrations, check-config judges the role, and the URLs never show."""

from __future__ import annotations

import io
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
from row_bot_org.db.migrations import load_migrations  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.logging import configure_logging  # noqa: E402
from row_bot_org.settings import OrgSettings  # noqa: E402

pytestmark = pytest.mark.postgres

MARKER = "marker7f3a"
SHIPPED = load_migrations()


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


def _bad_login(database) -> str:
    return database.cluster.url(database.name, f"user_{MARKER}", f"password_{MARKER}")


def test_serve_refuses_to_start_while_a_migration_is_pending(
    empty_org_database, monkeypatch: pytest.MonkeyPatch, served, capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", empty_org_database.runtime_url)

    assert cli.main(["serve"]) == 1

    assert served == {}
    assert capsys.readouterr().err == (
        f"Row-Bot Org didn't start: {len(SHIPPED)} migrations pending ({', '.join(m.file_name for m in SHIPPED)}). "
        "Run row-bot-org migrate, or set ROW_BOT_ORG_AUTO_MIGRATE=1.\n"
    )


def test_serve_with_auto_migrate_migrates_with_the_migration_url_and_serves_with_the_runtime_url(
    empty_org_database, monkeypatch: pytest.MonkeyPatch, served
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", empty_org_database.runtime_url)
    monkeypatch.setenv("ROW_BOT_ORG_MIGRATION_DATABASE_URL", empty_org_database.owner_url)
    monkeypatch.setenv("ROW_BOT_ORG_AUTO_MIGRATE", "1")

    assert cli.main(["serve"]) == 0

    assert "ROW_BOT_ORG_MIGRATION_DATABASE_URL" not in os.environ
    assert _applied(empty_org_database.owner_url) == [m.version for m in SHIPPED]
    with psycopg.connect(empty_org_database.owner_url) as conn:
        # The tables belong to the migrating role, not to the role the server runs as.
        assert conn.execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'row_bot_org'"
                            ).fetchone() == ("row_bot_org_owner",)


def test_serve_listens_on_loopback_by_default_and_is_ready(
    org_database, monkeypatch: pytest.MonkeyPatch, served
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", org_database.runtime_url)

    assert cli.main(["serve"]) == 0

    assert "ROW_BOT_ORG_DATABASE_URL" not in os.environ
    assert (served["host"], served["port"]) == ("127.0.0.1", 8100)
    assert served["access_log"] is False and served["log_config"] is None and served["server_header"] is False
    # uvicorn's own proxy headers trust 127.0.0.1; forwarded headers count only from ROW_BOT_ORG_TRUSTED_PROXIES.
    assert served["proxy_headers"] is False
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


def test_readyz_turns_ready_once_the_database_is_migrated_and_the_role_admitted(empty_org_database) -> None:
    app = create_app(OrgSettings(), create_pool(empty_org_database.runtime_url))

    with TestClient(app) as client:
        before = client.get("/readyz")
        empty_org_database.migrate_and_admit()
        after = client.get("/readyz")

    assert (before.status_code, before.json()) == (503, {"status": "not_ready", "checks": {"database": "failing"}})
    assert (after.status_code, after.json()) == (200, {"status": "ready", "checks": {"database": "ok"}})


def test_readyz_is_not_ready_and_quiet_when_the_database_cant_be_reached(
    org_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checks, "READINESS_TIMEOUT", 1.0)
    stream = io.StringIO()
    configure_logging("INFO", stream)
    app = create_app(OrgSettings(), create_pool(_bad_login(org_database)))

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert (response.status_code, response.json()) == (503, {"status": "not_ready", "checks": {"database": "failing"}})
    logs = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert any(line["logger"] == "row_bot_org.health" and line["msg"] == "readiness check database failed: PoolTimeout"
               for line in logs)
    assert any(line["logger"] == "psycopg.pool" and line["level"] == "WARNING" for line in logs)
    assert MARKER not in stream.getvalue() and org_database.name not in stream.getvalue()
    assert str(org_database.cluster.port) not in stream.getvalue() and MARKER not in response.text


def test_database_log_records_and_errors_name_no_part_of_the_url(org_database) -> None:
    """psycopg logs a failed rollback with the Transaction, whose repr has the host, user and database;
    a connection error's message has the user."""
    refused = None
    try:
        psycopg.connect(_bad_login(org_database))
    except psycopg.OperationalError as error:
        refused = error
    assert refused is not None and MARKER in str(refused)
    stream = io.StringIO()
    configure_logging("INFO", stream)
    with psycopg.connect(org_database.runtime_url, autocommit=True) as conn, conn.transaction() as transaction:
        assert "row_bot_org_app" in repr(transaction)
        for name in ("psycopg", "psycopg.transaction", "psycopg_pool.sched"):
            logging.getLogger(name).warning("error ignored in rollback of %s: %s", transaction, "boom")
        try:
            raise RuntimeError("the request failed") from refused
        except RuntimeError as wrapped:
            logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=wrapped)

    text = stream.getvalue()
    assert "row_bot_org_app" not in text and org_database.name not in text and MARKER not in text
    assert [(line["logger"], line["msg"], line.get("exc")) for line in map(json.loads, text.splitlines())] == [
        ("psycopg", "details withheld: they can name the database", None),
        ("psycopg.transaction", "details withheld: they can name the database", None),
        ("psycopg_pool.sched", "details withheld: they can name the database", None),
        ("uvicorn.error", "Exception in ASGI application", "RuntimeError"),
    ]


@pytest.mark.parametrize("command", ["serve", "migrate", "check-config"])
def test_a_refused_connection_never_shows_the_url(
    org_database, monkeypatch: pytest.MonkeyPatch, served, capsys, command: str
) -> None:
    variable = "ROW_BOT_ORG_MIGRATION_DATABASE_URL" if command == "migrate" else "ROW_BOT_ORG_DATABASE_URL"
    monkeypatch.setenv(variable, _bad_login(org_database))

    assert cli.main([command]) == 1

    output = capsys.readouterr()
    assert output.err == "Row-Bot Org can't use the database (OperationalError).\n"
    assert MARKER not in output.out + output.err
    assert served == {}


@pytest.mark.parametrize(
    ("url", "message"),
    [
        (f"postgresql://app:{MARKER}@127.0.0.1:1/db\x07tail", "must not contain control characters"),
        (f"postgresql://app:{MARKER}@127.0.0.1:1/db?evil={MARKER}", "is not a usable postgresql:// address"),
        (f"postgresql://app:{MARKER}%zz@127.0.0.1:1/db", "is not a usable postgresql:// address"),
    ],
    ids=["control-character", "unknown-option", "bad-percent-encoding"],
)
@pytest.mark.parametrize("command", ["check-config", "migrate"])
def test_a_malformed_url_is_refused_before_connecting_without_showing_it(
    monkeypatch: pytest.MonkeyPatch, capsys, command: str, url: str, message: str
) -> None:
    variable = "ROW_BOT_ORG_MIGRATION_DATABASE_URL" if command == "migrate" else "ROW_BOT_ORG_DATABASE_URL"
    monkeypatch.setenv(variable, url)

    assert cli.main([command]) == 1

    err = capsys.readouterr().err
    assert message in err and variable in err
    assert MARKER not in err and "evil" not in err


def test_a_url_with_a_bad_port_reports_only_the_error_type(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", f"postgresql://app:{MARKER}@127.0.0.1:notaport/db")

    assert cli.main(["check-config"]) == 1
    assert capsys.readouterr().err == "Row-Bot Org can't use the database (OperationalError).\n"


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
    assert capsys.readouterr().out.endswith(
        f"the database is reachable, {len(SHIPPED)} migrations pending (row-bot-org migrate).\n")


def test_check_config_refuses_the_role_that_owns_the_tables(
    org_database, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", org_database.owner_url)

    assert cli.main(["check-config"]) == 1

    assert capsys.readouterr().err.splitlines() == [
        "Error: the database role owns (or is a member of the owner of) the row_bot_org schema or its tables, "
        "so row-level security wouldn't hold it back. Serve as the app role, not the owner that migrates: "
        "deploy/org/README.md describes the two roles.",
        "Row-Bot Org wouldn't start with this database role.",
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
    assert org_database.roles.app_password not in output and org_database.name not in output


def test_pooled_connections_keep_their_statement_lock_and_idle_limits(org_database) -> None:
    pool = create_pool(org_database.runtime_url, max_size=1, statement_timeout=7, lock_timeout=3,
                       idle_in_transaction_timeout=11)
    pool.open(wait=True, timeout=30)
    try:
        for _ in range(2):  # and again after DISCARD ALL wiped the session
            with pool.connection() as conn:
                assert conn.execute(
                    "SELECT current_setting('statement_timeout'), current_setting('lock_timeout'),"
                    " current_setting('idle_in_transaction_session_timeout')"
                ).fetchone() == ("7s", "3s", "11s")
                conn.execute("SET statement_timeout = '1h'")
    finally:
        pool.close()


def test_no_pool_connection_is_made_while_unreachable_and_the_url_stays_out_of_its_logs(org_database) -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    pool = create_pool(_bad_login(org_database))
    pool.open(wait=False)
    try:
        with pytest.raises(PoolTimeout), pool.connection(timeout=1.0):
            pass
    finally:
        pool.close()

    assert '"logger": "psycopg.pool"' in stream.getvalue()
    assert MARKER not in stream.getvalue() and org_database.name not in stream.getvalue()
