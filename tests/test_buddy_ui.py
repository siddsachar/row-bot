from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_FILES = {"app.py", "launcher.py", "notifications.py", "tasks.py"}
_RUNTIME_PREFIXES = (
    "buddy/",
    "designer/",
    "ui/",
)


def _read(relative: str) -> str:
    if relative in _RUNTIME_FILES or relative.startswith(_RUNTIME_PREFIXES):
        relative = f"src/row_bot/{relative}"
    return (ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "scenario",
    [
        "click_below_threshold",
        "first_drag",
        "snapshot_refresh",
        "edge_idempotent",
        "release_over_dock",
        "pointer_cancel",
        "capture_loss",
        "native_failure_retry",
        "native_unavailable",
        "native_rejection",
    ],
)
def test_docked_buddy_drag_runtime_is_one_terminal_gesture(tmp_path, scenario: str) -> None:
    from row_bot.ui.buddy import _install_in_app_buddy_drag_js

    generated = tmp_path / "buddy_drag.js"
    generated.write_text(
        _install_in_app_buddy_drag_js("buddy", "dock"),
        encoding="utf-8",
    )
    harness = ROOT / "tests" / "fixtures" / "buddy_drag_runtime_test.cjs"
    result = subprocess.run(
        ["node", str(harness), scenario, str(generated)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_buddy_ui_surfaces_are_wired():
    app_src = _read("src/row_bot/app.py")
    sidebar_src = _read("src/row_bot/ui/sidebar.py")
    settings_src = _read("src/row_bot/ui/settings.py")

    assert "build_in_app_buddy" in app_src
    assert "build_sidebar_buddy" in sidebar_src
    assert 'ui.tab("Buddy"' in settings_src
    assert "build_buddy_settings_tab" in settings_src
    assert 'app.add_static_files("/_buddy"' in app_src
    assert "health_result = await run.io_bound(_run_health_check)" in app_src
    assert "Startup health check returned invalid result" in app_src
    assert "ok, err = health_result" in app_src


def test_buddy_events_are_emitted_from_runtime_sources():
    streaming_src = _read("src/row_bot/ui/streaming.py")
    tasks_src = _read("src/row_bot/tasks.py")
    notifications_src = _read("src/row_bot/notifications.py")
    brain_src = _read("buddy/brain.py")

    for marker in [
        "GENERATION_STARTED",
        "TOOL_STARTED",
        "APPROVAL_NEEDED",
        "APPROVAL_APPROVED",
        "APPROVAL_DENIED",
        "GENERATION_DONE",
        "GENERATION_ERROR",
    ]:
        assert marker in streaming_src or marker in tasks_src
    assert "WORKFLOW_STARTED" in tasks_src
    assert "WORKFLOW_STEP" in tasks_src
    assert "WORKFLOW_DONE" in tasks_src
    assert "APPROVAL_TIMED_OUT" in tasks_src
    assert 'BuddyEventType.APPROVAL_DENIED: ("approval", "workflow")' in brain_src
    assert "BuddyEventType.NOTIFICATION" in notifications_src


def test_buddy_hatch_prompts_request_keyable_motion_assets():
    hatch_src = _read("buddy/hatch.py")

    assert "Create exactly one animated-app companion character" in hatch_src
    assert "single centered avatar portrait" in hatch_src
    assert "Do not create a sprite sheet" in hatch_src
    assert "contact sheet" in hatch_src
    assert "multiple poses" in hatch_src
    assert "Create one video clip only" in hatch_src
    assert "flat solid" in hatch_src
    assert "keyable background" in hatch_src
    assert "no transparent background" in hatch_src
    assert "no alpha checkerboard" in hatch_src
    assert "motion_source.png" in hatch_src
    assert "_prepare_motion_source_image" in hatch_src
    assert "18 percent empty margin" in hatch_src
    assert "frame edge" in hatch_src
    assert "rim light or outline" in hatch_src
    assert "for attempt in range(2)" in hatch_src
    assert "reused existing clip" in hatch_src
    assert "reuse_existing" in hatch_src
    assert "_is_rate_limited_generation_result" in hatch_src
    assert "ROW_BOT_BUDDY_GOOGLE_VIDEO_SPACING_SECONDS" in hatch_src
    assert "The user concept is the source of truth" in hatch_src
    assert "do not force ancient, mystical, ink, gold, teal, glyph, or Row-Bot-like motifs" in hatch_src
    assert "forced Row-Bot motifs" not in hatch_src
    assert "teal-gold magical glow" not in hatch_src


def test_buddy_settings_keeps_rive_import_out_of_normal_ux():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    settings_src = _read("src/row_bot/ui/settings.py")

    assert "Install Buddy .riv" not in buddy_ui_src
    assert "accept='.riv'" not in buddy_ui_src
    assert "Open Preferences" not in buddy_ui_src
    assert "Generate full Buddy" in buddy_ui_src
    assert "start_hatch_generation_job" in buddy_ui_src
    assert "get_hatch_generation_status" in buddy_ui_src
    assert "Retry motion" in buddy_ui_src
    assert "Use still only" in buddy_ui_src
    assert 'ui.tab("Preferences"' in settings_src
    assert "Save Buddy preferences" not in settings_src
    assert "Companion personality" in buddy_ui_src
    assert "Style notes (optional)" in buddy_ui_src
    assert "APP_DISPLAY_NAME} handles sizing and motion automatically" in buddy_ui_src
    assert "_compose_hatch_prompt" in buddy_ui_src
    assert "_clean_hatch_concept" in buddy_ui_src
    assert "hatch_generation_prompt" in buddy_ui_src
    assert 'value=_clean_hatch_concept(str(cfg.get("hatch_prompt") or cfg.get("hatch_generation_prompt")' in buddy_ui_src
    assert '"hatch_prompt": concept_prompt' in buddy_ui_src
    assert "display_prompt=concept_prompt" in buddy_ui_src
    assert 'str(latest_cfg.get("hatch_generation_prompt") or "") or _compose_hatch_prompt' not in buddy_ui_src
    assert "bubble_verbosity" in buddy_ui_src
    assert "Buddy name" not in buddy_ui_src
    assert "buddy_name_input" not in buddy_ui_src
    assert '"display_name": buddy_name' not in buddy_ui_src


def test_buddy_settings_does_not_render_loose_pack_labels():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert 'for pack in packs:\n            ui.label(pack.name)' not in buddy_ui_src
    assert 'with ui.element("div").classes("row-bot-buddy-pack-grid")' in buddy_ui_src


def test_buddy_settings_uses_visual_pack_picker():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert "row-bot-buddy-pack-grid" in buddy_ui_src
    assert "row-bot-buddy-pack-card" in buddy_ui_src
    assert "row-bot-buddy-pack-card-selected" in buddy_ui_src
    assert "row-bot-buddy-pack-preview" in buddy_ui_src
    assert "static_url_for_path(pack.preview_path)" in buddy_ui_src
    assert "selected_pack_id" in buddy_ui_src
    assert "pack_selection_touched" in buddy_ui_src
    assert "row-bot-buddy-pack-title" in buddy_ui_src
    assert "row-bot-buddy-pack-meta" in buddy_ui_src
    assert "_clear_hatch_media_overrides" in buddy_ui_src
    assert '"active_hatch_preview"' in buddy_ui_src
    assert '"active_hatch_motion_pack"' in buddy_ui_src
    assert '"latest_hatch_preview"' in buddy_ui_src
    assert '"latest_hatch_motion_pack"' in buddy_ui_src
    assert "_refresh_existing_buddy_surfaces" in buddy_ui_src
    assert '{"sidebar": _surface_html("sidebar")}' in buddy_ui_src
    assert "client.run_javascript(code)" in buddy_ui_src
    assert "element.replaceWith(next)" in buddy_ui_src
    assert 'latest_cfg["pack_id"] = pack_id' in buddy_ui_src
    assert "0.20),\n.row-bot-buddy-pack-grid" not in buddy_ui_src
    assert "pack_select = ui.select" not in buddy_ui_src


def test_buddy_settings_save_preserves_latest_hatch_media():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    save_section = buddy_ui_src.split("def _save()", 1)[1].split("async def _hatch()", 1)[0]

    assert "latest_cfg = get_buddy_config()" in save_section
    assert "latest_cfg.update({" in save_section
    assert "_clear_hatch_media_overrides(latest_cfg)" in save_section
    assert "save_buddy_config(latest_cfg)" in save_section
    assert "_apply_buddy_surface_settings(latest_cfg)" in save_section


def test_buddy_settings_can_retry_motion_for_existing_hatch_art():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    retry_section = buddy_ui_src.split("async def _retry_motion()", 1)[1].split("with ui.row().classes", 1)[0]

    assert "_selected_generated_pack_preview(latest_cfg)" in retry_section
    assert "start_hatch_generation_job" in retry_section
    assert "pack_id=target_pack_id" in retry_section
    assert "reuse_existing=False" in retry_section
    assert "mode=\"motion\"" in retry_section
    assert "Buddy motion regeneration started in the background" in retry_section


def test_buddy_settings_starts_full_hatch_generation_in_background():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    hatch_section = buddy_ui_src.split("async def _hatch()", 1)[1].split("async def _retry_motion()", 1)[0]

    assert "start_hatch_generation_job" in hatch_section
    assert "mode=\"full\"" in hatch_section
    assert "Generating Buddy art and motion pack" not in hatch_section
    assert "Buddy generation started in the background" in hatch_section
    assert "await run.io_bound(\n                start_hatch_generation_job" in hatch_section


def test_buddy_settings_can_retry_motion_for_selected_generated_pack():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    preview_section = buddy_ui_src.split("def _selected_generated_pack_preview", 1)[1].split("def _select_pack", 1)[0]

    assert "selected_pack_id" in preview_section
    assert "pack_id.startswith(\"hatch-\")" in preview_section
    assert "load_buddy_pack(pack_id)" in preview_section
    assert "pack.preview_path.exists()" in preview_section
    assert "latest_hatch_preview" in preview_section
    assert "active_hatch_preview" in preview_section


def test_buddy_settings_can_switch_generated_pack_to_still_only():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    still_section = buddy_ui_src.split("def _use_still_only()", 1)[1].split("with ui.row().classes", 1)[0]

    assert "use_hatch_still_only" in still_section
    assert "pack_id.startswith(\"hatch-\")" in still_section
    assert 'latest_cfg.pop(key, None)' in still_section
    assert "hatch_motion.set_content(\"\")" in still_section
    assert "Using still image only" in still_section


def test_buddy_settings_can_delete_generated_pack_from_picker():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")
    delete_section = buddy_ui_src.split("def _delete_selected_generated_pack()", 1)[1].split("with ui.row().classes", 1)[0]

    assert "delete_generated_buddy_pack" in buddy_ui_src
    assert "confirm_destructive" in delete_section
    assert "pack_id.startswith(\"hatch-\")" in delete_section
    assert 'pack.runtime not in {"generated_motion_pack", "generated_still"}' in delete_section
    assert "_clear_hatch_media_overrides(latest_cfg)" in delete_section
    assert "hatch_motion.set_content(\"\")" in delete_section
    assert "Delete generated look" in buddy_ui_src
    assert "Deleted generated Buddy look" in delete_section


def test_home_status_bar_shows_buddy_hatch_progress():
    status_src = _read("src/row_bot/ui/status_bar.py")

    assert "get_hatch_generation_status" in status_src
    assert "row-bot-buddy-hatch-progress" in status_src
    assert "Buddy Hatch generation status" in status_src
    assert "completed_clips" in status_src
    assert "safe_timer(2.0, _poll_buddy_hatch_status)" in status_src


def test_chat_avatar_uses_buddy_preview_only_for_default(monkeypatch):
    import row_bot.ui.status_bar as status_bar

    monkeypatch.setattr(status_bar, "_get_buddy_avatar_url", lambda: "/static/buddy/builtins/glyph/preview.png")

    monkeypatch.setattr(status_bar, "_load_avatar_config", lambda: {})
    assert 'src="/static/buddy/builtins/glyph/preview.png"' in status_bar.get_bot_avatar_html()

    monkeypatch.setattr(status_bar, "_load_avatar_config", lambda: {"emoji": "spark"})
    assert status_bar.get_bot_avatar_html() == "spark"

    monkeypatch.setattr(
        status_bar,
        "_load_avatar_config",
        lambda: {"mode": "image", "image": "abc123"},
    )
    assert 'src="data:image/png;base64,abc123"' in status_bar.get_bot_avatar_html()


def test_buddy_settings_visibility_controls_are_not_redundant():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert '_section("Visibility"' in buddy_ui_src
    assert '_section("Where it appears"' not in buddy_ui_src
    assert '_section("Surfaces"' not in buddy_ui_src
    assert 'ui.switch("Show Buddy"' in buddy_ui_src
    assert '"Desktop overlay"' not in buddy_ui_src
    assert "in_app_initial" not in buddy_ui_src
    assert "desktop_initial" not in buddy_ui_src
    assert '"enabled": visible' in buddy_ui_src
    settings_src = buddy_ui_src.split("def build_buddy_settings_tab", 1)[1]
    assert '"show_buddy_window" if visible else "hide_buddy_window"' in settings_src
    assert "api.{native_method}(true)" in settings_src
    assert '"visible": visible' in buddy_ui_src
    assert '"sidebar_enabled"' not in buddy_ui_src
    assert '"desktop_enabled"' not in buddy_ui_src
    assert '"floating_enabled"' not in buddy_ui_src


def test_buddy_settings_strips_redundant_pack_prefixes():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert "def _display_pack_name" in buddy_ui_src
    assert 'value.lower().startswith("buddy ")' in buddy_ui_src
    assert "ui.label(pack_label).classes(\"row-bot-buddy-pack-title\")" in buddy_ui_src
    assert "Selected: {pack_label}" in buddy_ui_src
    assert "{pack_label} selected" in buddy_ui_src
    assert 'ui.label("Buddy look")' not in buddy_ui_src
    assert 'label="Buddy concept"' not in buddy_ui_src


def test_buddy_surface_sizing_and_docked_drag_are_targeted():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert '.row-bot-buddy-wrap[data-surface="sidebar"] .row-bot-buddy-stage' in buddy_ui_src
    sidebar_stage = buddy_ui_src.split(
        '.row-bot-buddy-wrap[data-surface="sidebar"] .row-bot-buddy-stage',
        1,
    )[1].split("}", 1)[0]
    assert "width: 132px" in sidebar_stage
    assert "height: 132px" in sidebar_stage
    assert ".row-bot-buddy-sidebar-action" in buddy_ui_src
    assert "width: 158px" in buddy_ui_src
    assert "width: 140px" in buddy_ui_src
    assert "row-bot-buddy-sidebar-ring" in buddy_ui_src
    assert "data-buddy-display-name" not in buddy_ui_src
    assert "data-display-name" not in buddy_ui_src
    assert "sidebar_avatar_label = ui.label" not in buddy_ui_src
    assert ".row-bot-buddy-in-app.row-bot-buddy-drag-preview .row-bot-buddy-stage::after" in buddy_ui_src
    assert "display: none" in buddy_ui_src
    assert "buddyDragInstalled" in buddy_ui_src
    assert "RowBotBuddyDock" in buddy_ui_src
    assert "row-bot-buddy-dock-empty" in buddy_ui_src
    assert "row-bot-buddy-docked" in buddy_ui_src
    assert "row-bot-buddy-undocked" not in buddy_ui_src
    assert "document.body.appendChild(target)" in buddy_ui_src
    assert "targetDock.appendChild(target)" in buddy_ui_src
    assert "setSurface('floating')" not in buddy_ui_src
    assert "api.tear_off_buddy" in buddy_ui_src
    assert "resetAll()" in buddy_ui_src
    assert "setPointerCapture" in buddy_ui_src
    assert "CustomEvent('buddy-click'" in buddy_ui_src
    assert "moved = true" in buddy_ui_src
    assert "_ensure_buddy_client_runtime" in buddy_ui_src
    assert "_buddy_pump_clients" in buddy_ui_src
    assert "build_in_app_buddy()" in buddy_ui_src
    assert "_row_bot_buddy_floating_shell" not in buddy_ui_src
    assert "data-buddy-floating-shell" not in buddy_ui_src
    assert "ui.element(\"div\").classes(\"row-bot-buddy-floating\")" not in buddy_ui_src
    assert "ui.timer(0.6, lambda: _push_snapshot(client))" in buddy_ui_src
    assert "ui.timer(0.1, lambda: _install_floating_drag" not in buddy_ui_src


def test_buddy_sidebar_click_replaces_toolbar_buttons():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert "row-bot-buddy-sidebar-action" in buddy_ui_src
    assert "data-buddy-sidebar-shell" in buddy_ui_src
    assert "data-buddy-in-app-shell" in buddy_ui_src
    assert "open_settings(\"Buddy\")" in buddy_ui_src
    assert "_emit_buddy_hi" in buddy_ui_src
    assert "row-bot-buddy-toolbar" not in buddy_ui_src
    assert "icon=\"favorite\"" not in buddy_ui_src


def test_buddy_status_bubbles_and_hot_apply_are_wired():
    buddy_ui_src = _read("src/row_bot/ui/buddy.py")

    assert "data-bubble-verbosity" in buddy_ui_src
    assert '.row-bot-buddy-wrap[data-surface="sidebar"] .row-bot-buddy-status' in buddy_ui_src
    assert ".row-bot-buddy-in-app.row-bot-buddy-drag-preview .row-bot-buddy-status" in buddy_ui_src
    assert "def _client_is_live(client)" in buddy_ui_src
    assert "not getattr(client, \"_deleted\", False)" in buddy_ui_src
    assert "lambda: _push_snapshot(client)" in buddy_ui_src
    assert "pump_timer.cancel(with_current_invocation=True)" in buddy_ui_src
    snapshot_section = buddy_ui_src.split("def _push_snapshot", 1)[1].split("def _ensure_buddy_client_runtime", 1)[0]
    assert "if (docked && window.RowBotBuddyDock) window.RowBotBuddyDock.resetAll();" in snapshot_section
    assert "_apply_buddy_surface_settings" in buddy_ui_src
    assert "window.pywebview.api" in buddy_ui_src
    assert "api.tear_off_buddy" in buddy_ui_src
    assert "hide_buddy_window" in buddy_ui_src
    assert "document.querySelectorAll('[data-buddy-in-app-shell]')" in buddy_ui_src


def test_the_nicegui_desktop_overlay_and_its_runtime_are_retired():
    """The torn-off Buddy is the React overlay (``/app-v2/buddy-overlay``)."""
    import row_bot.buddy.overlay as overlay
    import row_bot.ui.buddy as buddy_ui

    assert not hasattr(buddy_ui, "build_buddy_overlay_page")
    assert "/static/buddy/runtime/" not in buddy_ui._BUDDY_HEAD
    assert "buddy-overlay" not in buddy_ui._BUDDY_HEAD
    assert not (ROOT / "static" / "buddy" / "runtime" / "buddy.js").exists()
    for retired in ("build_thread_snapshot", "project_approval", "ForegroundAppTracker", "NativeBuddyLifecycle"):
        assert not hasattr(overlay, retired), retired


def test_legacy_buddy_surfaces_show_the_active_look_as_a_still_image(monkeypatch, tmp_path):
    import row_bot.ui.buddy as buddy_ui

    preview = tmp_path / "look.png"
    preview.write_bytes(b"not decoded")
    monkeypatch.setattr(buddy_ui, "static_url_for_path", lambda value: "/_buddy/" + Path(value).name)
    monkeypatch.setattr(buddy_ui, "get_buddy_config", lambda: {
        "active_hatch_preview": str(preview), "bubble_verbosity": "quiet", "personality": "calm"})
    rendered = buddy_ui._surface_html("sidebar")
    assert '<img src="/_buddy/look.png" alt="" draggable="false">' in rendered
    assert 'data-surface="sidebar"' in rendered and 'data-bubble-verbosity="quiet"' in rendered
    assert "<canvas" not in rendered and "data-motion" not in rendered

    # Without a generated look, the selected pack's still image is shown.
    monkeypatch.setattr(buddy_ui, "get_buddy_config", lambda: {"pack_id": "glyph"})
    assert '<img src="/_buddy/preview.png"' in buddy_ui._surface_html("sidebar")

    # With no image at all, the glyph fallback stays visible.
    monkeypatch.setattr(buddy_ui, "load_buddy_pack", lambda _pack_id: type("Pack", (), {"preview_path": None})())
    monkeypatch.setattr(buddy_ui, "get_buddy_config", lambda: {})
    fallback = buddy_ui._surface_html("sidebar")
    assert "<img" not in fallback and "row-bot-buddy-fallback" in fallback


def test_builtin_glyph_pack_ships_a_still_and_motion_clips():
    manifest_src = _read("static/buddy/builtins/glyph/manifest.json")

    assert '"runtime": "generated_motion_pack"' in manifest_src
    assert '"preview": "preview.png"' in manifest_src
    assert '"path": "motions/idle.mp4"' in manifest_src


def test_installers_ship_the_runtime_package():
    assert "..\\src\\row_bot\\*" in _read("installer/row_bot_setup.iss")
    assert "src/row_bot" in _read("installer/build_mac_app.sh")
    assert "src/row_bot" in _read("installer/build_linux_app.sh")
