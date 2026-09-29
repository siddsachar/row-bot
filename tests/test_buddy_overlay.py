from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.buddy.overlay import (
    BuddyPlacement,
    BuddyPlacementState,
    OverlayTurnTarget,
    RuntimeSurface,
    ScreenArea,
    clamp_overlay_position,
    enable_windows_per_monitor_dpi,
    native_overlay_transparency,
    placement_state_from_config,
    placement_state_for_app_startup,
    position_for_drop,
    screen_areas_from_native,
    should_defer_native_show,
)


def _state(**overrides):
    values = {
        "thread_id": "thread-1",
        "thread_name": "Overlay work",
        "thread_model_override": "openai:gpt-test",
        "thread_approval_mode": "always_ask",
        "active_developer_workspace_id": None,
        "active_designer_project": None,
        "pending_interrupt": None,
        "messages": [{"role": "assistant", "content": "Previous **answer**"}],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_surface_config_migrates_to_one_placement(monkeypatch, tmp_path):
    import row_bot.buddy.config as config_mod

    config_path = tmp_path / "buddy_config.json"
    monkeypatch.setattr(config_mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "_BUDDY_CONFIG_PATH", config_path)
    config_path.write_text(
        '{"desktop_enabled": true, "floating_enabled": true, "overlay": {"width": 260, "height": 260, "x": -410, "y": 55}}',
        encoding="utf-8",
    )

    config = config_mod.get_buddy_config()

    assert config["placement"] == "desktop"
    assert config["visible"] is True
    assert config["overlay"] == {
        "width": 380,
        "height": 230,
        "always_on_top": True,
        "x": -410,
        "y": 55,
    }
    assert "floating_enabled" not in config
    assert "desktop_enabled" not in config


def test_legacy_non_desktop_users_migrate_to_docked_even_if_floating():
    state = placement_state_from_config({"floating_enabled": True, "mode": "floating"})
    assert state == BuddyPlacementState(BuddyPlacement.DOCKED, True, False)


def test_canonical_visibility_keeps_legacy_enabled_mirror_in_sync():
    from row_bot.buddy.config import _normalize_config

    hidden = _normalize_config({"placement": "desktop", "visible": False, "enabled": True})
    shown = _normalize_config({"placement": "desktop", "visible": True, "enabled": False})
    assert hidden["enabled"] is False
    assert shown["enabled"] is True


def test_manual_native_show_never_waits_forever_for_page_ready():
    assert should_defer_native_show(ready=False, manual=False) is True
    assert should_defer_native_show(ready=False, manual=True) is False
    assert should_defer_native_show(ready=True, manual=False) is False


def test_app_startup_returns_buddy_to_dock_without_reviving_hidden_visibility():
    shown = placement_state_for_app_startup(
        {"placement": "desktop", "visible": True, "collapsed": True}
    )
    hidden = placement_state_for_app_startup(
        {"placement": "desktop", "visible": False, "collapsed": True}
    )

    assert shown == BuddyPlacementState(BuddyPlacement.DOCKED, True, False)
    assert hidden == BuddyPlacementState(BuddyPlacement.DOCKED, False, False)


def test_windows_native_overlay_uses_hit_testable_per_monitor_mode():
    contexts: list[int] = []

    assert native_overlay_transparency("win32") is False
    assert native_overlay_transparency("darwin") is True
    assert enable_windows_per_monitor_dpi(
        "win32", set_context=lambda context: contexts.append(context) or True
    ) is True
    assert contexts == [-4]
    assert enable_windows_per_monitor_dpi(
        "darwin", set_context=lambda _context: contexts.append(99) or True
    ) is False
    assert contexts == [-4]


def test_placement_transitions_keep_hidden_and_collapsed_as_conditions():
    desktop = BuddyPlacementState().tear_off().collapse().hide()
    assert desktop == BuddyPlacementState(BuddyPlacement.DESKTOP, False, True)
    assert desktop.show().expand() == BuddyPlacementState(BuddyPlacement.DESKTOP, True, False)
    assert desktop.dock() == BuddyPlacementState(BuddyPlacement.DOCKED, True, False)
    assert BuddyPlacementState().collapse() == BuddyPlacementState()


def test_positioning_retains_negative_coordinates_and_recovers_missing_monitor():
    screens = [ScreenArea(-1920, 0, 1920, 1080), ScreenArea(0, 0, 1920, 1040)]
    assert clamp_overlay_position(-1800, 80, screens) == (-1800, 80)
    assert position_for_drop(-20, 20, screens) == (-388, 8)
    assert clamp_overlay_position(3000, 1500, [screens[1]]) == (1532, 802)


def test_native_screen_normalization_prefers_windows_work_area():
    frame = SimpleNamespace(X=-1920, Y=40, Width=1920, Height=1000)
    native = SimpleNamespace(x=-1920, y=0, width=1920, height=1080, frame=frame)
    assert screen_areas_from_native([native]) == [ScreenArea(-1920, 40, 1920, 1000)]


@pytest.mark.parametrize(
    ("state", "surface", "extra"),
    [
        (_state(), RuntimeSurface.CHAT, {}),
        (
            _state(active_developer_workspace_id="workspace-7"),
            RuntimeSurface.DEVELOPER,
            {"developer_workspace_id": "workspace-7"},
        ),
        (
            _state(active_designer_project=SimpleNamespace(id="project-4", mode="edit")),
            RuntimeSurface.DESIGNER,
            {"designer_project_id": "project-4", "designer_mode": "edit"},
        ),
    ],
)
def test_overlay_turn_capture_preserves_normal_developer_and_designer_surface(state, surface, extra):
    target = OverlayTurnTarget.capture(state)
    assert target.thread_id == "thread-1"
    assert target.runtime_surface is surface
    assert target.configurable_values() == {
        "runtime_surface": surface.value,
        "model_override": "openai:gpt-test",
        "approval_mode": "always_ask",
        **extra,
    }


def test_turn_capture_keeps_original_message_list_when_selected_thread_changes():
    original_messages = [{"role": "assistant", "content": "Thread one"}]
    state = _state(messages=original_messages)
    target = OverlayTurnTarget.capture(state)
    state.thread_id = "thread-2"
    state.messages = [{"role": "assistant", "content": "Thread two"}]
    assert target.thread_id == "thread-1"
    assert target.messages is original_messages


def test_thread_draft_continuity_between_full_composer_and_overlay(monkeypatch, tmp_path):
    import row_bot.threads as threads

    monkeypatch.setattr(threads, "_THREAD_UI_DIR", tmp_path)
    threads.save_thread_draft("thread-1", "from full composer", source="normal_chat")
    assert threads.load_thread_draft("thread-1")["text"] == "from full composer"
    threads.save_thread_draft("thread-1", "edited in overlay", source="buddy_overlay")
    loaded = threads.load_thread_draft("thread-1")
    assert loaded["text"] == "edited in overlay"
    assert loaded["source"] == "buddy_overlay"
