"""Shared parent-thread messages for Agent Run lifecycle updates."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


TERMINAL_STATUSES = {
    "completed",
    "completed_delivery_failed",
    "failed",
    "stopped",
    "blocked",
    "timed_out",
    "cancelled",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def agent_run_terminal_summary(run_row: Mapping[str, Any] | None) -> str:
    """Return the user-visible terminal summary for child Agent runs."""

    if not run_row:
        return ""
    kind = str(run_row.get("kind") or "subagent").strip().lower()
    if kind == "goal":
        return goal_run_terminal_summary(run_row)
    status = str(run_row.get("status") or "").strip().lower()
    if status not in TERMINAL_STATUSES:
        return ""
    name = str(run_row.get("display_name") or run_row.get("id") or "Agent").strip()
    detail = str(
        run_row.get("summary")
        or run_row.get("status_message")
        or run_row.get("error")
        or ""
    ).strip()
    if status in {"completed", "completed_delivery_failed"}:
        prefix = f"Done. {name} completed."
    elif status == "stopped":
        prefix = f"{name} was stopped."
    elif status == "cancelled":
        prefix = f"{name} was cancelled."
    elif status == "timed_out":
        prefix = f"{name} timed out."
    elif status == "blocked":
        prefix = f"{name} is blocked."
    else:
        prefix = f"{name} failed."
    if detail:
        if detail.lower().startswith(prefix.lower()):
            return detail
        return f"{prefix}\n\n{detail}"
    return prefix


def goal_run_terminal_summary(run_row: Mapping[str, Any] | None) -> str:
    """Return a compact terminal notice for channel-started Goal Mode runs."""

    if not run_row:
        return ""
    status = str(run_row.get("status") or "").strip().lower()
    if status not in TERMINAL_STATUSES:
        return ""
    objective = _clean_text(run_row.get("prompt") or run_row.get("display_name") or "Goal")
    if objective.lower().startswith("goal:"):
        objective = objective.split(":", 1)[1].strip() or "Goal"
    if len(objective) > 180:
        objective = objective[:177].rstrip() + "..."

    if status in {"completed", "completed_delivery_failed"}:
        return f"Goal completed: {objective}"
    detail = _clean_text(run_row.get("error") or run_row.get("status_message") or "")
    if status == "blocked":
        prefix = f"Goal blocked: {objective}"
    elif status == "stopped":
        prefix = f"Goal stopped: {objective}"
    elif status == "cancelled":
        prefix = f"Goal cancelled: {objective}"
    elif status == "timed_out":
        prefix = f"Goal timed out: {objective}"
    else:
        prefix = f"Goal failed: {objective}"
    return f"{prefix}\n\n{detail}" if detail else prefix


def terminal_notification_key(run_row: Mapping[str, Any]) -> str:
    """Return the durable notification key for a terminal run."""

    run_id = str(run_row.get("id") or "").strip()
    kind = str(run_row.get("kind") or "").strip().lower()
    if kind == "goal":
        goal_id = str(run_row.get("goal_id") or run_id).strip()
        status = str(run_row.get("status") or "").strip().lower()
        return f"goal_terminal:{goal_id}:{status}"
    return f"agent_run_terminal:{run_id}"


def terminal_ui_metadata(run_row: Mapping[str, Any], *, key: str = "") -> dict[str, Any]:
    """Return checkpoint metadata for a terminal parent-thread message."""

    run_id = str(run_row.get("id") or "").strip()
    kind = str(run_row.get("kind") or "").strip().lower()
    metadata: dict[str, Any] = {
        "timestamp": datetime.now().strftime("%H:%M"),
        "channel_notification_key": key or terminal_notification_key(run_row),
    }
    if kind == "goal":
        metadata["goal_completion_for"] = str(run_row.get("goal_id") or run_id)
        metadata["goal_run_id"] = run_id
        metadata["goal_status"] = str(run_row.get("status") or "")
    else:
        metadata["agent_completion_for"] = run_id
    return metadata


def terminal_ui_message(run_row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a UI message dict for a terminal Agent Run update."""

    summary = agent_run_terminal_summary(run_row)
    if not summary:
        return {}
    key = terminal_notification_key(run_row)
    metadata = terminal_ui_metadata(run_row, key=key)
    message = {
        "role": "assistant",
        "content": summary,
        "timestamp": metadata["timestamp"],
        "channel_notification_key": key,
    }
    message.update({k: v for k, v in metadata.items() if k != "channel_notification_key"})
    return message
