"""Prepared compaction (B6): the next summary is written while the chat is idle near its point.

A turn that settles at 85% or more of the compaction point, on a cloud model, prepares the summary a
compaction at the point would write, with the same functions. When the point is reached the turn uses
it: no summarizer call and no "Compacting context…" pause. It is used only while it still fits.
Preparation runs in line here (the background thread is replaced); the summarizer is a fake.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import row_bot.agent as agent
from row_bot import threads
from tests.helpers.agent_requests import wire

pytestmark = pytest.mark.subsystem

THREAD = "thread-prepared"
HEADINGS = (
    "## Current Goal", "## Constraints and Decisions", "## Completed Work",
    "## Current State", "## Relevant Details", "## Next Step",
)


@pytest.fixture(autouse=True)
def thread_db(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    # These conversations live in memory: one checkpoint revision stands for them.
    monkeypatch.setattr(threads, "get_latest_checkpoint_revision", lambda thread_id: "rev-1")
    monkeypatch.setattr(threads, "_latest_checkpoint_revision_conn", lambda conn, thread_id: "rev-1")
    monkeypatch.setattr("row_bot.providers.routing.task_route_model", lambda purpose: "")
    agent._COMPACTION_FAILURES.clear()
    threads.create_thread("Prepared", thread_id=THREAD, seed_default_skills=False)
    token = agent._current_thread_id_var.set(THREAD)
    yield
    agent._current_thread_id_var.reset(token)


class Summarizer:
    """The compaction model: a valid structured summary of exactly what it was asked to summarize."""

    def __init__(self, monkeypatch) -> None:
        self.requests: list[tuple[str, str]] = []
        self.fail = False
        self.during: list = []
        monkeypatch.setattr(agent, "_invoke_compaction_model", self)

    def __call__(self, _inputs, *, prior_summary, transcript, output_tokens):
        self.requests.append((prior_summary, transcript))
        for hook in self.during:
            hook()
        if self.fail:
            raise RuntimeError("The summarizer is unreachable.")
        digest = hashlib.sha256((prior_summary + transcript).encode()).hexdigest()[:12]
        return "\n".join(f"{heading}\n{digest}" for heading in HEADINGS)


@pytest.fixture
def summarizer(monkeypatch) -> Summarizer:
    return Summarizer(monkeypatch)


@pytest.fixture
def events(monkeypatch) -> list[str]:
    """The compaction events shown to the person (the plain usage counts are left out)."""
    emitted: list[str] = []

    def emit(kind, payload):
        if kind != "context_usage":
            emitted.append(f"{kind}:prepared" if payload.get("prepared") else kind)

    monkeypatch.setattr(agent, "_emit_context_event", emit)
    return emitted


@pytest.fixture(autouse=True)
def in_line(monkeypatch):
    monkeypatch.setattr(agent, "_submit_preparation", lambda job: job())


def _conversation(groups: int, *, start: int = 0) -> list:
    raw = []
    for index in range(start, start + groups):
        raw.extend((
            HumanMessage(content=f"question {index} " + "word " * 400, id=f"person-{index}"),
            AIMessage(content=f"answer {index}", id=f"answer-{index}"),
        ))
    return raw


def _inputs(raw, *, mode: str = "agent", model_ref: str = "model:openai:test", provider_id: str = "openai"):
    # A small window: the compaction point is 4,000 tokens, so 85% of it is 3,400.
    policy = SimpleNamespace(
        native_limit_tokens=5_334, requested_limit_tokens=None, effective_limit_tokens=5_334,
        usable_input_tokens=4_533, compact_at_tokens=4_000, capacity_source="test",
        capacity_state="ready", limit_kind="combined",
    )
    return agent.PreparationInputs(
        complete_messages=(SystemMessage(content="system"), *raw), raw_messages=tuple(raw),
        canonical_tools=(), policy=policy, mode=mode, model_ref=model_ref, provider_id=provider_id,
        checkpoint_revision="rev-1", prompt_fingerprint="prompt", tool_fingerprint="tools",
        policy_fingerprint="policy",
    )


def _settle(raw, **kwargs) -> agent.ContextUsage:
    """A turn ends with *raw* in its checkpoint."""
    return agent._persist_settled_context_usage(_inputs(raw, **kwargs), list(raw))


def _slot(mode: str = "agent") -> dict | None:
    return threads.load_prepared_summary(THREAD, mode)


def _compactions() -> int:
    with closing(sqlite3.connect(threads.DB_PATH)) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM thread_events WHERE thread_id = ? AND event_type = 'context_compacted'",
            (THREAD,),
        ).fetchone()[0]


# Seven exchanges settle at 85-100% of the point (six stay under 85%); two more take the next
# request past it.
NEAR = _conversation(7)
PAST = NEAR + _conversation(2, start=7)


def test_a_turn_that_settles_near_the_point_prepares_one_summary(summarizer) -> None:
    usage = _settle(_conversation(6))
    assert summarizer.requests == [] and _slot() is None and usage.summary_ready is False

    _settle(NEAR)
    prepared = _slot()
    assert prepared is not None and summarizer.requests
    assert prepared["model_ref"] == "model:openai:test" and prepared["base_summary_digest"] == ""
    calls = len(summarizer.requests)

    # The next settled turn says it is ready, and does not prepare it again.
    again = _settle(NEAR)
    assert again.summary_ready is True
    assert len(summarizer.requests) == calls


def test_at_the_point_the_turn_uses_it_without_a_summarizer_call_or_a_pause(summarizer, events) -> None:
    _settle(NEAR)
    prepared = _slot()
    calls = len(summarizer.requests)

    request = agent._prepare_with_compaction(_inputs(PAST))

    assert len(summarizer.requests) == calls  # nothing summarized in the turn
    assert events == ["compaction_succeeded:prepared"]  # never "compaction_started"
    assert request.usage.estimated_input_tokens < 4_000
    in_force = threads.load_validated_summary_state(THREAD, "agent", messages=list(PAST))
    assert in_force["summary"] == prepared["summary"]
    assert in_force["boundary_message_count"] == prepared["boundary_message_count"]
    assert _slot() is None and _compactions() == 1
    assert not agent._compaction_failure_marker(THREAD, "agent")

    # The request is the one every later call builds from the summary in force: the splice is the same.
    later = agent._prepare_with_compaction(_inputs(PAST))
    assert wire(later.messages) == wire(request.messages)
    assert len(summarizer.requests) == calls


def test_a_prepared_summary_is_the_summary_a_compaction_in_the_turn_would_write(summarizer) -> None:
    _settle(NEAR)
    prepared = _slot()
    asked_ahead = list(summarizer.requests)

    # The same conversation, compacted in a turn by today's path, in a second chat.
    summarizer.requests.clear()
    threads.create_thread("Reactive", thread_id="thread-reactive", seed_default_skills=False)
    token = agent._current_thread_id_var.set("thread-reactive")
    try:
        agent._prepare_with_compaction(_inputs(NEAR), force=True)
    finally:
        agent._current_thread_id_var.reset(token)
    reactive = threads.load_validated_summary_state("thread-reactive", "agent", messages=list(NEAR))

    assert summarizer.requests == asked_ahead  # the very same summarizer requests
    assert reactive["summary"] == prepared["summary"]
    assert reactive["boundary_message_count"] == prepared["boundary_message_count"]
    assert reactive["boundary_digest"] == prepared["boundary_digest"]


def _edited_early_message(raw: list) -> list:
    return [HumanMessage(content="question 0 was edited", id="person-0"), *raw[1:]]


@pytest.mark.parametrize("change", ["model", "provider", "policy", "boundary", "base summary"])
def test_a_prepared_summary_that_no_longer_fits_is_ignored_cleared_and_compaction_runs(
    summarizer, events, change
) -> None:
    _settle(NEAR)
    assert _slot() is not None
    calls = len(summarizer.requests)
    raw, inputs = PAST, _inputs(PAST)
    if change == "model":
        inputs = _inputs(PAST, model_ref="model:openai:other")
    elif change == "provider":
        inputs = _inputs(PAST, model_ref="model:openai:test", provider_id="openrouter")
    elif change == "policy":
        inputs = replace(_inputs(PAST), policy_fingerprint="another policy")
    elif change == "boundary":
        # A retry or edit changed what the summary covers.
        raw = _edited_early_message(PAST)
        inputs = _inputs(raw)
    else:
        # A summary came into force that this one does not build on.
        earlier = agent._summary_state_for(_inputs(PAST), 2, summarizer("", prior_summary="", transcript="x",
                                                                         output_tokens=0))
        earlier["source_revision"] = "rev-0"
        assert threads.save_summary_state_cas(THREAD, earlier, expected_revision="rev-1")
        calls = len(summarizer.requests)

    agent._prepare_with_compaction(inputs)

    assert len(summarizer.requests) > calls  # summarized in the turn, as before
    assert events[0] == "compaction_started" and "compaction_succeeded:prepared" not in events
    assert _slot() is None
    in_force = threads.load_validated_summary_state(THREAD, "agent", messages=list(raw))
    assert in_force is not None and in_force["source_revision"] == "rev-1"


def test_a_prepared_summary_without_enough_room_starts_the_compaction_in_the_turn(summarizer, events) -> None:
    _settle(NEAR)
    prepared = _slot()
    calls = len(summarizer.requests)
    # The chat grew a lot since: the prepared summary alone leaves the request over the point.
    grown = NEAR + _conversation(2, start=7) + [
        HumanMessage(content="question 9 " + "word " * 1_600, id="person-9"), AIMessage(content="answer 9"),
        HumanMessage(content="question 10", id="person-10"),
    ]

    request = agent._prepare_with_compaction(_inputs(grown))

    asked = summarizer.requests[calls:]
    assert asked and events[0] == "compaction_started"
    # Only the newer range was summarized, on top of the prepared summary.
    assert all(prior == prepared["summary"] for prior, _ in asked[:1])
    assert "question 0 " not in "".join(transcript for _, transcript in asked)
    assert request.usage.estimated_input_tokens < 4_000 and _slot() is None


@pytest.mark.parametrize("route", ["thread", "compaction route"])
def test_a_local_model_never_prepares(summarizer, monkeypatch, route) -> None:
    if route == "thread":
        _settle(NEAR, model_ref="model:ollama:qwen3:8b", provider_id="ollama")
    else:
        # A cloud chat whose summaries are routed to a local model holds that GPU as well.
        monkeypatch.setattr("row_bot.providers.routing.task_route_model", lambda purpose: (
            "model:ollama:qwen3:8b" if purpose == "compaction" else ""))
        _settle(NEAR)

    assert summarizer.requests == [] and _slot() is None


def test_a_failed_preparation_leaves_nothing_and_compaction_at_the_point_still_works(summarizer, events) -> None:
    summarizer.fail = True
    usage = _settle(NEAR)

    assert summarizer.requests and _slot() is None and usage.summary_ready is False
    assert not agent._compaction_failure_marker(THREAD, "agent")
    assert threads.load_compaction_failure(THREAD, "agent") is None

    summarizer.fail = False
    request = agent._prepare_with_compaction(_inputs(PAST))
    assert events[0] == "compaction_started" and events[-1] == "compaction_succeeded"
    assert request.usage.estimated_input_tokens < 4_000 and _compactions() == 1


def test_a_compaction_under_way_makes_the_preparation_skip(summarizer) -> None:
    lock = agent._compaction_lock(THREAD)
    assert lock.acquire(blocking=False)
    try:
        _settle(NEAR)
    finally:
        lock.release()

    assert summarizer.requests == [] and _slot() is None


def test_another_turn_running_makes_the_preparation_skip(summarizer, monkeypatch) -> None:
    from row_bot.runtime.executions import generation_registry

    handle = generation_registry.register(THREAD)
    try:
        _settle(NEAR)
    finally:
        generation_registry.finish(handle)

    assert summarizer.requests == [] and _slot() is None


def test_a_turn_reaching_the_point_while_the_summary_is_prepared_waits_and_uses_it(
    summarizer, events, monkeypatch
) -> None:
    real_lock = agent._compaction_lock(THREAD)
    waiting = threading.Event()

    class ObservedLock:
        def acquire(self, blocking=True):
            if blocking:
                waiting.set()
            return real_lock.acquire(blocking)

        def release(self):
            real_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *exc):
            self.release()

    monkeypatch.setattr(agent, "_compaction_lock", lambda thread_id: ObservedLock())
    results: list = []

    def turn():
        token = agent._current_thread_id_var.set(THREAD)
        try:
            results.append(agent._prepare_with_compaction(_inputs(PAST)))
        finally:
            agent._current_thread_id_var.reset(token)

    worker = threading.Thread(target=turn)

    def start_the_turn_once():
        if not worker.is_alive() and not results:
            worker.start()
            assert waiting.wait(timeout=10)  # the turn now waits on the chat's compaction lock

    summarizer.during.append(start_the_turn_once)
    _settle(NEAR)
    summarizer.during.clear()
    worker.join(timeout=10)
    calls = len(summarizer.requests)

    # One compaction: the turn used the summary prepared while it waited, and asked for nothing.
    assert results and results[0].usage.estimated_input_tokens < 4_000
    assert len(summarizer.requests) == calls
    assert "compaction_started" not in events and events.count("compaction_succeeded:prepared") == 1
    assert _compactions() == 1


def test_agent_and_chat_only_keep_their_own_prepared_summary(summarizer, events) -> None:
    _settle(NEAR)
    assert _slot("agent") is not None and _slot("chat_only") is None
    calls = len(summarizer.requests)

    # Chat Only reaches its point: it has no summary prepared, so it compacts as before.
    agent._prepare_with_compaction(_inputs(PAST, mode="chat_only"))

    assert len(summarizer.requests) > calls and events[0] == "compaction_started"
    assert _slot("agent") is not None  # Agent's is still there for Agent mode


def test_the_prepared_call_is_counted_as_compaction_and_says_it_was_prepared(tmp_path, monkeypatch) -> None:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    from row_bot.providers.usage_ledger import attach_usage_ledger

    seen: list[dict] = []

    class FakeSummarizer(BaseChatModel):
        @property
        def _llm_type(self):
            return "summary-fixture"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(dict(run_manager.metadata))
            text = "\n".join(f"{heading}\nDone." for heading in HEADINGS)
            message = AIMessage(content=text, usage_metadata={"input_tokens": 3000, "output_tokens": 60,
                                                              "total_tokens": 3060})
            return ChatResult(generations=[ChatGeneration(message=message)])

    model = attach_usage_ledger(FakeSummarizer(), provider_id="openai", model_name="test")
    monkeypatch.setattr("row_bot.models.get_llm_for", lambda _ref, num_ctx=None, *, reasoning_plan=None: model)
    generation = agent._current_generation_id_var.set("the-turn-that-settled")
    try:
        _settle(NEAR)
    finally:
        agent._current_generation_id_var.reset(generation)

    assert _slot() is not None and seen
    assert all(metadata.get("row_bot_compaction_prepared") is True for metadata in seen)
    with closing(sqlite3.connect(tmp_path / "usage.db")) as connection:
        rows = connection.execute("SELECT purpose, thread_id, generation_id FROM model_usage").fetchall()
    # The conversation's call, after its turn ended: not counted against that turn.
    assert rows and set(rows) == {("compaction", THREAD, "")}


def test_a_ready_summary_reaches_the_context_ring(summarizer, monkeypatch) -> None:
    from row_bot.projection.conversation import conversation_projection

    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", lambda thread_id: list(NEAR))
    published: list[dict] = []
    monkeypatch.setattr(conversation_projection, "publish", lambda conversation, kind, payload, **_: (
        published.append({"conversation": conversation, "kind": kind, **payload})))

    _settle(NEAR)

    saved = threads.load_context_usage(THREAD)
    assert saved["summary_ready"] is True
    assert [(item["conversation"], item["kind"], item.get("summary_ready")) for item in published] == [
        (THREAD, "context.updated", True)]
