"""Shared set-up for "Save as a skill" (C1): a fresh data folder, an isolated skill library, a fake model."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class FakeModel:
    """The chat's own model for background calls: records each call and answers from a script."""

    replies: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def answer(self, purpose: str, *replies: Any) -> None:
        self.replies[purpose] = list(replies)

    def invoke_background(self, purpose, messages, *, answer=None, thread_model=None, thread_id="", **_kw):
        self.calls.append({"purpose": purpose, "system": str(messages[0].content), "body": str(messages[-1].content),
                           "thread_model": thread_model, "thread_id": thread_id, "schema": getattr(answer, "name", "")})
        queue = self.replies.get(purpose) or []
        if not queue:
            raise AssertionError(f"no scripted {purpose} reply")
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def purposes(self) -> list[str]:
        return [call["purpose"] for call in self.calls]


@pytest.fixture
def offer_stack(reload_for_data_dir, tmp_path, monkeypatch):
    root = tmp_path / "data"
    (threads,) = reload_for_data_dir(root, "row_bot.threads")
    from row_bot import models, skills, tasks
    from row_bot.providers import background

    for name, path in [
        ("DATA_DIR", root),
        ("USER_SKILLS_DIR", root / "skills"),
        ("BUNDLED_SKILLS_DIR", tmp_path / "bundled"),
        ("TOOL_GUIDES_DIR", tmp_path / "guides"),
        ("CONFIG_PATH", root / "skills_config.json"),
    ]:
        monkeypatch.setattr(skills, name, path)
    monkeypatch.setattr(skills, "_skills_cache", {})
    monkeypatch.setattr(skills, "_enabled", {})
    monkeypatch.setattr(skills, "_pinned", [])
    monkeypatch.setattr(tasks, "_DB_PATH", str(root / "tasks.db"))
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    model = FakeModel()
    monkeypatch.setattr(background, "invoke_background", model.invoke_background)
    return threads, model
