from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_developer_home_opens_latest_workspace_thread():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    open_block = source.split("async def _open_workspace", 1)[1].split("async def _open_path", 1)[0]

    assert "ensure_latest_workspace_thread" in open_block
    assert "_get_thread_developer_workspace" in open_block
    assert "get_thread_name" in open_block
    assert "state.active_developer_workspace_id = active_workspace_id or workspace.id" in open_block
    assert 'state.thread_name = thread_name or f"Developer: {workspace.name}"' in open_block


def test_developer_workspace_has_thread_switcher_and_new_thread_action():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    workspace_block = source.split("def build_developer_workspace(", 1)[1].split(
        "def _build_developer_inspector(",
        1,
    )[0]

    assert "def _switch_developer_thread" in workspace_block
    assert "def _create_new_developer_thread" in workspace_block
    assert "def _create_worktree_for_current_thread" in workspace_block
    assert "New Thread in Worktree (Recommended)" in workspace_block
    assert "New Thread in Current Folder" in workspace_block
    assert "list_workspace_threads(project_workspace.id)" in workspace_block
    assert "create_workspace_thread" in workspace_block
    assert "project_workspace.id" in workspace_block
    assert "Create Worktree" in workspace_block
    assert "worktree_reason" in workspace_block
    assert 'label="Run in"' in workspace_block
    assert "row-bot-composer-select" in workspace_block
    assert "row-bot-developer-workspace-shell" in workspace_block
    assert "New Thread" in workspace_block
    assert 'reason="thread_switch"' in workspace_block
    assert "rebuild_main(immediate=True, reason=\"developer_thread_switch\")" in workspace_block


def test_developer_workspace_header_wraps_controls_without_overlap():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
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
    assert "row-bot-composer-control-group" in workspace_block
    assert "w-full items-center justify-between no-wrap" not in workspace_block


def test_developer_inspector_width_is_bounded_by_shell():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    inspector_block = source.split("def _build_developer_inspector(", 1)[1].split(
        "def _build_developer_inspector_static(",
        1,
    )[0]
    static_block = source.split("def _build_developer_inspector_static(", 1)[1].split(
        "latest: dict[str, InspectorSnapshot]",
        1,
    )[0]

    assert "row-bot-developer-inspector-host" in inspector_block
    assert "host._props[\"id\"] = host_id" in inspector_block
    assert "display: none; flex: 0 1 clamp" in inspector_block
    assert "initDeveloperInspectorLayout" in static_block
    assert "window.setTimeout(() => initDeveloperInspectorLayout" in static_block
    assert "panel.closest('.row-bot-developer-workspace-shell')" in static_block
    assert "syncHostVisibility" in static_block
    assert "shellWidth() < 760" in static_block
    assert "shellWidth() * 0.42" in static_block
    assert "panel.parentElement.style.flexBasis" in static_block


def test_app_passes_rebuild_hooks_to_developer_workspace():
    source = (ROOT / "src" / "row_bot" / "app.py").read_text(encoding="utf-8")
    call_block = source[source.index("build_developer_workspace("):source.index("elif state.thread_id is None:")]

    assert "rebuild_main=lambda **kw: _rebuild_main(**kw)" in call_block
    assert "rebuild_thread_list=rebuild_thread_list" in call_block
