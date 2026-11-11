from __future__ import annotations

import threading

import pytest


@pytest.fixture(autouse=True)
def _isolate_buddy_durable_activity(monkeypatch):
    """Keep Buddy unit tests independent of stale deterministic-lane state."""

    import row_bot.agent_orchestrator as orchestrator
    import row_bot.tasks as tasks

    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    monkeypatch.setattr(orchestrator, "get_thread_orchestration_activity", lambda: {})


def test_buddy_event_bus_assigns_ids_and_retains_recent_events():
    from row_bot.buddy.events import BuddyEventBus, BuddyEventType

    bus = BuddyEventBus(max_events=2)
    first = bus.emit(BuddyEventType.GENERATION_STARTED, source="test", payload={"label": "Thinking"})
    second = bus.emit(BuddyEventType.TOKEN, source="test")
    third = bus.emit(BuddyEventType.GENERATION_DONE, source="test")

    assert (first.id, second.id, third.id) == (1, 2, 3)
    assert [event.id for event in bus.recent()] == [2, 3]
    assert bus.latest() == third


def test_buddy_brain_maps_events_to_runtime_friendly_state(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    brain = BuddyBrain()
    state = brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"label": "Needs approval"}, id=7))

    assert state.event_id == 7
    assert state.mood == "concerned"
    assert state.animation == "tap_glass"
    assert state.alert > 80
    assert state.message == "Needs approval"


def test_buddy_brain_decays_transient_actions_back_to_idle(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    done = brain.resolve(BuddyEvent(BuddyEventType.GENERATION_DONE, source="test", id=8))
    assert done.animation == "celebrate_small"

    now = 1005.0
    idle = brain.resolve(None)

    assert idle.event_id == 8
    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_brain_returns_to_active_generation_after_tool_finishes(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.GENERATION_STARTED, source="test", id=9))
    brain.resolve(BuddyEvent(BuddyEventType.TOOL_STARTED, source="test", id=10))
    tool_done = brain.resolve(BuddyEvent(BuddyEventType.TOOL_FINISHED, source="test", id=11))
    assert tool_done.animation == "nod"

    now = 1003.0
    active = brain.resolve(None)

    assert active.animation == "lean_in"
    assert active.message == "Thinking"

    brain.resolve(BuddyEvent(BuddyEventType.GENERATION_DONE, source="test", id=12))
    now = 1006.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"


def test_buddy_brain_clears_approval_on_resolution(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.GENERATION_STARTED, source="test", id=12))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", id=13))
    now = 1010.0
    approval = brain.resolve(None)

    assert approval.animation == "tap_glass"

    denied = brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_DENIED, source="test", id=14))
    assert denied.animation == "pause"

    now = 1013.0
    active = brain.resolve(None)

    assert active.animation == "lean_in"
    assert active.message == "Thinking"

    brain.resolve(BuddyEvent(BuddyEventType.GENERATION_DONE, source="test", id=15))
    now = 1016.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"


def test_buddy_brain_clears_legacy_generic_approval_after_resolution(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STARTED, source="test", payload={"thread_id": "thread-1", "label": "Approval Flow"}, id=30))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"approval_id": "approval-1", "run_id": "run-1", "task_id": "task-1", "label": "Real approval"}, id=31))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"task_id": "task-1", "thread_id": "thread-1", "label": "Needs approval"}, id=32))
    approved = brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_APPROVED, source="test", payload={"approval_id": "approval-1", "run_id": "run-1", "task_id": "task-1", "label": "Approved"}, id=33))

    assert approved.animation == "nod"

    done = brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_DONE, source="test", payload={"thread_id": "thread-1", "label": "Approval Flow done"}, id=34))
    assert done.animation == "celebrate_big"

    now = 1004.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_brain_clears_resume_token_only_approval_after_resolution(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(
        BuddyEventType.APPROVAL_NEEDED,
        source="tasks",
        payload={"resume_token": "resume-1", "label": "Approve step"},
        id=40,
    ))
    now = 1001.0
    approval = brain.resolve(None)

    assert approval.animation == "tap_glass"

    resolved = brain.resolve(BuddyEvent(
        BuddyEventType.APPROVAL_APPROVED,
        source="tasks",
        payload={"resume_token": "resume-1", "label": "Approved"},
        id=41,
    ))

    assert resolved.animation == "nod"

    now = 1009.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_reconcile_clears_stale_durable_approval(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType
    import row_bot.tasks as tasks

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(
        BuddyEventType.APPROVAL_NEEDED,
        source="tasks",
        payload={
            "approval_id": "approval-1",
            "resume_token": "resume-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "label": "Approve step",
        },
        id=42,
    ))
    now = 1007.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_reconcile_keeps_transient_chat_approval(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType
    import row_bot.tasks as tasks

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(
        BuddyEventType.APPROVAL_NEEDED,
        source="ui.streaming",
        payload={"thread_id": "thread-1", "label": "Approval pending"},
        id=43,
    ))
    now = 1007.0
    approval = brain.resolve(None)

    assert approval.animation == "tap_glass"
    assert approval.message == "Approval pending"


def test_buddy_tick_processes_approval_resolution_before_later_workflow_events(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEventBus, BuddyEventType

    now = 1000.0
    bus = BuddyEventBus()
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod, "get_buddy_event_bus", lambda: bus)
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    bus.emit(BuddyEventType.WORKFLOW_STARTED, source="test", payload={"thread_id": "thread-1", "label": "Approval Flow"})
    bus.emit(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"approval_id": "approval-1", "run_id": "run-1", "task_id": "task-1", "label": "Real approval"})
    bus.emit(BuddyEventType.APPROVAL_APPROVED, source="test", payload={"approval_id": "approval-1", "run_id": "run-1", "task_id": "task-1", "label": "Approved"})
    bus.emit(BuddyEventType.WORKFLOW_STARTED, source="test", payload={"thread_id": "thread-1", "label": "Approval Flow"})
    state = bus.emit(BuddyEventType.WORKFLOW_DONE, source="test", payload={"thread_id": "thread-1", "label": "Approval Flow done"})

    done = brain.tick()

    assert done.event_id == state.id
    assert done.animation == "celebrate_big"
    assert done.message == "Approval Flow done"

    now = 1004.0
    idle = brain.tick()

    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_brain_clears_workflow_after_denied_workflow_cancelled(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STARTED, source="test", id=20))
    brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STEP, source="test", payload={"label": "Step 1"}, id=21))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", id=22))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_DENIED, source="test", id=23))
    cancelled = brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_CANCELLED, source="test", payload={"label": "Workflow denied"}, id=24))

    assert cancelled.animation == "pause"
    assert cancelled.message == "Workflow denied"

    now = 1003.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_brain_denied_approval_clears_active_workflow(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STARTED, source="test", id=20))
    brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STEP, source="test", payload={"label": "Middle step"}, id=21))
    denied = brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_DENIED, source="test", payload={"label": "Denied"}, id=22))

    assert denied.animation == "pause"
    assert denied.message == "Denied"

    now = 1003.0
    idle = brain.resolve(None)

    assert idle.animation == "idle_breathe"
    assert idle.message == "Idle"


def test_buddy_config_round_trips_to_temp_data_dir(monkeypatch, tmp_path):
    import row_bot.buddy.config as config_mod

    monkeypatch.setattr(config_mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "_BUDDY_CONFIG_PATH", tmp_path / "buddy_config.json")

    saved = config_mod.save_buddy_config({**config_mod.get_buddy_config(), "placement": "desktop"})
    loaded = config_mod.get_buddy_config()

    assert saved["placement"] == "desktop"
    assert loaded["placement"] == "desktop"
    assert loaded["visible"] is True
    assert loaded["pack_id"] == "glyph"


def test_buddy_config_loads_utf8_bom_file(monkeypatch, tmp_path):
    import row_bot.buddy.config as config_mod

    config_path = tmp_path / "buddy_config.json"
    monkeypatch.setattr(config_mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "_BUDDY_CONFIG_PATH", config_path)
    config_path.write_text('{"floating_enabled": true, "display_name": "BOM Buddy"}', encoding="utf-8-sig")

    loaded = config_mod.get_buddy_config()

    assert loaded["placement"] == "docked"
    assert "floating_enabled" not in loaded
    assert loaded["display_name"] == "BOM Buddy"


def test_buddy_loader_maps_stale_legacy_absolute_hatch_paths(monkeypatch, tmp_path):
    import json
    import row_bot.buddy.assets as assets_mod

    row_bot_static = tmp_path / ".row-bot" / "buddy"
    monkeypatch.setattr(assets_mod, "_BUDDY_STATIC_DIR", row_bot_static)
    monkeypatch.setattr(assets_mod, "_USER_PACKS_DIR", row_bot_static / "packs")
    pack_dir = row_bot_static / "packs" / "hatch-legacy"
    (pack_dir / "motions").mkdir(parents=True)
    (pack_dir / "preview.png").write_bytes(b"PNG")
    (pack_dir / "motions" / "manifest.json").write_text(json.dumps({"clips": {}}), encoding="utf-8")
    legacy_pack_dir = tmp_path / "old-data" / "buddy" / "packs" / "hatch-legacy"
    (pack_dir / "manifest.json").write_text(json.dumps({
        "id": "hatch-legacy",
        "status": "motion_pack_generated",
        "preview_path": str(legacy_pack_dir / "preview.png"),
        "motion_pack_path": str(legacy_pack_dir / "motions" / "manifest.json"),
    }), encoding="utf-8")

    pack = assets_mod.load_buddy_pack("hatch-legacy")

    assert not (tmp_path / "old-data").exists()
    assert pack.status == "available" and pack.derived
    assert pack.runtime == "generated_motion_pack"
    assert pack.preview_path == (pack_dir / "preview.png").resolve()


def test_builtin_buddy_pack_contract_is_explicit():
    import json
    from row_bot.buddy.assets import list_buddy_packs, load_buddy_pack

    expected_pack_ids = {"ember", "glyph", "lumen", "orbit", "pixel", "sprout"}
    assert expected_pack_ids.issubset({pack.id for pack in list_buddy_packs()})
    for pack_id in sorted(expected_pack_ids):
        pack = load_buddy_pack(pack_id, strict=True)
        assert pack.status == "available", (pack_id, pack.message)
        # Every built-in ships as a living Buddy: cut-out art, a rig, no video.
        assert pack.runtime == "living" and not pack.derived
        assert not (pack.art_path.parent / "motions").exists()
        rig = json.loads(pack.rig_path.read_text(encoding="utf-8"))
        assert rig["family"] in {"floater", "biped", "winged", "free"}
        assert {"body", "head"} <= {bone["id"] for bone in rig["bones"]}
        assert rig["eyes"], pack_id


def test_stop_task_emits_buddy_cancel_immediately(monkeypatch):
    import row_bot.tasks as tasks_mod

    seen: list[tuple[str, dict[str, str]]] = []

    def _fake_emit(status: str, **payload: str) -> None:
        seen.append((status, payload))

    stop_event = threading.Event()
    monkeypatch.setattr(tasks_mod, "_emit_buddy_workflow_event", _fake_emit)
    with tasks_mod._active_lock:
        tasks_mod._active_runs["thread-1"] = {
            "task_id": "task-1",
            "name": "Daily Briefing",
            "stop_event": stop_event,
        }
    try:
        assert tasks_mod.stop_task("thread-1") is True
    finally:
        with tasks_mod._active_lock:
            tasks_mod._active_runs.pop("thread-1", None)

    assert stop_event.is_set()
    assert seen == [("cancelled", {"task_id": "task-1", "thread_id": "thread-1", "label": "Stopping Daily Briefing"})]


def test_buddy_hatch_background_job_starts_without_blocking(monkeypatch):
    from uuid import uuid4
    import row_bot.buddy.hatch as hatch_mod

    with hatch_mod._JOB_LOCK:
        hatch_mod._CURRENT_JOB.clear()
    started = {"value": False}

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            started["value"] = True

    monkeypatch.setattr(hatch_mod.threading, "Thread", FakeThread)
    admit = dict(mode="full", total_steps=7, _client_runner=lambda *args: None, _validate=lambda: None,
                 _checkpoint=lambda job_id: None)
    queued = hatch_mod.start_hatch_generation_job("A tiny dog named Honey", _command_id=str(uuid4()), **admit)
    running = hatch_mod.get_hatch_generation_status()
    assert started["value"] is True
    assert queued["status"] == "queued" and running["status"] == "running"
    assert running["mode"] == "full" and running["total_clips"] == 7
    with pytest.raises(RuntimeError, match="already running"):
        hatch_mod.start_hatch_generation_job("Another Buddy", _command_id=str(uuid4()), **admit)
    with hatch_mod._JOB_LOCK:
        hatch_mod._CURRENT_JOB.clear()


def test_buddy_brain_decays_stale_event_to_idle(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    brain = BuddyBrain()
    brain.resolve(BuddyEvent(BuddyEventType.GENERATION_STARTED, source="test", id=9))
    monkeypatch.setattr(brain_mod.time, "time", lambda: brain.state.updated_at + 121)

    state = brain.resolve(None)

    assert state.animation == "idle_breathe"
    assert state.message == "Idle"


def test_buddy_reconcile_clears_a_chat_approval_answered_elsewhere(monkeypatch):
    """B333: a chat's approval names its request, so an answer from anywhere clears it."""
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType
    import row_bot.tasks as tasks

    now = 1000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    # Answered (on a phone, say) before Buddy next read the waiting approvals.
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.GENERATION_STARTED, source="client_platform",
                             payload={"thread_id": "thread-1", "label": "Thinking"}, id=60))
    brain.resolve(BuddyEvent(
        BuddyEventType.APPROVAL_NEEDED,
        source="client_platform",
        payload={"thread_id": "thread-1", "label": "Approval pending", "approval_id": "chat-approval"},
        id=61,
    ))
    now = 1003.0
    working = brain.resolve(None)

    assert working.animation == "lean_in"
    assert working.message == "Thinking"
