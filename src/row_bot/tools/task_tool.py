"""Task tool — create, list, and run tasks from the agent.

Exposes three sub-tools so the agent can manage scheduled/one-shot tasks
and quick timers via natural language.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry
import row_bot.tasks as tasks_db
from row_bot.approval_policy import approval_label, legacy_safety_mode_to_approval_mode

logger = logging.getLogger(__name__)

_FULL_AUTONOMY_REFUSAL = (
    "Hands-off can only be turned on by the person, in the workflow's settings in Row-Bot. "
    "Use approval_mode='auto' (reviewed) or 'approve', or ask the person to turn it on."
)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

# An agent never chooses Hands-off for a workflow: only the person does, in the app.
ApprovalModeChoice = Literal["block", "approve", "auto"]
_SCHEDULE_FORMATS = (
    "'daily:HH:MM', 'weekly:DAY:HH:MM' (e.g. 'weekly:monday:09:00'), 'interval:H' (every H hours), "
    "'interval_minutes:M' or 'cron:EXPR' (e.g. 'cron:0 9 * * mon-fri')"
)
_TEMPLATE_VARIABLES = "{{date}}, {{day}}, {{time}}, {{month}}, {{year}} and {{task_id}} (this task's own id)"
_TASK_LIST_CHARS = 30_000  # Beyond this the list is saved, and read on with read_tool_output.


class _TaskCreateInput(BaseModel):
    name: str = Field(description="Short name, e.g. 'Morning Briefing' or 'Call Mom'.")
    prompts: list[str] = Field(
        default=[],
        description=(
            "Prompts the agent runs in order, in a background conversation. Empty for a notify-only "
            f"reminder. Template variables: {_TEMPLATE_VARIABLES}."
        ),
    )
    description: str = Field(default="", description="Optional longer description.")
    icon: str = Field(default="⚡", description="One emoji, e.g. '📰'.")
    schedule: str | None = Field(
        default=None,
        description=f"Recurring schedule: {_SCHEDULE_FORMATS}. Not with delay_minutes or run_at.",
    )
    delay_minutes: float | None = Field(
        default=None,
        description="One-shot timer after this many minutes; the task deletes itself after firing.",
    )
    run_at: str | None = Field(
        default=None,
        description="One-off local date and time 'YYYY-MM-DDTHH:MM', in the future.",
    )
    notify_only: bool = Field(
        default=False,
        description="Only notify (desktop and in-app); no agent run. For plain reminders and timers.",
    )
    notify_label: str = Field(default="", description="Notification text for notify-only tasks (default: the name).")
    delivery_channel: str | None = Field(
        default=None,
        description=(
            "Also send the output through this channel, e.g. 'telegram' (no target needed) or 'email'. "
            "Desktop and in-app notifications always fire."
        ),
    )
    delivery_target: str | None = Field(
        default=None,
        description="Chat id or email address for delivery_channel. Ask the person for an email address; never guess.",
    )
    model: str | None = Field(
        default=None,
        description="Model for this task: provider-qualified or pinned. Empty uses the current Brain model.",
    )
    agent_profile: str | None = Field(default=None, description="Agent Profile slug or id (default: Default).")
    steps: list[dict] | None = Field(
        default=None,
        description=(
            "Pipeline steps instead of prompts, for branching, approvals or notifications. Each step has a "
            "'type' (prompt|condition|approval|subtask|notify); ids are assigned as {type}_{counter} "
            "(prompt_1, condition_1). "
            "prompt: {'prompt': str, 'on_error': 'stop'|'skip', 'max_retries': int}. "
            "condition: {'condition': str, 'if_true': step_id, 'if_false': step_id}; it checks the previous "
            "step's output and is one of: " + tasks_db.CONDITION_SYNTAX + " (use llm:<question> for a judgement "
            "in words, e.g. 'is any item below its reorder level?'). "
            "approval: {'message': str, 'timeout_minutes': int, 'if_approved': step_id (default: next step), "
            "'if_denied': step_id or 'end' (default 'end')}. "
            "subtask: {'task_id': str, 'pass_output': bool}. "
            "notify: {'message': str, 'channel': 'desktop' or a configured channel name such as 'telegram'}; "
            "use a notify step, not a prompt step, when the person says 'notify me' or 'tell me'. "
            "'email' is not a notify channel: send email from a prompt step with the email tool. "
            "Every step needs its text (a prompt, or a message). Any step may set 'next' (step_id or 'end') "
            "to change the flow. Pass data with {{prev_output}} and {{step.<id>.output}}."
        ),
    )
    approval_mode: ApprovalModeChoice | None = Field(
        default=None,
        description=(
            "Destructive tools: block (default), approve (pause for the person) or auto (a reviewer approves "
            "what matches the workflow; otherwise it pauses for the person)."
        ),
    )
    persistent_thread: bool = Field(
        default=False,
        description=(
            "Share one conversation across runs so each run sees earlier results (monitoring, price "
            "tracking). Default: a fresh conversation per run."
        ),
    )


class _TaskListInput(BaseModel):
    include_history: bool = Field(default=False, description="Also show each task's recent runs.")


class _TaskRunNowInput(BaseModel):
    task_id: str = Field(description="Task id from task_list.")


class _TaskDeleteInput(BaseModel):
    task_id: str = Field(description="Task id from task_list.")


class _TaskUpdateInput(BaseModel):
    task_id: str = Field(description="Task id from task_list.")
    name: str | None = Field(default=None, description="New name.")
    schedule: str | None = Field(
        default=None,
        description="New schedule, in task_create's formats; '' clears it (run manually).",
    )
    run_at: str | None = Field(
        default=None,
        description="Move the task to one new local time 'YYYY-MM-DDTHH:MM'; replaces any schedule.",
    )
    prompts: list[str] | None = Field(default=None, description="New prompts (replaces all of them).")
    steps: list[dict] | None = Field(
        default=None,
        description="New pipeline steps (replaces all of them), in task_create's format.",
    )
    approval_mode: ApprovalModeChoice | None = Field(default=None, description="New approval mode.")
    enabled: bool | None = Field(default=None, description="false pauses the task, true resumes it.")
    model: str | None = Field(default=None, description="New model; '' uses the default model.")
    agent_profile: str | None = Field(
        default=None,
        description="Agent Profile slug or id; '' uses the Default profile.",
    )
    persistent_thread: bool | None = Field(
        default=None,
        description="true keeps one conversation across runs; false uses a fresh one each run.",
    )


# ── Tool functions ───────────────────────────────────────────────────────────

def _local_run_at(value: str) -> str:
    """A one-off time as the scheduler stores it: local, to the minute, in the future (B291)."""
    try:
        moment = datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"run_at must be a date and time like 2026-10-08T09:00, not {value!r}.") from None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    moment = moment.replace(second=0, microsecond=0)
    now = datetime.now()
    if moment <= now:
        raise ValueError(f"run_at {moment:%Y-%m-%d %H:%M} is in the past (it's now {now:%Y-%m-%d %H:%M}).")
    return moment.isoformat(timespec="minutes")


def _task_create(
    name: str,
    prompts: list[str] | None = None,
    description: str = "",
    icon: str = "⚡",
    schedule: str | None = None,
    delay_minutes: float | None = None,
    run_at: str | None = None,
    notify_only: bool = False,
    notify_label: str = "",
    delivery_channel: str | None = None,
    delivery_target: str | None = None,
    model: str | None = None,
    agent_profile: str | None = None,
    steps: list[dict] | None = None,
    approval_mode: str | None = None,
    safety_mode: str | None = None,
    persistent_thread: bool = False,
) -> str:
    """Create a new task or quick timer."""
    try:
        at = _local_run_at(run_at) if run_at else None
        # Default to notify_only for a timer or a one-off time with no prompts
        if (delay_minutes or at) and not prompts and not steps:
            notify_only = True

        # Generate persistent_thread_id if requested
        p_thread_id = None
        if persistent_thread:
            p_thread_id = f"pt_{uuid.uuid4().hex[:10]}"
        effective_approval_mode = legacy_safety_mode_to_approval_mode(approval_mode or safety_mode or "block")
        if effective_approval_mode == "full":
            return _FULL_AUTONOMY_REFUSAL

        task_id = tasks_db.create_task(
            name=name,
            prompts=prompts or [],
            description=description,
            icon=icon,
            schedule=schedule,
            at=at,
            delay_minutes=delay_minutes,
            notify_only=notify_only,
            notify_label=notify_label,
            delivery_channel=delivery_channel,
            delivery_target=delivery_target,
            model_override=model or None,
            persistent_thread_id=p_thread_id,
            steps=steps,
            safety_mode=effective_approval_mode,
            agent_profile_id=agent_profile or tasks_db.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
            apply_default_skills=False,
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
            from row_bot.tasks import generate_pipeline_mermaid
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

    from row_bot.tool_outputs import bounded, compact_json

    return bounded(compact_json(entries), tool_name="task_list", max_chars=_TASK_LIST_CHARS)


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
    agent_profile: str | None = None,
    persistent_thread: bool | None = None,
    run_at: str | None = None,
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
    if run_at:
        try:
            updates["at"] = _local_run_at(run_at)
        except ValueError as exc:
            return f"Error updating task: {exc}"
        updates["schedule"] = None
    if prompts is not None:
        updates["prompts"] = prompts
    if steps is not None:
        updates["steps"] = steps
    if approval_mode is not None or safety_mode is not None:
        updates["safety_mode"] = legacy_safety_mode_to_approval_mode(approval_mode or safety_mode or "block")
        if updates["safety_mode"] == "full":
            return _FULL_AUTONOMY_REFUSAL
    if enabled is not None:
        updates["enabled"] = enabled
    if model is not None:
        updates["model_override"] = model if model else None
    if agent_profile is not None:
        updates["agent_profile_id"] = agent_profile or tasks_db.DEFAULT_WORKFLOW_AGENT_PROFILE_ID
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
        if "at" in updates:
            parts.append(f"  Fires at: {task.get('at')}")
        elif "schedule" in updates:
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
            from row_bot.tasks import generate_pipeline_mermaid
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
    from row_bot.tools import registry as tool_registry

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
            "and one-shot reminders ('remind me in 30 minutes', 'remind me at 9 tomorrow')."
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
                # The one home for scheduling guidance; the system prompt only points here.
                description=(
                    "Create a reminder, timer, scheduled task or monitor. "
                    "Reminders: 'in N minutes' uses delay_minutes; a stated time uses run_at in local time "
                    "('remind me at 9am on 8 October' -> run_at='2026-10-08T09:00'), never minutes worked out "
                    "from now; add notify_only=true for a plain notification. To move a reminder, use "
                    "task_update with run_at instead of deleting and re-creating it. "
                    "Recurring work: a schedule plus prompts. A task that should do something (check the "
                    "weather, search news, read email) needs prompts; notify_only is only for passive "
                    "reminders. Monitoring ('check X and tell me when Y', 'alert me if W changes'): "
                    "schedule='interval_minutes:M', prompts, persistent_thread=true, and a prompt that ends "
                    "it with task_update(task_id='{{task_id}}', enabled=false) once done. "
                    "Branching, approvals or notifications: steps instead of prompts."
                ),
                args_schema=_TaskCreateInput,
            ),
            StructuredTool.from_function(
                func=_task_list,
                name="task_list",
                description="List tasks with their ids, schedules and last runs.",
                args_schema=_TaskListInput,
            ),
            StructuredTool.from_function(
                func=_task_run_now,
                name="task_run_now",
                description="Run a task now, whatever its schedule.",
                args_schema=_TaskRunNowInput,
            ),
            StructuredTool.from_function(
                func=_task_delete,
                name="task_delete",
                description="Delete a task permanently; the person confirms first.",
                args_schema=_TaskDeleteInput,
            ),
            StructuredTool.from_function(
                func=_task_update,
                name="task_update",
                description="Change a task; give only the fields to change.",
                args_schema=_TaskUpdateInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return "Use task_create, task_list, task_run_now, task_delete, or task_update."


registry.register(TaskTool())
