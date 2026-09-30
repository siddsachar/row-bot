"""Bounded public navigation over the retained agent-run owner."""
from __future__ import annotations

import logging
from contextlib import closing
from typing import Any

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application.conversation_search import _cursor, _decode

_LOG = logging.getLogger(__name__)


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
        # Its icon follows the profile it runs as (B240).
        "profile_id": str(run.get("profile_id") or "")[:256],
    }


def _title(service: Any, conversation_id: str) -> str | None:
    """The conversation's title while it is open to navigation, else None."""
    if not _available(service, conversation_id):
        return None
    return str(service._metadata(conversation_id).get("name") or "")[:256]


def read_run(service: Any, conversation_id: str, run_id: str) -> dict:
    from row_bot import agent_runs
    _require(service, conversation_id)
    run = agent_runs.get_agent_run(run_id)
    if not run or run.get("kind") != "subagent" or run.get("parent_thread_id") != conversation_id:
        raise ClientPlatformError("not_found")
    result = _public(service, run)
    _require(service, conversation_id)
    return result


def _controlled_run(service: Any, conversation_id: str, run_id: str) -> dict:
    """A delegated run seen from its parent conversation or its own thread."""
    from row_bot import agent_runs
    _require(service, conversation_id)
    run = agent_runs.get_agent_run(run_id)
    if (not run or run.get("kind") != "subagent"
            or conversation_id not in {run.get("parent_thread_id"), run.get("thread_id")}):
        raise ClientPlatformError("not_found")
    return run


def stop_run(service: Any, conversation_id: str, run_id: str) -> dict:
    """Stop a delegated agent (Agents › Stop, or Stop inside its thread)."""
    from row_bot import agent_orchestrator, agent_runner, agent_runs
    run = _controlled_run(service, conversation_id, run_id)
    if str(run.get("status") or "") not in agent_runs.TERMINAL_STATUSES:
        member = agent_orchestrator.get_member_for_run(run_id)
        if member and member.get("orchestration_id"):
            agent_orchestrator.stop_orchestration(str(member["orchestration_id"]), run_id=run_id)
        else:
            agent_runner.stop_agent_run(run_id)
    return _public(service, agent_runs.get_agent_run(run_id) or run)


def resume_work(service: Any, conversation_id: str) -> None:
    """Resume the conversation's interrupted agent work (a model call; B220)."""
    from row_bot import agent_orchestrator
    _require(service, conversation_id)
    if service.registry.active(conversation_id):
        raise ClientPlatformError("generation_active")
    activity = agent_orchestrator.get_thread_orchestration_activity([conversation_id]).get(conversation_id) or {}
    if activity.get("phase") != "resume_required":
        raise ClientPlatformError("agent_work_not_resumable")
    try:
        agent_orchestrator.resume_orchestration(str(activity["orchestration_id"]))
    except agent_orchestrator.OrchestrationError as exc:
        _LOG.info("Agent work in %s did not resume: %s", conversation_id, exc)
        raise ClientPlatformError("agent_resume_unavailable") from exc


def dismiss_work(service: Any, conversation_id: str) -> None:
    """Close the conversation's interrupted agent work without running it."""
    from row_bot import agent_orchestrator
    _require(service, conversation_id)
    agent_orchestrator.dismiss_orchestrations(conversation_id)


def message_run(service: Any, conversation_id: str, run_id: str, text: str, message_id: str) -> dict:
    """Queue a message for a delegated agent; it reads it at its next step."""
    from row_bot import agent_orchestrator, agent_runs
    run = _controlled_run(service, conversation_id, run_id)
    if str(run.get("status") or "") in agent_runs.TERMINAL_STATUSES:
        raise ClientPlatformError("agent_run_finished")
    content = str(text or "").strip()
    if not content:
        raise ClientPlatformError("invalid_command")
    member = agent_orchestrator.get_member_for_run(run_id)
    if member and member.get("orchestration_id"):
        agent_orchestrator.message_orchestration(str(member["orchestration_id"]), content, run_id=run_id)
    else:
        agent_runs.append_agent_parent_message(run_id, content, message_id=message_id)
    return _public(service, agent_runs.get_agent_run(run_id) or run)


def start_run(service: Any, conversation_id: str, text: str) -> dict:
    """Start a delegated agent from ``/agent [profile] <task>`` in the composer."""
    from row_bot.agent_commands import parse_agent_spawn_text, spawn_agent_from_request
    from row_bot.tools import registry as tool_registry
    _require(service, conversation_id)
    if service.registry.active(conversation_id):
        raise ClientPlatformError("generation_active")
    request = parse_agent_spawn_text("/agent " + str(text or "").strip())
    if request is None:
        raise ClientPlatformError("invalid_command")
    try:
        run = spawn_agent_from_request(
            conversation_id, request,
            enabled_tool_names=[tool.name for tool in tool_registry.get_enabled_tools()],
        )
    except ValueError as exc:
        raise ClientPlatformError("invalid_command") from exc
    return _public(service, run)


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
            "SELECT id,parent_thread_id,thread_id,display_name,status,substr(summary,1,4096) AS summary,profile_id "
            "FROM agent_runs WHERE parent_thread_id=? AND kind='subagent' AND id>? ORDER BY id LIMIT 51",
            (conversation_id, after),
        ).fetchall()
    own_run = agent_runs.get_agent_run_for_thread(conversation_id)
    parent = str((own_run or {}).get("parent_thread_id") or "")
    items = [_public(service, dict(row)) for row in rows[:50]]
    _require(service, conversation_id)
    own = own_run if own_run and own_run.get("kind") == "subagent" and parent else None
    parent_title = _title(service, parent)
    return {
        "conversation_id": conversation_id,
        "parent_conversation_id": parent if parent_title is not None else None,
        # The child's header links back to it by name (B242).
        "parent_title": parent_title,
        # A child thread's own run, so its Stop and Message sit with it.
        "own_run": _public(service, own) if own else None,
        "items": items,
        "next_cursor": _cursor([conversation_id, items[-1]["run_id"]]) if len(rows) > 50 else None,
        "has_more": len(rows) > 50,
    }
