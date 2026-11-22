"""``row-bot-org`` without a database: settings errors, the missing URL, and JSON logs on stdout.

The commands that reach a database are in ``test_org_database_cli.py`` (marked ``postgres``).
"""

from __future__ import annotations

import io
import json
import logging
import os

import pytest
import uvicorn

from row_bot_org import cli
from row_bot_org.logging import WITHHELD, configure_logging

MARKER = "marker-value-7f3a"


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}

    def run(app, **options):
        calls["app"] = app
        calls.update(options)

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def test_check_config_exits_1_and_names_the_bad_setting_only(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_PORT", MARKER)

    assert cli.main(["check-config"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "ROW_BOT_ORG_PORT must be a port number from 1 to 65535" in output.err
    assert MARKER not in output.err


@pytest.mark.parametrize(
    ("command", "variable"),
    [("serve", "ROW_BOT_ORG_DATABASE_URL"), ("check-config", "ROW_BOT_ORG_DATABASE_URL"),
     ("migrate", "ROW_BOT_ORG_MIGRATION_DATABASE_URL")],
)
def test_every_command_needs_its_database_url(
    monkeypatch: pytest.MonkeyPatch, command: str, variable: str, served, capsys
) -> None:
    # The other URL doesn't stand in: serve never runs as the migrating role, nor migrate as the runtime one.
    other = "ROW_BOT_ORG_DATABASE_URL" if command == "migrate" else "ROW_BOT_ORG_MIGRATION_DATABASE_URL"
    monkeypatch.setenv(other, f"postgresql://org:{MARKER}@db/org")

    assert cli.main([command]) == 1

    assert served == {}
    assert capsys.readouterr().err == f"Row-Bot Org settings are not valid: {variable} is not set\n"
    assert other not in os.environ


def test_serve_refuses_to_start_with_bad_settings(served, capsys) -> None:
    assert cli.main(["serve", "--port", "99999"]) == 1

    assert served == {}
    assert "ROW_BOT_ORG_PORT must be a port number from 1 to 65535" in capsys.readouterr().err


def test_the_database_url_leaves_the_environment_even_when_a_setting_is_bad(
    monkeypatch: pytest.MonkeyPatch, private_environment: list[bool], capsys
) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_DATABASE_URL", f"postgresql://org:{MARKER}@db/org")
    monkeypatch.setenv("ROW_BOT_ORG_LOG_LEVEL", "loud")

    assert cli.main(["serve"]) == 1

    assert "ROW_BOT_ORG_DATABASE_URL" not in os.environ
    assert private_environment == [True]
    assert MARKER not in capsys.readouterr().err


def test_database_library_records_keep_only_their_logger_level_and_exception_type() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        configure_logging("INFO", stream)
        for name in ("psycopg", "psycopg.pool", "psycopg_pool.sched", "psycopg.transaction"):
            logging.getLogger(name).warning("error connecting to %s", f"host=db user={MARKER}")
        try:
            raise ConnectionError(f"user={MARKER}")
        except ConnectionError:
            logging.getLogger("psycopg.pool").exception(f"connection {MARKER} failed")
        logging.getLogger("psycopgish").warning("not the library: %s", "kept")
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert MARKER not in stream.getvalue()
    assert [(line["logger"], line["level"], line["msg"], line.get("exc")) for line in lines] == [
        ("psycopg", "WARNING", WITHHELD, None),
        ("psycopg.pool", "WARNING", WITHHELD, None),
        ("psycopg_pool.sched", "WARNING", WITHHELD, None),
        ("psycopg.transaction", "WARNING", WITHHELD, None),
        ("psycopg.pool", "ERROR", WITHHELD, "ConnectionError"),
        ("psycopgish", "WARNING", "not the library: kept", None),
    ]


def test_logs_are_json_lines() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        configure_logging("INFO", stream)
        logging.getLogger("row_bot_org.test").info("started on %s", "127.0.0.1")
        logging.getLogger("row_bot_org.test").debug("below the level")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logging.getLogger("row_bot_org.test").exception("failed")
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [(line["level"], line["logger"], line["msg"]) for line in lines] == [
        ("INFO", "row_bot_org.test", "started on 127.0.0.1"),
        ("ERROR", "row_bot_org.test", "failed"),
    ]
    assert "RuntimeError: boom" in lines[1]["exc"]
    assert all(line["ts"].endswith("+00:00") for line in lines)
