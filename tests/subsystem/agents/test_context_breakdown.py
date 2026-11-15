"""What fills the context after a reply: the breakdown on the settled snapshot (B7).

Each case drives the real Agent (or Chat Only) preparation over a scripted conversation, settles the
turn as a finished reply does, and reads the breakdown off the settled measurement. The model, its
context policy and recall are fixed; nothing reaches a provider.
"""

from __future__ import annotations

import contextvars
import math
from types import SimpleNamespace

import pytest

import row_bot.agent as agent
from tests.helpers.agent_requests import MODEL_REF, AgentConversation, text_of_tokens

pytestmark = pytest.mark.subsystem

BUCKETS = ("instructions", "tools", "skills", "memory", "conversation")
GUIDES = "Tool guide: read a file before you change it.\n" * 40
TOOLS = tuple(
    {"type": "function", "function": {"name": f"tool_{index}", "description": "Does one thing. " * 20,
                                      "parameters": {"type": "object", "properties": {}}}}
    for index in range(6)
)
MEMORY = {"id": "mem_a", "entity_type": "fact", "subject": "Notes", "description": "Notes live in notes.txt.",
          "score": 0.9}


@pytest.fixture
def conversation(monkeypatch) -> AgentConversation:
    conversation = AgentConversation(agent, monkeypatch)
    monkeypatch.setattr(agent, "_tool_guides_prompt", lambda: GUIDES)
    monkeypatch.setattr("row_bot.skill_discovery.render_active_skills_prompt", lambda records: "")
    return conversation


def _recall(monkeypatch, memories: list[dict]) -> None:
    monkeypatch.setattr(
        "row_bot.memory_policy.build_auto_recall",
        lambda *args, **kwargs: SimpleNamespace(
            allowed=bool(memories), reason="selected" if memories else "no_match",
            candidates_seen=len(memories), selected=list(memories), trace={},
        ),
    )


def _settle(conversation: AgentConversation, *, tools: tuple[dict, ...] = (), answer: str = "Done."):
    """One finished turn: its last model call's preparation, the reply, then the settled measurement."""
    conversation.request()  # the turn's first call persists its turn context

    def run():
        conversation.agent._set_active_runtime_context(
            thread_id=conversation.thread_id, runtime_surface="normal_chat", requested_runtime_mode="agent",
            selected_runtime_mode="agent", generation_id=f"{conversation.thread_id}-turn-{conversation.turns}",
            model_override=MODEL_REF, enabled_tool_names=("filesystem",),
        )
        agent._current_canonical_tools_var.set(tuple(tools))
        inputs = agent._collect_agent_preparation_inputs(conversation.state)
        conversation.answer(answer)
        return inputs, agent._persist_settled_context_usage(inputs, conversation.state["messages"])

    return contextvars.copy_context().run(run)


def _total(breakdown: dict[str, int]) -> int:
    return sum(breakdown[key] for key in BUCKETS)


def test_the_settled_buckets_add_up_to_the_estimate_and_count_each_part(conversation, monkeypatch):
    _recall(monkeypatch, [MEMORY])
    conversation.say("Where do my notes live?")

    inputs, usage = _settle(conversation, tools=TOOLS)

    breakdown = usage.breakdown
    assert usage.snapshot_kind == "settled"
    assert _total(breakdown) == usage.estimated_input_tokens
    assert breakdown["tools"] == agent._count_prepared_tokens([], TOOLS)
    assert breakdown["tool_count"] == len(TOOLS)
    assert breakdown["skills"] == math.ceil(len(GUIDES.strip()) / 4)
    assert breakdown["instructions"] > 0 and breakdown["conversation"] > 0
    assert breakdown["memory"] > 0
    # Live per-call measurements carry no breakdown: it is counted once per settled turn.
    per_call = agent._prepare_model_input(inputs.raw_messages, inputs, None, mode="agent")
    assert per_call.usage.breakdown is None


def test_a_turn_without_recalled_memories_has_no_memory_bucket(conversation):
    conversation.say("Hello there.")

    _inputs, usage = _settle(conversation, tools=TOOLS)

    assert usage.breakdown["memory"] == 0
    assert _total(usage.breakdown) == usage.estimated_input_tokens


def test_a_compaction_summary_counts_as_conversation(conversation, monkeypatch):
    conversation.say("First question.")
    conversation.request()
    conversation.answer("First answer.")
    conversation.say("Second question.")
    _inputs, before = _settle(conversation, tools=TOOLS)

    summary = text_of_tokens("summary", 2_000)
    monkeypatch.setattr(agent, "_validated_summary_for_inputs", lambda inputs: {
        "schema_version": 1, "mode": "agent", "boundary_message_count": 3, "summary": summary,
    })
    conversation.say("Third question.")
    _inputs, after = _settle(conversation, tools=TOOLS)

    assert after.breakdown["conversation"] >= 2_000
    # Only the short guidance that comes with a summary joins the instructions.
    assert after.breakdown["instructions"] - before.breakdown["instructions"] < 100
    assert _total(after.breakdown) == after.estimated_input_tokens


def test_calibration_scales_every_bucket_and_keeps_the_sum(conversation, monkeypatch):
    _recall(monkeypatch, [MEMORY])
    conversation.say("Where do my notes live?")
    _inputs, plain = _settle(conversation, tools=TOOLS)

    monkeypatch.setattr("row_bot.context_calibration.calibrated", lambda tokens, provider, model: tokens * 3 // 2)
    conversation.say("And my drafts?")
    _inputs, calibrated = _settle(conversation, tools=TOOLS)

    assert _total(calibrated.breakdown) == calibrated.estimated_input_tokens
    assert calibrated.count_source == "calibrated_approximate"
    assert abs(calibrated.breakdown["tools"] - plain.breakdown["tools"] * 1.5) <= 1
    assert abs(calibrated.breakdown["skills"] - plain.breakdown["skills"] * 1.5) <= 1


def test_chat_only_has_no_tools_or_skills(conversation):
    from langchain_core.messages import AIMessage, HumanMessage

    def run():
        conversation.agent._set_active_runtime_context(
            thread_id=conversation.thread_id, runtime_surface="normal_chat", requested_runtime_mode="chat_only",
            selected_runtime_mode="chat_only", model_override=MODEL_REF,
        )
        inputs = agent._collect_chat_only_preparation_inputs(conversation.thread_id, "Tell me a joke.")
        settled = [HumanMessage(content="Tell me a joke."), AIMessage(content="A short one.")]
        return agent._persist_settled_context_usage(inputs, settled)

    usage = contextvars.copy_context().run(run)

    assert usage.mode == "chat_only"
    assert (usage.breakdown["tools"], usage.breakdown["tool_count"], usage.breakdown["skills"]) == (0, 0, 0)
    assert usage.breakdown["instructions"] > 0 and usage.breakdown["conversation"] > 0
    assert _total(usage.breakdown) == usage.estimated_input_tokens
