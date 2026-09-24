"""Small, safe view model for generations started by another local client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_ACTIVE_STATUSES = {"admitted", "running", "stopping"}
_ATTENTION_STATUSES = {"failed", "blocked", "cancelled", "uncertain"}


@dataclass(frozen=True)
class LiveProjectionTrace:
    label: str
    status: str


@dataclass(frozen=True)
class LiveProjectionView:
    active: bool
    text: str = ""
    thinking: bool = False
    traces: tuple[LiveProjectionTrace, ...] = ()
    stopping: bool = False


def build_live_projection_view(
    snapshot: dict[str, Any],
    events: Iterable[dict[str, Any]],
) -> LiveProjectionView:
    """Project only bounded, public generation state into legacy UI content."""
    generation = snapshot.get("generation") or {}
    status = str(generation.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        return LiveProjectionView(active=False)

    text_parts: list[str] = []
    for row in snapshot.get("rows") or ():
        if not str(row.get("id") or "").startswith("assistant:live:"):
            continue
        for block in row.get("blocks") or ():
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))

    thinking = False
    traces: dict[str, LiveProjectionTrace] = {}
    for record in events:
        event = record.get("event", record)
        event_type = str(event.get("type") or "")
        payload = event.get("payload") or {}
        if event_type == "generation.activity" and payload.get("state") == "thinking":
            thinking = True
        if event_type != "tool.activity":
            continue
        identity = str(payload.get("tool_call_id") or payload.get("item_id") or "")
        if not identity:
            continue
        trace_status = str(payload.get("status") or "pending")
        prefix = (
            "Using"
            if trace_status == "pending"
            else "Needs attention"
            if trace_status in _ATTENTION_STATUSES
            else "Done"
        )
        name = str(payload.get("group_name") or payload.get("tool_name") or "tool")
        traces[identity] = LiveProjectionTrace(f"{prefix} {name}", trace_status)

    return LiveProjectionView(
        active=True,
        text="".join(text_parts),
        thinking=thinking,
        traces=tuple(traces.values()),
        stopping=status == "stopping",
    )
