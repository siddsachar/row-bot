from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_developer_home_opens_latest_workspace_thread():
    source = (ROOT / "developer" / "ui.py").read_text(encoding="utf-8")
    open_block = source.split("async def _open_workspace", 1)[1].split("async def _open_path", 1)[0]

    assert "ensure_latest_workspace_thread" in open_block
    assert "get_thread_name" in open_block
    assert 'state.thread_name = thread_name or f"Developer: {workspace.name}"' in open_block


def test_developer_workspace_has_thread_switcher_and_new_thread_action():
    source = (ROOT / "developer" / "ui.py").read_text(encoding="utf-8")
    workspace_block = source.split("def build_developer_workspace(", 1)[1].split(
        "def _build_developer_inspector(",
        1,
    )[0]

    assert "def _switch_developer_thread" in workspace_block
    assert "def _create_new_developer_thread" in workspace_block
    assert "list_workspace_threads(workspace.id)" in workspace_block
    assert "create_workspace_thread" in workspace_block
    assert "New thread" in workspace_block
    assert 'reason="thread_switch"' in workspace_block
    assert "rebuild_main(immediate=True, reason=\"developer_thread_switch\")" in workspace_block


def test_developer_workspace_header_wraps_controls_without_overlap():
    source = (ROOT / "developer" / "ui.py").read_text(encoding="utf-8")
    workspace_block = source.split("def build_developer_workspace(", 1)[1].split(
        "workspace_header[\"badges\"]",
        1,
    )[0]

    assert 'with ui.row().classes("w-full items-start gap-2").style("flex-wrap: wrap;")' in workspace_block
    assert "flex: 1 1 280px" in workspace_block
    assert "flex: 1 1 520px" in workspace_block
    assert "flex-wrap: wrap;" in workspace_block
    assert "flex: 1 1 220px" in workspace_block
    assert "min-width: min(100%, 180px)" in workspace_block
    assert "w-full items-center justify-between no-wrap" not in workspace_block


def test_app_passes_rebuild_hooks_to_developer_workspace():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    call_block = source[source.index("build_developer_workspace("):source.index("elif state.thread_id is None:")]

    assert "rebuild_main=lambda **kw: _rebuild_main(**kw)" in call_block
    assert "rebuild_thread_list=rebuild_thread_list" in call_block
