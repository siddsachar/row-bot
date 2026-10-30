"""The request-size estimate is calibrated per model from what providers report (WP A4b, F29).

Compaction now waits until near the limit, so an estimate that undercounts would overflow. The
estimate (about four characters a token) is scaled per provider and model by a bounded moving
average of reported input over estimated input, learned only from plausible samples, raised by a
context-overflow error, and kept in usage.db (counts only).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import row_bot.agent as agent
from row_bot import context_calibration

pytestmark = pytest.mark.subsystem

PROVIDER = "anthropic"
MODEL = "model:anthropic:claude-opus-5-5"


@pytest.fixture(autouse=True)
def usage_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    context_calibration.reset_for_tests()
    return tmp_path


def test_an_unmeasured_model_keeps_the_plain_estimate() -> None:
    assert context_calibration.factor(PROVIDER, MODEL) == 1.0
    assert context_calibration.calibrated(10_000, PROVIDER, MODEL) == 10_000


def test_reported_usage_moves_the_estimate_towards_the_provider_count() -> None:
    assert context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=12_000) == pytest.approx(1.2)
    assert context_calibration.calibrated(10_000, PROVIDER, MODEL) == 12_000

    # Later samples average in: one outlier does not swing the factor.
    for _ in range(10):
        context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=14_000)
    assert 1.3 < context_calibration.factor(PROVIDER, MODEL) < 1.4


def test_the_factor_is_bounded_and_implausible_samples_are_ignored() -> None:
    low, high = context_calibration.FACTOR_BOUNDS
    # A count of cached tokens only, or a fake, says nothing about the request's size.
    assert context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=100) is None
    assert context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=900_000) is None
    assert context_calibration.factor(PROVIDER, MODEL) == 1.0

    for _ in range(20):
        context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=70_000)
    assert context_calibration.factor(PROVIDER, MODEL) == high
    for _ in range(40):
        context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=2_600)
    assert context_calibration.factor(PROVIDER, MODEL) == pytest.approx(low, abs=0.01)
    assert context_calibration.factor(PROVIDER, MODEL) >= low


def test_small_requests_and_local_ollama_are_never_learned_from() -> None:
    assert context_calibration.observe(PROVIDER, MODEL, estimated=400, reported=800) is None
    # Ollama's prompt_eval_count counts only what it had not cached.
    assert context_calibration.observe("ollama", "model:ollama:qwen3:14b", estimated=20_000, reported=30_000) is None
    assert context_calibration.factor("ollama", "model:ollama:qwen3:14b") == 1.0
    assert context_calibration.factor(PROVIDER, MODEL) == 1.0


def test_the_factor_is_kept_in_usage_db_and_holds_no_conversation(usage_folder) -> None:
    context_calibration.observe(PROVIDER, MODEL, estimated=10_000, reported=15_000)
    context_calibration._cache.clear()  # a new process

    assert context_calibration.factor(PROVIDER, MODEL) == pytest.approx(1.5)
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(usage_folder / "usage.db")) as connection:
        rows = connection.execute("SELECT * FROM context_calibration").fetchall()
    assert [row[:4] for row in rows] == [(PROVIDER, MODEL, pytest.approx(1.5), 1)]


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("prompt is too long: 215000 tokens > 200000 maximum", 215_000),
        ("This model's maximum context length is 128000 tokens. However, your messages resulted in 130,021 tokens.",
         130_021),
        ("you requested 140000 tokens (130000 in the messages, 10000 in the completion)", 130_000),
        ("The input token count (1100000) exceeds the maximum number of tokens allowed (1048576).", 1_100_000),
        ("context_length_exceeded", None),
    ],
)
def test_an_overflow_error_states_the_request_size(text, tokens) -> None:
    assert context_calibration.reported_input_tokens(text) == tokens


def test_an_overflow_raises_the_factor_past_the_window() -> None:
    # The error named no size: the refused request was at least the window.
    factor = context_calibration.observe_overflow(PROVIDER, MODEL, estimated=100_000, limit=200_000)
    assert factor == pytest.approx(2.04)
    assert context_calibration.calibrated(100_000, PROVIDER, MODEL) > 200_000

    # One that names it is an ordinary sample.
    context_calibration.reset_for_tests()
    context_calibration.observe_overflow(
        PROVIDER, MODEL, estimated=100_000, limit=200_000, error_text="prompt is too long: 230000 tokens > 200000 maximum"
    )
    assert context_calibration.factor(PROVIDER, MODEL) == pytest.approx(2.3)


def test_a_thinking_block_is_counted_by_its_text_not_its_signature() -> None:
    thought = "Weighing the two options. " * 40
    signature = "Eq" + "x" * 8_000
    with_block = AIMessage(content=[
        {"type": "thinking", "thinking": thought, "signature": signature},
        {"type": "text", "text": "The answer."},
    ])
    as_text = AIMessage(content=[{"type": "text", "text": thought}, {"type": "text", "text": "The answer."}])

    counted = agent._count_prepared_tokens([HumanMessage(content="Q"), with_block], ())

    assert counted == agent._count_prepared_tokens([HumanMessage(content="Q"), as_text], ())
    assert counted < len(signature) // 4


def test_reasoning_kept_beside_a_reply_is_counted() -> None:
    """30 Oct demo run: a local thinking model's steps carried ~27k tokens of reasoning the estimate missed, so the
    request filled the window (63k of 65k) and the reply was cut off before it began."""
    thought = "Planning the scenes: title card, the search, the results. " * 200
    step = AIMessage(content="Let me make the music first.", additional_kwargs={"reasoning_content": thought})
    plain = AIMessage(content="Let me make the music first.")

    with_reasoning = agent._count_prepared_tokens([HumanMessage(content="Q"), step], ())
    without = agent._count_prepared_tokens([HumanMessage(content="Q"), plain], ())

    assert with_reasoning - without >= len(thought) // 5
