"""Bounded public navigation over the retained agent-run owner."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application.conversation_search import _cursor, _decode


def _available(service: Any, conversation_id: str) -> bool:
    from row_bot.runtime import admissions
    if not conversation_id or admissions.deletion_state(conversation_id) != "active":
        return False
    try:
        service._metadata(conversation_id)
    except ClientPlatformError:
        return False
    return True


def _require(service: Any, conversation_id: str) -> None:
    if not _available(service, conversation_id):
        raise ClientPlatformError("not_found")


def _public(service: Any, run: dict) -> dict:
    child = str(run.get("thread_id") or "")
    return {
        "run_id": str(run["id"]),
        "parent_conversation_id": str(run["parent_thread_id"]),
        "child_conversation_id": child if _available(service, child) else None,
        "name": str(run.get("display_name") or "Delegated task")[:256],
        "status": str(run.get("status") or "unknown")[:64],
        "summary": str(run.get("summary") or "")[:4096],
    }


def read_run(service: Any, conversation_id: str, run_id: str) -> dict:
    from row_bot import agent_runs
    _require(service, conversation_id)
    run = agent_runs.get_agent_run(run_id)
    if not run or run.get("kind") != "subagent" or run.get("parent_thread_id") != conversation_id:
        raise ClientPlatformError("not_found")
    result = _public(service, run)
    _require(service, conversation_id)
    return result


def read_activity(service: Any, conversation_id: str, *, cursor: str | None = None) -> dict:
    from row_bot import agent_runs
    _require(service, conversation_id)
    after = ""
    if cursor:
        values = _decode(cursor)
        if len(values) != 2 or values[0] != conversation_id or not isinstance(values[1], str):
            raise ClientPlatformError("cursor_expired")
        after = values[1]
    agent_runs.ensure_agent_run_schema()
    # Read only public columns from the existing owner, with a stable keyset.
    with closing(agent_runs._get_conn()) as conn:
        rows = conn.execute(
            "SELECT id,parent_thread_id,thread_id,display_name,status,substr(summary,1,4096) AS summary "
            "FROM agent_runs WHERE parent_thread_id=? AND kind='subagent' AND id>? ORDER BY id LIMIT 51",
            (conversation_id, after),
        ).fetchall()
    own_run = agent_runs.get_agent_run_for_thread(conversation_id)
    parent = str((own_run or {}).get("parent_thread_id") or "")
    items = [_public(service, dict(row)) for row in rows[:50]]
    _require(service, conversation_id)
    return {
        "conversation_id": conversation_id,
        "parent_conversation_id": parent if _available(service, parent) else None,
        "items": items,
        "next_cursor": _cursor([conversation_id, items[-1]["run_id"]]) if len(rows) > 50 else None,
        "has_more": len(rows) > 50,
    }
