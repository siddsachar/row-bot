"""Background runner for chat-spawned child Agents."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Mapping, Sequence

from row_bot.approval_policy import DEFAULT_APPROVAL_MODE, normalize_approval_mode

logger = logging.getLogger(__name__)


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_AGENT_RUNS: dict[str, dict[str, Any]] = {}

_READ_ONLY_DEFAULT_DENY_TOOLS = {
    "calendar",
    "custom_tool_builder",
    "designer",
    "gmail",
    "goal",
    "image_gen",
    "row_bot_updater",
    "task",
    "tracker",
    "video_gen",
    "x",
}


class AgentRunnerError(ValueError):
    """Raised when a child Agent cannot be created or started."""


def _short_title(text: str, *, limit: int = 64) -> str:
    title = " ".join(str(text or "").strip().split())
    if not title:
        return "Agent"
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "..."


def _enabled_tool_names(enabled_tool_names: Sequence[str] | None) -> list[str]:
    if enabled_tool_names is not None:
        return [str(name) for name in enabled_tool_names if str(name or "").strip()]
    from row_bot.tools import registry as tool_registry

    return [tool.name for tool in tool_registry.get_enabled_tools()]


def _profile_tool_allowlist(profile_snapshot: Mapping[str, Any]) -> list[str]:
    tool_policy = profile_snapshot.get("tool_policy_json") or {}
    if not isinstance(tool_policy, dict):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in tool_policy.get("allow_tools") or []:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _filter_child_tools(
    enabled_tool_names: Sequence[str],
    profile_snapshot: Mapping[str, Any],
) -> list[str]:
    """Apply profile tool restrictions and deny recursive delegation by default."""
    tool_policy = profile_snapshot.get("tool_policy_json") or {}
    if not isinstance(tool_policy, dict):
        tool_policy = {}
    requested = [str(name) for name in enabled_tool_names if str(name or "").strip()]
    allow = {str(name) for name in tool_policy.get("allow_tools") or [] if str(name or "").strip()}
    deny: set[str] = set()
    capability = str(tool_policy.get("capability") or "read_only")
    if capability == "read_only" and not allow:
        deny.update(_READ_ONLY_DEFAULT_DENY_TOOLS)
    if not tool_policy.get("allow_delegation"):
        deny.add("agents")
    filtered = [name for name in requested if name not in deny]
    if allow:
        mcp_allowed = "mcp" in allow or any(name.startswith("mcp_") for name in allow)
        filtered = [
            name
            for name in filtered
            if name in allow or (name == "mcp" and mcp_allowed)
        ]
    return filtered


def _profile_child_skills(
    _parent_skills_override: Sequence[str] | None,
    profile_snapshot: Mapping[str, Any],
) -> list[str]:
    skill_policy = profile_snapshot.get("skill_policy_json") or {}
    if not isinstance(skill_policy, dict):
        skill_policy = {}
    base = [
        str(name)
        for name in skill_policy.get("skills_override") or []
        if str(name or "").strip()
    ]
    deny = {
        str(name)
        for name in skill_policy.get("deny_skills") or []
        if str(name or "").strip()
    }
    return [name for name in base if name not in deny]


def _profile_requires_write_lock(profile_snapshot: Mapping[str, Any]) -> bool:
    tool_policy = profile_snapshot.get("tool_policy_json") or {}
    workspace_policy = profile_snapshot.get("workspace_policy_json") or {}
    if not isinstance(tool_policy, dict):
        tool_policy = {}
    if not isinstance(workspace_policy, dict):
        workspace_policy = {}
    capability = str(tool_policy.get("capability") or "read_only")
    return capability in {"write_capable", "orchestrator"} or bool(
        workspace_policy.get("write_lock_required")
    )


def _profile_workspace_mode(profile_snapshot: Mapping[str, Any]) -> str:
    workspace_policy = profile_snapshot.get("workspace_policy_json") or {}
    if not isinstance(workspace_policy, dict):
        return "auto"
    return str(workspace_policy.get("workspace_mode_default") or "auto")


def _writer_lock_key(
    *,
    developer_workspace_id: str = "",
    parent_thread_id: str = "",
    child_thread_id: str = "",
) -> str:
    if developer_workspace_id:
        return f"developer:{developer_workspace_id}"
    if parent_thread_id:
        return f"thread:{parent_thread_id}"
    return f"thread:{child_thread_id or 'default'}"


def _parent_thread_defaults(parent_thread_id: str) -> dict[str, Any]:
    if not parent_thread_id:
        return {
            "approval_mode": DEFAULT_APPROVAL_MODE,
            "model_override": "",
            "developer_workspace_id": "",
            "skills_override": None,
        }
    from row_bot.threads import (
        _get_thread_approval_mode,
        _get_thread_developer_workspace,
        _get_thread_model_override,
        get_thread_skills_override,
    )

    return {
        "approval_mode": _get_thread_approval_mode(parent_thread_id),
        "model_override": _get_thread_model_override(parent_thread_id),
        "developer_workspace_id": _get_thread_developer_workspace(parent_thread_id),
        "skills_override": get_thread_skills_override(parent_thread_id),
    }


def _invoke_agent(
    prompt: str,
    enabled_tool_names: list[str],
    config: dict[str, Any],
    *,
    stop_event: threading.Event,
) -> str | dict:
    from row_bot.agent import invoke_agent

    return invoke_agent(prompt, enabled_tool_names, config, stop_event=stop_event)


def _resume_invoke_agent(
    enabled_tool_names: list[str],
    config: dict[str, Any],
    approved: bool,
    *,
    interrupt_ids: list[str] | None = None,
    stop_event: threading.Event,
) -> str | dict:
    from row_bot.agent import resume_invoke_agent

    return resume_invoke_agent(
        enabled_tool_names,
        config,
        approved,
        interrupt_ids=interrupt_ids,
        stop_event=stop_event,
    )


def _is_task_stopped(exc: BaseException) -> bool:
    return exc.__class__.__name__ == "TaskStoppedError"


def _build_child_config(
    *,
    child_thread_id: str,
    approval_mode: str,
    model_override: str = "",
    developer_workspace_id: str = "",
    parent_thread_id: str = "",
    parent_run_id: str = "",
    profile_snapshot: Mapping[str, Any],
    tool_allowlist: Sequence[str] | None = None,
) -> dict[str, Any]:
    try:
        from row_bot.agent import RECURSION_LIMIT_TASK
    except Exception:
        RECURSION_LIMIT_TASK = 50
    configurable = {
        "thread_id": child_thread_id,
        "runtime_surface": "agent_child",
        "runtime_mode": "agent",
        "approval_mode": approval_mode,
        "agent_profile_id": str(profile_snapshot.get("id") or ""),
        "agent_profile_snapshot": dict(profile_snapshot),
        "parent_thread_id": parent_thread_id,
        "parent_run_id": parent_run_id,
    }
    if tool_allowlist:
        configurable["tool_allowlist"] = [
            str(name)
            for name in tool_allowlist
            if str(name or "").strip()
        ]
    if model_override:
        configurable["model_override"] = model_override
    if developer_workspace_id:
        configurable["developer_workspace_id"] = developer_workspace_id
    return {
        "configurable": configurable,
        "recursion_limit": RECURSION_LIMIT_TASK,
    }


def spawn_agent_run(
    objective: str,
    *,
    parent_thread_id: str = "",
    parent_run_id: str = "",
    parent_message_id: str = "",
    profile: str = "",
    agent_profile_id: str = "",
    display_name: str = "",
    context: str = "",
    context_mode: str = "",
    enabled_tool_names: Sequence[str] | None = None,
    model_override: str = "",
    approval_mode: str = "",
    wait: bool = False,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Create and start a single child Agent run."""
    objective = str(objective or "").strip()
    if not objective:
        raise AgentRunnerError("Child Agent objective cannot be empty.")

    parent_defaults = _parent_thread_defaults(parent_thread_id)
    parent_approval = normalize_approval_mode(
        approval_mode or parent_defaults["approval_mode"],
        DEFAULT_APPROVAL_MODE,
    )
    profile_ref = str(agent_profile_id or profile or "worker").strip()
    from row_bot.agent_profiles import resolve_profile_for_run

    resolved_profile = resolve_profile_for_run(
        profile_ref,
        parent_approval_mode=parent_approval,
    )
    profile_snapshot = resolved_profile["profile_snapshot"]
    effective_approval = normalize_approval_mode(
        resolved_profile["effective_approval_mode"],
        parent_approval,
    )
    model = str(model_override or parent_defaults["model_override"] or "")
    developer_workspace_id = str(parent_defaults["developer_workspace_id"] or "")

    from row_bot.agent_context import build_child_agent_prompt
    from row_bot.agent_runs import append_agent_event, create_agent_run, create_agent_run_edge
    from row_bot.threads import create_thread, set_thread_skills_override

    packet = build_child_agent_prompt(
        objective=objective,
        profile_snapshot=profile_snapshot,
        context=context,
        context_mode=context_mode,
        parent_thread_id=parent_thread_id,
        parent_run_id=parent_run_id,
    )
    run_id = uuid.uuid4().hex[:12]
    child_display = display_name or f"{profile_snapshot.get('display_name', 'Agent')}: {_short_title(objective, limit=42)}"
    child_thread_id = create_thread(
        child_display,
        thread_type="agent_child",
        developer_workspace_id=developer_workspace_id,
        approval_mode=effective_approval,
        model_override=model,
        agent_profile_id=str(profile_snapshot.get("id") or ""),
        agent_profile_slug=str(profile_snapshot.get("slug") or ""),
        seed_default_skills=False,
    )
    child_skills = _profile_child_skills(parent_defaults["skills_override"], profile_snapshot)
    set_thread_skills_override(child_thread_id, child_skills)

    tool_allowlist = _profile_tool_allowlist(profile_snapshot)
    child_tools = _filter_child_tools(
        _enabled_tool_names(enabled_tool_names),
        profile_snapshot,
    )
    requires_write_lock = _profile_requires_write_lock(profile_snapshot)
    write_lock_key = (
        _writer_lock_key(
            developer_workspace_id=developer_workspace_id,
            parent_thread_id=parent_thread_id,
            child_thread_id=child_thread_id,
        )
        if requires_write_lock
        else ""
    )
    workspace_mode = _profile_workspace_mode(profile_snapshot)
    config = _build_child_config(
        child_thread_id=child_thread_id,
        approval_mode=effective_approval,
        model_override=model,
        developer_workspace_id=developer_workspace_id,
        parent_thread_id=parent_thread_id,
        parent_run_id=parent_run_id,
        profile_snapshot=profile_snapshot,
        tool_allowlist=tool_allowlist,
    )
    if tool_allowlist:
        try:
            from row_bot.agent_tool_catalog import count_tool_ids_by_source

            logger.info(
                "child Agent tool allow-list active: profile=%s selected=%d counts=%s",
                profile_snapshot.get("slug") or profile_snapshot.get("id") or "",
                len(tool_allowlist),
                count_tool_ids_by_source(tool_allowlist),
            )
        except Exception:
            logger.info(
                "child Agent tool allow-list active: profile=%s selected=%d",
                profile_snapshot.get("slug") or profile_snapshot.get("id") or "",
                len(tool_allowlist),
            )
    run = create_agent_run(
        run_id=run_id,
        kind="subagent",
        status="queued",
        parent_run_id=parent_run_id,
        parent_thread_id=parent_thread_id,
        parent_message_id=parent_message_id,
        thread_id=child_thread_id,
        depth=1 if parent_thread_id else 0,
        profile_id=str(profile_snapshot.get("id") or ""),
        profile_snapshot_json=profile_snapshot,
        display_name=child_display,
        prompt=objective,
        context_mode=packet["mode"],
        context_summary=packet["summary"],
        model_override=model,
        tools_override=tool_allowlist if tool_allowlist else child_tools,
        skills_override=child_skills,
        approval_mode=effective_approval,
        workspace_id=developer_workspace_id,
        workspace_mode=workspace_mode,
        write_lock_key=write_lock_key,
    )
    if parent_run_id:
        create_agent_run_edge(parent_run_id, run_id, "spawned_by_tool")
    append_agent_event(
        run_id,
        "context.packed",
        {
            "mode": packet["mode"],
            "fallback": packet.get("fallback", ""),
            "message_count": packet.get("message_count", "0"),
            "estimated_tokens": packet.get("estimated_tokens", "0"),
        },
        visibility="internal",
    )

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_agent_thread,
        args=(
            run_id,
            packet["prompt"],
            child_tools,
            config,
            stop_event,
            requires_write_lock,
            write_lock_key,
        ),
        daemon=True,
        name=f"agent-run-{run_id}",
    )
    with _ACTIVE_LOCK:
        _ACTIVE_AGENT_RUNS[run_id] = {
            "thread": thread,
            "stop_event": stop_event,
            "started_at": datetime.now().isoformat(),
        }
    thread.start()
    if wait:
        thread.join(timeout=timeout)
        return wait_for_agent_run(run_id, timeout=0)
    return run


def _interrupt_message(display_name: str, interrupts: list[dict[str, Any]]) -> str:
    details: list[str] = []
    for intr in interrupts:
        tool = str(intr.get("tool") or "tool")
        desc = str(intr.get("description") or "").strip()
        details.append(desc or f"Tool '{tool}' needs approval")
    suffix = "; ".join(details) if details else "Approval is required to continue."
    return f"{display_name or 'Child Agent'} needs approval: {suffix}"


def _pause_agent_for_approval(
    run_id: str,
    result: dict[str, Any],
    config: dict[str, Any],
    enabled_tool_names: list[str],
) -> None:
    from row_bot.agent_runs import get_agent_run, save_agent_resume_state
    from row_bot.tasks import create_approval_request

    run = get_agent_run(run_id) or {}
    interrupts = result.get("interrupts") or []
    if not isinstance(interrupts, list):
        interrupts = []
    message = _interrupt_message(str(run.get("display_name") or ""), interrupts)
    configurable = config.get("configurable") or {}
    resume_state = {
        "config": config,
        "enabled_tool_names": enabled_tool_names,
        "tool_allowlist": list(configurable.get("tool_allowlist") or []),
        "interrupts": interrupts,
    }
    resume_token, approval_id = create_approval_request(
        run_id=run_id,
        task_id="",
        step_id="agent_interrupt",
        message=message,
        agent_run_id=run_id,
        resume_kind="agent_run",
        source_label=str(run.get("display_name") or "Child Agent"),
        source_thread_id=str(configurable.get("thread_id") or run.get("thread_id") or ""),
    )
    resume_state["resume_token"] = resume_token
    resume_state["approval_id"] = approval_id
    save_agent_resume_state(
        run_id,
        resume_state,
        status="waiting_approval",
        status_message="Waiting for approval",
    )


def _run_agent_thread(
    run_id: str,
    prompt: str,
    enabled_tool_names: list[str],
    config: dict[str, Any],
    stop_event: threading.Event,
    requires_write_lock: bool = False,
    write_lock_key: str = "",
) -> None:
    from row_bot.agent_runs import (
        acquire_agent_write_lock,
        append_agent_event,
        finish_agent_run,
        get_agent_parent_messages,
        release_agent_write_lock,
        start_agent_run,
        update_agent_status,
    )

    lock_acquired = False
    try:
        if requires_write_lock:
            update_agent_status(run_id, "queued", "Queued for writer lock")
            while not stop_event.is_set():
                if acquire_agent_write_lock(
                    write_lock_key,
                    run_id,
                    thread_id=(config.get("configurable") or {}).get("thread_id", ""),
                    workspace_id=(config.get("configurable") or {}).get("developer_workspace_id", ""),
                    metadata_json={"runtime_surface": "agent_child"},
                ):
                    lock_acquired = True
                    break
                time.sleep(0.05)
            if stop_event.is_set() and not lock_acquired:
                finish_agent_run(run_id, "stopped", status_message="Stop requested")
                return
        start_agent_run(run_id)
        append_agent_event(
            run_id,
            "turn.started",
            {"thread_id": (config.get("configurable") or {}).get("thread_id", "")},
        )
        parent_messages = get_agent_parent_messages(run_id)
        if parent_messages:
            joined = "\n".join(f"- {message}" for message in parent_messages[-5:])
            prompt = f"{prompt}\n\n[Parent follow-up before start]\n{joined}"
            append_agent_event(
                run_id,
                "parent.messages.applied",
                {"count": len(parent_messages)},
                visibility="internal",
            )
        result = _invoke_agent(
            prompt,
            enabled_tool_names,
            config,
            stop_event=stop_event,
        )
        if isinstance(result, dict) and result.get("type") == "interrupt":
            _pause_agent_for_approval(run_id, result, config, enabled_tool_names)
            return
        text = str(result or "")
        append_agent_event(run_id, "turn.completed", {"length": len(text)})
        finish_agent_run(
            run_id,
            "completed",
            summary=text,
            result_json={"response": text},
        )
    except BaseException as exc:
        if _is_task_stopped(exc) or stop_event.is_set():
            finish_agent_run(run_id, "stopped", status_message="Stop requested")
        else:
            finish_agent_run(
                run_id,
                "failed",
                error=str(exc),
                status_message=str(exc),
            )
    finally:
        if lock_acquired:
            release_agent_write_lock(run_id=run_id)
        with _ACTIVE_LOCK:
            _ACTIVE_AGENT_RUNS.pop(run_id, None)


def resume_agent_run(
    run_id: str,
    *,
    resume_token: str = "",
    approved: bool = True,
) -> dict[str, Any] | None:
    """Resume or stop an interrupted child Agent after approval response."""
    from row_bot.agent_runs import append_agent_event, finish_agent_run, get_agent_run

    run = get_agent_run(run_id)
    if not run:
        return None
    if not approved:
        append_agent_event(
            run_id,
            "approval.resolved",
            {"approved": False, "resume_token": resume_token},
        )
        return finish_agent_run(
            run_id,
            "stopped",
            status_message="Approval denied by user",
        )
    resume_state = run.get("resume_state_json") or {}
    config = resume_state.get("config")
    enabled_tool_names = resume_state.get("enabled_tool_names")
    if not isinstance(config, dict) or not isinstance(enabled_tool_names, list):
        return finish_agent_run(
            run_id,
            "failed",
            error="Missing Agent resume state",
            status_message="Missing Agent resume state",
        )
    interrupt_ids = [
        str(intr.get("id"))
        for intr in resume_state.get("interrupts", [])
        if isinstance(intr, dict) and intr.get("id")
    ]
    append_agent_event(
        run_id,
        "approval.resolved",
        {"approved": True, "resume_token": resume_token},
    )
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_resume_agent_thread,
        args=(run_id, enabled_tool_names, config, interrupt_ids, stop_event),
        daemon=True,
        name=f"agent-resume-{run_id}",
    )
    with _ACTIVE_LOCK:
        _ACTIVE_AGENT_RUNS[run_id] = {
            "thread": thread,
            "stop_event": stop_event,
            "started_at": datetime.now().isoformat(),
        }
    thread.start()
    return get_agent_run(run_id)


def _resume_agent_thread(
    run_id: str,
    enabled_tool_names: list[str],
    config: dict[str, Any],
    interrupt_ids: list[str],
    stop_event: threading.Event,
) -> None:
    from row_bot.agent_runs import (
        acquire_agent_write_lock,
        append_agent_event,
        finish_agent_run,
        get_agent_run,
        release_agent_write_lock,
        start_agent_run,
        update_agent_status,
    )

    lock_acquired = False
    try:
        run = get_agent_run(run_id) or {}
        write_lock_key = str(run.get("write_lock_key") or "")
        if write_lock_key:
            update_agent_status(run_id, "queued", "Queued for writer lock")
            while not stop_event.is_set():
                if acquire_agent_write_lock(
                    write_lock_key,
                    run_id,
                    thread_id=str(run.get("thread_id") or ""),
                    workspace_id=str(run.get("workspace_id") or ""),
                    metadata_json={"runtime_surface": "agent_child_resume"},
                ):
                    lock_acquired = True
                    break
                time.sleep(0.05)
            if stop_event.is_set() and not lock_acquired:
                finish_agent_run(run_id, "stopped", status_message="Stop requested")
                return
        start_agent_run(run_id)
        result = _resume_invoke_agent(
            enabled_tool_names,
            config,
            True,
            interrupt_ids=interrupt_ids or None,
            stop_event=stop_event,
        )
        if isinstance(result, dict) and result.get("type") == "interrupt":
            _pause_agent_for_approval(run_id, result, config, enabled_tool_names)
            return
        text = str(result or "")
        append_agent_event(run_id, "turn.completed", {"length": len(text), "resumed": True})
        finish_agent_run(
            run_id,
            "completed",
            summary=text,
            result_json={"response": text, "resumed": True},
        )
    except BaseException as exc:
        if _is_task_stopped(exc) or stop_event.is_set():
            finish_agent_run(run_id, "stopped", status_message="Stop requested")
        else:
            finish_agent_run(
                run_id,
                "failed",
                error=str(exc),
                status_message=str(exc),
            )
    finally:
        if lock_acquired:
            release_agent_write_lock(run_id=run_id)
        with _ACTIVE_LOCK:
            _ACTIVE_AGENT_RUNS.pop(run_id, None)


def wait_for_agent_run(run_id: str, timeout: float | None = None) -> dict[str, Any] | None:
    """Wait for a live child Agent thread, then return its durable row."""
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        with _ACTIVE_LOCK:
            entry = _ACTIVE_AGENT_RUNS.get(run_id)
        if not entry:
            from row_bot.agent_runs import get_agent_run

            return get_agent_run(run_id)
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        thread = entry["thread"]
        thread.join(timeout=remaining)
        if not thread.is_alive():
            continue
        if deadline is not None and time.monotonic() >= deadline:
            from row_bot.agent_runs import get_agent_run

            return get_agent_run(run_id)


def stop_agent_run(run_id: str) -> dict[str, Any] | None:
    """Request stop for a live child Agent and update durable state."""
    with _ACTIVE_LOCK:
        entry = _ACTIVE_AGENT_RUNS.get(run_id)
        if entry:
            entry["stop_event"].set()
    from row_bot.agent_runs import stop_agent_run as _stop_agent_run

    return _stop_agent_run(run_id)


def list_active_agent_run_ids() -> list[str]:
    with _ACTIVE_LOCK:
        return sorted(_ACTIVE_AGENT_RUNS)
