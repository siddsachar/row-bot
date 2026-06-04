from __future__ import annotations

import json
import types


def test_document_status_uses_processed_file_count(monkeypatch, tmp_path):
    import row_bot.documents as documents
    from row_bot.ui.status_checks import check_document_store

    processed_path = tmp_path / "processed_files.json"
    vector_dir = tmp_path / "vector_store"
    vector_dir.mkdir()
    processed_path.write_text(json.dumps(["alpha.pdf", "beta.md", "gamma.txt"]), encoding="utf-8")

    monkeypatch.setattr(documents, "PROCESSED_FILES_PATH", processed_path)
    monkeypatch.setattr(documents, "VECTOR_STORE_DIR", vector_dir)
    monkeypatch.setattr(documents, "document_vector_status", lambda: {"exists": True, "stale": False})

    result = check_document_store()

    assert result.name == "Documents"
    assert result.status == "ok"
    assert result.detail == "3 docs indexed"
    assert result.settings_tab == "Documents"


def test_workflow_status_reports_running_and_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path))
    import row_bot.tasks as tasks
    from row_bot.ui.status_checks import check_task_scheduler

    monkeypatch.setattr(tasks, "_scheduler", types.SimpleNamespace(get_jobs=lambda: [object(), object()]))
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {"thread-1": {}, "thread-2": {}})
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [{"id": "a1"}])

    result = check_task_scheduler()

    assert result.name == "Workflows"
    assert result.status == "warn"
    assert "2 scheduled" in result.detail
    assert "2 running" in result.detail
    assert "1 approval waiting" in result.detail


def test_home_status_has_aggregate_pills_for_current_settings_tabs():
    src = open("ui/status_checks.py", "r", encoding="utf-8").read()

    expected = {
        "Search": "check_search_tools",
        "Skills": "check_skills",
        "Tracker": "check_tracker",
        "Buddy": "check_buddy",
        "MCP": "check_mcp",
        "Plugins": "check_plugins",
    }
    for tab, function_name in expected.items():
        assert f'settings_tab="{tab}"' in src
        assert f"def {function_name}" in src


def test_home_status_uses_compact_icon_pills():
    src = open("ui/status_bar.py", "r", encoding="utf-8").read()

    assert "_STATUS_ICON_MAP" in src
    assert "status-icon-pill" in src
    assert "status-alert-badge" in src
    assert "status-warn" in src
    assert "status-error" in src
    assert "aria-label" in src
    assert "mid = (len(items) + 1) // 2" not in src
    assert "for row_items in (items[:mid], items[mid:])" not in src
    assert "row-bot-buddy-hatch-progress" in src
    assert "extraction_pill" in src
    assert ".tooltip(f\"{r.name}: {r.status_label} - {r.detail}\")" in src
    for status_name in ("Ollama", "Documents", "Search", "MCP", "Plugins"):
        assert f'"{status_name}"' in src


def test_home_status_icon_map_uses_safe_material_icons():
    src = open("ui/status_bar.py", "r", encoding="utf-8").read()

    assert '"Disk": "save"' in src
    assert '"Threads DB": "storage"' in src
    assert '"FAISS Index": "bubble_chart"' in src
    for unsupported in ('"hard_drive"', '"database"', '"deployed_code"', '"sd_storage"', '"grain"'):
        assert unsupported not in src


def test_home_status_has_single_faiss_check():
    from row_bot.ui.status_checks import ALL_CHECKS, run_all_checks

    faiss_check_count = sum(1 for fn in ALL_CHECKS if fn.__name__ == "check_faiss_index")
    assert faiss_check_count == 1

    results = run_all_checks()
    names = [result.name for result in results]
    assert names.count("FAISS Index") == 1
    assert names.count("Disk") == 1
    assert names.count("Threads DB") == 1


def test_command_center_has_persisted_collapsed_rail_contract():
    src = open("ui/command_center.py", "r", encoding="utf-8").read()

    assert "workflow_console_collapsed" in src
    assert "workflow-console-rail" in src
    assert "workflow-console-collapsed .workflow-console-rail" in src
    assert "workflow-console-approval-alert" in src
    assert "workflow-console-alert-flash" in src
    assert "workflow-console-rail-badge insights" in src
    assert "_save_command_center_collapsed" in src
    assert "data-workflow-console-drawer" in src


def test_buddy_state_machine_preserves_workflow_after_approval(monkeypatch):
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

    brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_STARTED, source="test", payload={"thread_id": "thread-1", "label": "Daily Briefing"}, id=1))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"approval_id": "approval-1", "label": "Review step"}, id=2))
    now = 1010.0
    approval = brain.resolve(None)
    assert approval.animation == "tap_glass"
    assert approval.message == "Review step"

    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_APPROVED, source="test", payload={"approval_id": "approval-1", "label": "Approved"}, id=3))
    now = 1013.0
    workflow = brain.resolve(None)
    assert workflow.animation == "pack_bag"
    assert workflow.message == "Daily Briefing"

    done = brain.resolve(BuddyEvent(BuddyEventType.WORKFLOW_DONE, source="test", payload={"thread_id": "thread-1", "label": "Daily Briefing done"}, id=4))
    assert done.animation == "celebrate_big"
    now = 1017.0
    idle = brain.resolve(None)
    assert idle.animation == "idle_breathe"


def test_buddy_state_machine_keeps_other_pending_approval(monkeypatch):
    import row_bot.buddy.brain as brain_mod
    from row_bot.buddy.brain import BuddyBrain
    from row_bot.buddy.events import BuddyEvent, BuddyEventType

    now = 2000.0
    monkeypatch.setattr(
        brain_mod,
        "get_buddy_config",
        lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"},
    )
    monkeypatch.setattr(brain_mod.time, "time", lambda: now)
    brain = BuddyBrain()

    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"approval_id": "a1", "label": "First approval"}, id=10))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_NEEDED, source="test", payload={"approval_id": "a2", "label": "Second approval"}, id=11))
    brain.resolve(BuddyEvent(BuddyEventType.APPROVAL_APPROVED, source="test", payload={"approval_id": "a1", "label": "Approved"}, id=12))

    now = 2003.0
    still_pending = brain.resolve(None)

    assert still_pending.animation == "tap_glass"
    assert still_pending.message == "Second approval"
