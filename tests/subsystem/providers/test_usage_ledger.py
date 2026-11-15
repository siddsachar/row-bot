"""The local usage ledger: one row per model call, counts only, never content."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from row_bot.providers import usage_ledger
from row_bot.providers.usage_ledger import (
    UsageLedgerCallback,
    attach_usage_ledger,
    with_usage_purpose,
)

pytestmark = pytest.mark.subsystem

PROMPT = "PRIVATE PROMPT about the quarterly plan"
REPLY = "PRIVATE REPLY with the answer"
USAGE = {
    "input_tokens": 15230,
    "output_tokens": 420,
    "total_tokens": 15650,
    "input_token_details": {"cache_read": 15000, "cache_creation": 200},
    "output_token_details": {"reasoning": 64},
}


class _UsageModel(BaseChatModel):
    """Answers with fixed text and reports usage the way Anthropic streams it."""

    usage: dict[str, Any] = USAGE
    report_usage: bool = True

    @property
    def _llm_type(self) -> str:
        return "usage-fixture"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=list(tools), **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = AIMessage(content=REPLY, usage_metadata=self.usage if self.report_usage else None)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        input_part = {
            "input_tokens": self.usage["input_tokens"],
            "output_tokens": 0,
            "total_tokens": self.usage["input_tokens"],
            "input_token_details": dict(self.usage["input_token_details"]),
        }
        output_part = {
            "input_tokens": 0,
            "output_tokens": self.usage["output_tokens"],
            "total_tokens": self.usage["output_tokens"],
            "output_token_details": dict(self.usage["output_token_details"]),
        }
        # Text first; usage arrives at the end, split over two chunks.
        yield ChatGenerationChunk(message=AIMessageChunk(content=REPLY))
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata=input_part if self.report_usage else None))
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", chunk_position="last", usage_metadata=output_part if self.report_usage else None))


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    return tmp_path / "usage.db"


def _rows(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM model_usage ORDER BY id")]


def _model(**kwargs) -> _UsageModel:
    return attach_usage_ledger(_UsageModel(**kwargs), provider_id="anthropic", model_name="claude-sonnet-4-5")


def test_a_call_writes_one_row_of_counts_and_never_the_prompt_or_reply(ledger):
    result = _model().invoke([HumanMessage(content=PROMPT)])

    assert result.content == REPLY
    [row] = _rows(ledger)
    assert row["provider"] == "anthropic" and row["model"] == "claude-sonnet-4-5"
    assert row["purpose"] == "other" and row["thread_id"] == ""
    assert (row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"]) == (15230, 15000, 200)
    assert (row["output_tokens"], row["reasoning_tokens"]) == (420, 64)
    assert row["latency_ms"] is not None and row["latency_ms"] >= 0
    data = ledger.read_bytes()
    assert b"PRIVATE" not in data


def test_a_streamed_call_sums_its_usage_chunks_and_times_the_first_token(ledger):
    chunks = list(_model().stream([HumanMessage(content=PROMPT)]))

    assert "".join(str(chunk.content) for chunk in chunks) == REPLY
    [row] = _rows(ledger)
    assert (row["input_tokens"], row["output_tokens"]) == (15230, 420)
    assert (row["cache_read_tokens"], row["cache_write_tokens"]) == (15000, 200)
    assert row["ttft_ms"] is not None and 0 <= row["ttft_ms"] <= row["latency_ms"]


def test_a_tagged_call_carries_its_purpose_and_conversation(ledger):
    model = with_usage_purpose(_model(), "chat_only", thread_id="thread-a", generation_id="gen-1")

    list(model.stream([HumanMessage(content=PROMPT)]))
    with_usage_purpose(_model(), "not-a-purpose", thread_id="thread-a").invoke("hi")

    first, second = _rows(ledger)
    assert (first["purpose"], first["thread_id"], first["generation_id"]) == ("chat_only", "thread-a", "gen-1")
    assert second["purpose"] == "other"


def test_a_test_double_that_is_not_a_runnable_is_left_alone():
    class Double:
        def stream(self, messages):
            yield from ()

    double = Double()

    assert with_usage_purpose(double, "chat_only") is double
    assert attach_usage_ledger(double, provider_id="x", model_name="y") is double


def test_the_agent_model_node_is_the_turn_and_other_graph_nodes_are_not(ledger, monkeypatch):
    import row_bot.agent as agent

    model = _model()
    model.invoke("hi", config={"metadata": {"langgraph_node": "agent", "thread_id": "thread-a", "langgraph_step": 4}})
    model.invoke("hi", config={"metadata": {"langgraph_node": "tools", "thread_id": "thread-a", "langgraph_step": 5}})
    monkeypatch.setattr(agent, "get_active_runtime_context", lambda: {
        "thread_id": "thread-child", "runtime_surface": "agent_child", "generation_id": "gen-child"})
    model.invoke("hi", config={"metadata": {"langgraph_node": "agent", "thread_id": "thread-child"}})
    # The running turn's facts only count for the conversation they describe.
    model.invoke("hi", config={"metadata": {"langgraph_node": "agent", "thread_id": "thread-other"}})

    main, tool, child, other_thread = _rows(ledger)
    assert (main["purpose"], main["thread_id"], main["iteration"]) == ("main", "thread-a", 4)
    assert (tool["purpose"], tool["thread_id"]) == ("other", "thread-a")
    assert (child["purpose"], child["generation_id"]) == ("child", "gen-child")
    assert (other_thread["purpose"], other_thread["generation_id"]) == ("main", "")


def test_a_reasoning_fallback_wrapper_records_the_inner_call_once_with_its_tag(ledger):
    from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan, ReasoningSelection
    from row_bot.providers.transports.reasoning_fallback import ReasoningFallbackChatModel

    capabilities = ReasoningCapabilities(
        supported_efforts=("low", "high"),
        request_style="anthropic",
        thinking_mode="adaptive",
    )
    # As create_chat_model builds it: the ledger on the wrapper, plain inner models.
    wrapper = attach_usage_ledger(
        ReasoningFallbackChatModel(
            primary=_UsageModel(),
            provider_default=_UsageModel(),
            plan=ReasoningRequestPlan(
                "model:anthropic:claude-sonnet-4-5",
                ReasoningSelection(kind="effort", effort="low"),
                capabilities,
            ),
            provider_id="anthropic",
        ),
        provider_id="anthropic",
        model_name="claude-sonnet-4-5",
    )

    list(with_usage_purpose(wrapper, "chat_only", thread_id="thread-a").stream("hi"))
    with_usage_purpose(wrapper, "compaction", thread_id="thread-a").invoke("hi")
    wrapper.bind_tools([]).invoke("hi", config={"metadata": {"langgraph_node": "agent", "thread_id": "thread-a"}})

    streamed, invoked, tooled = _rows(ledger)
    assert (streamed["purpose"], streamed["thread_id"], streamed["input_tokens"]) == ("chat_only", "thread-a", 15230)
    assert streamed["output_tokens"] == 420
    assert (invoked["purpose"], invoked["thread_id"], invoked["input_tokens"]) == ("compaction", "thread-a", 15230)
    assert (tooled["purpose"], tooled["thread_id"]) == ("main", "thread-a")


def test_a_failed_ledger_write_never_breaks_the_model_call(tmp_path, monkeypatch):
    blocked = tmp_path / "usage.db"
    blocked.mkdir()  # a folder where the database file should be
    monkeypatch.setattr(usage_ledger, "get_usage_db_path", lambda **_kwargs: blocked)

    assert _model().invoke("hi").content == REPLY
    assert "".join(str(chunk.content) for chunk in _model().stream("hi")) == REPLY


def test_a_stopped_stream_with_no_usage_yet_writes_nothing(ledger):
    stream = _model().stream("hi")
    next(stream)
    stream.close()

    assert _rows(ledger) == []


def test_a_call_that_reports_no_usage_is_still_counted_as_a_call(ledger):
    _model(report_usage=False).invoke("hi")

    [row] = _rows(ledger)
    assert row["input_tokens"] is None and row["output_tokens"] is None
    assert usage_ledger.usage_summary()["unreported_calls"] == 1


def _record(**overrides) -> None:
    from row_bot.prompt_cache import ModelUsage

    values = {
        "purpose": "main",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "usage": ModelUsage(input_tokens=1000, output_tokens=100, cache_read_tokens=800, cache_write_tokens=100),
        "thread_id": "thread-a",
    }
    values.update(overrides)
    assert usage_ledger.record_model_usage(usage_ledger.UsageRecord(**values))


def test_summaries_split_by_model_and_purpose_with_the_cache_share(ledger):
    from row_bot.prompt_cache import ModelUsage

    _record()
    _record(purpose="compaction", usage=ModelUsage(input_tokens=1000, output_tokens=50, cache_read_tokens=0))
    _record(provider="ollama", model="qwen3:14b", purpose="chat_only",
            usage=ModelUsage(input_tokens=500, output_tokens=20))
    _record(provider="openrouter", model="anthropic/claude-sonnet-4.5", thread_id="thread-b",
            usage=ModelUsage(input_tokens=100, output_tokens=10, cache_read_tokens=50, cost=0.002))

    conversation = usage_ledger.conversation_usage("thread-a")
    assert conversation["calls"] == 3
    assert (conversation["input_tokens"], conversation["output_tokens"]) == (2500, 170)
    # Ollama reports no cache counts, so its input does not dilute the share.
    assert conversation["cache_read_percent"] == 40
    assert conversation["cost"] is None
    assert usage_ledger.conversation_usage("thread-b")["cost"] == pytest.approx(0.002)
    assert usage_ledger.conversation_usage("thread-none") is None

    summary = usage_ledger.usage_summary(since=time.time() - 3600)
    assert summary["calls"] == 4
    assert {row["purpose"] for row in summary["by_purpose"]} == {"main", "compaction", "chat_only"}
    models = {(row["provider"], row["model"]): row for row in summary["by_model"]}
    assert models[("anthropic", "claude-sonnet-4-5")]["calls"] == 2
    assert models[("ollama", "qwen3:14b")]["cache_read_percent"] is None
    assert usage_ledger.usage_summary(since=time.time() + 60)["calls"] == 0


def test_the_last_turn_requests_cache_share_skips_background_calls(ledger):
    from row_bot.prompt_cache import ModelUsage

    _record(created_at=100.0)  # 800 of 1,000 read from the cache
    _record(purpose="compaction", created_at=200.0,
            usage=ModelUsage(input_tokens=1000, output_tokens=50, cache_read_tokens=0))
    assert usage_ledger.conversation_usage("thread-a")["last_cache_read_percent"] == 80

    _record(purpose="chat_only", created_at=300.0, usage=ModelUsage(input_tokens=500, output_tokens=20))
    assert usage_ledger.conversation_usage("thread-a")["last_cache_read_percent"] is None


def test_old_rows_are_pruned_and_the_table_is_capped(ledger):
    now = time.time()
    _record(created_at=now - 91 * 86_400)
    for offset in range(4, -1, -1):
        _record(created_at=now - offset)

    assert usage_ledger.prune_usage(now=now, max_rows=3) == 3
    assert len(_rows(ledger)) == 3
    assert min(row["created_at"] for row in _rows(ledger)) >= now - 2


def test_reads_never_create_the_ledger(ledger):
    assert usage_ledger.conversation_usage("thread-a") is None
    assert usage_ledger.usage_summary()["calls"] == 0
    assert usage_ledger.prune_usage() == 0
    assert usage_ledger.delete_thread_usage("thread-a") == 0
    assert not ledger.exists()


def test_deleting_a_conversation_removes_its_rows(ledger):
    import uuid

    from row_bot import threads
    from row_bot.thread_cleanup import delete_thread

    gone = threads.create_thread("Ledger fixture", thread_id=f"ledger-{uuid.uuid4().hex[:8]}",
                                 seed_default_skills=False)
    _record(thread_id=gone)
    _record(thread_id="thread-kept")

    assert delete_thread(gone).deleted is True

    assert [row["thread_id"] for row in _rows(ledger)] == ["thread-kept"]


def test_created_chat_models_carry_the_ledger_and_ask_for_stream_usage(monkeypatch):
    import row_bot.providers.runtime as runtime

    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id: "sk-test")

    for model_name in ("gpt-4o", "gpt-5.5"):
        model = runtime.create_chat_model(model_name, provider_id="openai")
        # A custom http_client turns stream usage off in langchain-openai.
        assert model.stream_usage is True
        [handler] = [item for item in model.callbacks if isinstance(item, UsageLedgerCallback)]
        assert (handler.provider_id, handler.model_name) == ("openai", model_name)

    again = attach_usage_ledger(model, provider_id="openai", model_name="gpt-5.5")
    assert sum(isinstance(item, UsageLedgerCallback) for item in again.callbacks) == 1


def test_a_reasoning_override_counts_each_call_once_on_the_wrapper(monkeypatch):
    import row_bot.providers.runtime as runtime
    from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan, ReasoningSelection
    from row_bot.providers.transports.reasoning_fallback import ReasoningFallbackChatModel

    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider_id: "sk-test")
    plan = ReasoningRequestPlan(
        "model:openai:gpt-5",
        ReasoningSelection(kind="effort", effort="high"),
        ReasoningCapabilities(supported_efforts=("high",), request_style="openai"),
    )

    model = runtime.create_chat_model("gpt-5", "openai", reasoning_plan=plan)

    assert isinstance(model, ReasoningFallbackChatModel)
    assert any(isinstance(item, UsageLedgerCallback) for item in model.callbacks)
    # The inner calls run with callbacks stripped, so they carry no ledger of their own.
    for inner in (model.primary, model.provider_default):
        assert not any(isinstance(item, UsageLedgerCallback) for item in inner.callbacks or ())
    assert model.bind_tools([]).callbacks == model.callbacks
