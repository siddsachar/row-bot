"""Shared Goal Mode UI for chat-like surfaces."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from nicegui import context, ui

from row_bot.ui.state import AppState, P

logger = logging.getLogger(__name__)


def _goal_status_color(status: str) -> str:
    return {
        "active": "primary",
        "queued": "grey-6",
        "running": "primary",
        "waiting_approval": "warning",
        "waiting_user": "warning",
        "paused": "amber",
        "completed": "positive",
        "failed": "negative",
        "blocked": "negative",
        "stopped": "orange",
        "cleared": "grey-7",
    }.get(str(status or ""), "grey-6")


def _shorten(text: Any, limit: int = 140) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


async def _call_send_message(send_message: Callable[..., Any], text: str) -> None:
    try:
        result = send_message(text, internal_goal_continuation=True)
    except TypeError:
        result = send_message(text)
    if inspect.isawaitable(result):
        await result


def _schedule_goal_continuation(
    send_message: Callable[..., Any] | None,
    prompt: str,
) -> None:
    if not send_message or not prompt:
        return
    asyncio.create_task(_call_send_message(send_message, prompt))


def _set_composer_text(p: P, value: str) -> bool:
    chat_input = getattr(p, "chat_input", None)
    if not chat_input:
        return False
    try:
        chat_input.value = value
        chat_input.update()
        try:
            chat_input.run_method("focus")
        except Exception:
            pass
        return True
    except Exception:
        logger.debug("Could not seed Goal edit text", exc_info=True)
        return False


def _render_list(items: list[Any], *, empty: str) -> None:
    if not items:
        ui.label(empty).classes("text-xs text-grey-6")
        return
    with ui.column().classes("w-full gap-1"):
        for item in items:
            if isinstance(item, str):
                text = item
            else:
                try:
                    text = json.dumps(item, sort_keys=True)
                except TypeError:
                    text = str(item)
            ui.label(_shorten(text, 220)).classes("text-xs text-grey-4").style(
                "white-space: normal;"
            )


def _open_goal_detail_dialog(
    thread_id: str,
    *,
    rebuild_main: Callable[..., None],
    send_message: Callable[..., Any] | None = None,
) -> None:
    try:
        from row_bot import goals

        goal = goals.get_current_goal(thread_id, include_terminal=True)
    except Exception as exc:
        logger.debug("Could not load Goal detail", exc_info=True)
        ui.notify(f"Could not load goal: {exc}", type="negative", close_button=True)
        return
    if not goal:
        ui.notify("No goal is active for this thread.", type="info")
        return

    def _refresh_after(action: Callable[[], Any]) -> None:
        try:
            action()
            try:
                rebuild_main()
            except TypeError:
                rebuild_main()
        except Exception as exc:
            ui.notify(f"Could not update goal: {exc}", type="negative", close_button=True)

    def _resume_from_detail() -> None:
        try:
            resumed = goals.resume_goal(thread_id)
            try:
                rebuild_main()
            except TypeError:
                rebuild_main()
            if resumed:
                _schedule_goal_continuation(
                    send_message,
                    goals.build_continuation_prompt(resumed),
                )
        except Exception as exc:
            ui.notify(f"Could not resume goal: {exc}", type="negative", close_button=True)

    active_run_id = str(goal.get("active_run_id") or "")
    child_runs: list[dict[str, Any]] = []
    goal_events: list[dict[str, Any]] = []
    pending_approvals: list[dict[str, Any]] = []
    try:
        from row_bot.agent_runs import get_agent_events, list_agent_runs

        child_runs = list_agent_runs(parent_thread_id=thread_id, kind="subagent", limit=8)
        if active_run_id:
            goal_events = get_agent_events(active_run_id, limit=12)
    except Exception:
        logger.debug("Could not load Goal dependencies/events", exc_info=True)
    try:
        from row_bot.tasks import get_pending_approvals

        approvals = get_pending_approvals()
        for approval in approvals:
            if str(approval.get("source_thread_id") or "") == thread_id:
                pending_approvals.append(approval)
            elif active_run_id and str(approval.get("agent_run_id") or "") == active_run_id:
                pending_approvals.append(approval)
    except Exception:
        logger.debug("Could not load Goal approvals", exc_info=True)

    status = str(goal.get("status") or "unknown")
    turns_used = int(goal.get("turns_used") or 0)
    max_turns = int(goal.get("max_turns") or 0)
    objective = str(goal.get("objective") or "Goal")
    evidence = list(goal.get("evidence_json") or [])
    blockers = list(goal.get("blockers_json") or [])

    # Mount outside the Goal strip subtree. The strip is refreshed on a timer,
    # and clearing that subtree would otherwise close the detail dialog.
    with context.client.content:
        with ui.dialog() as dialog, ui.card().classes("q-pa-md").style(
            "width: min(760px, 96vw); max-height: 86vh; overflow-y: auto;"
        ):
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon("flag").classes("text-primary")
                with ui.column().classes("gap-0").style("flex: 1; min-width: 0;"):
                    ui.label("Goal").classes("text-h6")
                    ui.label(_shorten(objective, 180)).classes("text-sm text-grey-4").style(
                        "white-space: normal;"
                    )
                ui.badge(status, color=_goal_status_color(status)).props("outline")
                ui.button(icon="close", on_click=dialog.close).props("flat dense round")

            with ui.row().classes("w-full flex-wrap gap-2 q-mt-sm"):
                ui.badge(f"{turns_used}/{max_turns} turns", color="grey-7").props("outline")
                if active_run_id:
                    ui.badge(f"run {active_run_id[:8]}", color="grey-7").props("outline")

            progress = _shorten(goal.get("last_progress"), 260)
            reason = _shorten(goal.get("last_reason"), 260)
            if progress or reason:
                with ui.column().classes("w-full gap-1 q-mt-sm"):
                    if progress:
                        ui.label("Progress").classes("text-xs text-weight-bold text-grey-5")
                        ui.label(progress).classes("text-sm").style("white-space: normal;")
                    if reason:
                        ui.label("Verifier Reason").classes("text-xs text-weight-bold text-grey-5")
                        ui.label(reason).classes("text-sm").style("white-space: normal;")

            with ui.expansion("Evidence", icon="fact_check").classes("w-full q-mt-sm").props("dense default-opened"):
                _render_list(evidence[-8:], empty="No evidence recorded yet.")

            with ui.expansion("Blockers", icon="report_problem").classes("w-full").props("dense"):
                _render_list(blockers[-5:], empty="No blockers recorded.")

            with ui.expansion("Pending Approvals", icon="approval").classes("w-full").props("dense"):
                if not pending_approvals:
                    ui.label("No pending approvals for this goal.").classes("text-xs text-grey-6")
                for approval in pending_approvals:
                    label = str(approval.get("source_label") or approval.get("label") or "Approval")
                    message = str(approval.get("message") or "")
                    ui.label(_shorten(f"{label}: {message}", 220)).classes("text-xs text-grey-4")

            with ui.expansion("Child-Agent Dependencies", icon="hub").classes("w-full").props("dense"):
                if not child_runs:
                    ui.label("No child agents linked to this thread.").classes("text-xs text-grey-6")
                for run in child_runs:
                    name = str(run.get("display_name") or run.get("id") or "Agent")
                    run_status = str(run.get("status") or "unknown")
                    summary = str(run.get("summary") or run.get("status_message") or "")
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.badge(run_status, color=_goal_status_color(run_status)).props("outline dense")
                        ui.label(name).classes("text-xs ellipsis").style("flex: 1; min-width: 0;")
                        if summary:
                            ui.label(_shorten(summary, 80)).classes("text-xs text-grey-6 ellipsis")

            with ui.expansion("Current Goal Events", icon="timeline").classes("w-full").props("dense"):
                if not goal_events:
                    ui.label("No goal events recorded yet.").classes("text-xs text-grey-6")
                for event in goal_events[-8:]:
                    event_type = str(event.get("type") or "event")
                    payload = event.get("payload_json") or {}
                    ui.label(_shorten(f"{event_type}: {json.dumps(payload, sort_keys=True)}", 220)).classes(
                        "text-xs text-grey-4"
                    )

            with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                if status in {"active", "waiting_approval"}:
                    ui.button(
                        "Pause",
                        icon="pause",
                        on_click=lambda: _refresh_after(lambda: goals.pause_goal(thread_id)),
                    ).props("flat dense no-caps color=amber")
                if status in {"paused", "blocked"}:
                    ui.button("Resume", icon="play_arrow", on_click=_resume_from_detail).props(
                        "flat dense no-caps color=positive"
                    )
                if status in {"active", "paused", "waiting_approval", "blocked"}:
                    ui.button(
                        "Mark Done",
                        icon="done",
                        on_click=lambda: _refresh_after(
                            lambda: goals.complete_goal(thread_id, reason="Marked complete from Goal detail.")
                        ),
                    ).props("flat dense no-caps color=positive")
                ui.button(
                    "Clear",
                    icon="clear",
                    on_click=lambda: _refresh_after(lambda: goals.clear_goal(thread_id)),
                ).props("flat dense no-caps color=negative")
        dialog.open()


def build_goal_progress_panel(
    state: AppState,
    p: P,
    *,
    rebuild_main: Callable[..., None],
    send_message: Callable[..., Any] | None = None,
    surface: str = "chat",
) -> str | None:
    """Render the current thread-bound Goal controls for any chat surface."""

    if not state.thread_id:
        return None
    try:
        from row_bot import goals

        goal = goals.get_current_goal(state.thread_id, include_terminal=True)
    except Exception:
        logger.debug("Could not load Goal Mode status for %s", surface, exc_info=True)
        return None
    if not goal:
        return None
    status = str(goal.get("status") or "")
    if status == "cleared":
        return None

    objective = str(goal.get("objective") or "Goal")
    turns_used = int(goal.get("turns_used") or 0)
    max_turns = int(goal.get("max_turns") or 0)
    detail = _shorten(goal.get("last_progress") or goal.get("last_reason"), 140)
    thread_id = str(state.thread_id or "")

    def _edit_goal() -> None:
        if not _set_composer_text(p, f"/goal {objective}".strip()):
            ui.notify("Use /goal <objective> to replace the current goal.", type="info")

    def _pause_goal() -> None:
        try:
            goals.pause_goal(thread_id)
            rebuild_main()
        except Exception as exc:
            ui.notify(f"Could not pause goal: {exc}", type="negative", close_button=True)

    def _resume_goal() -> None:
        try:
            resumed = goals.resume_goal(thread_id)
            rebuild_main()
            if resumed:
                _schedule_goal_continuation(
                    send_message,
                    goals.build_continuation_prompt(resumed),
                )
        except Exception as exc:
            ui.notify(f"Could not resume goal: {exc}", type="negative", close_button=True)

    def _complete_goal() -> None:
        try:
            goals.complete_goal(thread_id, reason="Marked complete from Goal strip.")
            rebuild_main()
        except Exception as exc:
            ui.notify(f"Could not mark goal done: {exc}", type="negative", close_button=True)

    def _clear_goal() -> None:
        try:
            goals.clear_goal(thread_id)
            rebuild_main()
        except Exception as exc:
            ui.notify(f"Could not clear goal: {exc}", type="negative", close_button=True)

    with ui.column().classes(
        f"w-full gap-0 row-bot-goal-panel row-bot-goal-panel-{surface}"
    ).style("padding: 0 12px 3px 12px;"):
        with ui.row().classes("w-full items-center no-wrap gap-2").style(
            "border: 1px solid rgba(59, 130, 246, 0.30); "
            "border-radius: 12px 12px 8px 8px; padding: 6px 9px; "
            "background: rgba(59, 130, 246, 0.055); "
            "margin: 0 6px -1px 6px;"
        ):
            ui.icon("flag", size="xs").classes("text-primary")
            ui.badge(status, color=_goal_status_color(status)).props("outline dense")
            ui.label(objective).classes("text-xs ellipsis").style("flex: 1; min-width: 0;")
            ui.label(f"{turns_used}/{max_turns} turns").classes("text-xs text-grey-6 no-wrap")
            if detail:
                ui.label(detail).classes("text-xs text-grey-7 ellipsis").style("max-width: 240px;")
            if status in {"active", "waiting_approval"}:
                ui.button(icon="pause", on_click=_pause_goal).props(
                    "flat dense round size=xs color=amber"
                ).tooltip("Pause goal")
            if status in {"paused", "blocked"}:
                ui.button(icon="play_arrow", on_click=_resume_goal).props(
                    "flat dense round size=xs color=positive"
                ).tooltip("Resume goal")
            if status in {"active", "paused", "waiting_approval", "blocked"}:
                ui.button(icon="done", on_click=_complete_goal).props(
                    "flat dense round size=xs color=positive"
                ).tooltip("Mark goal done")
            ui.button(icon="info", on_click=lambda: _open_goal_detail_dialog(
                thread_id,
                rebuild_main=rebuild_main,
                send_message=send_message,
            )).props("flat dense round size=xs").tooltip("Open goal details")
            ui.button(icon="edit", on_click=_edit_goal).props(
                "flat dense round size=xs"
            ).tooltip("Edit goal")
            ui.button(icon="clear", on_click=_clear_goal).props(
                "flat dense round size=xs color=negative"
            ).tooltip("Clear goal")
    return status
