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
    assert 'label="Run in"' not in workspace_block
    assert 'label="Thread"' not in workspace_block
    assert "Choose where Developer commands run" in workspace_block
    assert "Switch Developer thread" in workspace_block
    assert "row-bot-composer-select" in workspace_block
    assert "row-bot-developer-workspace-shell" in workspace_block
    assert "New Thread" in workspace_block
    assert 'reason="thread_switch"' in workspace_block
    assert "rebuild_main(immediate=True, reason=\"developer_thread_switch\")" in workspace_block


def test_developer_workspace_header_wraps_controls_without_overlap():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    workspace_block = source.split("def build_developer_workspace(", 1)[1].split(
        "from row_bot.ui.agent_drawer",
        1,
    )[0]

    assert "_ensure_developer_workspace_css()" in workspace_block
    assert "row-bot-developer-workspace-shell row-bot-dev-shell" in workspace_block
    assert "row-bot-dev-header" in workspace_block
    assert "row-bot-dev-titlebar" in workspace_block
    assert "row-bot-dev-identity" in workspace_block
    assert "row-bot-dev-thread-zone" in workspace_block
    assert "row-bot-dev-control-zone" in workspace_block
    assert "row-bot-dev-profile-slot" in workspace_block
    assert "row-bot-dev-run-slot" in workspace_block
    assert "row-bot-dev-status-strip" in workspace_block
    assert "row-bot-dev-short-path" in workspace_block
    assert "Workspace details" in workspace_block
    assert "_short_path(workspace.path)" in workspace_block
    assert "Developer Preview" not in workspace_block
    assert "Project: {project_workspace.path}" not in workspace_block
    assert "Worktree: {workspace.path}" not in workspace_block
    assert "row-bot-composer-control-group" in workspace_block


def test_developer_workspace_uses_compact_branch_and_status_actions():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    status_block = source.split("def _render_workspace_status_badges(", 1)[1].split(
        "def _render_file_tree(",
        1,
    )[0]
    workspace_block = source.split("def build_developer_workspace(", 1)[1].split(
        "from row_bot.ui.agent_drawer",
        1,
    )[0]

    assert "row-bot-dev-chip" in status_block
    assert "_branch_label(git_summary[\"branch\"])" in status_block
    assert "Branch:" not in status_block
    assert "Run in Local" not in status_block
    assert "Run in Docker Sandbox" not in status_block
    assert "def _render_branch_action" in workspace_block
    assert "row-bot-dev-branch-button" in workspace_block
    assert "New branch" in workspace_block
    assert "Create branch" in workspace_block


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
    assert "display: flex; flex: 0 0 clamp" in inspector_block
    assert "host_id=host_id" in inspector_block
    assert "initDeveloperInspectorLayout" in static_block
    assert "window.setTimeout(() => initDeveloperInspectorLayout" in static_block
    assert "const host = document.getElementById({host_id!r});" in static_block
    assert "const panelRow = panel ? panel.parentElement : null;" in static_block
    assert "panel.closest('.row-bot-developer-workspace-shell')" in static_block
    assert "syncHostVisibility" in static_block
    assert "shellWidth() < 760" in static_block
    assert "host.style.display = shellWidth() < 760 ? 'none' : 'flex';" in static_block
    assert "shellWidth() * 0.34" in static_block
    assert "host.style.flexBasis" in static_block
    assert "host.offsetWidth || panel.offsetWidth" in static_block


def test_developer_inspector_uses_summary_and_tabs_not_open_expansion_wall():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    static_block = source.split("def _build_developer_inspector_static(", 1)[1].split(
        "def _summary()",
        1,
    )[0]

    assert "row-bot-dev-inspector-panel" in static_block
    assert "row-bot-dev-inspector-header" in static_block
    assert "section_bodies[\"summary\"]" in static_block
    assert "row-bot-dev-inspector-tabs" in static_block
    assert "row-bot-dev-overview-card" in source
    assert "row-bot-dev-detail-grid" in source
    assert "grid-template-columns: 1fr" in source
    assert "text-overflow: ellipsis" in source
    assert 'ui.tab("Overview", icon="dashboard")' in static_block
    assert 'ui.tab("Changes", icon="difference")' in static_block
    assert 'ui.tab("Files", icon="folder_open")' in static_block
    assert 'ui.tab("Run", icon="science")' in static_block
    assert 'ui.tab("Safety", icon="shield")' in static_block
    assert 'ui.tab("GitHub", icon="account_tree")' in static_block
    assert '("sandbox", "Sandbox", "inventory_2")' in static_block
    assert "ui.expansion(label, icon=icon)" not in static_block
    overview_panel_block = static_block.split("with ui.tab_panel(overview_tab)", 1)[1].split(
        "with ui.tab_panel(changes_tab)",
        1,
    )[0]
    assert "ui.label(label)" not in overview_panel_block


def test_developer_inspector_overview_uses_short_values_without_raw_path_noise():
    source = (ROOT / "src" / "row_bot" / "developer" / "ui.py").read_text(encoding="utf-8")
    overview_block = source.split("def _overview() -> None:", 1)[1].split(
        "_render_if_changed(\"overview\"",
        1,
    )[0]
    working_block = source.split("def _working_copy() -> None:", 1)[1].split(
        "_render_if_changed(\n            \"working_copy\"",
        1,
    )[0]
    todos_block = source.split("def _todos() -> None:", 1)[1].split(
        "_render_if_changed(\"todos\"",
        1,
    )[0]

    assert "row-bot-dev-overview-card" in overview_block
    assert "row-bot-dev-detail-grid" in overview_block
    assert "_short_path(workspace_now.path)" in overview_block
    assert "Approval:" not in overview_block
    assert "_short_path(workspace_now.path)" in working_block
    assert "_short_path(project_path)" in working_block
    assert "_branch_label(branch)" in working_block
    assert "ui.label(workspace_now.path)" not in working_block
    assert "Project: {worktree_row['project_path']}" not in working_block
    assert "No todos yet." in todos_block


def test_app_passes_rebuild_hooks_to_developer_workspace():
    source = (ROOT / "src" / "row_bot" / "app.py").read_text(encoding="utf-8")
    call_block = source[source.index("build_developer_workspace("):source.index("elif state.thread_id is None:")]

    assert "rebuild_main=lambda **kw: _rebuild_main(**kw)" in call_block
    assert "rebuild_thread_list=rebuild_thread_list" in call_block
