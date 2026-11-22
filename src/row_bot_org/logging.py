"""JSON lines on stdout, one per record: what a container's log driver collects. No log files.

psycopg's records (and psycopg's exceptions anywhere) can name the database's host, port, user and
name, all parts of the database URL, which is a secret. Those keep only their logger, level and the
exception's type: no message, no arguments, no traceback.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TextIO

_DATABASE_LOGGERS = ("psycopg", "psycopg_pool")
WITHHELD = "details withheld: they can name the database"


def _from_database_library(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _DATABASE_LOGGERS)


def _involves_database_library(error: BaseException | None) -> bool:
    """Whether ``error`` or anything in its chain (cause, context) is one of psycopg's exceptions."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if _from_database_library(type(error).__module__):
            return True
        error = error.__cause__ or error.__context__
    return False


class JsonLineFormatter(logging.Formatter):
    """One JSON object per record: time (UTC), level, logger, message and any exception."""

    def format(self, record: logging.LogRecord) -> str:
        withheld = _from_database_library(record.name)
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": WITHHELD if withheld else record.getMessage(),
        }
        # Structured fields a record carries (the access log's), never in place of the four above.
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and not withheld:
            entry.update({key: value for key, value in fields.items() if key not in entry and key != "exc"})
        if record.exc_info and record.exc_info[0] is not None:
            error_type = record.exc_info[0]
            if withheld or _involves_database_library(record.exc_info[1]):
                entry["exc"] = error_type.__name__
            else:
                entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Send every record at ``level`` and above to ``stream`` (stdout) as JSON lines.

    uvicorn's records reach it too, as long as uvicorn is started with ``log_config=None``.
    """
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(JsonLineFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs each call's full URL at INFO; the server's own access log is the one request log.
    logging.getLogger("httpx").setLevel(max(logging.WARNING, root.level))


__all__ = ["WITHHELD", "JsonLineFormatter", "configure_logging"]
