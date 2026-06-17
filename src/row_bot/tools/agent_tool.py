"""Agents tool for delegating and inspecting child Agent runs."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot import agent_runner
from row_bot.agent_profiles import (
    duplicate_agent_profile,
    list_agent_profiles,
    save_agent_profile,
)
from row_bot.agent_runs import (
    TERMINAL_STATUSES,
    append_agent_parent_message,
    get_agent_events,
    get_agent_parent_messages,
    get_agent_run,
    list_agent_runs,
)
from row_bot.tools import registry
from row_bot.tools.base import BaseTool


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _runtime_context() -> dict[str, Any]:
    try:
        from row_bot.agent import get_active_runtime_context

        return get_active_runtime_context()
    except Exception:
        return {}


def _public_run(run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {}
    parent_messages: list[str] = []
    run_id = str(run.get("id") or "")
    if run_id:
        try:
            parent_messages = get_agent_parent_messages(run_id, limit=20)
        except Exception:
            parent_messages = []
    return {
        "id": run.get("id", ""),
        "kind": run.get("kind", ""),
        "status": run.get("status", ""),
        "status_message": run.get("status_message", ""),
        "display_name": run.get("display_name", ""),
        "thread_id": run.get("thread_id", ""),
        "parent_thread_id": run.get("parent_thread_id", ""),
        "parent_run_id": run.get("parent_run_id", ""),
        "profile": {
            "id": run.get("profile_id", ""),
            "slug": run.get("profile_slug", ""),
            "display_name": run.get("profile_display_name", ""),
        },
        "created_at": run.get("created_at", ""),
        "started_at": run.get("started_at", ""),
        "finished_at": run.get("finished_at", ""),
        "last_event_at": run.get("last_event_at", ""),
        "turns_used": run.get("turns_used", 0),
        "max_turns": run.get("max_turns", 0),
        "summary": run.get("summary", ""),
        "error": run.get("error", ""),
        "stop_requested": bool(run.get("stop_requested", False)),
        "parent_message_count": len(parent_messages),
        "latest_parent_message": parent_messages[-1] if parent_messages else "",
    }


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    tool_policy = profile.get("tool_policy_json") or {}
    skill_policy = profile.get("skill_policy_json") or {}
    context_policy = profile.get("context_policy_json") or {}
    workspace_policy = profile.get("workspace_policy_json") or {}
    approval_policy = profile.get("approval_policy_json") or {}
    if not isinstance(tool_policy, dict):
        tool_policy = {}
    if not isinstance(skill_policy, dict):
        skill_policy = {}
    if not isinstance(context_policy, dict):
        context_policy = {}
    if not isinstance(workspace_policy, dict):
        workspace_policy = {}
    if not isinstance(approval_policy, dict):
        approval_policy = {}
    return {
        "id": profile.get("id", ""),
        "slug": profile.get("slug", ""),
        "display_name": profile.get("display_name", ""),
        "description": profile.get("description", ""),
        "when_to_use": profile.get("when_to_use", ""),
        "scope": profile.get("scope", ""),
        "source": profile.get("source", ""),
        "enabled": bool(profile.get("enabled", True)),
        "capability": tool_policy.get("capability", "read_only"),
        "allow_tools": tool_policy.get("allow_tools") or [],
        "skills": skill_policy.get("skills_override") or [],
        "context_mode": context_policy.get("default_context_mode", "auto"),
        "workspace_mode": workspace_policy.get("workspace_mode_default", "auto"),
        "approval_mode": approval_policy.get("mode", "inherit"),
        "usage_count": profile.get("usage_count", 0),
    }


def _public_workflow(task: dict[str, Any] | None) -> dict[str, Any]:
    if not task:
        return {}
    return {
        "id": task.get("id", ""),
        "name": task.get("name", ""),
        "description": task.get("description", ""),
        "enabled": bool(task.get("enabled", False)),
        "advanced_mode": bool(task.get("advanced_mode", False)),
        "agent_profile_id": task.get("agent_profile_id", ""),
        "model_override": task.get("model_override", ""),
        "tools_override": task.get("tools_override"),
        "skills_override": task.get("skills_override"),
        "steps": task.get("steps", []),
    }


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _promoted_slug(run_id: str) -> str:
    clean = "".join(
        char if char.isalnum() else "_"
        for char in str(run_id or "agent").lower()
    ).strip("_")
    return f"promoted_{clean or 'agent'}"


class _DelegateWorkInput(BaseModel):
    objective: str = Field(description="Specific objective for the child Agent.")
    profile: str = Field(
        default="",
        description="Agent Profile slug or id, such as reviewer, researcher, explorer, tester, or worker.",
    )
    context: str = Field(
        default="",
        description="Focused context packet for the child. Include only relevant facts, files, constraints, and expected output.",
    )
    context_mode: str = Field(
        default="auto",
        description="Context mode: auto, focused, recent, full, empty, or resume.",
    )
    display_name: str = Field(default="", description="Optional short display name for the child Agent run.")
    parent_thread_id: str = Field(
        default="",
        description="Optional parent thread id. Omit to use the current thread.",
    )
    parent_run_id: str = Field(default="", description="Optional parent Agent Run id for nested tracking.")
    parent_message_id: str = Field(default="", description="Optional parent message id that triggered delegation.")
    wait: bool = Field(default=False, description="If true, wait briefly for the child result before returning.")
    timeout_seconds: float = Field(default=60.0, description="Maximum seconds to wait when wait=true.")


class _AgentStatusInput(BaseModel):
    run_id: str = Field(default="", description="Agent Run id. If omitted, list runs for the parent thread.")
    parent_thread_id: str = Field(default="", description="Parent thread id. Omit to use the current thread.")
    statuses: list[str] = Field(default=[], description="Optional statuses to filter by.")
    include_events: bool = Field(default=False, description="Include recent event log entries.")
    limit: int = Field(default=10, description="Maximum runs or events to return.")


class _AgentWaitInput(BaseModel):
    run_id: str = Field(description="Agent Run id to wait for.")
    timeout_seconds: float = Field(default=60.0, description="Maximum seconds to wait.")
    include_events: bool = Field(default=True, description="Include recent events in the result.")


class _AgentStopInput(BaseModel):
    run_id: str = Field(description="Agent Run id to stop.")


class _AgentProfilesInput(BaseModel):
    query: str = Field(default="", description="Optional profile slug/name/capability text to filter.")
    enabled_only: bool = Field(default=True, description="Only include enabled profiles.")
    include_builtins: bool = Field(default=True, description="Include built-in Agent Profiles.")


class _AgentProfileSaveInput(BaseModel):
    slug: str = Field(description="Stable lowercase slug for the Agent Profile.")
    display_name: str = Field(description="Human-readable Agent Profile name.")
    description: str = Field(default="", description="Short description of what the profile does.")
    when_to_use: str = Field(default="", description="When parent agents should choose this profile.")
    instructions: str = Field(description="Focused instructions for agents using this profile.")
    capability: str = Field(default="read_only", description="read_only, write_capable, or orchestrator.")
    allow_tools: list[str] = Field(default=[], description="Optional exact tool names this profile may use.")
    skills: list[str] = Field(default=[], description="Manual skills pinned/injected when this profile is used.")
    context_mode: str = Field(default="focused", description="Default context mode.")
    workspace_mode: str = Field(default="read_only", description="auto, read_only, single_writer, or worktree.")
    approval_mode: str = Field(default="inherit", description="inherit, block, approve, or allow_all.")


class _AgentMessageInput(BaseModel):
    run_id: str = Field(description="Agent Run id.")
    message: str = Field(description="Message to send to the Agent.")


class _AgentPromoteInput(BaseModel):
    run_id: str = Field(description="Completed Agent Run id to promote.")
    target: str = Field(default="profile", description="profile or workflow.")


def _delegate_work(
    objective: str,
    profile: str = "",
    context: str = "",
    context_mode: str = "auto",
    display_name: str = "",
    parent_thread_id: str = "",
    parent_run_id: str = "",
    parent_message_id: str = "",
    wait: bool = False,
    timeout_seconds: float = 60.0,
) -> str:
    runtime = _runtime_context()
    parent_thread_id = parent_thread_id or str(runtime.get("thread_id") or "")
    enabled_tool_names = list(runtime.get("enabled_tool_names") or ())
    run = agent_runner.spawn_agent_run(
        objective,
        parent_thread_id=parent_thread_id,
        parent_run_id=parent_run_id,
        parent_message_id=parent_message_id,
        profile=profile,
        display_name=display_name,
        context=context,
        context_mode=context_mode,
        enabled_tool_names=enabled_tool_names,
        wait=wait,
        timeout=timeout_seconds if wait else None,
    )
    if wait:
        status = str((run or {}).get("status") or "")
        if status in TERMINAL_STATUSES:
            message = "Child Agent completed."
        else:
            message = "Child Agent is still running after the wait timeout."
    else:
        message = "Child Agent started."
    return _json_response({
        "ok": True,
        "run": _public_run(run),
        "message": message,
    })


def _agent_status(
    run_id: str = "",
    parent_thread_id: str = "",
    statuses: list[str] | None = None,
    include_events: bool = False,
    limit: int = 10,
) -> str:
    runtime = _runtime_context()
    parent_thread_id = parent_thread_id or str(runtime.get("thread_id") or "")
    if run_id:
        run = get_agent_run(run_id)
        payload: dict[str, Any] = {"ok": bool(run), "run": _public_run(run)}
        if include_events and run:
            payload["events"] = get_agent_events(run_id, limit=limit)
        return _json_response(payload)
    runs = list_agent_runs(
        parent_thread_id=parent_thread_id or None,
        statuses=statuses or None,
        limit=limit,
    )
    return _json_response({
        "ok": True,
        "parent_thread_id": parent_thread_id,
        "runs": [_public_run(run) for run in runs],
    })


def _agent_wait(run_id: str, timeout_seconds: float = 60.0, include_events: bool = True) -> str:
    run = agent_runner.wait_for_agent_run(run_id, timeout=timeout_seconds)
    payload: dict[str, Any] = {"ok": bool(run), "run": _public_run(run)}
    if include_events and run:
        payload["events"] = get_agent_events(run_id, limit=20)
    return _json_response(payload)


def _agent_stop(run_id: str) -> str:
    run = agent_runner.stop_agent_run(run_id)
    return _json_response({"ok": bool(run), "run": _public_run(run)})


def _agent_profiles(
    query: str = "",
    enabled_only: bool = True,
    include_builtins: bool = True,
) -> str:
    query_l = str(query or "").strip().lower()
    profiles = list_agent_profiles(
        enabled_only=enabled_only,
        include_builtins=include_builtins,
    )
    summaries = [_public_profile(profile) for profile in profiles]
    if query_l:
        summaries = [
            profile
            for profile in summaries
            if query_l in json.dumps(profile, sort_keys=True).lower()
        ]
    return _json_response({"ok": True, "profiles": summaries})


def _agent_profile_save(
    slug: str,
    display_name: str,
    instructions: str,
    description: str = "",
    when_to_use: str = "",
    capability: str = "read_only",
    allow_tools: list[str] | None = None,
    skills: list[str] | None = None,
    context_mode: str = "focused",
    workspace_mode: str = "read_only",
    approval_mode: str = "inherit",
) -> str:
    profile = save_agent_profile(
        slug=slug,
        display_name=display_name,
        description=description,
        when_to_use=when_to_use,
        instructions=instructions,
        tool_policy_json={
            "capability": capability,
            "allow_tools": _as_list(allow_tools),
        },
        skill_policy_json={
            "skills_override": _as_list(skills),
        },
        context_policy_json={"default_context_mode": context_mode},
        workspace_policy_json={"workspace_mode_default": workspace_mode},
        approval_policy_json={"mode": approval_mode},
    )
    return _json_response({"ok": True, "profile": _public_profile(profile)})


def _agent_message(run_id: str, message: str) -> str:
    run = get_agent_run(run_id)
    if not run:
        return _json_response({"ok": False, "message": "Agent Run not found."})
    status = str(run.get("status") or "")
    if status in TERMINAL_STATUSES:
        return _json_response({
            "ok": False,
            "run": _public_run(run),
            "message": "Completed or stopped Agent Runs cannot be steered.",
        })
    updated = append_agent_parent_message(run_id, message)
    effect = (
        "Message recorded and will be included before the child starts."
        if status == "queued"
        else "Message recorded for parent tracking; active turns cannot be interrupted mid-call."
    )
    return _json_response({
        "ok": bool(updated),
        "run": _public_run(updated),
        "message": effect,
    })


def _agent_promote(run_id: str, target: str = "profile") -> str:
    target = str(target or "profile").strip().lower()
    run = get_agent_run(run_id)
    if not run:
        return _json_response({"ok": False, "message": "Agent Run not found."})
    if not str(run.get("status") or "").startswith("completed"):
        return _json_response({
            "ok": False,
            "message": "Only completed Agent Runs can be promoted.",
        })
    if target == "profile":
        base_profile = (run.get("profile_snapshot_json") or {}).get("id") or "worker"
        duplicate = duplicate_agent_profile(
            base_profile,
            {
                "slug": _promoted_slug(run_id),
                "display_name": f"Promoted {run.get('display_name') or run_id}",
                "created_from_run_id": run_id,
            },
        )
        return _json_response({"ok": True, "profile": _public_profile(duplicate)})
    if target == "workflow":
        from row_bot import tasks as tasks_db

        objective = str(run.get("prompt") or run.get("display_name") or "").strip()
        summary = str(run.get("summary") or run.get("status_message") or "").strip()
        context_summary = str(run.get("context_summary") or "").strip()
        profile_ref = str(run.get("profile_id") or "").strip()
        prompt_lines = [
            "Run this promoted Agent workflow.",
            "",
            f"Original objective: {objective or run_id}",
        ]
        if context_summary:
            prompt_lines.extend(["", f"Original context summary: {context_summary}"])
        if summary:
            prompt_lines.extend(["", f"Successful output summary: {summary}"])
        prompt_lines.extend([
            "",
            "Produce a fresh result for the same kind of request. Preserve the source run's safety and profile constraints.",
        ])
        steps = [
            {
                "type": "prompt",
                "label": "Promoted Agent run",
                "prompt": "\n".join(prompt_lines),
            }
        ]
        if profile_ref:
            steps[0]["agent_profile_id"] = profile_ref

        tools_override = _as_list(run.get("tools_override"))
        task_id = tasks_db.create_task(
            name=f"Promoted {run.get('display_name') or run_id}",
            description=(
                "Promoted from completed Agent Run "
                f"{run_id}. Review before enabling or scheduling."
            ),
            icon="hub",
            steps=steps,
            model_override=str(run.get("model_override") or "") or None,
            skills_override=_as_list(run.get("skills_override")),
            tools_override=tools_override if tools_override else None,
            safety_mode=str(run.get("approval_mode") or "block"),
            agent_profile_id=profile_ref or None,
            enabled=False,
            apply_default_skills=False,
        )
        if not tools_override:
            tasks_db.update_task(task_id, tools_override=[])
        task = tasks_db.get_task(task_id)
        return _json_response({
            "ok": True,
            "workflow": _public_workflow(task),
            "message": "Workflow promoted as a disabled manual workflow. Review before enabling.",
        })
    return _json_response({
        "ok": False,
        "message": "Promotion target must be 'profile' or 'workflow'.",
    })


class AgentsTool(BaseTool):
    @property
    def name(self) -> str:
        return "agents"

    @property
    def display_name(self) -> str:
        return "Agents"

    @property
    def description(self) -> str:
        return "Delegate focused work to child Agents and inspect their durable runs."

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"agent_profile_save", "agent_promote"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_delegate_work,
                name="delegate_work",
                description="Start a child Agent for focused background work.",
                args_schema=_DelegateWorkInput,
            ),
            StructuredTool.from_function(
                func=_agent_status,
                name="agent_status",
                description="Inspect one Agent Run or list child runs for the current parent thread.",
                args_schema=_AgentStatusInput,
            ),
            StructuredTool.from_function(
                func=_agent_wait,
                name="agent_wait",
                description="Wait for a child Agent Run and return its latest durable status.",
                args_schema=_AgentWaitInput,
            ),
            StructuredTool.from_function(
                func=_agent_stop,
                name="agent_stop",
                description="Request stop for a child Agent Run.",
                args_schema=_AgentStopInput,
            ),
            StructuredTool.from_function(
                func=_agent_profiles,
                name="agent_profiles",
                description="List available Agent Profiles and when to use them.",
                args_schema=_AgentProfilesInput,
            ),
            StructuredTool.from_function(
                func=_agent_profile_save,
                name="agent_profile_save",
                description="Create or update a user Agent Profile after explicit user request. This is approval-gated.",
                args_schema=_AgentProfileSaveInput,
            ),
            StructuredTool.from_function(
                func=_agent_message,
                name="agent_message",
                description="Record a parent follow-up or steering message for a non-terminal child Agent.",
                args_schema=_AgentMessageInput,
            ),
            StructuredTool.from_function(
                func=_agent_promote,
                name="agent_promote",
                description="Promote a completed Agent Run into a reusable profile or disabled manual workflow. This is approval-gated.",
                args_schema=_AgentPromoteInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return "Use delegate_work, agent_status, agent_wait, agent_stop, agent_profiles, agent_profile_save, agent_message, or agent_promote."


registry.register(AgentsTool())
