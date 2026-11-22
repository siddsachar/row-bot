"""``row-bot-org``: ``serve`` starts the Org server, ``migrate`` brings its database up to date and
``check-config`` checks its settings and database role.

The database URLs are read once, from a copy of the environment, and taken out of the environment
before anything else runs. No message shows them, or any part of them: a database error is reported by
its type, or by the server's message about the SQL.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

from row_bot_org.settings import (
    DATABASE_URL_ENV,
    ENV_PREFIX,
    MIGRATION_DATABASE_URL_ENV,
    OrgSettings,
    SettingsError,
    keep_environment_private,
    load_settings,
    remove_secret_variables,
)

EXIT_FAILED = 1
AUTO_MIGRATE_ENV = ENV_PREFIX + "AUTO_MIGRATE"
CONNECT_TIMEOUT = 10
ROLE_ADVICE = "Serve as a role like deploy/org/README.md describes."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="row-bot-org", description="The Row-Bot Org server (pre-release).")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the server (on 127.0.0.1:8100 unless set otherwise)")
    serve.add_argument("--host", help=f"address to listen on (or {ENV_PREFIX}HOST)")
    serve.add_argument("--port", help=f"port to listen on (or {ENV_PREFIX}PORT)")
    commands.add_parser("migrate", help=f"apply the pending database migrations, with {MIGRATION_DATABASE_URL_ENV}")
    commands.add_parser(
        "check-config", help="check the settings and the database role and exit: 0 when they are usable, 1 when not"
    )
    principals = commands.add_parser("principals", help=f"operator commands, with {MIGRATION_DATABASE_URL_ENV}")
    actions = principals.add_subparsers(dest="action", required=True)
    revoke = actions.add_parser("revoke-sessions", help="sign a principal out everywhere: revoke its sessions")
    revoke.add_argument("principal_id", type=_principal_id, help="the principal's id (a UUID)")
    return parser


def _principal_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError("must be a principal id (a UUID)") from None


def _fail(message: str) -> int:
    sys.stderr.write(f"{message}\n")
    return EXIT_FAILED


def _database_unreachable(error: Exception) -> int:
    # The type only: a connection error's message names the host, port, user and database.
    return _fail(f"Row-Bot Org can't use the database ({type(error).__name__}).")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _unparseable(urls: dict[str, str | None]) -> str | None:
    """The first setting whose URL libpq can't parse (named, never shown), before anything connects."""
    from psycopg.conninfo import conninfo_to_dict

    for name, url in urls.items():
        if url is None:
            continue
        try:
            conninfo_to_dict(url)
        except Exception:  # noqa: BLE001 - libpq's message quotes the URL
            return name
    return None


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


def _revoke_sessions(url: str, principal_id: str) -> int:
    import psycopg

    from row_bot_org.identity.store import revoke_sessions

    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=CONNECT_TIMEOUT) as conn:
            revoked = revoke_sessions(conn, principal_id)
    except psycopg.Error as error:
        return _database_unreachable(error)
    sys.stdout.write(f"Revoked {_plural(revoked, 'session')} of principal {principal_id}.\n")
    return 0


def _check_config(settings: OrgSettings, url: str) -> int:
    import psycopg

    from row_bot_org.db.checks import privileged_role_problems, runtime_role_warnings
    from row_bot_org.db.migrations import MigrationError, load_migrations, migration_status

    try:
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT) as conn:
            problems = privileged_role_problems(conn)
            warnings = runtime_role_warnings(conn)
            pending = migration_status(conn, load_migrations()).pending
    except MigrationError as error:
        return _fail(f"Row-Bot Org's database doesn't match its migrations: {error}.")
    except psycopg.Error as error:
        return _database_unreachable(error)
    if problems:
        for problem in problems:
            sys.stderr.write(f"Error: {problem}, so row-level security wouldn't hold it back. {ROLE_ADVICE}\n")
        return _fail("Row-Bot Org wouldn't start with this database role.")
    sys.stdout.write(
        f"Row-Bot Org settings are valid: host {settings.host}, port {settings.port}, "
        f"log level {settings.log_level}, OpenAPI {'on' if settings.openapi else 'off'}, "
        f"auto-migrate {'on' if settings.auto_migrate else 'off'}; the database is reachable"
        + (f", {_plural(len(pending), 'migration')} pending (row-bot-org migrate).\n" if pending else ".\n")
    )
    for warning in warnings:
        sys.stderr.write(f"Warning: {warning}. {ROLE_ADVICE}\n")
    return 0


def _serve(settings: OrgSettings, url: str) -> int:
    import psycopg

    from row_bot_org.db.checks import privileged_role_problems
    from row_bot_org.db.migrations import MigrationError, load_migrations, migrate, migration_status

    try:
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT) as conn:
            problems = privileged_role_problems(conn)
            pending = () if problems else migration_status(conn, load_migrations()).pending
        if problems:
            return _fail(
                f"Row-Bot Org didn't start: {'; '.join(problems)}, so row-level security wouldn't hold it back. "
                + ROLE_ADVICE
            )
        if pending and settings.auto_migrate and settings.migration_database_url:
            migrate(settings.migration_database_url)
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
    pool = create_pool(
        url,
        statement_timeout=settings.statement_timeout,
        lock_timeout=settings.lock_timeout,
        idle_in_transaction_timeout=settings.idle_in_transaction_timeout,
    )
    # Not uvicorn's access log: its lines carry query strings, and sign-in callbacks put codes there.
    # The app logs each request itself, by route template (row_bot_org.identity.gate.AccessLog).
    uvicorn.run(
        create_app(settings, pool),
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
    as_owner = args.command in {"migrate", "principals"}
    needed = MIGRATION_DATABASE_URL_ENV if as_owner else DATABASE_URL_ENV
    url = settings.migration_database_url if as_owner else settings.database_url
    if url is None:
        return _fail(f"Row-Bot Org settings are not valid: {needed} is not set")
    if bad := _unparseable({DATABASE_URL_ENV: settings.database_url,
                            MIGRATION_DATABASE_URL_ENV: settings.migration_database_url}):
        return _fail(f"Row-Bot Org settings are not valid: {bad} is not a usable postgresql:// address")
    if args.command == "migrate":
        return _migrate(url)
    if args.command == "principals":
        return _revoke_sessions(url, args.principal_id)
    if args.command == "check-config":
        return _check_config(settings, url)
    return _serve(settings, url)


if __name__ == "__main__":
    raise SystemExit(main())
