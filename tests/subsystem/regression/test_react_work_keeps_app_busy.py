"""A running React turn makes the app busy for every idle check (B128, B187).

The idle and busy checks used to read the NiceGUI page's own generation list,
which never holds a React turn, so Dream Cycle, memory extraction, checkpoint
cleanup, browser-tab eviction, conversation deletion and the status tool all
treated the app as idle while a React chat was streaming. They now read the
one generation registry every producer registers with.
"""
from __future__ import annotations

import pytest

from row_bot.runtime import executions


pytestmark = pytest.mark.subsystem

CONVERSATION = "react-conversation"


@pytest.fixture
def running_turn(monkeypatch):
    """A React turn in flight, registered the way `admit_execution` does."""
    registry = executions.GenerationRuntimeRegistry()
    monkeypatch.setattr(executions, "generation_registry", registry)
    handle = registry.register(CONVERSATION)
    yield handle
    registry.finish(handle)


@pytest.fixture
def no_turn(monkeypatch):
    registry = executions.GenerationRuntimeRegistry()
    monkeypatch.setattr(executions, "generation_registry", registry)
    return registry


def _dream_gates_open(monkeypatch):
    from row_bot import dream_cycle, models

    monkeypatch.setattr(models, "_current_model", "model:ollama:fixture-model")
    monkeypatch.setattr(dream_cycle, "is_enabled", lambda: True)
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: False)
    monkeypatch.setattr(dream_cycle, "_in_dream_window", lambda: True)
    monkeypatch.setattr(dream_cycle, "_is_idle", lambda: True)
    monkeypatch.setattr(dream_cycle, "_is_ollama_busy", lambda: False)
    return dream_cycle


def test_dream_cycle_waits_while_a_react_turn_streams(monkeypatch, running_turn) -> None:
    dream_cycle = _dream_gates_open(monkeypatch)

    assert dream_cycle._should_dream() is False


def test_dream_cycle_runs_when_no_turn_streams(monkeypatch, no_turn) -> None:
    dream_cycle = _dream_gates_open(monkeypatch)

    assert dream_cycle._should_dream() is True


def _extraction_otherwise_idle(monkeypatch):
    from row_bot import document_extraction, memory_extraction, tasks

    monkeypatch.setattr(memory_extraction, "idle_seconds", lambda: 10_000.0)
    monkeypatch.setattr(document_extraction, "get_extraction_status", lambda: {"status": "idle"})
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    return memory_extraction


def test_memory_extraction_and_checkpoint_cleanup_wait_for_a_react_turn(monkeypatch, running_turn) -> None:
    memory_extraction = _extraction_otherwise_idle(monkeypatch)

    assert memory_extraction.is_app_idle() is False


def test_memory_extraction_is_idle_without_a_turn(monkeypatch, no_turn) -> None:
    memory_extraction = _extraction_otherwise_idle(monkeypatch)

    assert memory_extraction.is_app_idle() is True


def test_checkpoint_cleanup_skips_the_streaming_conversation(monkeypatch, tmp_path, running_turn) -> None:
    from row_bot import threads

    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "missing.db"))

    assert CONVERSATION in threads._checkpoint_cleanup_skip_threads("9999-01-01T00:00:00")


def test_conversation_deletion_sees_the_streaming_producer(running_turn) -> None:
    from row_bot import thread_cleanup

    assert thread_cleanup._thread_has_active_producer(CONVERSATION) is True
    assert thread_cleanup._thread_has_active_producer("another-conversation") is False


def test_browser_tabs_of_a_streaming_conversation_are_not_evicted(running_turn) -> None:
    from row_bot.browser import service

    assert service._active_generation_thread_ids() == {CONVERSATION}


def test_a_stopped_turn_no_longer_counts_for_browser_eviction(running_turn) -> None:
    from row_bot.browser import service

    executions.generation_registry.finish(running_turn, status="stopped")

    assert service._active_generation_thread_ids() == set()


def test_status_tool_reports_the_streaming_turn(monkeypatch, running_turn) -> None:
    from row_bot.tools.row_bot_status_tool import _query_voice

    monkeypatch.setattr("row_bot.voice.openai_realtime.get_key", lambda name: "")

    output = _query_voice()

    assert "Active Row-Bot runs: 1" in output
    assert CONVERSATION in output
