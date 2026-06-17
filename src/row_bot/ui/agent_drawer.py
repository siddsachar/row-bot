"""Shared parent-thread Agent run drawer for chat-like surfaces."""

from __future__ import annotations

import logging
from typing import Callable

from nicegui import ui

from row_bot.ui.render import open_agent_peek_dialog
from row_bot.ui.state import AppState, P, _active_generations

logger = logging.getLogger(__name__)


_TERMINAL_STATUSES = {
    "completed",
    "completed_delivery_failed",
    "failed",
    "stopped",
    "blocked",
    "cancelled",
    "timed_out",
}


def _agent_status_color(status: str) -> str:
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


def _open_agent_thread(
    agent_run: dict,
    *,
    state: AppState,
    p: P,
    rebuild_main: Callable[..., None],
    rebuild_thread_list: Callable[[], None] | None = None,
) -> None:
    thread_id = str(agent_run.get("thread_id") or "").strip()
    if not thread_id:
        ui.notify("This Agent run has no child thread.", type="warning", close_button=True)
        return
    try:
        from row_bot.memory_extraction import set_active_thread
        from row_bot.threads import (
            _get_thread_approval_mode,
            _get_thread_model_override,
            get_thread_name,
        )
        from row_bot.ui.helpers import load_thread_messages
        from row_bot.ui.voice_lifecycle import stop_voice_for_thread_change

        prev = state.thread_id
        prev_gen = _active_generations.get(prev) if prev else None
        if prev_gen and str(getattr(prev_gen, "status", "")) == "streaming":
            from row_bot.ui.streaming import _detach_generation

            _detach_generation(prev_gen, state, "open_agent_child_thread")
        stop_voice_for_thread_change(state, p, reason="open_agent_child_thread")
        state.active_designer_project = None
        state.active_developer_workspace_id = None
        state.thread_id = thread_id
        state.thread_name = get_thread_name(thread_id) or str(
            agent_run.get("display_name") or "Agent"
        )
        state.thread_model_override = _get_thread_model_override(thread_id)
        state.thread_approval_mode = _get_thread_approval_mode(thread_id)
        state.messages = load_thread_messages(thread_id)
        try:
            p.pending_files.clear()
        except Exception:
            pass
        set_active_thread(thread_id, previous_id=prev)
        rebuild_main()
        if rebuild_thread_list is not None:
            rebuild_thread_list()
    except Exception as exc:
        logger.debug("Could not open Agent child thread", exc_info=True)
        ui.notify(f"Could not open Agent thread: {exc}", type="negative", close_button=True)


def build_parent_agent_drawer(
    state: AppState,
    p: P,
    *,
    rebuild_main: Callable[..., None],
    rebuild_thread_list: Callable[[], None] | None = None,
    limit: int = 6,
) -> None:
    """Render the current parent thread's Agent runs with peek and full-thread actions."""

    if not state.thread_id:
        return
    try:
        from row_bot.agent_runs import (
            get_agent_parent_messages,
            list_agent_runs,
            stop_agent_run,
        )

        runs = list_agent_runs(parent_thread_id=state.thread_id, kind="subagent", limit=limit)
    except Exception:
        logger.debug("Could not load parent Agent Runs", exc_info=True)
        return
    if not runs:
        return

    with ui.column().classes("w-full gap-1 q-px-md q-pb-xs row-bot-parent-agent-drawer"):
        with ui.row().classes("w-full items-center gap-2").style(
            "border: 1px solid rgba(148, 163, 184, 0.22); "
            "border-radius: 8px; padding: 6px 8px; "
            "background: rgba(148, 163, 184, 0.045);"
        ):
            ui.icon("hub", size="xs").classes("text-primary")
            ui.label("Agents").classes("text-xs font-bold text-grey-5")
            ui.space()
            ui.label(f"{len(runs)} recent").classes("text-xs text-grey-7")

        for agent_run in runs[:4]:
            run_id = str(agent_run.get("id") or "")
            child_thread_id = str(agent_run.get("thread_id") or "")
            name = str(agent_run.get("display_name") or run_id or "Agent")
            status = str(agent_run.get("status") or "unknown")
            profile = str(
                agent_run.get("profile_display_name")
                or agent_run.get("profile_slug")
                or "Agent"
            )
            message = str(
                agent_run.get("status_message")
                or agent_run.get("summary")
                or agent_run.get("error")
                or ""
            )
            try:
                parent_notes = get_agent_parent_messages(run_id, limit=3)
            except Exception:
                logger.debug("Could not load Agent parent messages", exc_info=True)
                parent_notes = []
            if parent_notes:
                latest_note = str(parent_notes[-1])
                note_preview = latest_note if len(latest_note) <= 80 else latest_note[:79].rstrip() + "..."
                message = f"Note queued: {note_preview}"

            with ui.row().classes("w-full items-center no-wrap gap-2 q-px-md").style(
                "min-height: 30px;"
            ):
                ui.badge(status, color=_agent_status_color(status)).props("outline dense")
                ui.label(name).classes("text-xs font-medium ellipsis").style("flex: 1; min-width: 0;")
                ui.label(profile).classes("text-xs text-grey-6 ellipsis").style("max-width: 120px;")
                if message:
                    ui.label(message).classes("text-xs text-grey-7 ellipsis").style("max-width: 180px;")
                if run_id:
                    ui.button(
                        icon="visibility",
                        on_click=lambda rid=run_id: open_agent_peek_dialog(rid),
                    ).props("flat dense round size=xs").tooltip("Peek Agent activity")
                if child_thread_id:
                    ui.button(
                        icon="open_in_new",
                        on_click=lambda row=agent_run: _open_agent_thread(
                            row,
                            state=state,
                            p=p,
                            rebuild_main=rebuild_main,
                            rebuild_thread_list=rebuild_thread_list,
                        ),
                    ).props("flat dense round size=xs").tooltip("Open full Agent thread")
                if status not in _TERMINAL_STATUSES:
                    ui.button(
                        icon="stop",
                        on_click=lambda rid=run_id: (stop_agent_run(rid), rebuild_main()),
                    ).props("flat dense round size=xs color=orange").tooltip("Stop Agent")
