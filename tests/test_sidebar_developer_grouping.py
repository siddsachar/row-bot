from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_groups_developer_threads_under_expandable_workspace_rows():
    source = (ROOT / "ui" / "sidebar.py").read_text(encoding="utf-8")
    list_block = source.split("def _rebuild_thread_list()", 1)[1].split(
        "if len(threads) > SIDEBAR_MAX_THREADS:",
        1,
    )[0]

    assert "_SIDEBAR_DEV_EXPANDED" in source
    assert "_ensure_sidebar_dev_state_loaded" in source
    assert "_persist_sidebar_dev_expanded" in source
    assert "def _sidebar_display_items" in list_block
    assert 'filter_key not in {"all", "code"}' in list_block
    assert "top_workspace_id" in list_block
    assert "not _SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE" in list_block
    assert "_SIDEBAR_DEV_EXPANDED.add(top_workspace_id)" in list_block
    assert 'items.append(("developer_group", group, cat, False))' in list_block
    assert "if dev_ws in _SIDEBAR_DEV_EXPANDED" in list_block
    assert '"folder_open" if is_expanded else "folder"' in list_block
    assert "def _toggle_workspace" in list_block
    assert "developer-thread-child" in list_block


def test_sidebar_developer_grouping_preserves_thread_action_menu_on_children():
    source = (ROOT / "ui" / "sidebar.py").read_text(encoding="utf-8")
    list_block = source.split("def _rebuild_thread_list()", 1)[1].split(
        "if len(threads) > SIDEBAR_MAX_THREADS:",
        1,
    )[0]

    assert "show_rename_thread_dialog" in list_block
    assert 'ui.menu_item("Rename"' in list_block
    assert 'ui.menu_item("Delete"' in list_block
    assert "is_developer_child" in list_block


def test_all_conversations_modal_reuses_developer_workspace_grouping():
    source = (ROOT / "ui" / "sidebar.py").read_text(encoding="utf-8")
    modal_block = source.split("def _show_all():", 1)[1].split(
        "def _do_bulk_delete",
        1,
    )[0]

    assert "_sidebar_display_items(_filtered, _MODAL_FILTER)" in modal_block
    assert 'if item_kind == "developer_group"' in modal_block
    assert '"folder_open" if is_expanded else "folder"' in modal_block
    assert "def _toggle_workspace" in modal_block
    assert "developer-thread-child" in modal_block
    assert 'ui.icon("code" if is_developer_child else "chat_bubble_outline", size="xs")' in modal_block
    assert "show_rename_thread_dialog" in modal_block


def test_all_conversations_modal_folder_toggle_does_not_rebuild_sidebar():
    source = (ROOT / "ui" / "sidebar.py").read_text(encoding="utf-8")
    modal_block = source.split("def _show_all():", 1)[1].split(
        "caption_bits = [",
        1,
    )[0]
    toggle_block = modal_block.split("def _toggle_workspace", 1)[1]

    assert "_rebuild_dialog_list()" in toggle_block
    assert "_rebuild_thread_list_ref[0]()" not in toggle_block
    assert "_persist_sidebar_dev_expanded()" in toggle_block


def test_sidebar_developer_expanded_state_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path))
    sys.modules.pop("ui.sidebar", None)
    import row_bot.ui.sidebar as sidebar

    sidebar = importlib.reload(sidebar)
    state_file = tmp_path / "sidebar_state.json"
    state_file.write_text(json.dumps({"other_sidebar_state": "keep"}), encoding="utf-8")

    sidebar._ensure_sidebar_dev_state_loaded()

    assert sidebar._SIDEBAR_DEV_EXPANDED == set()
    assert sidebar._SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE is False

    sidebar._SIDEBAR_DEV_EXPANDED = {"dev_a", "dev_b"}
    sidebar._SIDEBAR_DEV_EXPANDED_LOADED = True

    sidebar._persist_sidebar_dev_expanded()

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["other_sidebar_state"] == "keep"
    assert saved["developer_workspace_expanded"] == ["dev_a", "dev_b"]

    sidebar._SIDEBAR_DEV_EXPANDED = set()
    sidebar._SIDEBAR_DEV_EXPANDED_LOADED = False
    sidebar._SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE = False
    sidebar._ensure_sidebar_dev_state_loaded()

    assert sidebar._SIDEBAR_DEV_EXPANDED == {"dev_a", "dev_b"}
    assert sidebar._SIDEBAR_DEV_EXPANDED_HAS_SAVED_STATE is True
