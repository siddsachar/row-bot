from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _fresh_thread_action_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / "data"))
    for name in [
        "threads",
        "designer.state",
        "designer.storage",
        "ui.thread_actions",
    ]:
        sys.modules.pop(name, None)
    import row_bot.threads as threads
    import row_bot.ui.thread_actions as thread_actions

    return importlib.reload(threads), importlib.reload(thread_actions)


def test_apply_thread_rename_updates_active_state_and_manual_source(tmp_path, monkeypatch):
    threads, thread_actions = _fresh_thread_action_modules(tmp_path, monkeypatch)
    tid = threads.create_thread("Thread Jun 03, 20:45")
    state = SimpleNamespace(thread_id=tid, thread_name="Thread Jun 03, 20:45")

    saved_name = thread_actions.apply_thread_rename(tid, "  Better title  ", state=state)

    assert saved_name == "Better title"
    assert state.thread_name == "Better title"
    assert threads.get_thread_name(tid) == "Better title"
    assert threads.get_thread_name_source(tid) == "manual"


def test_apply_thread_rename_routes_designer_thread_to_project_name(tmp_path, monkeypatch):
    threads, thread_actions = _fresh_thread_action_modules(tmp_path, monkeypatch)
    from row_bot.designer.state import DesignerProject
    from row_bot.designer.storage import load_project, save_project

    project = DesignerProject(id="project_1", name="Old name", thread_id="designer_thread")
    save_project(project)
    threads.create_thread("Design thread", thread_id="designer_thread", project_id=project.id)
    state = SimpleNamespace(thread_id="designer_thread", thread_name="Design thread", active_designer_project=project)

    saved_name = thread_actions.apply_thread_rename("designer_thread", "New name", state=state)

    assert saved_name.endswith("New name")
    assert state.thread_name.endswith("New name")
    assert state.active_designer_project.name == "New name"
    assert load_project(project.id).name == "New name"
    assert threads.get_thread_name_source("designer_thread") == "manual"


def test_rename_affordances_are_wired_into_sidebar_chat_and_developer_header():
    sidebar_source = (ROOT / "ui" / "sidebar.py").read_text(encoding="utf-8")
    chat_source = (ROOT / "ui" / "chat.py").read_text(encoding="utf-8")
    developer_source = (ROOT / "developer" / "ui.py").read_text(encoding="utf-8")

    assert "show_rename_thread_dialog" in sidebar_source
    assert "ui.menu_item(\"Rename\"" in sidebar_source
    assert "icon=\"more_vert\"" in sidebar_source
    assert "show_rename_thread_dialog" in chat_source
    assert ".tooltip(\"Rename\")" in chat_source
    assert "def _show_current_thread_rename" in developer_source
    assert "show_rename_thread_dialog" in developer_source
