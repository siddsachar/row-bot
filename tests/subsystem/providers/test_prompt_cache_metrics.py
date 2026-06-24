from __future__ import annotations

import logging
from types import SimpleNamespace

from langchain_core.messages import AIMessage


def test_prompt_cache_usage_normalizes_anthropic_and_openai_shapes():
    from row_bot.prompt_cache import normalize_prompt_cache_usage

    assert normalize_prompt_cache_usage({
        "token_usage": {
            "cache_read_input_tokens": 12,
            "cache_creation_input_tokens": 34,
        }
    }) == {
        "prompt_cache_read_tokens": 12,
        "prompt_cache_write_tokens": 34,
    }

    assert normalize_prompt_cache_usage({
        "token_usage": {
            "input_tokens_details": {"cached_tokens": "56"},
            "prompt_tokens_details": {"cache_creation_tokens": 78},
        }
    }) == {
        "prompt_cache_read_tokens": 56,
        "prompt_cache_write_tokens": 78,
    }


def test_stream_completion_diagnostics_log_prompt_cache_counts_without_prompt_text(monkeypatch, caplog):
    import row_bot.agent as agent

    monkeypatch.setattr(agent, "get_current_model", lambda: "model:anthropic:claude-sonnet-4-5")
    monkeypatch.setattr(agent, "get_context_size", lambda model=None: 200_000)
    monkeypatch.setattr(
        "row_bot.providers.resolution.resolve_provider_config",
        lambda *args, **kwargs: SimpleNamespace(
            provider_id="anthropic",
            runtime_model="claude-sonnet-4-5",
            selection_ref="model:anthropic:claude-sonnet-4-5",
        ),
    )

    latest = AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "cache_read_input_tokens": 12,
                "cache_creation_input_tokens": 34,
            },
            "debug_prompt_text": "SECRET_PROMPT_TEXT",
        },
    )

    with caplog.at_level(logging.INFO, logger="row_bot.agent"):
        agent._log_stream_completion(
            config={"configurable": {"thread_id": "thread-cache", "model_override": "model:anthropic:claude-sonnet-4-5"}},
            answer_chars=2,
            answer_chunks=1,
            reasoning_chars=0,
            reasoning_chunks=0,
            tool_call_count=0,
            tool_result_count=0,
            finish_reason="stop",
            stopped_by_user=False,
            loop_detected=False,
            browser_budget_exceeded=False,
            latest_ai_message=latest,
            phase_timings=None,
        )

    record = next(
        record for record in caplog.records
        if record.name == "row_bot.agent" and record.message.startswith("stream completion diagnostics=")
    )
    diagnostics = record.args[0] if isinstance(record.args, tuple) else record.args
    assert diagnostics["prompt_cache_read_tokens"] == 12
    assert diagnostics["prompt_cache_write_tokens"] == 34
    assert "SECRET_PROMPT_TEXT" not in record.message
