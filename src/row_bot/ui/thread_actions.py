"""Shared conversation thread actions for sidebar and headers."""

from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

logger = logging.getLogger(__name__)


def apply_thread_rename(thread_id: str, new_name: str, *, state: Any | None = None) -> str:
    """Rename a normal thread, or the owning Designer project for Designer threads."""
    from row_bot.threads import _get_thread_project_id, rename_thread

    clean_name = str(new_name or "").strip()
    if not clean_name:
        raise ValueError("Thread name cannot be empty.")
    project_id = _get_thread_project_id(thread_id)
    if project_id:
        from row_bot.designer.storage import load_project, save_project

        project = load_project(project_id)
        if project is not None:
            project.name = clean_name
            save_project(project)
            active_project = getattr(state, "active_designer_project", None)
            if active_project is not None and getattr(active_project, "id", "") == project.id:
                active_project.name = project.name
        saved_name = rename_thread(thread_id, f"\U0001f3a8 {clean_name}", source="manual")
    else:
        saved_name = rename_thread(thread_id, clean_name, source="manual")
    if state is not None and getattr(state, "thread_id", None) == thread_id:
        state.thread_name = saved_name
    return saved_name


def apply_thread_pin(thread_id: str, pinned: bool) -> str:
    """Set a conversation pin state and return the stored pin timestamp."""

    from row_bot.threads import set_thread_pinned

    return set_thread_pinned(thread_id, pinned)


def _dialog_initial_name(thread_id: str, current_name: str) -> str:
    try:
        from row_bot.threads import _get_thread_project_id

        project_id = _get_thread_project_id(thread_id)
        if project_id:
            from row_bot.designer.storage import load_project

            project = load_project(project_id)
            if project is not None:
                return project.name
    except Exception:
        logger.debug("Could not load Designer project for thread rename", exc_info=True)
    return str(current_name or "")


def _request_main_rebuild(rebuild_main: Callable[..., None] | None) -> None:
    if rebuild_main is None:
        return
    try:
        rebuild_main(immediate=True, reason="thread_rename")
    except TypeError:
        rebuild_main()


def show_rename_thread_dialog(
    *,
    thread_id: str,
    current_name: str,
    state: Any,
    rebuild_thread_list: Callable[[], None],
    rebuild_main: Callable[..., None] | None = None,
    on_renamed: Callable[[str], None] | None = None,
) -> None:
    """Open a small shared rename dialog for conversation threads."""

    initial_name = _dialog_initial_name(thread_id, current_name)
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "width: min(460px, 92vw); border-radius: 8px;"
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label("Rename conversation").classes("text-h6")
            ui.button(icon="close", on_click=dlg.close).props("flat dense round")
        name_input = ui.input(value=initial_name).props("dense outlined autofocus").classes("w-full")

        def _save() -> None:
            try:
                saved_name = apply_thread_rename(thread_id, str(name_input.value or ""), state=state)
            except ValueError as exc:
                ui.notify(str(exc), type="negative", close_button=True)
                return
            if on_renamed is not None:
                on_renamed(saved_name)
            rebuild_thread_list()
            if getattr(state, "thread_id", None) == thread_id:
                _request_main_rebuild(rebuild_main)
            dlg.close()

        name_input.on("keydown.enter", lambda _e: _save())
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
            ui.button("Save", icon="check", on_click=_save).props("color=primary no-caps")
    dlg.open()
