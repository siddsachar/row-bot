"""agent_wait must not stall on a child that needs this turn's own writer."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def wait_tool(monkeypatch):
    from row_bot import agent_runs
    from row_bot.tools import agent_tool

    queued = {
        "id": "child-1",
        "status": "queued",
        "write_lock_key": "developer:workspace-1",
        "workspace_id": "workspace-1",
    }
    holder = {"run_id": "chat-execution-1", "thread_id": "parent-1"}
    state = {"queued": [queued], "holder": holder, "waited": []}
    monkeypatch.setattr(agent_tool, "_runtime_context", lambda: {"thread_id": "parent-1"})
    monkeypatch.setattr(
        agent_tool,
        "list_agent_runs",
        lambda **kwargs: [
            run for run in state["queued"] if "queued" in kwargs.get("statuses", [])
        ],
    )
    monkeypatch.setattr(agent_tool, "get_agent_parent_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(agent_tool, "get_agent_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        agent_runs,
        "get_agent_write_lock",
        lambda key: state["holder"] if key == "developer:workspace-1" else None,
    )

    def wait(run_id, timeout=None):
        state["waited"].append(run_id)
        return {**queued, "status": "completed"}

    monkeypatch.setattr(agent_tool.agent_runner, "wait_for_agent_run", wait)
    return agent_tool, state


def test_wait_returns_at_once_when_the_child_needs_this_turns_writer(wait_tool):
    agent_tool, state = wait_tool
    payload = json.loads(agent_tool._agent_wait(run_id="child-1", timeout_seconds=60))
    assert state["waited"] == []
    assert [run["id"] for run in payload["blocked_by_this_turn"]] == ["child-1"]
    assert "end this turn" in payload["message"]


def test_wait_still_waits_when_another_run_holds_the_writer(wait_tool):
    agent_tool, state = wait_tool
    state["holder"] = {"run_id": "other-child", "thread_id": "another-thread"}
    payload = json.loads(agent_tool._agent_wait(run_id="child-1", timeout_seconds=1))
    assert state["waited"] == ["child-1"]
    assert "blocked_by_this_turn" not in payload


def test_group_wait_does_not_stall_on_a_child_blocked_by_this_turn(wait_tool, monkeypatch):
    from row_bot import agent_orchestrator

    agent_tool, _state = wait_tool
    monkeypatch.setattr(
        agent_orchestrator,
        "get_orchestration",
        lambda identifier: {"id": identifier, "parent_thread_id": "parent-1"},
    )

    def never(*args, **kwargs):
        raise AssertionError("waited on a cohort that cannot start")

    monkeypatch.setattr(agent_orchestrator, "wait_for_required_group", never)
    payload = json.loads(
        agent_tool._agent_wait(orchestration_id="orchestration-1", timeout_seconds=60)
    )
    assert payload["ok"] is True
    assert payload["blocked_by_this_turn"][0]["id"] == "child-1"
