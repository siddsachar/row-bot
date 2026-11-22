"""The Org server's settings, read once at start from ``ROW_BOT_ORG_*`` variables.

A host can hand any setting as a file of the same name in ``ROW_BOT_SECRETS_DIR`` instead (Compose
and Kubernetes secrets), by the rules the app uses for its server secrets: a regular file, not a
link, at most 64 KiB of UTF-8, with one trailing newline dropped. A variable wins over a file.

Errors name the setting and what is wrong with it, never its value: some settings are secrets.
``ROW_BOT_ORG_DATABASE_URL``, ``ROW_BOT_ORG_MIGRATION_DATABASE_URL``, ``ROW_BOT_ORG_HMAC_PEPPER`` and
``ROW_BOT_ORG_IDP_CLIENT_SECRET`` are: ``remove_secret_variables`` takes them out of the process
environment once read, so no program the server starts inherits them, and they never appear in the
settings' ``repr``.

Sign-in (``row_bot_org.identity``) needs ``ROW_BOT_ORG_IDP`` (``identity/config.py`` describes it),
its client secret, ``ROW_BOT_ORG_PUBLIC_URL`` (the address browsers use, for the redirect URI and the
same-origin check) and ``ROW_BOT_ORG_HMAC_PEPPER`` (the key of every stored verifier). Without
``ROW_BOT_ORG_IDP`` nobody can sign in, and every route but health and readiness is refused.
"""

from __future__ import annotations

import ipaddress
import os
import stat
import sys
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from row_bot_org.identity.config import IdpConfig, IdpConfigError, is_loopback_host, parse_idp

ENV_PREFIX = "ROW_BOT_ORG_"
SECRETS_DIR_ENV = "ROW_BOT_SECRETS_DIR"
MAX_SECRET_FILE_BYTES = 64 * 1024
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DATABASE_URL_ENV = ENV_PREFIX + "DATABASE_URL"
MIGRATION_DATABASE_URL_ENV = ENV_PREFIX + "MIGRATION_DATABASE_URL"
HMAC_PEPPER_ENV = ENV_PREFIX + "HMAC_PEPPER"
IDP_ENV = ENV_PREFIX + "IDP"
IDP_CLIENT_SECRET_ENV = ENV_PREFIX + "IDP_CLIENT_SECRET"
PUBLIC_URL_ENV = ENV_PREFIX + "PUBLIC_URL"
# Taken out of the environment as soon as they are read (remove_secret_variables).
SECRET_VARIABLES = (DATABASE_URL_ENV, MIGRATION_DATABASE_URL_ENV, HMAC_PEPPER_ENV, IDP_CLIENT_SECRET_ENV)
MIN_PEPPER_LENGTH = 32
_PR_SET_DUMPABLE = 4


class SettingsError(ValueError):
    """The settings can't be used; ``problems`` lists each one, without values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Row-Bot Org settings are not valid: " + "; ".join(problems))


@dataclass(frozen=True)
class OrgSettings:
    host: str = "127.0.0.1"
    port: int = 8100
    log_level: str = "INFO"
    # Publishes /openapi.json. There is never an interactive docs page: it would load scripts from a CDN.
    openapi: bool = False
    # The runtime role's connection (a secret): what `serve` and `check-config` use.
    database_url: str | None = dataclass_field(default=None, repr=False)
    # The migrating role's connection (a secret): what `migrate`, and `serve` with auto-migrate, use.
    migration_database_url: str | None = dataclass_field(default=None, repr=False)
    # `serve` applies pending migrations itself (with the migration URL) instead of refusing to start.
    auto_migrate: bool = False
    # Limits on every pooled connection, in seconds.
    statement_timeout: int = 30
    lock_timeout: int = 10
    idle_in_transaction_timeout: int = 60
    # Sign-in. The origin browsers reach the server at (https://host[:port]; http only on loopback).
    public_url: str | None = None
    # The key of every stored verifier: HMAC-SHA256(pepper, secret). A secret.
    hmac_pepper: str | None = dataclass_field(default=None, repr=False)
    idp: IdpConfig | None = None
    # A server session ends this many seconds after sign-in, or after this many without a request.
    session_lifetime: int = 12 * 3600
    session_idle_timeout: int = 3600
    # Requests to /auth/login and /auth/callback a minute from one client address (an IPv6 address's /64).
    sign_in_rate_limit: int = 60
    # Reverse proxies (addresses or networks) whose X-Forwarded-For and X-Forwarded-Proto are believed.
    trusted_proxies: tuple[str, ...] = ()


class _Invalid(Exception):
    """A setting's problem, worded so it never carries the value."""


def _host(raw: str) -> str:
    value = raw.strip()
    if value == "localhost":
        return value
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise _Invalid("must be an IP address or localhost") from None


def _port(raw: str) -> int:
    value = raw.strip()
    if not (len(value) <= 5 and value.isascii() and value.isdigit() and 1 <= int(value) <= 65535):
        raise _Invalid("must be a port number from 1 to 65535")
    return int(value)


def _log_level(raw: str) -> str:
    value = raw.strip().upper()
    if value not in LOG_LEVELS:
        raise _Invalid("must be one of " + ", ".join(LOG_LEVELS))
    return value


def _flag(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true"}:
        return True
    if value in {"0", "false"}:
        return False
    raise _Invalid("must be 1, true, 0 or false")


def _database_url(raw: str) -> str:
    value = raw.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _Invalid("must not contain control characters")
    scheme, separator, rest = value.partition("://")
    if not (separator and scheme.lower() in {"postgresql", "postgres"} and rest):
        raise _Invalid("must be a postgresql:// address")
    return value


def _seconds(raw: str) -> int:
    value = raw.strip()
    if not (len(value) <= 5 and value.isascii() and value.isdigit() and 1 <= int(value) <= 86400):
        raise _Invalid("must be a number of seconds from 1 to 86400")
    return int(value)


def _session_seconds(raw: str) -> int:
    value = raw.strip()
    if not (len(value) <= 7 and value.isascii() and value.isdigit() and 60 <= int(value) <= 30 * 86400):
        raise _Invalid("must be a number of seconds from 60 to 2592000 (30 days)")
    return int(value)


def _public_url(raw: str) -> str:
    value = raw.strip()
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise _Invalid("must be an https:// origin") from None
    if (parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username or parts.password
            or not parts.hostname or any(ord(c) < 33 or ord(c) == 127 for c in value)):
        raise _Invalid("must be an https:// origin, with no path, query or credentials")
    if not (parts.scheme == "https" or (parts.scheme == "http" and (is_loopback_host(parts.hostname)
                                                                     or parts.hostname == "localhost"))):
        raise _Invalid("must be an https:// origin (http only on a loopback address)")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    default = {"https": 443, "http": 80}[parts.scheme]
    return f"{parts.scheme}://{host.lower()}" + (f":{port}" if port not in (None, default) else "")


def _pepper(raw: str) -> str:
    if len(raw) < MIN_PEPPER_LENGTH or len(raw) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise _Invalid(f"must be {MIN_PEPPER_LENGTH} to 1024 printable characters (a random secret)")
    return raw


def _json_text(raw: str) -> str:
    return raw.strip()


def _client_secret(raw: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise _Invalid("must not contain control characters")
    return raw


def _rate_limit(raw: str) -> int:
    value = raw.strip()
    if not (len(value) <= 5 and value.isascii() and value.isdigit() and 1 <= int(value) <= 10000):
        raise _Invalid("must be a number of requests a minute from 1 to 10000")
    return int(value)


def _trusted_proxies(raw: str) -> tuple[str, ...]:
    try:
        networks = [ipaddress.ip_network(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError:
        raise _Invalid("must be a comma-separated list of IP addresses or networks (10.0.0.0/8)") from None
    return tuple(dict.fromkeys(str(network) for network in networks))


_FIELDS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "HOST": ("host", _host),
    "PORT": ("port", _port),
    "LOG_LEVEL": ("log_level", _log_level),
    "OPENAPI": ("openapi", _flag),
    "DATABASE_URL": ("database_url", _database_url),
    "MIGRATION_DATABASE_URL": ("migration_database_url", _database_url),
    "AUTO_MIGRATE": ("auto_migrate", _flag),
    "STATEMENT_TIMEOUT": ("statement_timeout", _seconds),
    "LOCK_TIMEOUT": ("lock_timeout", _seconds),
    "IDLE_IN_TRANSACTION_TIMEOUT": ("idle_in_transaction_timeout", _seconds),
    "PUBLIC_URL": ("public_url", _public_url),
    "HMAC_PEPPER": ("hmac_pepper", _pepper),
    "SESSION_LIFETIME": ("session_lifetime", _session_seconds),
    "SESSION_IDLE_TIMEOUT": ("session_idle_timeout", _session_seconds),
    "SIGN_IN_RATE_LIMIT": ("sign_in_rate_limit", _rate_limit),
    "TRUSTED_PROXIES": ("trusted_proxies", _trusted_proxies),
    # Combined after the loop: the provider needs its client secret.
    "IDP": ("idp", _json_text),
    "IDP_CLIENT_SECRET": ("idp_client_secret", _client_secret),
}


def _secret_file(name: str, env: Mapping[str, str]) -> str | None:
    configured = (env.get(SECRETS_DIR_ENV) or "").strip()
    if not configured:
        return None
    try:
        base = Path(configured).resolve(strict=True)
    except OSError:
        return None
    candidate = base / name
    try:
        if candidate.is_symlink():
            raise _Invalid("in the secrets folder must not be a link")
        info = candidate.stat()
    except FileNotFoundError:
        return None
    except OSError:
        raise _Invalid("in the secrets folder can't be read") from None
    if not stat.S_ISREG(info.st_mode):
        raise _Invalid("in the secrets folder must be a regular file")
    if info.st_size > MAX_SECRET_FILE_BYTES:
        raise _Invalid("in the secrets folder is larger than 64 KiB")
    try:
        raw = candidate.read_bytes()
    except OSError:
        raise _Invalid("in the secrets folder can't be read") from None
    if len(raw) > MAX_SECRET_FILE_BYTES:
        raise _Invalid("in the secrets folder is larger than 64 KiB")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _Invalid("in the secrets folder is not UTF-8") from None
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    return value or None


def load_settings(environ: Mapping[str, str] | None = None) -> OrgSettings:
    """The settings from ``environ`` (the process environment by default); ``SettingsError`` if any is bad."""
    env = os.environ if environ is None else environ
    problems = [
        f"{name} is not a Row-Bot Org setting"
        for name in sorted(env)
        if name.startswith(ENV_PREFIX) and name[len(ENV_PREFIX):] not in _FIELDS
    ]
    values: dict[str, Any] = {}
    for suffix, (field, parse) in _FIELDS.items():
        name = ENV_PREFIX + suffix
        try:
            raw = env.get(name)
            if raw is None or not raw.strip():
                raw = _secret_file(name, env)
            if raw is not None:
                values[field] = parse(raw)
        except _Invalid as problem:
            problems.append(f"{name} {problem}")
    if values.get("auto_migrate") and not values.get("migration_database_url"):
        problems.append(f"{ENV_PREFIX}AUTO_MIGRATE needs {MIGRATION_DATABASE_URL_ENV}")
    client_secret = values.pop("idp_client_secret", None)
    if "idp" in values:
        try:
            values["idp"] = parse_idp(values["idp"], client_secret or "")
        except IdpConfigError as problem:
            problems.append(f"{IDP_ENV} {problem}")
        for name, given in ((IDP_CLIENT_SECRET_ENV, client_secret), (PUBLIC_URL_ENV, values.get("public_url")),
                            (HMAC_PEPPER_ENV, values.get("hmac_pepper"))):
            if given is None and not any(problem.startswith(f"{name} ") for problem in problems):
                problems.append(f"{IDP_ENV} needs {name}")
    elif client_secret is not None:
        problems.append(f"{IDP_CLIENT_SECRET_ENV} needs {IDP_ENV}")
    if values.get("session_idle_timeout", 0) > values.get("session_lifetime", OrgSettings.session_lifetime):
        problems.append(f"{ENV_PREFIX}SESSION_IDLE_TIMEOUT must not be longer than {ENV_PREFIX}SESSION_LIFETIME")
    if problems:
        raise SettingsError(problems)
    return OrgSettings(**values)


def remove_secret_variables(environ: MutableMapping[str, str] | None = None) -> None:
    """Take the secret settings out of ``environ`` (the process environment by default).

    Read the settings from a copy first. Shells, workers and every other child get a copy of the
    environment; a secret still in it would reach them all.
    """
    env = os.environ if environ is None else environ
    for name in SECRET_VARIABLES:
        env.pop(name, None)


def keep_environment_private() -> None:
    """On Linux, show this process's starting environment to root alone.

    ``remove_secret_variables`` keeps a secret from the programs the server starts, but the kernel
    still shows the environment a process started with at ``/proc/<pid>/environ`` to every program
    of the same user. A process that isn't dumpable shows it (and its memory) to root alone.
    Elsewhere this does nothing. (As ``row_bot.environment_secrets``; the server never imports the app.)
    """
    if not sys.platform.startswith("linux"):
        return
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    if library.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Row-Bot Org couldn't keep its environment private")


__all__ = [
    "DATABASE_URL_ENV", "ENV_PREFIX", "HMAC_PEPPER_ENV", "IDP_CLIENT_SECRET_ENV", "IDP_ENV",
    "MIGRATION_DATABASE_URL_ENV", "PUBLIC_URL_ENV", "SECRETS_DIR_ENV", "SECRET_VARIABLES", "OrgSettings",
    "SettingsError", "keep_environment_private", "load_settings", "remove_secret_variables",
]
