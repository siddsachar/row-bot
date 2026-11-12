"""The vision model's backup (R10): a video's checks were skipped when the Claude plan's limit was reached.

When the vision model can't answer for a reason a chat's backup model would take over (a usage limit, an
outage), the backup model the person chose looks instead, and the answer says so. Nothing calls a provider.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem


class _Limited(RuntimeError):
    status_code = 429


def _look(monkeypatch, *, backup: str | None, backup_fails: bool = False):
    from row_bot import models, vision
    from row_bot.providers import backup as backup_owner

    asked: list[str] = []

    def llm_for(ref: str):
        def invoke(_messages):
            asked.append(ref)
            if ref == "model:claude_subscription:claude-opus-5-5" or backup_fails:
                raise _Limited("Your Claude plan's limit is reached until 4:50pm.")
            return SimpleNamespace(content="The titles are clear of the results.")
        return SimpleNamespace(invoke=invoke)

    monkeypatch.setattr(models, "get_llm_for", llm_for)
    monkeypatch.setattr(backup_owner, "backup_model", lambda: backup)
    monkeypatch.setattr(backup_owner, "model_name", lambda ref: ref.rsplit(":", 1)[-1])
    service = vision.VisionService.__new__(vision.VisionService)
    answer = service._invoke_provider([{"type": "text", "text": "Are the titles clear?"}],
                                      "model:claude_subscription:claude-opus-5-5")
    return answer, asked


def test_the_backup_model_looks_when_the_vision_model_is_at_its_limit(monkeypatch):
    answer, asked = _look(monkeypatch, backup="model:openai:gpt-5.6-sol")

    assert asked == ["model:claude_subscription:claude-opus-5-5", "model:openai:gpt-5.6-sol"]
    assert answer.startswith("claude-opus-5-5 didn't answer (it's busy right now), so gpt-5.6-sol took over.")
    assert answer.endswith("The titles are clear of the results.")


@pytest.mark.parametrize("backup", [None, "model:claude_subscription:claude-opus-5-5"])
def test_without_another_backup_it_says_the_vision_model_failed(monkeypatch, backup):
    answer, asked = _look(monkeypatch, backup=backup)

    assert asked == ["model:claude_subscription:claude-opus-5-5"]
    assert answer.startswith("Vision analysis failed for")


def test_a_backup_that_cannot_look_either_leaves_the_first_failure(monkeypatch):
    answer, asked = _look(monkeypatch, backup="model:openai:gpt-5.6-sol", backup_fails=True)

    assert len(asked) == 2 and answer.startswith("Vision analysis failed for")
