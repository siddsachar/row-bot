"""``row-bot-org``: ``serve`` starts the Org server, ``migrate`` brings its database up to date and
``check-config`` checks its settings and database role.

The database URL is read once, from a copy of the environment, and taken out of the environment
before anything else runs. No message shows it, or any part of it: a database error is reported by
its type, or by the server's message about the SQL.
"""

from __future__ import annotations

import argparse
import os
import sys

from row_bot_org.settings import (
    DATABASE_URL_ENV,
    ENV_PREFIX,
    OrgSettings,
    SettingsError,
    keep_environment_private,
    load_settings,
    remove_secret_variables,
)

EXIT_FAILED = 1
AUTO_MIGRATE_ENV = ENV_PREFIX + "AUTO_MIGRATE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="row-bot-org", description="The Row-Bot Org server (pre-release).")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the server (on 127.0.0.1:8100 unless set otherwise)")
    serve.add_argument("--host", help=f"address to listen on (or {ENV_PREFIX}HOST)")
    serve.add_argument("--port", help=f"port to listen on (or {ENV_PREFIX}PORT)")
    commands.add_parser("migrate", help="apply the pending database migrations, as the migrating role")
    commands.add_parser(
        "check-config", help="check the settings and the database role and exit: 0 when they are usable, 1 when not"
    )
    return parser


def _fail(message: str) -> int:
    sys.stderr.write(f"{message}\n")
    return EXIT_FAILED


def _database_unreachable(error: Exception) -> int:
    # The type only: a connection error's message names the host, port, user and database.
    return _fail(f"Row-Bot Org can't use the database ({type(error).__name__}).")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _migrate(url: str) -> int:
    import psycopg

    from row_bot_org.db.migrations import MigrationError, migrate

    try:
        applied = migrate(url)
    except MigrationError as error:
        return _fail(f"Row-Bot Org didn't migrate: {error}.")
    except psycopg.Error as error:
        return _database_unreachable(error)
    if applied:
        sys.stdout.write("Applied " + ", ".join(m.file_name for m in applied) + ".\n")
    else:
        sys.stdout.write("The database is up to date: no migration was pending.\n")
    return 0


def _check_config(settings: OrgSettings, url: str) -> int:
    import psycopg

    from row_bot_org.db.checks import runtime_role_warnings
    from row_bot_org.db.migrations import MigrationError, load_migrations, migration_status

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            warnings = runtime_role_warnings(conn)
            pending = migration_status(conn, load_migrations()).pending
    except MigrationError as error:
        return _fail(f"Row-Bot Org's database doesn't match its migrations: {error}.")
    except psycopg.Error as error:
        return _database_unreachable(error)
    sys.stdout.write(
        f"Row-Bot Org settings are valid: host {settings.host}, port {settings.port}, "
        f"log level {settings.log_level}, OpenAPI {'on' if settings.openapi else 'off'}, "
        f"auto-migrate {'on' if settings.auto_migrate else 'off'}; the database is reachable"
        + (f", {_plural(len(pending), 'migration')} pending (row-bot-org migrate).\n" if pending else ".\n")
    )
    for warning in warnings:
        sys.stderr.write(f"Warning: {warning}. Serve with a role like deploy/org/README.md describes.\n")
    return 0


def _serve(settings: OrgSettings, url: str) -> int:
    import psycopg

    from row_bot_org.db.migrations import MigrationError, load_migrations, migrate, migration_status

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            pending = migration_status(conn, load_migrations()).pending
        if pending and settings.auto_migrate:
            migrate(url)
        elif pending:
            names = ", ".join(m.file_name for m in pending)
            return _fail(
                f"Row-Bot Org didn't start: {_plural(len(pending), 'migration')} pending ({names}). "
                f"Run row-bot-org migrate, or set {AUTO_MIGRATE_ENV}=1."
            )
    except MigrationError as error:
        return _fail(f"Row-Bot Org didn't start: {error}.")
    except psycopg.Error as error:
        return _database_unreachable(error)

    import uvicorn

    from row_bot_org.app import create_app
    from row_bot_org.db.pool import create_pool
    from row_bot_org.logging import configure_logging

    configure_logging(settings.log_level)
    # No access log: request lines carry query strings, and sign-in callbacks will put codes there.
    uvicorn.run(
        create_app(settings, create_pool(url)),
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        server_header=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Read once, from this copy; then the secrets leave the environment every child would inherit.
    environ = dict(os.environ)
    remove_secret_variables()
    keep_environment_private()
    if args.command == "serve":
        # Options go through the same checks as the variables they stand for.
        for option, suffix in ((args.host, "HOST"), (args.port, "PORT")):
            if option is not None:
                environ[ENV_PREFIX + suffix] = option
    try:
        settings = load_settings(environ)
    except SettingsError as error:
        return _fail(str(error))
    del environ
    url = settings.database_url
    if url is None:
        return _fail(f"Row-Bot Org settings are not valid: {DATABASE_URL_ENV} is not set")
    if args.command == "migrate":
        return _migrate(url)
    if args.command == "check-config":
        return _check_config(settings, url)
    return _serve(settings, url)


if __name__ == "__main__":
    raise SystemExit(main())
