"""The server's connection pool, and logs that never show where it connects.

psycopg and its pool log connection failures and connections with their details (host, port, user,
database). Those are parts of the database URL, a secret, so their records keep only the type of
each argument other than numbers and the pool's own name.
"""

from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

POOL_NAME = "row_bot_org"
_PSYCOPG_LOGGERS = ("psycopg", "psycopg.pool")


def _shown(value: object) -> object:
    if isinstance(value, (int, float)) or value == POOL_NAME:
        return value
    return type(value).__name__


class _ConnectionDetailsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else (record.args,) if record.args else ()
        try:
            record.msg = str(record.msg) % tuple(_shown(arg) for arg in args) if args else str(record.msg)
        except (TypeError, ValueError):
            record.msg = f"{record.name} logged a record without its details"
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.msg += f" ({type(record.exc_info[1]).__name__})"
        record.exc_info = None
        record.exc_text = None
        return True


_FILTER = _ConnectionDetailsFilter()
for _name in _PSYCOPG_LOGGERS:
    logging.getLogger(_name).addFilter(_FILTER)


def create_pool(conninfo: str, *, max_size: int = 10) -> ConnectionPool:
    """A closed pool for ``conninfo`` (the app opens it at start). Its connections are autocommit:
    every transaction is explicit, and transaction-local settings end with it."""
    return ConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        open=False,
        name=POOL_NAME,
        kwargs={"autocommit": True},
    )


__all__ = ["POOL_NAME", "create_pool"]
