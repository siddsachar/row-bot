"""Goal Mode tool for structured progress updates."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, field_validator

from row_bot import goals
from row_bot.tools import registry
from row_bot.tools.base import BaseTool


def _json_response(payload: dict[str, Any]) -> str:
    from row_bot.tool_outputs import compact_json

    return compact_json(payload, sort_keys=True, drop_empty_fields=False)


def _runtime_context() -> dict[str, Any]:
    try:
        from row_bot.agent import get_active_runtime_context

        return get_active_runtime_context()
    except Exception:
        return {}


def _public_goal(goal: dict[str, Any] | None) -> dict[str, Any]:
    if not goal:
        return {}
    return {
        "id": goal.get("id", ""),
        "thread_id": goal.get("thread_id", ""),
        "objective": goal.get("objective", ""),
        "status": goal.get("status", ""),
        "turns_used": goal.get("turns_used", 0),
        "max_turns": goal.get("max_turns", 0),
        "last_verdict": goal.get("last_verdict", ""),
        "last_reason": goal.get("last_reason", ""),
        "last_progress": goal.get("last_progress", ""),
        "evidence": goal.get("evidence_json", []),
        "blockers": goal.get("blockers_json", []),
        "active_run_id": goal.get("active_run_id", ""),
        # What done means and the person's check (B3); read-only here: only the person sets them.
        **({"done_means": goal["done_means"]} if goal.get("done_means") else {}),
        **({"check": {"command": goal["check_command"], "state": goal.get("check_state", "")}}
           if goal.get("check_command") else {}),
    }


# The conversation is not in these schemas: goal tools act on the running turn's conversation.
class _GoalUpdateInput(BaseModel):
    status: Literal["active", "blocked", "waiting_approval", "completed", "cleared"] = Field(
        default="active",
        description=(
            "Use blocked when the goal waits on the person (you asked them something); it shows as Needs you. "
            "Only the person pauses a goal."
        ),
    )
    progress: str = Field(default="", description="Concise progress update.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence that progress or completion is real.",
    )
    blockers: list[str] = Field(
        default_factory=list,
        description="Current blockers or user decisions needed.",
    )
    next_step: str = Field(default="", description="Next action planned for the goal.")
    goal_id: str = Field(default="", description="Optional goal id. Omit to update the current thread goal.")

    @field_validator("status", mode="before")
    @classmethod
    def _paused_means_blocked(cls, value: Any) -> Any:
        # An agent waiting on the person is blocked on them ("Needs you"), never quietly paused (B316).
        return "blocked" if str(value or "").strip().lower() == "paused" else value

    @field_validator("evidence", "blockers", mode="before")
    @classmethod
    def _normalize_text_items(cls, value: Any) -> list[str]:
        """Keep legacy rich entries while advertising typed array items.

        Gemini requires every array parameter to declare an item schema.  Older
        callers could still have supplied JSON-compatible objects because these
        fields previously used ``list[Any]``; encode those entries
        deterministically instead of rejecting or losing them.
        """
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set, frozenset)):
            value = [value]
        if isinstance(value, (set, frozenset)):
            value = sorted(value, key=lambda item: repr(item))
        return [
            item
            if isinstance(item, str)
            else json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            for item in value
        ]


class _GoalStatusInput(BaseModel):
    goal_id: str = Field(default="", description="Optional goal id.")


def _goal_update(
    status: str = "active",
    progress: str = "",
    evidence: list[Any] | None = None,
    blockers: list[Any] | None = None,
    next_step: str = "",
    goal_id: str = "",
    thread_id: str = "",
) -> str:
    runtime = _runtime_context()
    thread_id = thread_id or str(runtime.get("thread_id") or "")
    if str(status or "").strip().lower() == "paused":
        # An agent that stops to wait for the person's answer is blocked on them ("Needs you"), never quietly
        # paused (B316).
        status = "blocked"
    try:
        goal = goals.update_goal_progress(
            thread_id=thread_id,
            goal_id=goal_id,
            status=status,
            progress=progress,
            evidence=evidence or [],
            blockers=blockers or [],
            next_step=next_step,
        )
        if status == "completed" and goal.get("check_state") == "pending":
            # The person's check decides whether it is done (B3).
            return _json_response({
                "ok": True, "goal": _public_goal(goal),
                "note": f"Row-Bot runs `{goal['check_command']}` before this goal counts as done; end your turn.",
            })
        return _json_response({"ok": True, "goal": _public_goal(goal)})
    except Exception as exc:
        return _json_response({"ok": False, "error": str(exc)})


def _goal_status(goal_id: str = "", thread_id: str = "") -> str:
    runtime = _runtime_context()
    thread_id = thread_id or str(runtime.get("thread_id") or "")
    goal = goals.get_goal(goal_id) if goal_id else goals.get_current_goal(thread_id, include_terminal=True)
    return _json_response({"ok": bool(goal), "goal": _public_goal(goal)})


class GoalTool(BaseTool):
    @property
    def name(self) -> str:
        return "goal"

    @property
    def display_name(self) -> str:
        return "Goal"

    @property
    def description(self) -> str:
        return "Update and inspect the current thread's durable Goal Mode state."

    @property
    def enabled_by_default(self) -> bool:
        return True

    def execute(self, query: str) -> str:
        runtime = _runtime_context()
        thread_id = str(runtime.get("thread_id") or "")
        return goals.format_goal_status(thread_id) if thread_id else "No active thread."

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_goal_update,
                name="goal_update",
                description=(
                    "Update the current /goal with progress, evidence, blockers, next step, "
                    "or completion/block status."
                ),
                args_schema=_GoalUpdateInput,
            ),
            StructuredTool.from_function(
                func=_goal_status,
                name="goal_status",
                description="Inspect the current /goal status.",
                args_schema=_GoalStatusInput,
            ),
        ]


registry.register(GoalTool())
