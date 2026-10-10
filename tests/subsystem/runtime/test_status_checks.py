from __future__ import annotations

import importlib
import json
import types

import pytest


@pytest.fixture
def no_durable_activity(monkeypatch):
    """The brain also reconciles with running workflows, pending approvals and Agent groups: none here,
    whatever an earlier test on the worker left behind."""
    import row_bot.agent_orchestrator as orchestrator
    import row_bot.tasks as tasks
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda *args, **kwargs: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda *args, **kwargs: {})
    monkeypatch.setattr(orchestrator, "get_thread_orchestration_activity", lambda *args, **kwargs: {})


def test_document_status_uses_processed_file_count(monkeypatch, tmp_path):
    # Its own data folder: the durable document store starts empty, whatever
    # another test on the worker left in the shared one.
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    # The module the check imports, not a package attribute an earlier test's
    # re-import may have left pointing at another one.
    documents = importlib.import_module("row_bot.documents")
    from row_bot.status_checks import check_document_store

    processed_path = tmp_path / "processed_files.json"
    vector_dir = tmp_path / "vector_store"
    vector_dir.mkdir()
    processed_path.write_text(json.dumps(["alpha.pdf", "beta.md", "gamma.txt"]), encoding="utf-8")

    # Its durable records are read from the module's data folder, bound at import.
    monkeypatch.setattr(documents, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(documents, "PROCESSED_FILES_PATH", processed_path)
    monkeypatch.setattr(documents, "VECTOR_STORE_DIR", vector_dir)
    monkeypatch.setattr(documents, "document_vector_status", lambda: {"exists": True, "stale": False})

    result = check_document_store()

    assert result.name == "Documents"
    assert result.status == "ok"
    assert result.detail == "3 docs indexed"
    assert result.settings_tab == "Documents"


def test_workflow_status_reports_running_and_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    import row_bot.tasks as tasks
    from row_bot.status_checks import check_task_scheduler

    monkeypatch.setattr(tasks, "_scheduler", types.SimpleNamespace(get_jobs=lambda: [object(), object()]))
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {"thread-1": {}, "thread-2": {}})
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [{"id": "a1"}])

    result = check_task_scheduler()

    assert result.name == "Workflows"
    assert result.status == "warn"
    assert "2 scheduled" in result.detail
    assert "2 running" in result.detail
    assert "1 approval waiting" in result.detail


def test_home_status_has_single_faiss_check(monkeypatch):
    import socket

    import row_bot.github_account as github_account
    import row_bot.models as models
    import row_bot.status_checks as status_checks
    from row_bot.status_checks import ALL_CHECKS, run_checks

    def offline(*_args, **_kwargs):
        raise OSError("offline")

    # The live probes (local Ollama, GitHub, the internet) answer as offline (B192).
    monkeypatch.setattr(models, "_ollama_reachable", lambda **_kwargs: False)
    monkeypatch.setattr(github_account, "get_verified_github_account_status", offline)
    monkeypatch.setattr(socket, "create_connection", offline)
    monkeypatch.setattr(status_checks, "_probe_cache", {})
    faiss_check_count = sum(1 for fn in ALL_CHECKS if fn.__name__ == "check_faiss_index")
    assert faiss_check_count == 1

    results = run_checks(tuple(ALL_CHECKS))
    names = [result.name for _check_id, result in results]
    assert names.count("FAISS Index") == 1
    assert names.count("Disk") == 1
    assert names.count("Threads DB") == 1


def test_buddy_state_machine_preserves_workflow_after_approval(monkeypatch, no_durable_activity):
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


def test_buddy_state_machine_keeps_other_pending_approval(monkeypatch, no_durable_activity):
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
