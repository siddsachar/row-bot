"""Background memory extraction reads each conversation once, from where it left off (WP C2, F46).

Every run used to pick conversations by ``updated_at`` and send the first 6000
characters: a long conversation sent the same opening every two hours and its
new messages were never read, and renaming a chat or switching one of its
settings sent it again. Each conversation now keeps a watermark (the last
messages read, by id and content hash, and the checkpoint they came from).
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.subsystem

PROMPT_END = "\n\nRespond with ONLY a valid JSON array."


class _Model:
    """The default chat model as extraction sees it: records what each call read."""

    def __init__(self) -> None:
        self.read: list[str] = []
        self.on_call = None
        self.fail = False

    def bind(self, **_kwargs):
        return self

    def invoke(self, messages):
        prompt = str(messages[-1].content)
        self.read.append(prompt.split("CONVERSATION:\n", 1)[1].split(PROMPT_END, 1)[0])
        if self.on_call is not None:
            self.on_call()
        if self.fail:
            raise TimeoutError("the model did not answer")
        return AIMessage(content='{"memories": []}')


@pytest.fixture
def stack(reload_for_data_dir, tmp_path, monkeypatch):
    threads, memory_extraction = reload_for_data_dir(
        tmp_path / "data", "row_bot.threads", "row_bot.memory_extraction")
    from row_bot import models
    from row_bot.providers import background

    model = _Model()
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(models, "get_llm_for", lambda _ref, num_ctx=None, *, reasoning_plan=None: model)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: set())
    return threads, memory_extraction, model


def _chat(threads, thread_id: str, turns: list[tuple[str, str]], *, name: str = "", thread_type: str = "") -> None:
    threads.create_thread(name or thread_id, thread_id=thread_id, thread_type=thread_type, seed_default_skills=False)
    _say(threads, thread_id, turns)


def _say(threads, thread_id: str, turns: list[tuple[str, str]], *, start: int | None = None) -> None:
    offset = len(threads.get_latest_checkpoint_messages(thread_id)) if start is None else start
    messages = []
    for index, (person, answer) in enumerate(turns, start=offset):
        messages += [HumanMessage(id=f"{thread_id}-h{index}", content=person),
                     AIMessage(id=f"{thread_id}-a{index}", content=answer)]
    assert threads.append_checkpoint_messages(thread_id, messages)


def _fact(index: int) -> str:
    return f"Fact {index:02d}: " + "I keep notes about my garden and my week. " * 6


def test_a_long_conversation_is_read_in_full_then_only_what_is_new(stack) -> None:
    threads, memory_extraction, model = stack
    _chat(threads, "long", [(_fact(index), f"Noted {index:02d}.") for index in range(70)])

    memory_extraction.run_extraction()

    # Every message is read once, oldest first, in windows within the budget: before, only the newest
    # 6,000 characters were read and the rest was passed over for good.
    assert len(model.read) >= 3
    assert all(len(text) <= memory_extraction._CONVERSATION_BUDGET for text in model.read)
    new_parts = [text.split(memory_extraction._NEW_LABEL)[-1] for text in model.read]
    assert all(sum(f"Fact {index:02d}" in part for part in new_parts) == 1 for index in range(70))
    assert "Fact 00" in model.read[0] and "Fact 69" in model.read[-1]
    calls = len(model.read)

    memory_extraction.run_extraction()
    assert len(model.read) == calls  # nothing new: no call

    _say(threads, "long", [("I just adopted a cat called Miso.", "Congratulations!")])
    memory_extraction.run_extraction()

    second = model.read[-1]
    assert second.endswith("User: I just adopted a cat called Miso.\nAssistant: Congratulations!")
    # The two messages before it come first, as context; nothing older is sent again.
    earlier, new = second.split(memory_extraction._NEW_LABEL)
    assert earlier.startswith(memory_extraction._EARLIER_LABEL)
    assert "Fact 69" in earlier and "Noted 69." in earlier
    assert "Fact 68" not in second and "Fact 00" not in second
    assert "Miso" in new


def test_a_backlog_longer_than_one_run_is_read_over_the_next_runs_without_gaps(stack, monkeypatch) -> None:
    threads, memory_extraction, model = stack
    monkeypatch.setattr(memory_extraction, "_CALLS_PER_RUN", 2)
    _chat(threads, "backlog", [(_fact(index), f"Noted {index:02d}.") for index in range(40)])

    memory_extraction.run_extraction()
    assert len(model.read) == 2
    for _ in range(10):
        memory_extraction.run_extraction()

    new_parts = [text.split(memory_extraction._NEW_LABEL)[-1] for text in model.read]
    assert all(sum(f"Fact {index:02d}" in part for part in new_parts) == 1 for index in range(40))
    calls = len(model.read)
    memory_extraction.run_extraction()
    assert len(model.read) == calls


def test_an_outage_mid_conversation_resumes_at_the_window_it_missed(stack) -> None:
    threads, memory_extraction, model = stack
    _chat(threads, "outage", [(_fact(index), f"Noted {index:02d}.") for index in range(40)])
    model.on_call = lambda: setattr(model, "fail", len(model.read) == 2)

    memory_extraction.run_extraction()
    missed = model.read[1].split(memory_extraction._NEW_LABEL)[-1]
    model.on_call = None
    model.fail = False
    memory_extraction.run_extraction()

    assert model.read[2].split(memory_extraction._NEW_LABEL)[-1] == missed
    new_parts = [text.split(memory_extraction._NEW_LABEL)[-1] for text in model.read[:1] + model.read[2:]]
    assert all(sum(f"Fact {index:02d}" in part for part in new_parts) == 1 for index in range(40))


def test_renaming_or_changing_a_chat_setting_reads_nothing(stack, monkeypatch) -> None:
    threads, memory_extraction, model = stack
    _chat(threads, "chat", [("I live in Leeds.", "Lovely city.")])
    memory_extraction.run_extraction()
    assert len(model.read) == 1

    threads._save_thread_meta("chat", "Leeds chat")  # a rename moves updated_at
    threads.set_thread_skills_override("chat", ["notes"])
    threads.set_thread_app("chat", "app:calendar", False)
    loaded: list[str] = []
    original = threads.get_latest_checkpoint_messages
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages",
                        lambda thread_id: loaded.append(thread_id) or original(thread_id))

    memory_extraction.run_extraction()

    assert len(model.read) == 1
    assert loaded == []  # the conversation is not even loaded


def test_child_workflow_internal_and_row_bot_messages_are_never_read(stack, monkeypatch) -> None:
    threads, memory_extraction, model = stack
    from row_bot import turn_context

    _chat(threads, "child", [("Child objective: summarise the repo.", "Done.")], thread_type="agent_child")
    _chat(threads, "run-1", [("Workflow prompt: daily digest.", "Digest.")], name="⚡ Digest — Oct 11")
    _chat(threads, "run-2", [("Workflow prompt renamed.", "Digest.")], name="Renamed digest")
    _chat(threads, "orchestration-synthesis:o1", [("Synthesis prompt.", "Combined answer.")])
    monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: {"run-2"})

    threads.create_thread("Chat", thread_id="chat", seed_default_skills=False)
    context = turn_context.build_message(
        turn_id="turn-1",
        values=turn_context.TurnContextValues(
            now=datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc),
            fields=(turn_context.ContextField("state", "Current state:\n- zebracontext model"),),
            memories=(), recall_block=lambda _memories: ""),
        baseline=turn_context.Baseline(), summary_marker="")
    goal_event = HumanMessage(id="event-1", content='{"goal": "zebraevent"}',
                              additional_kwargs={"row_bot_ui": {"thread_event": True}})
    assert threads.append_checkpoint_messages("chat", [
        HumanMessage(id="h1", content="My sister Ana lives in Porto."), context,
        AIMessage(id="a1", content="Noted."), goal_event,
        AIMessage(id="a2", content="zebrainternal", additional_kwargs={"row_bot_internal_event": True}),
    ])

    memory_extraction.run_extraction()

    [read] = model.read
    assert read == "User: My sister Ana lives in Porto.\nAssistant: Noted."
    for marker in ("zebracontext", "zebraevent", "zebrainternal", "Child objective", "Workflow prompt", "Synthesis"):
        assert marker not in read


def test_a_conversation_read_before_watermarks_is_read_once_by_the_old_rule(stack) -> None:
    threads, memory_extraction, model = stack
    memory_extraction._save_state({"last_extraction": "2026-10-01T00:00:00"})
    _chat(threads, "unchanged", [(_fact(index), "Ok.") for index in range(30)])
    _chat(threads, "changed", [(_fact(index), "Ok.") for index in range(30)])
    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET created_at = ?", ("2026-09-01T00:00:00",))
        conn.execute("UPDATE thread_meta SET updated_at = ? WHERE thread_id = ?", ("2026-09-20T00:00:00", "unchanged"))
        conn.execute("UPDATE thread_meta SET updated_at = ? WHERE thread_id = ?", ("2026-10-05T00:00:00", "changed"))

    memory_extraction.run_extraction()

    # "changed" moved after the last run before the upgrade: its newest messages are read once.
    [read] = model.read
    assert "Fact 29" in read and "Fact 00" not in read
    memory_extraction.run_extraction()
    assert len(model.read) == 1

    # "unchanged" starts from its end: only what is said next is read.
    _say(threads, "unchanged", [("My new job starts in May.", "Good luck!")])
    memory_extraction.run_extraction()
    assert len(model.read) == 2
    assert "My new job starts in May." in model.read[-1] and "Fact 00" not in model.read[-1]


def test_a_retried_answer_is_read_and_a_model_outage_leaves_the_conversation_unread(stack) -> None:
    threads, memory_extraction, model = stack
    _chat(threads, "chat", [("I play the cello.", "Nice."), ("Which strings suit a cello?", "Steel ones.")])
    memory_extraction.run_extraction()

    assert threads.append_checkpoint_messages("chat", [
        HumanMessage(id="retry-h", content="Which strings suit a cello?"),
        AIMessage(id="retry-a", content="Gut or steel, depending on the sound you like."),
    ], retry_text="Which strings suit a cello?")
    model.fail = True
    memory_extraction.run_extraction()
    model.fail = False
    memory_extraction.run_extraction()

    assert len(model.read) == 3
    assert model.read[1] == model.read[2]  # the failed call's messages are read again
    assert "Gut or steel" in model.read[2] and "I play the cello." in model.read[2]  # with its context
    memory_extraction.run_extraction()
    assert len(model.read) == 3


@pytest.mark.parametrize("returns", ["person", "turn"])
def test_extraction_stops_when_the_person_comes_back_or_a_turn_starts(stack, monkeypatch, returns) -> None:
    threads, memory_extraction, model = stack
    from row_bot import document_extraction, tasks
    from row_bot.runtime import executions

    registry = executions.GenerationRuntimeRegistry()
    monkeypatch.setattr(executions, "generation_registry", registry)
    monkeypatch.setattr(document_extraction, "get_extraction_status", lambda: None)
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    monkeypatch.setattr(memory_extraction, "_last_activity_ts", time.monotonic() - 3600)
    _chat(threads, "first", [("I run on Sundays.", "Great.")])
    _chat(threads, "second", [("I am learning Welsh.", "Pob lwc!")])
    turn = []

    def comes_back() -> None:
        if returns == "person":
            memory_extraction.mark_user_activity("conversation turn")
        else:
            turn.append(registry.register("first"))

    model.on_call = comes_back
    statuses: list[str] = []
    memory_extraction.run_extraction_if_idle(on_status=statuses.append)

    assert len(model.read) == 1
    assert statuses[-1] == "No new memories found; 1 conversation(s) left for the next run"

    model.on_call = None
    for handle in turn:
        registry.finish(handle)
    monkeypatch.setattr(memory_extraction, "_last_activity_ts", time.monotonic() - 3600)
    memory_extraction.run_extraction_if_idle()

    assert len(model.read) == 2
    assert {"I run on Sundays." in text for text in model.read} == {True, False}
