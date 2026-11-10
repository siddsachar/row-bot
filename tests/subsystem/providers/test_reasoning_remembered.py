"""New chats start on the Thinking choice the person last made for a model (#80)."""
from __future__ import annotations

import pytest

from row_bot.providers import reasoning
from row_bot.providers.reasoning import ReasoningCapabilities, ReasoningRequestPlan, ReasoningSelection

QWEN = "model:ollama:qwen3.8:27b"
OTHER = "model:ollama:llama3.3:8b"
TOGGLE = ReasoningCapabilities(thinking_mode="toggle", can_disable=True, request_style="ollama")


@pytest.fixture
def chats(tmp_path, monkeypatch):
    import row_bot.threads as threads

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    threads._init_thread_db(raise_on_error=True)
    caps = {QWEN: TOGGLE, OTHER: TOGGLE}
    monkeypatch.setattr(reasoning, "resolve_reasoning_capabilities_for_ref", caps.get)
    return threads, caps


def new_chat(threads) -> str:
    return threads.create_thread("Chat", seed_default_skills=False)


def test_thinking_off_in_one_chat_starts_new_chats_with_that_model_off(chats) -> None:
    from row_bot.providers.runtime import _reasoning_constructor_kwargs

    threads, _caps = chats
    first = new_chat(threads)
    earlier = new_chat(threads)
    assert reasoning.apply_reasoning_command(first, QWEN, "off") == f"Reasoning for {QWEN}: Off."

    later = new_chat(threads)
    plan = reasoning.request_plan_for(later, QWEN, use_snapshot=False)
    assert plan.selection == ReasoningSelection(kind="off")
    # ChatOllama's reasoning=False is Ollama's think=false.
    assert _reasoning_constructor_kwargs(plan, provider="ollama") == {"reasoning": False}
    # Chats the person already has keep their own choice, and other models are untouched.
    assert reasoning.request_plan_for(earlier, QWEN, use_snapshot=False).is_default
    assert reasoning.request_plan_for(later, OTHER, use_snapshot=False).is_default


def test_choosing_default_forgets_and_saved_selections_alone_are_not_remembered(chats) -> None:
    threads, _caps = chats
    first = new_chat(threads)
    reasoning.apply_reasoning_command(first, QWEN, "off")
    reasoning.apply_reasoning_command(new_chat(threads), QWEN, "default")
    assert threads.get_thread_reasoning_selection(new_chat(threads), QWEN) is None

    # A helper agent's thread is given its profile's choice directly: that is not the person's choice.
    threads.set_thread_reasoning_selection(first, OTHER, {"kind": "off"})
    assert threads.get_thread_reasoning_selection(new_chat(threads), OTHER) is None


def test_a_remembered_choice_the_model_no_longer_offers_is_dropped_with_a_notice(chats) -> None:
    threads, caps = chats
    reasoning.apply_reasoning_command(new_chat(threads), QWEN, "off")
    caps[QWEN] = ReasoningCapabilities(supported_efforts=("low", "high"), request_style="ollama")

    stale = new_chat(threads)
    assert reasoning.request_plan_for(stale, QWEN, use_snapshot=False).is_default
    assert [notice["kind"] for notice in reasoning.consume_reasoning_notices(stale)] == ["stale_selection"]
    assert threads.get_thread_reasoning_selection(new_chat(threads), QWEN) is None


def test_a_choice_the_provider_refused_is_forgotten_for_new_chats(chats, monkeypatch) -> None:
    from langchain_core.messages import AIMessageChunk, HumanMessage

    import row_bot.providers.transports.reasoning_fallback as fallback_module
    from tests.subsystem.providers.test_reasoning_control import _FakeRunnable, _ReasoningRequestError

    threads, _caps = chats
    chat = new_chat(threads)
    reasoning.apply_reasoning_command(chat, QWEN, "off")
    monkeypatch.setattr(fallback_module, "_active_thread_id", lambda: chat)
    model = fallback_module.ReasoningFallbackChatModel(
        primary=_FakeRunnable(error=_ReasoningRequestError("HTTP 400: reasoning is unsupported")),
        provider_default=_FakeRunnable(events=[AIMessageChunk(content="answer")]),
        plan=ReasoningRequestPlan(QWEN, ReasoningSelection(kind="off"), TOGGLE),
        provider_id="ollama",
    )

    assert "".join(str(chunk.message.content) for chunk in model._stream([HumanMessage(content="hi")])) == "answer"
    assert threads.get_thread_reasoning_selection(new_chat(threads), QWEN) is None
