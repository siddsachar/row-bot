"""``row-bot-org``: ``serve`` starts the Org server, ``check-config`` checks its settings."""

from __future__ import annotations

import argparse
import os
import sys

from row_bot_org.settings import ENV_PREFIX, SettingsError, load_settings

EXIT_INVALID_SETTINGS = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="row-bot-org", description="The Row-Bot Org server (pre-release).")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the server (on 127.0.0.1:8100 unless set otherwise)")
    serve.add_argument("--host", help=f"address to listen on (or {ENV_PREFIX}HOST)")
    serve.add_argument("--port", help=f"port to listen on (or {ENV_PREFIX}PORT)")
    commands.add_parser("check-config", help="check the settings and exit: 0 when they are valid, 1 when not")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environ = dict(os.environ)
    if args.command == "serve":
        # Options go through the same checks as the variables they stand for.
        for option, suffix in ((args.host, "HOST"), (args.port, "PORT")):
            if option is not None:
                environ[ENV_PREFIX + suffix] = option
    try:
        settings = load_settings(environ)
    except SettingsError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_INVALID_SETTINGS
    if args.command == "check-config":
        sys.stdout.write(
            f"Row-Bot Org settings are valid: host {settings.host}, port {settings.port}, "
            f"log level {settings.log_level}, OpenAPI {'on' if settings.openapi else 'off'}.\n"
        )
        return 0

    import uvicorn

    from row_bot_org.app import create_app
    from row_bot_org.logging import configure_logging

    configure_logging(settings.log_level)
    # No access log: request lines carry query strings, and sign-in callbacks will put codes there.
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
