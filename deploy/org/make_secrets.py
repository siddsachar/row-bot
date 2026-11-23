"""Write the Compose deployment's secret files, with new random values (standard library only).

    python deploy/org/make_secrets.py              # into deploy/org/secrets/
    python deploy/org/make_secrets.py --dir PATH   # somewhere else (compose.yaml: ROW_BOT_ORG_SECRETS_DIR)

It writes every file or none: if any of them is already there it stops before writing anything, because
the folder in use holds the passwords the database was made with. Each database address is built from the
password written beside it, so the two always agree. Nothing it writes is printed.

The folder is made yours alone (0700) where the operating system has such permissions. The files in it are
readable (0644) because the containers read them through their mounts as their own users (the database as
uid 999, the server as 10001), which aren't you; only the folder keeps the other people on this machine out.

``ROW_BOT_ORG_IDP_CLIENT_SECRET`` is written empty: paste your identity provider's client secret into it.
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

DATABASE = "row_bot_org"
OWNER = "row_bot_org_owner"
APP = "row_bot_org_app"
# The Compose service the server and migrate reach the database at.
HOST = "postgres"
PORT = 5432
IDP_CLIENT_SECRET = "ROW_BOT_ORG_IDP_CLIENT_SECRET"
DEFAULT_FOLDER = Path(__file__).resolve().parent / "secrets"


def _password() -> str:
    # 256 bits in URL-safe characters, so an address carries it without escaping.
    return secrets.token_urlsafe(32)


def _address(role: str, password: str) -> str:
    return f"postgresql://{role}:{password}@{HOST}:{PORT}/{DATABASE}"


def secret_values() -> dict[str, str]:
    """Every secret file's name and new value."""
    owner, app = _password(), _password()
    return {
        "postgres_password": _password(),
        "owner_password": owner,
        "app_password": app,
        "ROW_BOT_ORG_MIGRATION_DATABASE_URL": _address(OWNER, owner),
        "ROW_BOT_ORG_DATABASE_URL": _address(APP, app),
        "ROW_BOT_ORG_HMAC_PEPPER": secrets.token_urlsafe(48),
        "ROW_BOT_ORG_GATEWAY_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        IDP_CLIENT_SECRET: "",
    }


def write_secrets(folder: Path) -> list[Path]:
    """Write every secret file into ``folder``; ``FileExistsError`` (and nothing written) if any is there."""
    values = secret_values()
    present = sorted(name for name in values if (folder / name).exists())
    if present:
        raise FileExistsError(", ".join(present))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        folder.chmod(0o700)
    except OSError:
        pass  # a folder someone else owns keeps its permissions; the files are still new
    written = []
    for name, value in values.items():
        path = folder / name
        # O_EXCL: never over a file that appeared meanwhile.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        with os.fdopen(descriptor, "wb") as file:
            file.write(value.encode("utf-8"))
        path.chmod(0o644)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the Row-Bot Org Compose deployment's secret files.")
    parser.add_argument("--dir", type=Path, default=DEFAULT_FOLDER,
                        help="the folder to write them into (default: deploy/org/secrets)")
    folder = parser.parse_args(argv).dir
    try:
        written = write_secrets(folder)
    except FileExistsError as error:
        sys.stderr.write(f"{folder} already has {error}: nothing was written. These files hold the passwords "
                         "the database was made with; keep them, or choose another folder with --dir.\n")
        return 1
    sys.stdout.write(f"Wrote {len(written)} files in {folder}. Paste your identity provider's client secret "
                     f"into {folder / IDP_CLIENT_SECRET}, and back the folder up somewhere safe.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
