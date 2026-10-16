"""Learning off (the default) is inert: no learning.db, no learning/ folder and
no other Learning file; no Learning module beyond the switch; no thread. Shown
through public behaviour: the switch itself, a Settings read, start-up sweeps,
a conversation deletion and a memory delete."""

from __future__ import annotations

import importlib
import sys
import threading
import uuid
from datetime import datetime

import pytest

# Bound to the session's data folder at import, as in the app (not to a test's folder).
from row_bot import threads, thread_cleanup
from tests.fixtures.learning import learning_files, save_learning_settings, set_learning_preview
from tests.fixtures.memory_stack import fresh_memory_stack

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def _learning_modules() -> list[str]:
    return sorted(name for name in sys.modules if name == "row_bot.learning" or name.startswith("row_bot.learning."))


@pytest.fixture
def no_learning_modules(monkeypatch):
    """Forget every Learning module beyond the switch, so the test sees what gets imported."""

    for name in _learning_modules():
        if name != "row_bot.learning":
            monkeypatch.delitem(sys.modules, name)
    assert _learning_modules() in ([], ["row_bot.learning"])


def test_importing_the_switch_loads_nothing_else_and_starts_nothing(data_dir, monkeypatch):
    for name in _learning_modules():
        monkeypatch.delitem(sys.modules, name)
    before = set(threading.enumerate())

    gate = importlib.import_module("row_bot.learning")
    assert gate.is_enabled() is False
    assert gate.read_settings().enabled is False
    assert gate.store_exists() is False

    assert _learning_modules() == ["row_bot.learning"]
    assert set(threading.enumerate()) <= before
    assert learning_files(data_dir) == []


def test_reading_settings_with_learning_off_creates_no_learning_files(data_dir, no_learning_modules):
    from row_bot.application.settings_snapshot import read_settings_snapshot

    snapshot = read_settings_snapshot()

    assert snapshot["learning"]["enabled"] is False and snapshot["learning"]["has_data"] is False
    assert learning_files(data_dir) == []
    assert _learning_modules() == ["row_bot.learning"]


def test_start_up_sweeps_with_learning_off_leave_no_learning_files(tmp_path, reload_for_data_dir, no_learning_modules):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fresh_threads, cleanup = reload_for_data_dir(
        data_dir,
        "row_bot.tasks",
        "row_bot.threads",
        "row_bot.thread_cleanup",
        "row_bot.agent_runs",
        "row_bot.designer.storage",
        "row_bot.designer.history",
        "row_bot.designer.publish",
        "row_bot.developer.storage",
        "row_bot.developer.todos",
        "row_bot.developer.sandbox_runtime",
    )[1:3]
    fresh_threads.create_thread("A chat", thread_id="startup-chat", seed_default_skills=False)

    cleanup.sweep_data_dir_leftovers(data_dir)
    stats = cleanup.sweep_orphaned_thread_artifacts(now=datetime.now())

    assert stats["learning_threads"] == 0
    assert learning_files(data_dir) == []
    assert _learning_modules() in ([], ["row_bot.learning"])


def test_model_lists_and_local_models_load_nothing_of_learning(data_dir, no_learning_modules):
    """L2c's hooks in the model paths (personal models kept out of lists, a local model's own
    serving settings) read no Learning module and no Learning file."""

    from row_bot import models
    from row_bot.providers import model_visibility
    from row_bot.providers.runtime import _local_reasoning_default

    assert not model_visibility.withheld("ollama", "qwen3:8b")
    assert models.local_model_settings("qwen3:8b") == {}
    assert _local_reasoning_default("qwen3:8b") == {}

    assert learning_files(data_dir) == []
    assert _learning_modules() in ([], ["row_bot.learning"])


def test_deleting_a_conversation_with_learning_off_touches_nothing_of_learning(data_dir, no_learning_modules):
    gone = threads.create_thread("Off", thread_id=f"off-{uuid.uuid4().hex[:10]}", seed_default_skills=False)
    session_files = learning_files(threads.DATA_DIR)
    result = thread_cleanup.delete_thread(gone)

    assert result.deleted is True
    assert "Some Learning data could not be removed." not in result.warnings
    assert learning_files(data_dir) == []
    assert learning_files(threads.DATA_DIR) == session_files
    assert _learning_modules() == ["row_bot.learning"]


def test_deleting_a_memory_with_learning_off_touches_nothing_of_learning(
    tmp_path, monkeypatch, reload_for_data_dir, no_learning_modules
):
    stack = fresh_memory_stack(tmp_path, monkeypatch, reload_for_data_dir)
    memory = stack["memory"]
    lesson = memory.save_memory("self_knowledge", "Browser: login page detection", "Wait for the form.")

    assert memory.delete_memory(lesson["id"]) is True
    stack["kg"].delete_all_entities()

    assert learning_files(tmp_path / "row-bot-data") == []
    assert _learning_modules() in ([], ["row_bot.learning"])


@pytest.mark.parametrize("state", ["off", "paused", "on_without_the_preview_flag"])
def test_full_turns_with_learning_off_capture_nothing_load_nothing_and_start_nothing(
    state, data_dir, no_learning_modules, monkeypatch
):
    """Model calls, tool calls, an approval, a Stop and a Retry pass every capture seam: with
    Learning off, paused, or on in a build without the preview flag (O34), each costs one check
    and nothing else happens."""
    from langchain_core.messages import HumanMessage

    if state == "paused":
        save_learning_settings(data_dir, enabled=True, paused=True)
    elif state == "on_without_the_preview_flag":
        save_learning_settings(data_dir, enabled=True)
        set_learning_preview(monkeypatch, False)
    settings_file = [] if state == "off" else ["learning_config.json"]
    from langchain_core.tools import tool

    from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

    stop = threading.Event()

    @tool
    def read_note(name: str) -> str:
        """Read a note."""
        return f"The {name} note."

    @tool
    def delete_file(path: str) -> str:
        """Delete a file (asks first)."""
        from row_bot.tools import approval_gate

        return approval_gate.gate_action({"tool": "delete_file", "label": "Delete", "description": path}) or "Deleted."

    @tool
    def slow_search(query: str) -> str:
        """Search slowly."""
        stop.set()  # the person presses Stop meanwhile
        return "partial"

    session_files = learning_files(threads.DATA_DIR)
    before = {thread.name for thread in threading.enumerate()}
    model = ScriptedModel(responses=[
        asks(call("read_note", "r1", name="home")), asks(call("delete_file", "d1", path="old.txt")), says("Done."),
        asks(call("slow_search", "s1", query="flights")), says("Hi there."),
    ], seen=[])
    turns = AgentTurns(monkeypatch, model, [read_note, delete_file, slow_search])

    first = turns.send("Read my home note, then delete old.txt")
    assert [kind for kind, _ in first if kind == "interrupt"]
    assert ("done", "Done.") in turns.answer(True, first)
    turns.send("Search flights", stop_event=stop)
    assert ("done", "Hi there.") in turns.send("Say hi")
    assert threads.append_checkpoint_messages(  # Retry in place
        turns.thread_id, [HumanMessage(content="Say hi", id=f"retry-{uuid.uuid4().hex[:6]}")], retry_text="Say hi")

    assert len(model.seen) == 5
    assert learning_files(data_dir) == settings_file
    assert learning_files(threads.DATA_DIR) == session_files
    assert _learning_modules() == ["row_bot.learning"]
    started = {thread.name for thread in threading.enumerate()} - before
    assert not [name for name in started if name.startswith("learning-")]


@pytest.mark.parametrize("state", ["off", "paused", "on_without_the_preview_flag"])
def test_training_starts_nothing_and_loads_nothing_while_learning_is_off(state, data_dir, no_learning_modules,
                                                                         monkeypatch):
    """Start-up's runner, a settings save, Buddy's check of what's running and Dream's check of
    the lease (L2a): with Learning off, paused, or on without the preview flag, none of them
    loads Learning's training, starts a thread or writes a file."""
    import row_bot.agent_orchestrator as orchestrator
    import row_bot.buddy.brain as brain_mod
    import row_bot.dream_cycle as dream_cycle
    import row_bot.tasks as tasks
    from row_bot import learning

    if state == "paused":
        save_learning_settings(data_dir, enabled=True, paused=True)
    elif state == "on_without_the_preview_flag":
        save_learning_settings(data_dir, enabled=True)
        set_learning_preview(monkeypatch, False)
    settings_file = [] if state == "off" else ["learning_config.json"]
    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    monkeypatch.setattr(orchestrator, "get_thread_orchestration_activity", lambda: {})
    monkeypatch.setattr(brain_mod, "get_buddy_config", lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"})
    for name in ("is_enabled", "_in_dream_window", "_is_idle", "_model_chosen"):
        monkeypatch.setattr(dream_cycle, name, lambda: True)
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: False)
    before = {thread.name for thread in threading.enumerate()}

    learning.start_runner()  # what start-up calls
    learning.settings_saved("train_in_quiet_hours", True)
    assert brain_mod.BuddyBrain().resolve(None).message != "Learning: training your personal model"
    dream_cycle._should_dream()

    assert _learning_modules() == ["row_bot.learning"]
    started = {thread.name for thread in threading.enumerate()} - before
    assert not [name for name in started if name.startswith("learning-")]
    assert learning_files(data_dir) == settings_file


@pytest.mark.parametrize("state", ["off", "paused", "on_without_the_preview_flag"])
def test_memory_extraction_and_settings_saves_load_no_engine_and_download_nothing_while_off(
        state, data_dir, no_learning_modules, monkeypatch):
    """Memory extraction's check of the lease (O41) and a settings save (L2b's download stop):
    with Learning off, paused, or on without the preview flag, neither loads the training
    engine, its download or the lease, and nothing appears under the data folder's runtimes."""
    import row_bot.memory_extraction as extraction
    import row_bot.models as models
    from row_bot import learning

    if state == "paused":
        save_learning_settings(data_dir, enabled=True, paused=True)
    elif state == "on_without_the_preview_flag":
        save_learning_settings(data_dir, enabled=True)
        set_learning_preview(monkeypatch, False)
    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:qwen3:8b")
    monkeypatch.setattr(extraction, "_read_conversations", lambda *args, **kwargs: 0)

    extraction.run_extraction()
    learning.settings_saved("paused", True)
    learning.settings_saved("enabled", False)

    assert _learning_modules() == ["row_bot.learning"]
    assert not (data_dir / "runtimes").exists()
