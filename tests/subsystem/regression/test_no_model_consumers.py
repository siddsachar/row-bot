"""Every consumer of the current model refuses politely when none is chosen.

Decision 9: nothing is preset and nothing silently falls back. Chat, workflows,
channels, Dream Cycle, memory extraction, Buddy and delegated agents all meet
the same "no model yet" answer instead of running on a built-in model.
"""
from __future__ import annotations

import contextvars
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from row_bot import models

pytestmark = pytest.mark.subsystem


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(models, "_current_model", "")
    monkeypatch.setattr(models, "_llm_instance", None)


def test_agent_graph_refuses_without_a_model(no_model):
    from row_bot import agent

    with pytest.raises(models.NoModelChosenError):
        agent.get_agent_graph([])
    with pytest.raises(models.NoModelChosenError):
        agent._selected_model_label_from_config({"configurable": {}})


def test_channel_message_gets_a_polite_reply(no_model, monkeypatch):
    from row_bot import agent

    monkeypatch.setattr(agent, "_route_waiting_parent_input", lambda *_args: None)
    monkeypatch.setattr(agent, "_custom_tool_builder_disabled_response", lambda *_args: "")
    events = list(agent.stream_agent(
        "hello", [], {"configurable": {"thread_id": "channel-thread", "runtime_surface": "channel"}},
    ))
    assert events == [("token", agent.NO_MODEL_CHANNEL_REPLY), ("done", agent.NO_MODEL_CHANNEL_REPLY)]
    assert "choose how it should think" in agent.NO_MODEL_CHANNEL_REPLY


def test_chat_turn_without_a_model_is_refused_before_running(no_model, monkeypatch):
    from row_bot import agent

    monkeypatch.setattr(agent, "_route_waiting_parent_input", lambda *_args: None)
    monkeypatch.setattr(agent, "_custom_tool_builder_disabled_response", lambda *_args: "")
    with pytest.raises(models.NoModelChosenError):
        list(agent.stream_agent("hello", [], {"configurable": {"thread_id": "chat-thread"}}))


def _fresh_task_modules(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name in ("row_bot.tasks", "row_bot.threads", "row_bot.agent_profiles", "row_bot.agent_runs"):
        sys.modules.pop(name, None)
    import row_bot.tasks as tasks
    import row_bot.threads as threads

    return importlib.reload(tasks), importlib.reload(threads)


def test_workflow_run_fails_with_the_reason_and_no_retries(tmp_path, monkeypatch, no_model):
    tasks, threads = _fresh_task_modules(tmp_path, monkeypatch)
    calls: list[str] = []
    fake_agent = types.ModuleType("row_bot.agent")

    class TaskStoppedError(Exception):
        pass

    def invoke_agent(prompt, _tools, _config, **_kwargs):
        calls.append(prompt)
        raise models.NoModelChosenError()

    fake_agent.TaskStoppedError = TaskStoppedError
    fake_agent.RECURSION_LIMIT_TASK = 12
    fake_agent.invoke_agent = invoke_agent
    fake_agent.resume_invoke_agent = lambda *args, **kwargs: ""
    fake_agent.repair_orphaned_tool_calls = lambda *args, **kwargs: None
    fake_agent._approval_mode_var = contextvars.ContextVar("approval_mode", default="approve")
    fake_agent._background_workflow_var = contextvars.ContextVar("background_workflow", default=False)
    fake_agent._persistent_thread_var = contextvars.ContextVar("persistent_thread", default=False)
    monkeypatch.setitem(sys.modules, "row_bot.agent", fake_agent)

    class ImmediateThread:
        def __init__(self, target, *args, **kwargs):
            self._target, self._args, self._kwargs = target, tuple(kwargs.get("args") or ()), dict(kwargs.get("kwargs") or {})

        def start(self):
            self._target(*self._args, **self._kwargs)

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

    monkeypatch.setattr(tasks.threading, "Thread", ImmediateThread)
    task_id = tasks.create_task(
        "No model yet",
        steps=[{"type": "prompt", "prompt": "Summarise today", "max_retries": 3, "retry_delay_seconds": 0}],
        channels=[],
        apply_default_skills=False,
    )
    thread_id = threads.create_thread("Workflow run", thread_id="workflow-no-model")

    tasks.run_task_background(task_id, thread_id, [], notification=False)

    assert calls == ["Summarise today"]
    [run] = tasks.get_run_history(task_id, limit=1)
    assert run["status"] == "failed"
    assert run["status_message"] == models.NO_MODEL_CHOSEN


def test_memory_extraction_waits_and_keeps_its_bookmark(no_model, monkeypatch):
    from row_bot import memory_extraction

    saved: list[dict] = []
    monkeypatch.setattr(memory_extraction, "_save_state", saved.append)
    monkeypatch.setattr("row_bot.threads._list_threads", lambda: [("t1", "Chat", "2026-01-01", "2026-09-01")])
    statuses: list[str] = []

    assert memory_extraction.run_extraction(on_status=statuses.append) == 0
    assert saved == []
    assert statuses == ["Waiting for a model: memories are read once one is chosen"]


def test_dream_cycle_waits_for_a_model(no_model, monkeypatch):
    from row_bot import dream_cycle

    for name in ("is_enabled", "_in_dream_window", "_is_idle"):
        monkeypatch.setattr(dream_cycle, name, lambda: True)
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: False)
    monkeypatch.setattr(dream_cycle, "_is_ollama_busy", lambda: False)
    assert dream_cycle._should_dream() is False

    journal: list[dict] = []
    monkeypatch.setattr(dream_cycle, "_append_journal", journal.append)
    summary = dream_cycle.run_dream_cycle()
    assert summary["summary"] == dream_cycle.NO_MODEL_SKIP
    assert journal == []


def test_buddy_hatch_asks_for_an_image_model(monkeypatch):
    from row_bot.application import buddy_policy
    from row_bot.application.client_platform import ClientPlatformError

    registry = types.SimpleNamespace(
        get_tool=lambda _name: object(),
        is_enabled=lambda _name: True,
        get_tool_config=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "row_bot.tools.registry", registry)
    with pytest.raises(ClientPlatformError) as refused:
        buddy_policy._media_state("image", {})
    assert refused.value.code == "buddy_media_capability_unavailable"


def test_delegated_agent_is_not_started_without_a_model(no_model, monkeypatch):
    from row_bot.tools import agent_tool

    monkeypatch.setattr(agent_tool, "_runtime_context", lambda: {"thread_id": "parent-thread"})
    answer = json.loads(agent_tool._delegate_work(objective="Summarise the notes"))
    assert answer["ok"] is False
    assert answer["message"] == models.NO_MODEL_CHOSEN
