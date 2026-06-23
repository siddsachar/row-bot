from __future__ import annotations

import pytest

from tests.fixtures.channels import FakeChannel
from tests.fixtures.tasks import fresh_tasks_module


pytestmark = pytest.mark.subsystem


def test_cross_channel_approval_resolution_updates_other_channels(tmp_path, monkeypatch) -> None:
    tasks = fresh_tasks_module(tmp_path, monkeypatch)
    from row_bot.channels import registry

    registry._reset()
    source = FakeChannel(name="source")
    mirror = FakeChannel(name="mirror")
    source._running = True
    mirror._running = True
    registry.register(source)
    registry.register(mirror)

    token, approval_id = tasks.create_approval_request("run-1", "task-1", "approval_1", "Approve?")
    tasks._store_approval_channel_ref(approval_id, "source", "source-ref")
    tasks._store_approval_channel_ref(approval_id, "mirror", "mirror-ref")
    monkeypatch.setattr(tasks, "_resume_pipeline", lambda *_args, **_kwargs: None)

    assert tasks.respond_to_approval(token, True, source="source") is True

    assert source.approval_updates == []
    assert mirror.approval_updates == [("mirror-ref", "approved", "source")]


def test_channel_approval_helpers_round_trip_interrupt_text() -> None:
    from row_bot.channels.approval import extract_interrupt_ids, format_interrupt_text, is_approval_text

    interrupt_data = [
        {"__interrupt_id": "abc", "tool": "developer_apply_patch", "description": "Apply patch"},
        {"__interrupt_id": "def", "tool": "shell", "description": "Run command"},
    ]
    text = format_interrupt_text(interrupt_data)

    assert "Apply patch" in text
    assert extract_interrupt_ids(interrupt_data) == ["abc", "def"]
    assert is_approval_text("approve") is True
    assert is_approval_text("deny") is False
    assert is_approval_text(text) is None
