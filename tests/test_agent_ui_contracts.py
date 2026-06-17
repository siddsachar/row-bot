from __future__ import annotations

from pathlib import Path


ROOT = Path("src/row_bot")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_activity_center_uses_current_agent_goal_runs_and_channel_monitor():
    src = _read("ui/command_center.py")

    assert "Activity Center" in src
    assert "Current work, approvals, schedules" in src
    assert "prepare_channel_goal_start" not in src
    assert "list_goals" in src
    assert 'statuses=["waiting_approval", "blocked"]' in src
    goal_section = src.split("def _rebuild_goal_activity", 1)[1].split("def _render_goal_activity_row", 1)[0]
    assert "waiting_user" not in goal_section
    assert "failed" not in goal_section
    assert 'status in {"active", "paused"}' in goal_section
    assert "Needs Attention" in src
    assert "Running Now" in src
    assert "list_agent_runs" in src
    assert 'kind="subagent"' in src
    assert "stop_agent_run" in src
    assert "Current Chat Agents" in src
    assert "No active Agents" in src
    assert "open_agent_peek_dialog" in src
    assert "_agent_preview_state" in src
    assert "_agent_preview_container" in src
    assert "_show_agent_preview" in src
    assert "list_agent_runs(limit=12)" not in src
    assert "Recent Workflows" not in src
    assert "get_recent_runs" not in src
    assert "completed in this chat" not in src
    assert "hidden_completed" not in src
    assert "build_channel_monitor" in src
    assert "Open Settings > Channels" in src
    assert src.index("build_channel_monitor") < src.index('ui.expansion("Insights"')
    assert "list_agent_profiles" not in src
    assert "_library_container" not in src
    assert "Allowed tools" not in src
    assert "Denied tools" not in src


def test_profile_library_lives_in_left_sidebar():
    sidebar = _read("ui/sidebar.py")
    library = _read("ui/profile_library.py")
    profile_panel = library.split("def build_profile_library", 1)[1]

    assert "build_profile_library" in sidebar
    assert "Channels" not in sidebar
    assert "build_profile_library" in library
    assert "open_profile_view_dialog" in library
    assert "open_profile_editor_dialog" in library
    assert "list_agent_tool_catalog" in library
    assert "Core" in library
    assert "MCP" in library
    assert "Plugins" in library
    assert "Custom Tools" in library
    assert "Purpose" in library
    assert "Profile command" in library
    assert "width: min(920px, 96vw)" in library
    assert "rows=8" in library
    assert "Routing hint" in library
    assert "ui.checkbox" in library
    assert "_TOOL_ACCESS_OPTIONS" in library
    assert "allow_tools" in library
    assert "Selected tools" in library
    assert "skills_override" in library
    assert "Pinned skills for this profile" in library
    assert "Smart skill suggestions still work normally" in library
    assert "Profile shortcut" not in library
    assert "Suggested skills" not in library
    assert "inherit_skills" not in library
    assert "list_agent_profiles" in library
    assert "duplicate_agent_profile" in library
    assert "save_agent_profile" in library
    assert "Start chat" in library
    assert "View Profile" in library
    assert "_toggle_profile_panel" in library
    assert "_set_profile_panel_open" in library
    assert "_refresh_profile_surfaces" in library
    assert "_refresh_sidebar_summary" in library
    assert "drawer.show()" in library
    assert "_duplicate_profile_from_panel" in library
    assert "duplicate_agent_profile(_profile_ref(profile))" in library
    assert "_refresh_profile_panel_after_action()" in library
    assert "on_saved=_refresh_profile_surfaces" in library
    assert "summary_label_ref" in library
    assert "ui.left_drawer" in profile_panel
    assert "_PROFILE_LIBRARY_DRAWER_CSS" in library
    assert "ui.add_head_html(_PROFILE_LIBRARY_DRAWER_CSS)" in library
    assert 'drawer._props["data-profile-library-drawer"] = "1"' in library
    assert '[data-profile-library-drawer="1"]' in library
    assert "left: 300px !important" in library
    assert 'panel_state: dict[str, Any] = {"drawer": None' in library
    assert 'panel_state["dialog"]' not in library
    assert "_discard_profile_panel" not in library
    assert 'dialog.on("hide"' not in profile_panel
    assert "position=left transition-show=slide-right transition-hide=slide-left" not in profile_panel
    assert "row-bot-profile-library-drawer" in library
    assert "no-swipe-open no-swipe-close" in library
    assert "chevron_right" in library
    assert "chevron_left" in library
    assert "grid-template-columns: 22px minmax(0, 1fr) auto" in library


def test_channel_monitor_component_preserves_channel_contract():
    src = _read("ui/channel_monitor.py")

    assert "all_channels" in src
    assert "get_last_activity" in src
    assert "SESSION_DIR" in src
    assert "_get_user_phone" in src
    assert "open_settings(\"Channels\")" in src
    assert "safe_timer(5.0" in src
    assert "telegram" in src
    assert "whatsapp" in src


def test_global_profile_picker_contract():
    picker = _read("ui/profile_picker.py")
    chat = _read("ui/chat.py")
    designer = Path("src/row_bot/designer/editor.py").read_text(encoding="utf-8")
    developer = Path("src/row_bot/developer/ui.py").read_text(encoding="utf-8")

    assert "list_agent_profiles" in picker
    assert "_set_thread_agent_profile" in picker
    assert "_clear_thread_agent_profile" in picker
    assert "set_thread_skills_override" in picker
    assert "skills_override" in picker
    assert "clear_agent_cache" in picker
    assert "open_profile_view_dialog" in picker
    assert "Developer workspace permissions still apply" in picker
    assert "build_profile_picker" in chat
    assert "build_profile_picker" in designer
    assert "build_profile_picker" in developer


def test_agent_drawer_contract_and_surface_usage():
    drawer = _read("ui/agent_drawer.py")
    chat = _read("ui/chat.py")
    designer = Path("src/row_bot/designer/editor.py").read_text(encoding="utf-8")
    developer = Path("src/row_bot/developer/ui.py").read_text(encoding="utf-8")
    app = Path("src/row_bot/app.py").read_text(encoding="utf-8")

    assert "build_parent_agent_drawer" in drawer
    assert "open_agent_peek_dialog" in drawer
    assert "open_in_new" in drawer
    assert "_open_agent_thread" in drawer
    assert "load_thread_messages" in drawer
    assert "set_active_thread" in drawer
    assert "list_agent_runs" in drawer
    assert 'kind="subagent"' in drawer
    assert "stop_agent_run" in drawer
    assert "get_agent_parent_messages" in drawer
    assert "Note queued:" in drawer
    assert "build_profile_picker" in chat
    assert "_render_thread_profile_chip" not in chat
    assert "_render_parent_agent_strip" in chat
    assert "parent_agent_strip_container" in chat
    assert "refresh_parent_agent_strip" in chat
    assert "build_parent_agent_drawer" in chat
    assert "build_parent_agent_drawer" in designer
    assert "build_parent_agent_drawer" in developer
    assert "rebuild_main=lambda **kw: _rebuild_main(**kw)" in app
    assert "agent_child_open" not in chat


def test_shared_goal_ui_is_used_by_all_chat_surfaces():
    goal_ui = _read("ui/goal_ui.py")
    chat = _read("ui/chat.py")
    designer = Path("src/row_bot/designer/editor.py").read_text(encoding="utf-8")
    developer = Path("src/row_bot/developer/ui.py").read_text(encoding="utf-8")

    assert "def build_goal_progress_panel" in goal_ui
    assert "def _open_goal_detail_dialog" in goal_ui
    assert "goals.get_current_goal" in goal_ui
    assert "goals.pause_goal" in goal_ui
    assert "goals.resume_goal" in goal_ui
    assert "goals.complete_goal" in goal_ui
    assert "goals.clear_goal" in goal_ui
    assert "build_continuation_prompt" in goal_ui
    assert "Child-Agent Dependencies" in goal_ui
    assert 'kind="subagent"' in goal_ui
    assert "Pending Approvals" in goal_ui
    assert "Current Goal Events" in goal_ui
    assert "_render_goal_progress_row" not in chat
    assert "build_goal_progress_panel" in chat
    assert "build_goal_progress_panel" in developer
    assert "build_goal_progress_panel" in designer
    assert 'surface="main"' in chat
    assert 'surface="developer"' in developer
    assert 'surface="designer"' in designer


def test_sidebar_hides_child_agent_threads_by_default():
    src = _read("ui/sidebar.py")

    assert '"agents"' in src
    assert 'thread_type == "agent_child"' in src
    assert '{"key": "agents"' not in src
    assert '"label": "Agents"' not in src
    assert "parent_child_counts" not in src
    assert "_SIDEBAR_AGENT_EXPANDED" not in src
    assert 'payload["agent_parent_expanded"]' not in src
    assert 'payload.pop("agent_parent_expanded", None)' in src
    assert "for agent_run in _agent_run_rows" in src
    assert "for run in _agent_run_rows" not in src
    assert '!= str(agent_run.get("parent_thread_id") or "")' in src
    assert '_SIDEBAR_FILTER == "all"' in src
    assert 'c[1] != "agents"' in src
    assert "Show child Agent threads" not in src
    assert "valid_modal_keys" in src


def test_agent_card_uses_compact_peek_contract():
    src = _read("ui/render.py")

    assert "open_agent_peek_dialog" in src
    assert "Peek Agent activity" in src
    assert "agent_lifecycle" in src
    assert "-webkit-line-clamp: 2" in src
    assert "Raw Agent tool output" in src
    assert "Agents drawer" in src
