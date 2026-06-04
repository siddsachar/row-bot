"""Designer session state and shared mutation tracking."""

from __future__ import annotations

import logging

from row_bot.designer.history import UndoStack, snapshot
from row_bot.designer.state import DesignerProject

logger = logging.getLogger(__name__)

_active_projects_by_key: dict[str, DesignerProject] = {}
_undo_stacks_by_key: dict[str, UndoStack] = {}
_ui_active_key: str | None = None


def _project_key(project: DesignerProject) -> str:
    """Return the lookup key for a designer project."""

    if project.thread_id:
        return project.thread_id
    return f"project:{project.id}"


def _get_execution_key() -> str | None:
    """Return the current execution thread key when available."""

    try:
        from row_bot.agent import get_current_thread_id

        thread_id = get_current_thread_id()
    except Exception:
        return None
    if thread_id and thread_id in _active_projects_by_key:
        return thread_id
    return None


def _clear_agent_cache() -> None:
    """Drop cached graphs after the visible designer binding changes."""

    try:
        from row_bot.agent import clear_agent_cache

        clear_agent_cache()
    except Exception:
        logger.debug("Failed to clear agent cache after designer session change", exc_info=True)


def set_active_project(project: DesignerProject | None) -> None:
    """Called by the UI when entering or leaving the designer editor."""

    global _ui_active_key
    prev_key = _ui_active_key
    next_key = _project_key(project) if project is not None else None

    if project is not None:
        _active_projects_by_key[next_key] = project
        _undo_stacks_by_key.setdefault(next_key, UndoStack())
        _ui_active_key = next_key
    else:
        _ui_active_key = None

    if prev_key != _ui_active_key:
        _clear_agent_cache()


def get_ui_active_project() -> DesignerProject | None:
    """Return the project currently bound to the visible designer UI."""

    if _ui_active_key is None:
        return None
    return _active_projects_by_key.get(_ui_active_key)


def get_active_project() -> DesignerProject | None:
    """Return the active designer project for the current context."""

    execution_key = _get_execution_key()
    if execution_key is not None:
        return _active_projects_by_key.get(execution_key)
    return get_ui_active_project()


def get_undo_stack() -> UndoStack | None:
    """Return the undo stack for the current project context, if any."""

    execution_key = _get_execution_key()
    if execution_key is not None:
        return _undo_stacks_by_key.get(execution_key)
    if _ui_active_key is None:
        return None
    return _undo_stacks_by_key.get(_ui_active_key)


def prepare_project_mutation(project: DesignerProject, label: str = "",
                              *, author: str = "user") -> None:
    """Capture undo state and save a persistent snapshot before a mutation.

    ``author`` should be ``"agent"`` when the mutation originates from a
    designer-agent tool call; it defaults to ``"user"`` for UI actions.
    """

    stack = _undo_stacks_by_key.setdefault(_project_key(project), UndoStack())
    stack.push(project)
    try:
        snapshot(project, label=label, author=author)
    except Exception:
        logger.debug("Failed to save snapshot before mutation", exc_info=True)