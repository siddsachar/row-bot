from __future__ import annotations

from pathlib import Path


def test_mobile_workflow_editor_contains_simple_workflow_fields() -> None:
    src = Path("src/row_bot/ui/mobile_workflows.py").read_text(encoding="utf-8")

    assert "data-docs-id=mobile-workflow-editor" in src
    assert "Approval mode" in src
    assert "Model" in src
    assert "Agent profile" in src
    assert "Channels" in src
    assert "Keep conversation history across runs" in src
    assert "Add prompt step" in src
    assert "Schedule" in src


def test_mobile_workflow_save_uses_task_create_update_paths() -> None:
    src = Path("src/row_bot/ui/mobile_workflows.py").read_text(encoding="utf-8")

    assert "create_task(" in src
    assert "update_task(" in src
    assert "apply_default_skills=False" in src
    assert "run_task_background(" in src
    assert "mobile_workflow_started" in src
    assert "you'll be notified when done" in src
    assert "open_thread_on_mobile(" not in src


def test_mobile_workflow_editor_preserves_advanced_workflows() -> None:
    src = Path("src/row_bot/ui/mobile_workflows.py").read_text(encoding="utf-8")

    assert "def _is_advanced_workflow(" in src
    assert "Advanced graph steps are preserved" in src
    assert "if not advanced and cur_prompts" in src
    assert 'updates["steps"] = []' in src
