from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.fixtures.memory_stack import fresh_memory_stack


pytestmark = pytest.mark.subsystem


def test_active_thread_tracking_and_idle_detection_use_fake_monotonic(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]
    now = {"value": 1000.0}

    monkeypatch.setattr(memory_extraction.time, "monotonic", lambda: now["value"])

    memory_extraction.mark_user_activity("test")
    assert memory_extraction.idle_seconds() == 0.0

    now["value"] += 301
    assert memory_extraction.is_app_idle(min_idle_s=300) is True

    memory_extraction.set_active_thread("thread-2", previous_id="thread-1")
    with memory_extraction._active_lock:
        assert memory_extraction._active_threads == {"thread-2"}

    memory_extraction.set_active_thread(None, previous_id="thread-2")
    with memory_extraction._active_lock:
        assert memory_extraction._active_threads == set()


def test_state_and_journal_survive_reload_and_corrupt_files_fall_back(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]

    memory_extraction._save_state({"last_extraction": "2026-06-23T00:00:00", "threads_scanned": 2, "entities_saved": 3})
    for idx in range(105):
        memory_extraction._append_extraction_journal({"id": idx})

    reloaded = fresh_memory_stack(tmp_path, monkeypatch)["memory_extraction"]
    assert reloaded.get_extraction_status()["threads_scanned"] == 2
    assert len(reloaded.get_extraction_journal(limit=0)) == 100
    assert reloaded.get_extraction_journal(limit=1) == [{"id": 104}]

    reloaded._STATE_FILE.write_text("{bad-json", encoding="utf-8")
    reloaded._JOURNAL_FILE.write_text("{bad-json", encoding="utf-8")
    assert reloaded.get_extraction_status()["threads_scanned"] == 0
    assert reloaded.get_extraction_journal() == []


def test_get_thread_messages_normalizes_langchain_content_blocks(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]

    @dataclass
    class Message:
        type: str
        content: object

    monkeypatch.setattr(
        "row_bot.threads.get_latest_checkpoint_messages",
        lambda _thread_id: [
            Message("human", [{"type": "text", "text": "hello"}, "world"]),
            Message("ai", "a" * 3000),
            Message("tool", "ignored"),
            Message("human", {"not": "text"}),
        ],
    )

    messages = memory_extraction._get_thread_messages("thread-1")

    assert messages[0] == {"role": "user", "content": "hello\nworld"}
    assert messages[1]["role"] == "assistant"
    assert len(messages[1]["content"]) == 2000
    assert messages[2] == {"role": "user", "content": "{'not': 'text'}"}


def test_format_conversation_truncates_assistant_and_skips_malformed_messages(tmp_path, monkeypatch) -> None:
    memory_extraction = fresh_memory_stack(tmp_path, monkeypatch)["memory_extraction"]

    text = memory_extraction._format_conversation(
        [
            {"role": "user", "content": "I like deterministic tests."},
            {"role": "assistant", "content": "x" * 250},
            {"role": "tool", "content": "ignored"},
            {"content": "missing role"},
            "not a dict",
        ]
    )

    assert "User: I like deterministic tests." in text
    assert "Assistant: " + ("x" * 200) + " [...]" in text
    assert "ignored" not in text
    assert "missing role" not in text


def test_run_extraction_if_idle_and_idle_scheduler_use_active_thread_exclusions(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]
    calls: list[set[str] | None] = []

    monkeypatch.setattr(memory_extraction, "is_app_idle", lambda: False)
    monkeypatch.setattr(memory_extraction, "idle_seconds", lambda: 1)
    monkeypatch.setattr(memory_extraction, "run_extraction", lambda **kwargs: calls.append(kwargs.get("exclude_thread_ids")) or 7)
    assert memory_extraction.run_extraction_if_idle() == 0

    monkeypatch.setattr(memory_extraction, "is_app_idle", lambda: True)
    assert memory_extraction.run_extraction_if_idle(exclude_thread_ids={"active"}) == 7
    assert calls == [{"active"}]

    with memory_extraction._active_lock:
        memory_extraction._active_threads.clear()
        memory_extraction._active_threads.add("thread-live")
    memory_extraction._timer_stop.clear()
    memory_extraction.schedule_idle_extraction(delay_s=0)
    memory_extraction._idle_once_thread.join(timeout=5)

    assert calls[-1] == {"thread-live"}
    memory_extraction.stop_periodic_extraction()


def test_run_extraction_handles_no_threads_and_excludes_active_threads(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]
    statuses: list[str] = []

    monkeypatch.setattr("row_bot.threads._list_threads", lambda: [])
    assert memory_extraction.run_extraction(on_status=statuses.append) == 0
    assert statuses == ["No conversations to process"]

    statuses.clear()
    monkeypatch.setattr(
        "row_bot.threads._list_threads",
        lambda: [
            ("active", "Active", "", "2099-01-01T00:00:00"),
            ("background", "\u26a1 Background", "", "2099-01-01T00:00:00"),
            ("done", "Done", "", "1999-01-01T00:00:00"),
        ],
    )
    assert memory_extraction.run_extraction(on_status=statuses.append, exclude_thread_ids={"active"}) == 0
    assert statuses == ["No new conversations since last extraction"]
