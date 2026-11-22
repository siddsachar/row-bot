"""The Org server's settings, read once at start from ``ROW_BOT_ORG_*`` variables.

A host can hand any setting as a file of the same name in ``ROW_BOT_SECRETS_DIR`` instead (Compose
and Kubernetes secrets), by the rules the app uses for its server secrets: a regular file, not a
link, at most 64 KiB of UTF-8, with one trailing newline dropped. A variable wins over a file.

Errors name the setting and what is wrong with it, never its value: settings that later WPs add
(the database address, the sign-in client secret) are secrets.
"""

from __future__ import annotations

import ipaddress
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_PREFIX = "ROW_BOT_ORG_"
SECRETS_DIR_ENV = "ROW_BOT_SECRETS_DIR"
MAX_SECRET_FILE_BYTES = 64 * 1024
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


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


_FIELDS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "HOST": ("host", _host),
    "PORT": ("port", _port),
    "LOG_LEVEL": ("log_level", _log_level),
    "OPENAPI": ("openapi", _flag),
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
    if problems:
        raise SettingsError(problems)
    return OrgSettings(**values)


__all__ = ["ENV_PREFIX", "SECRETS_DIR_ENV", "OrgSettings", "SettingsError", "load_settings"]
