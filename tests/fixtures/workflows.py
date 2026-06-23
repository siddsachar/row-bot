from __future__ import annotations

import importlib
from pathlib import Path


def fresh_tasks_module(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    import row_bot.tasks as tasks

    return importlib.reload(tasks)


def sample_workflow_steps() -> list[dict]:
    return [
        {"type": "prompt", "id": "draft", "prompt": "Draft a summary", "next": "approval"},
        {"type": "approval", "id": "approval", "message": "Approve summary?", "if_approved": "notify", "if_denied": "end"},
        {"type": "notify", "id": "notify", "channel": "fake", "message": "Done"},
    ]
