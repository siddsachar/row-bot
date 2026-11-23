"""Reading the Org server's audit store in tests, as the database owner (who may read every link)."""

from __future__ import annotations

from typing import Any


def stored_events(owner_url: str, *actions: str) -> list[dict[str, Any]]:
    """The stored events (their JSON), oldest first, only of ``actions`` when any are named."""
    import psycopg

    with psycopg.connect(owner_url) as conn:
        rows = conn.execute("SELECT event FROM row_bot_org.audit_events ORDER BY seq").fetchall()
    return [event for (event,) in rows if not actions or event["action"] in actions]


def summary(event: dict[str, Any]) -> tuple:
    """An event as (action, actor kind, actor id, target ref or None, outcome, details): what tests compare."""
    target = event["target"]
    return (event["action"], event["actor"]["kind"], event["actor"]["id"], target and target["ref"],
            event["outcome"], event["details"])
