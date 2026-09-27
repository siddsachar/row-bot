from __future__ import annotations

import asyncio
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
from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401


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


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (OverlayTurnTarget("thread-1", "Chat", RuntimeSurface.CHAT), {"runtime_surface": "normal_chat"}),
        (
            OverlayTurnTarget(
                "thread-1",
                "Developer",
                RuntimeSurface.DEVELOPER,
                developer_workspace_id="workspace-1",
            ),
            {"runtime_surface": "developer", "developer_workspace_id": "workspace-1"},
        ),
        (
            OverlayTurnTarget(
                "thread-1",
                "Designer",
                RuntimeSurface.DESIGNER,
                designer_project_id="project-1",
                designer_mode="edit",
            ),
            {
                "runtime_surface": "designer",
                "designer_project_id": "project-1",
                "designer_mode": "edit",
            },
        ),
    ],
)
def test_overlay_send_uses_captured_surface_and_never_adds_implicit_images(monkeypatch, tmp_path, platform, target, expected):
    import importlib

    import row_bot.developer.agent_context as developer_context
    import row_bot.developer.profile as developer_profile
    import row_bot.agent as agent
    import row_bot.threads as threads
    import row_bot.tools.registry as tool_registry
    import row_bot.ui.helpers as helpers
    import row_bot.ui.streaming as streaming
    import row_bot.ui.state as ui_state
    from row_bot.application import client_platform
    from row_bot.ui.legacy_adapter import generation as legacy

    # The overlay uses the real shared admission/worker owner. Register the
    # captured conversation and resource domains in the fixture's private stores.
    monkeypatch.setattr(client_platform, "client_platform_service", platform)
    monkeypatch.setattr(legacy, "client_platform_service", platform)
    active_generations = {}
    monkeypatch.setattr(streaming, "_active_generations", active_generations)
    monkeypatch.setattr(ui_state, "_active_generations", active_generations)
    if target.developer_workspace_id:
        storage = importlib.import_module("row_bot.developer.storage")
        from row_bot.developer.state import DeveloperWorkspace
        root = tmp_path / "developer"
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "DEVELOPER_DIR", root)
        monkeypatch.setattr(storage, "WORKSPACES_PATH", root / "workspaces.json")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        storage.save_workspace(DeveloperWorkspace(id=target.developer_workspace_id, name="Captured workspace", path=str(workspace)))
    if target.designer_project_id:
        storage = importlib.import_module("row_bot.designer.storage")
        from row_bot.designer.state import DesignerProject
        root = tmp_path / "designer"
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "DESIGNER_DIR", root)
        for name, folder in (("PROJECTS_DIR", "projects"), ("ASSETS_DIR", "assets"), ("REFERENCES_DIR", "references")):
            monkeypatch.setattr(storage, name, root / folder)
        storage.save_project(DesignerProject(id=target.designer_project_id, name="Captured artifact"))
    threads.create_thread(target.thread_name, thread_id=target.thread_id,
                          developer_workspace_id=target.developer_workspace_id,
                          project_id=target.designer_project_id, seed_default_skills=False)
    from langchain_core.messages import AIMessage
    from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream
    output_id = f"buddy-{target.runtime_surface.value}-output"
    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(content="Fixture reply", id=output_id),), output_id),
                                ("done", "Fixture reply")))
    calls = []

    def fake_stream(text, enabled_tools, config, **kwargs):
        from row_bot.conversation_resources import current_execution_context
        context = current_execution_context()
        calls.append((text, config["configurable"].copy(), context))
        return fake.stream(text, enabled_tools, config, **kwargs)

    async def ready(*_args, **_kwargs):
        return True

    async def consume_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent, "stream_agent", fake_stream)
    monkeypatch.setattr(streaming, "_context_capacity_ready_for_send", ready)
    monkeypatch.setattr(streaming, "_agent_ready_forced_surface", ready)
    monkeypatch.setattr(streaming, "_subscription_auth_block_message", lambda *_args: None)
    monkeypatch.setattr(streaming, "_profile_runtime_config_for_thread", lambda *_args: {})
    monkeypatch.setattr(streaming, "_child_agent_run_ids_for_thread", lambda *_args: set())
    monkeypatch.setattr(streaming, "_build_assistant_placeholder", lambda *_args: None)
    monkeypatch.setattr(streaming, "consume_generation", consume_noop)
    monkeypatch.setattr(threads, "should_auto_rename_thread", lambda *_args: False)
    monkeypatch.setattr(threads, "touch_thread", lambda *_args: None)
    monkeypatch.setattr(threads, "_get_thread_approval_mode", lambda *_args: "always_ask")
    monkeypatch.setattr(threads, "_get_thread_project_workspace", lambda *_args: "")
    monkeypatch.setattr(helpers, "persist_thread_media_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: [])
    monkeypatch.setattr(developer_context, "build_developer_agent_context", lambda *_args: "context")
    monkeypatch.setattr(developer_profile, "effective_tool_names", lambda names: names)

    state = _state(
        messages=[],
        active_developer_workspace_id=None,
        active_designer_project=None,
        attached_data_cache={},
        vision_service=None,
        voice_coordinator=SimpleNamespace(transport=""),
        tts_service=SimpleNamespace(enabled=False),
        voice_enabled=False,
        voice_input_mode="talk",
    )
    state.cache_active_messages = lambda: None
    p = SimpleNamespace(
        pending_files=[],
        file_chips_row=None,
        chat_header_label=None,
        stop_btn=None,
        chat_container=None,
        chat_scroll=None,
    )
    cb = SimpleNamespace(
        rebuild_main=lambda *args, **kwargs: None,
        rebuild_thread_list=lambda: None,
        add_chat_message=lambda *_args: None,
    )

    try:
        asyncio.run(streaming.send_message("raw input", state=state, p=p, cb=cb, turn_target=target))
        generation = active_generations[target.thread_id]
        handle = generation.q.handle
        assert platform.registry.get(handle.execution_id) is handle
        assert handle.producer_done.wait(10)
        assert handle.status == "completed" and handle.cleanup_complete
        assert handle.output_message_id == output_id and fake.quiesced.is_set()
        assert len(calls) == 1 and calls[0][2].conversation_id == target.thread_id
        configurable = generation.config["configurable"]
        for key, value in expected.items():
            assert configurable[key] == value
            assert calls[0][1][key] == value
        assert generation.captured_images == []
        submission_id = configurable["platform_submission_id"]
        assert state.messages == [{"role": "user", "content": "raw input", "message_id": submission_id}]
        from langchain_core.messages import HumanMessage
        admitted = [message for message in threads.get_latest_checkpoint_messages(target.thread_id)
                    if isinstance(message, HumanMessage)]
        assert len(admitted) == 1 and admitted[0].id == submission_id
        assert not platform.registry.active(target.thread_id)
    finally:
        generation = active_generations.pop(target.thread_id, None)
        if generation is not None:
            generation.q.close_consumer()
            if not generation.q.handle.producer_done.is_set():
                platform.registry.stop(target.thread_id)
                assert generation.q.handle.producer_done.wait(10)


def test_no_thread_raw_slash_command_creates_normal_thread_and_forwards_text(monkeypatch):
    import row_bot.slash_commands as slash_commands
    import row_bot.threads as threads
    import row_bot.tools.registry as tool_registry
    import row_bot.ui.helpers as helpers
    import row_bot.ui.streaming as streaming

    created: list[tuple[str, str, str]] = []
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        threads,
        "create_thread",
        lambda name, *, thread_id, approval_mode, **_kwargs: created.append((name, thread_id, approval_mode)),
    )
    monkeypatch.setattr(threads, "touch_thread", lambda *_args: None)
    monkeypatch.setattr(helpers, "persist_thread_media_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: [])
    monkeypatch.setattr(
        slash_commands,
        "resolve_command_text",
        lambda text, **_kwargs: (SimpleNamespace(id="status"), "") if text == "/status raw" else None,
    )
    monkeypatch.setattr(
        slash_commands,
        "dispatch_text_command",
        lambda thread_id, text, **_kwargs: dispatched.append((thread_id, text)) or "Status ready",
    )

    state = _state(
        thread_id=None,
        thread_name=None,
        messages=[],
        active_developer_workspace_id="stale-workspace",
        active_designer_project=SimpleNamespace(id="stale-project", mode="edit"),
    )
    state.show_onboarding = True
    state.cache_active_messages = lambda: None
    p = SimpleNamespace(pending_files=[])
    cb = SimpleNamespace(
        rebuild_main=lambda *args, **kwargs: None,
        rebuild_thread_list=lambda: None,
        add_chat_message=lambda *_args: None,
    )

    asyncio.run(streaming.send_message("/status raw", state=state, p=p, cb=cb))

    assert len(created) == 1
    assert created[0][1] == state.thread_id
    assert dispatched == [(state.thread_id, "/status raw")]
    assert state.messages == [
        {"role": "user", "content": "/status raw"},
        {"role": "assistant", "content": "Status ready"},
    ]
