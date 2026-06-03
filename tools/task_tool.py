"""Task tool — create, list, and run tasks from the agent.

Exposes three sub-tools so the agent can manage scheduled/one-shot tasks
and quick timers via natural language.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tools.base import BaseTool
from tools import registry
import tasks as tasks_db
from approval_policy import approval_label, legacy_safety_mode_to_approval_mode

logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class _TaskCreateInput(BaseModel):
    name: str = Field(
        description="Short descriptive name for the task (e.g. 'Morning Briefing', 'Remind me to call Mom')."
    )
    prompts: list[str] = Field(
        default=[],
        description=(
            "Ordered list of prompts the agent will execute sequentially. "
            "Leave empty for notify-only tasks (reminders/timers). "
            "Supports template variables: {{date}}, {{day}}, {{time}}, "
            "{{month}}, {{year}}, {{task_id}} (the task's own ID, useful "
            "for self-management like task_update(task_id='{{task_id}}', enabled=false))."
        ),
    )
    description: str = Field(
        default="",
        description="Optional longer description of what the task does.",
    )
    icon: str = Field(
        default="⚡",
        description="Single emoji icon for the task (e.g. '📰', '🔬', '⏰').",
    )
    schedule: str | None = Field(
        default=None,
        description=(
            "Recurring schedule. Formats: "
            "'daily:HH:MM' (e.g. 'daily:08:00'), "
            "'weekly:DAY:HH:MM' (e.g. 'weekly:monday:09:00'), "
            "'interval:H' for every H hours (e.g. 'interval:2'), "
            "'interval_minutes:M' for every M minutes (e.g. 'interval_minutes:30'), "
            "'cron:EXPR' for advanced cron (e.g. 'cron:0 9 * * mon-fri'). "
            "Mutually exclusive with delay_minutes."
        ),
    )
    delay_minutes: float | None = Field(
        default=None,
        description=(
            "Set a one-shot timer that fires after this many minutes. "
            "Use for quick reminders like 'remind me in 30 minutes'. "
            "The task auto-deletes after firing. "
            "Mutually exclusive with schedule."
        ),
    )
    notify_only: bool = Field(
        default=False,
        description=(
            "If true, the task fires a desktop notification only — "
            "no agent invocation. Use for simple reminders/timers."
        ),
    )
    notify_label: str = Field(
        default="",
        description="Custom notification label for notify-only tasks (defaults to the task name).",
    )
    delivery_channel: str | None = Field(
        default=None,
        description=(
            "Optional delivery channel: 'telegram' or 'email'. "
            "When set, task output is also sent via this channel. "
            "Desktop + in-app notification always fires regardless."
        ),
    )
    delivery_target: str | None = Field(
        default=None,
        description=(
            "Target for the delivery channel: Telegram chat ID (number) "
            "or email address. Required if delivery_channel is set."
        ),
    )
    model: str | None = Field(
        default=None,
        description=(
            "Optional Ollama model name to use for this task instead of the global default "
            "(e.g. 'qwen3:32b'). Leave empty to use the current brain model."
        ),
    )
    steps: list[dict] | None = Field(
        default=None,
        description=(
            "Advanced pipeline steps (overrides prompts). Each step is a dict with: "
            "'type' (prompt|condition|approval|subtask|notify) — "
            "IDs are auto-assigned as {type}_{counter} (prompt_1, condition_1, etc.). "
            "prompt: {'prompt': str, 'on_error': 'stop'|'skip', 'max_retries': int}. "
            "condition: {'condition': str, 'if_true': step_id, 'if_false': step_id}. "
            "approval: {'message': str, 'timeout_minutes': int, 'if_approved': step_id (optional, default: next step), 'if_denied': step_id or 'end' (optional, default: 'end')}. "
            "subtask: {'task_id': str, 'pass_output': bool}. "
            "notify: {'message': str, 'channel': 'desktop' or any configured channel name (e.g. 'telegram')}. "
            "IMPORTANT: 'email' is NOT a valid notify channel. To send email, use a prompt step that calls the gmail/email tool instead. "
            "All step types support an optional 'next' field (step_id or 'end') to override linear flow after that step. "
            "Use {{prev_output}} and {{step.<id>.output}} for data passing between steps."
        ),
    )
    approval_mode: str | None = Field(
        default=None,
        description=(
            "Approval mode for this task: 'block', 'approve', or 'allow_all'. Defaults to 'block'."
        ),
    )
    safety_mode: str | None = Field(
        default=None,
        description="Legacy alias for approval_mode.",
    )
    persistent_thread: bool = Field(
        default=False,
        description=(
            "If true, all runs of this task share one conversation thread "
            "so the agent can see prior outputs across runs. Use for "
            "monitoring/polling tasks that need to compare against previous "
            "results (e.g. price tracking, status dashboards). "
            "Default false = each run gets a fresh thread."
        ),
    )


class _TaskListInput(BaseModel):
    include_history: bool = Field(
        default=False,
        description="If true, include last run info for each task.",
    )


class _TaskRunNowInput(BaseModel):
    task_id: str = Field(
        description="The ID of the task to run immediately (from task_list output)."
    )


class _TaskDeleteInput(BaseModel):
    task_id: str = Field(
        description="The ID of the task to delete (from task_list output)."
    )


class _TaskUpdateInput(BaseModel):
    task_id: str = Field(
        description="The ID of the task to update (from task_list output)."
    )
    name: str | None = Field(
        default=None,
        description="New name for the task.",
    )
    schedule: str | None = Field(
        default=None,
        description=(
            "New schedule string. Same formats as task_create: "
            "'daily:HH:MM', 'weekly:DAY:HH:MM', 'interval:H', "
            "'interval_minutes:M', 'cron:EXPR'. "
            "Set to empty string '' to clear schedule (make manual)."
        ),
    )
    prompts: list[str] | None = Field(
        default=None,
        description=(
            "New prompt list (full replacement — provide ALL steps). "
            "Supports template variables: {{date}}, {{day}}, {{time}}, {{month}}, {{year}}."
        ),
    )
    steps: list[dict] | None = Field(
        default=None,
        description=(
            "New pipeline steps (full replacement). Same format as task_create steps. "
            "Overrides prompts when set."
        ),
    )
    approval_mode: str | None = Field(
        default=None,
        description="Approval mode: 'block', 'approve', or 'allow_all'.",
    )
    safety_mode: str | None = Field(
        default=None,
        description="Legacy alias for approval_mode.",
    )
    enabled: bool | None = Field(
        default=None,
        description="Set to false to pause the task, true to resume it.",
    )
    model: str | None = Field(
        default=None,
        description=(
            "Optional Ollama model name to use for this task instead of the global default. "
            "Set to empty string '' to clear and use the default model."
        ),
    )
    persistent_thread: bool | None = Field(
        default=None,
        description=(
            "Set to true to keep conversation history across runs, "
            "or false to use a fresh thread each run."
        ),
    )


# ── Tool functions ───────────────────────────────────────────────────────────

def _task_create(
    name: str,
    prompts: list[str] | None = None,
    description: str = "",
    icon: str = "⚡",
    schedule: str | None = None,
    delay_minutes: float | None = None,
    notify_only: bool = False,
    notify_label: str = "",
    delivery_channel: str | None = None,
    delivery_target: str | None = None,
    model: str | None = None,
    steps: list[dict] | None = None,
    approval_mode: str | None = None,
    safety_mode: str | None = None,
    persistent_thread: bool = False,
) -> str:
    """Create a new task or quick timer."""
    try:
        # Default to notify_only for delay_minutes with no prompts
        if delay_minutes and not prompts and not steps:
            notify_only = True

        # Generate persistent_thread_id if requested
        p_thread_id = None
        if persistent_thread:
            p_thread_id = f"pt_{uuid.uuid4().hex[:10]}"
        effective_approval_mode = legacy_safety_mode_to_approval_mode(approval_mode or safety_mode or "block")

        task_id = tasks_db.create_task(
            name=name,
            prompts=prompts or [],
            description=description,
            icon=icon,
            schedule=schedule,
            delay_minutes=delay_minutes,
            notify_only=notify_only,
            notify_label=notify_label,
            delivery_channel=delivery_channel,
            delivery_target=delivery_target,
            model_override=model or None,
            persistent_thread_id=p_thread_id,
            steps=steps,
            safety_mode=effective_approval_mode,
        )

        task = tasks_db.get_task(task_id)
        if not task:
            return f"Task created with ID: {task_id}"

        parts = [f"Task created successfully."]
        parts.append(f"  ID: {task_id}")
        parts.append(f"  Name: {task['icon']} {task['name']}")
        if task.get("schedule"):
            parts.append(f"  Schedule: {task['schedule']}")
        if task.get("at"):
            parts.append(f"  Fires at: {task['at']}")
        if task.get("notify_only"):
            parts.append(f"  Type: Notification only")
        elif task.get("steps"):
            step_types = [s.get("type", "prompt") for s in task["steps"]]
            parts.append(f"  Pipeline: {len(task['steps'])} steps ({', '.join(set(step_types))})")
        else:
            parts.append(f"  Steps: {len(task['prompts'])}")
        if task.get("safety_mode") and task["safety_mode"] != "block":
            parts.append(f"  Approval: {approval_label(task['safety_mode'])}")
        if task.get("delivery_channel"):
            parts.append(f"  Delivery: {task['delivery_channel']} → {task.get('delivery_target', 'default')}")

        # Include Mermaid flow diagram for pipeline tasks
        if task.get("steps"):
            from tasks import generate_pipeline_mermaid
            mermaid = generate_pipeline_mermaid(task["steps"])
            if mermaid:
                parts.append(f"\nPipeline flow:\n```mermaid\n{mermaid}\n```")
                parts.append("\nIMPORTANT: Always include the pipeline flow diagram above in your response to the user.")

        return "\n".join(parts)
    except ValueError as exc:
        return f"Error creating task: {exc}"
    except Exception as exc:
        logger.error("task_create error: %s", exc, exc_info=True)
        return f"Error creating task: {exc}"


def _task_list(include_history: bool = False) -> str:
    """List all tasks with their status."""
    all_tasks = tasks_db.list_tasks()
    if not all_tasks:
        return "No tasks configured yet. Use task_create to set one up."

    entries = []
    for t in all_tasks:
        entry = {
            "id": t["id"],
            "name": f"{t['icon']} {t['name']}",
            "enabled": t.get("enabled", True),
        }
        if t.get("description"):
            entry["description"] = t["description"]
        if t.get("schedule"):
            entry["schedule"] = t["schedule"]
        if t.get("at"):
            entry["fires_at"] = t["at"]
        if t.get("notify_only"):
            entry["type"] = "notification_only"
        elif t.get("steps"):
            entry["pipeline_steps"] = len(t["steps"])
            entry["step_types"] = list(set(s.get("type", "prompt") for s in t["steps"]))
        else:
            entry["steps"] = len(t.get("prompts", []))
        if t.get("safety_mode") and t["safety_mode"] != "block":
            entry["approval_mode"] = t["safety_mode"]
        if t.get("delivery_channel"):
            entry["delivery"] = f"{t['delivery_channel']} → {t.get('delivery_target', 'default')}"
        if t.get("last_run"):
            entry["last_run"] = t["last_run"][:16]
        if include_history:
            runs = tasks_db.get_run_history(t["id"], limit=3)
            if runs:
                entry["recent_runs"] = [
                    {
                        "status": r["status"],
                        "started": r["started_at"][:16],
                        "steps": f"{r['steps_done']}/{r['steps_total']}",
                    }
                    for r in runs
                ]
        entries.append(entry)

    return json.dumps(entries, indent=2)


def _task_delete(task_id: str) -> str:
    """Delete a task permanently."""
    task = tasks_db.get_task(task_id)
    if not task:
        return f"Task '{task_id}' not found. Use task_list to see available tasks."
    name = f"{task['icon']} {task['name']}"
    tasks_db.delete_task(task_id)
    return f"Deleted task '{name}'."


def _task_update(
    task_id: str,
    name: str | None = None,
    schedule: str | None = None,
    prompts: list[str] | None = None,
    steps: list[dict] | None = None,
    approval_mode: str | None = None,
    safety_mode: str | None = None,
    enabled: bool | None = None,
    model: str | None = None,
    persistent_thread: bool | None = None,
) -> str:
    """Update fields on an existing task."""
    task = tasks_db.get_task(task_id)
    if not task:
        return f"Task '{task_id}' not found. Use task_list to see available tasks."

    updates: dict = {}
    if name is not None:
        updates["name"] = name
    if schedule is not None:
        updates["schedule"] = schedule if schedule else None
    if prompts is not None:
        updates["prompts"] = prompts
    if steps is not None:
        updates["steps"] = steps
    if approval_mode is not None or safety_mode is not None:
        updates["safety_mode"] = legacy_safety_mode_to_approval_mode(approval_mode or safety_mode or "block")
    if enabled is not None:
        updates["enabled"] = enabled
    if model is not None:
        updates["model_override"] = model if model else None
    if persistent_thread is not None:
        if persistent_thread:
            # Only generate a new ID if one doesn't already exist
            if not task.get("persistent_thread_id"):
                updates["persistent_thread_id"] = f"pt_{uuid.uuid4().hex[:10]}"
        else:
            updates["persistent_thread_id"] = None

    if not updates:
        return "No fields to update. Provide at least one of: name, schedule, prompts, steps, approval_mode, enabled, model, persistent_thread."

    try:
        tasks_db.update_task(task_id, **updates)
        task = tasks_db.get_task(task_id)
        parts = [f"Updated task '{task['icon']} {task['name']}'."]
        if "name" in updates:
            parts.append(f"  Name: {updates['name']}")
        if "schedule" in updates:
            parts.append(f"  Schedule: {task.get('schedule') or 'manual'}")
        if "prompts" in updates:
            parts.append(f"  Steps: {len(task['prompts'])}")
        if "steps" in updates:
            parts.append(f"  Pipeline: {len(task.get('steps', []))} steps")
        if "safety_mode" in updates:
            parts.append(f"  Approval: {approval_label(task.get('safety_mode', 'block'))}")
        if "enabled" in updates:
            parts.append(f"  Enabled: {task.get('enabled', True)}")

        # Include Mermaid flow diagram when steps are updated
        if "steps" in updates and task.get("steps"):
            from tasks import generate_pipeline_mermaid
            mermaid = generate_pipeline_mermaid(task["steps"])
            if mermaid:
                parts.append(f"\nPipeline flow:\n```mermaid\n{mermaid}\n```")
                parts.append("\nIMPORTANT: Always include the pipeline flow diagram above in your response to the user.")

        return "\n".join(parts)
    except ValueError as exc:
        return f"Error updating task: {exc}"
    except Exception as exc:
        logger.error("task_update error: %s", exc, exc_info=True)
        return f"Error updating task: {exc}"


def _task_run_now(task_id: str) -> str:
    """Trigger immediate execution of an existing task."""
    from tools import registry as tool_registry

    task = tasks_db.get_task(task_id)
    if not task:
        return f"Task '{task_id}' not found. Use task_list to see available tasks."

    thread_id = tasks_db._prepare_task_thread(task)
    enabled = [t.name for t in tool_registry.get_enabled_tools()]
    tasks_db.run_task_background(task_id, thread_id, enabled, notification=True)

    return (
        f"Task '{task['icon']} {task['name']}' started.\n"
        f"It will run in the background and you'll be notified when done."
    )


# ── Tool class ───────────────────────────────────────────────────────────────

class TaskTool(BaseTool):

    @property
    def name(self) -> str:
        return "task"

    @property
    def display_name(self) -> str:
        return "📋 Tasks"

    @property
    def description(self) -> str:
        return (
            "Create, list, and run scheduled tasks and quick timers. "
            "Use for recurring automations (daily briefings, research digests) "
            "and one-shot reminders ('remind me in 30 minutes')."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"task_delete"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_task_create,
                name="task_create",
                description=(
                    "Create a new task or quick timer. For simple reminders, set "
                    "notify_only=true and use delay_minutes (e.g. 'remind me in "
                    "30 minutes' → delay_minutes=30, notify_only=true). For "
                    "recurring agent tasks, provide prompts and a schedule. "
                    "Supports template variables in prompts: {{date}}, {{day}}, "
                    "{{time}}, {{month}}, {{year}}. "
                    "For advanced pipelines with conditions, approvals, or "
                    "branching, use 'steps' instead of 'prompts'."
                ),
                args_schema=_TaskCreateInput,
            ),
            StructuredTool.from_function(
                func=_task_list,
                name="task_list",
                description=(
                    "List all configured tasks with their status, schedule, "
                    "and last run time. Set include_history=true to also see "
                    "recent run results."
                ),
                args_schema=_TaskListInput,
            ),
            StructuredTool.from_function(
                func=_task_run_now,
                name="task_run_now",
                description=(
                    "Run an existing task immediately, regardless of its "
                    "schedule. Use when the user wants to trigger a specific "
                    "task right now. Requires the task ID from task_list."
                ),
                args_schema=_TaskRunNowInput,
            ),
            StructuredTool.from_function(
                func=_task_delete,
                name="task_delete",
                description=(
                    "Delete a task permanently. Requires the task ID from "
                    "task_list. The user will be asked to confirm before "
                    "deletion proceeds."
                ),
                args_schema=_TaskDeleteInput,
            ),
            StructuredTool.from_function(
                func=_task_update,
                name="task_update",
                description=(
                    "Update an existing task. Can change name, schedule, "
                    "prompts, steps, approval_mode, model, or enabled state. "
                    "Requires the task ID from task_list. Only provide the "
                    "fields you want to change."
                ),
                args_schema=_TaskUpdateInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return "Use task_create, task_list, task_run_now, task_delete, or task_update."


registry.register(TaskTool())
