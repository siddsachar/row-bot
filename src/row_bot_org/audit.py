"""The audit hook: ``emit(conn, action, actor=..., details=...)``.

**Until S5 wires it to the audit store**, an event is one ``row_bot_org.audit`` log line (JSON, with the
action, the actor and the details). ``conn`` is the transaction the change was made in, so S5 can store
the event with the change, or neither.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import psycopg

logger = logging.getLogger("row_bot_org.audit")


def emit(conn: psycopg.Connection, action: str, *, actor: str, details: Mapping[str, object]) -> None:
    """Record that ``actor`` (a principal id) did ``action`` (such as ``policy.changed``)."""
    logger.info(action, extra={"fields": {"action": action, "actor": actor, **details}})


__all__ = ["emit"]
