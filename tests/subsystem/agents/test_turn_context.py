"""The turn context: one app-authored block per turn, after the turn's message, kept in the checkpoint.

What changes between turns (date and time, approval mode, tool groups, Row-Bot's state, recall) used
to sit in system sections ahead of the conversation, so no provider could reuse its prompt cache.
These cases drive the real Agent preparation (``_pre_model_trim``), and a real LangGraph graph, and
check that the context is built once per turn, persisted, replayed unchanged, diff-only after the
first, a full snapshot again once a compaction summary hides the earlier ones, and that what the
model is told is still all there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

from row_bot import turn_context

pytestmark = pytest.mark.subsystem

THREAD_ID = "turn-context"
MEMORY_A = {"id": "mem_a", "entity_type": "fact", "subject": "Notes", "description": "Notes live in notes.txt.",
            "score": 0.9}
MEMORY_B = {"id": "mem_b", "entity_type": "fact", "subject": "Style", "description": "Prefers short bullets.",
            "score": 0.9}


def _clock(start: datetime) -> type[datetime]:
    class Clock(datetime):
        current = start

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    return Clock


class Env:
    """The real Agent preparation with fixed providers, state and recall."""

    def __init__(self, agent: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.agent = agent
        self.clock = _clock(datetime(2026, 10, 11, 9, 0, 5, tzinfo=timezone.utc))
        self.memories: list[dict] = []
        self.dynamic = "Current state:\n- Current model: gpt-4.1"
        self.groups: tuple[str, ...] = ("filesystem", "web_search")
        self.approval = "approve"
        self.recalls = 0
        self.touches = 0
        self.traces = 0
        policy = SimpleNamespace(
            model_ref="model:openai:gpt-4.1", provider_id="openai", native_limit_tokens=200_000,
            requested_limit_tokens=None, effective_limit_tokens=200_000, usable_input_tokens=1_000_000_000,
            compact_at_tokens=1_000_000_000, capacity_source="test_metadata", capacity_state="ready",
            limit_kind="combined",
        )
        monkeypatch.setattr(agent, "_datetime", self.clock)
        monkeypatch.setattr(agent, "get_context_size", lambda *args, **kwargs: 200_000)
        monkeypatch.setattr(agent, "get_context_policy", lambda *args, **kwargs: policy)
        monkeypatch.setattr(agent, "get_current_model", lambda: "model:openai:gpt-4.1")
        monkeypatch.setattr(agent, "is_cloud_model", lambda model: True)
        monkeypatch.setattr(agent, "get_cloud_provider", lambda model: "openai")
        monkeypatch.setattr(agent, "is_background_workflow", lambda: False)
        monkeypatch.setattr("row_bot.self_knowledge.build_static_self_knowledge_block", lambda: "SELF KNOWLEDGE")
        monkeypatch.setattr("row_bot.self_knowledge.build_dynamic_self_knowledge_block", lambda: self.dynamic)
        monkeypatch.setattr("row_bot.skills.get_skills_prompt", lambda *args, **kwargs: "")
        monkeypatch.setattr("row_bot.plugins.registry.get_skills_prompt", lambda *args, **kwargs: "")
        monkeypatch.setattr("row_bot.memory_policy.build_auto_recall", self._recall)
        monkeypatch.setattr("row_bot.memory_policy.touch_selected_memories", self._touch)
        monkeypatch.setattr("row_bot.memory_policy.record_recall_trace", self._trace)

    def _recall(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.recalls += 1
        return SimpleNamespace(allowed=bool(self.memories), reason="selected" if self.memories else "no_match",
                               candidates_seen=len(self.memories), selected=list(self.memories), trace={})

    def _touch(self, decision: Any) -> None:
        self.touches += 1

    def _trace(self, decision: Any, **kwargs: Any) -> None:
        self.traces += 1

    def tick(self, **delta: int) -> None:
        self.clock.current = self.clock.current + timedelta(**delta)

    def activate(self, turn: str) -> None:
        self.agent._set_active_runtime_context(
            thread_id=THREAD_ID, runtime_surface="normal_chat", requested_runtime_mode="agent",
            selected_runtime_mode="agent", generation_id=turn, approval_mode=self.approval,
            enabled_tool_names=self.groups,
        )
        self.agent._current_authorized_skill_records_var.set(())

    def call(self, state: dict, turn: str) -> list:
        """One model call through the pre-model hook; its ``messages`` update applied as LangGraph does."""
        self.activate(turn)
        update = self.agent._pre_model_trim(state)
        if "messages" in update:
            state["messages"] = add_messages(state["messages"], update["messages"])
        return list(update["llm_input_messages"])

    def new_turn(self, state: dict | None, text: str, turn: str) -> dict:
        messages = list((state or {}).get("messages") or [])
        return {"messages": [*messages, HumanMessage(content=text, id=f"human-{turn}")],
                "execution_budget": self.agent.new_execution_budget(turn)}


@pytest.fixture
def env(tmp_path, monkeypatch, reload_for_data_dir) -> Env:
    agent, _activation = reload_for_data_dir(tmp_path / "data", "row_bot.agent", "row_bot.skills_activation")
    return Env(agent, monkeypatch)


def _block(messages: list) -> str:
    """The turn-context text of the latest user turn the model receives."""
    for message in reversed(messages):
        text = message.content if isinstance(message.content, str) else str(message.content)
        if turn_context.OPEN_TAG in text:
            return text[text.index(turn_context.OPEN_TAG):]
    raise AssertionError("no turn context in the request")


def _contexts(state: dict) -> list:
    return [message for message in state["messages"] if turn_context.is_turn_context_message(message)]


def _step(state: dict, call_id: str, result: str) -> None:
    state["messages"] = [
        *state["messages"],
        AIMessage(content="", tool_calls=[{"id": call_id, "name": "read_file", "args": {}, "type": "tool_call"}]),
        ToolMessage(content=result, name="read_file", tool_call_id=call_id),
    ]


def test_the_turn_context_follows_the_message_is_persisted_and_is_reused_by_every_call_of_the_turn(env):
    env.memories = [MEMORY_A]
    state = env.new_turn(None, "Summarise notes.txt.", "t1")

    first = env.call(state, "t1")
    contexts = _contexts(state)
    assert len(contexts) == 1
    assert state["messages"].index(contexts[0]) == 1  # right after the person's message
    _step(state, "call_1", "notes")
    env.tick(minutes=3)
    second = env.call(state, "t1")
    _step(state, "call_2", "more notes")
    third = env.call(state, "t1")

    assert len(_contexts(state)) == 1  # not rebuilt by later calls of the turn
    assert _block(first) == _block(second) == _block(third)
    assert second[: len(first)] == first and third[: len(second)] == second
    # The person's message and the block travel as one user turn; nothing of Row-Bot's metadata does.
    user = first[-1]
    assert user.type == "human" and str(user.content).startswith("Summarise notes.txt.")
    for message in third:
        assert turn_context.METADATA_KEY not in (getattr(message, "additional_kwargs", None) or {})
    # Recall, its touch and its trace ran once for the whole turn (F3).
    assert (env.recalls, env.touches, env.traces) == (1, 1, 1)


class _RecordingModel(FakeMessagesListChatModel):
    """A fake chat model that records each provider-facing input."""

    def bind_tools(self, tools, **kwargs):
        del tools, kwargs
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _SEEN.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


_SEEN: list[list] = []


def test_a_real_graph_persists_the_context_through_the_hook_and_replays_it(env):
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.prebuilt import create_react_agent

    from row_bot.agent_budget import RowBotAgentState, post_model_budget_hook

    @tool
    def read_file(path: str) -> str:
        """Read a file."""
        return f"contents of {path}"

    _SEEN.clear()
    model = _RecordingModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "n.txt"},
                                           "type": "tool_call"}]),
        AIMessage(content="Three bullets."),
        AIMessage(content="Compared."),
    ])
    graph = create_react_agent(model=model, tools=[read_file], state_schema=RowBotAgentState,
                               pre_model_hook=env.agent._pre_model_trim, post_model_hook=post_model_budget_hook,
                               checkpointer=InMemorySaver(), version="v2")
    config = {"configurable": {"thread_id": THREAD_ID}, "recursion_limit": 40}
    env.memories = [MEMORY_A]

    env.activate("t1")
    graph.invoke({"messages": [HumanMessage(content="Summarise n.txt.", id="h1")],
                  "execution_budget": env.agent.new_execution_budget("t1")}, config)
    env.tick(minutes=10)
    env.activate("t2")
    graph.invoke({"messages": [HumanMessage(content="Compare with last quarter.", id="h2")],
                  "execution_budget": env.agent.new_execution_budget("t2")}, config)

    messages = graph.get_state(config).values["messages"]
    kinds = ["context" if turn_context.is_turn_context_message(m) else m.type for m in messages]
    assert kinds == ["human", "context", "ai", "tool", "ai", "human", "context", "ai"]
    assert len(_SEEN) == 3
    assert _SEEN[1][: len(_SEEN[0])] == _SEEN[0]  # iteration i+1 starts with iteration i
    assert _SEEN[2][: len(_SEEN[1])] == _SEEN[1]  # turn N+1 starts with turn N's last call
    for request in _SEEN:
        for message in request:
            assert turn_context.METADATA_KEY not in (getattr(message, "additional_kwargs", None) or {})
    assert env.recalls == 2


def test_a_later_turn_replays_earlier_contexts_unchanged(env):
    env.memories = [MEMORY_A]
    state = env.new_turn(None, "Summarise notes.txt.", "t1")
    env.call(state, "t1")
    _step(state, "call_1", "notes")
    last = env.call(state, "t1")
    state["messages"] = [*state["messages"], AIMessage(content="Three bullets.")]
    env.tick(minutes=45)
    env.memories = [MEMORY_B]
    state = env.new_turn(state, "Now compare them with last quarter.", "t2")
    following = env.call(state, "t2")

    assert following[: len(last)] == last
    assert len(_contexts(state)) == 2
    assert env.recalls == 2  # once per turn


def test_after_the_first_the_turn_context_says_only_what_changed(env):
    env.memories = [MEMORY_A]
    state = env.new_turn(None, "Summarise notes.txt.", "t1")
    first = _block(env.call(state, "t1"))
    assert "Current date and time: Sunday, October 11, 2026 at" in first
    assert "Approval level for this chat: Ask." in first
    assert "Available tool groups for this turn include: filesystem, web_search." in first
    assert "Current model: gpt-4.1" in first
    assert "[id=mem_a]" in first

    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    env.tick(minutes=30)
    env.memories = [MEMORY_A, MEMORY_B]
    state = env.new_turn(state, "And the totals?", "t2")
    second = _block(env.call(state, "t2"))
    assert "Current time:" in second and "Current date and time" not in second
    for unchanged in ("Approval mode", "Available tool groups", "Current model", "[id=mem_a]"):
        assert unchanged not in second
    assert "[id=mem_b]" in second  # only the memory not shown before

    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    env.approval = "block"
    env.groups = ("filesystem",)
    env.dynamic = "Current state:\n- Current model: gpt-5.5"
    state = env.new_turn(state, "Carry on.", "t3")
    third = _block(env.call(state, "t3"))
    assert "Approval level for this chat: Block." in third
    assert "Available tool groups for this turn include: filesystem." in third
    assert "Current model: gpt-5.5" in third
    assert "Relevant long-term memory" not in third

    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    env.tick(days=1)
    state = env.new_turn(state, "Good morning.", "t4")
    fourth = _block(env.call(state, "t4"))
    assert "Current date and time: Monday, October 12, 2026 at" in fourth  # a new day says the date again
    assert "Approval mode" not in fourth


def test_a_compaction_summary_brings_back_a_full_snapshot(env, monkeypatch):
    env.memories = [MEMORY_A]
    state = env.new_turn(None, "Summarise notes.txt.", "t1")
    env.call(state, "t1")
    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    state = env.new_turn(state, "And the totals?", "t2")
    second = _block(env.call(state, "t2"))
    assert "Approval mode" not in second and "[id=mem_a]" not in second
    state["messages"] = [*state["messages"], AIMessage(content="Totals.")]

    boundary = 3  # the first turn (message, context, answer) is summarised
    summary = {
        "schema_version": 1, "mode": "agent", "boundary_message_count": boundary, "boundary_digest": "d" * 64,
        "summary": "\n".join(f"{heading}\n- earlier work" for heading in (
            "## Current Goal", "## Constraints and Decisions", "## Completed Work", "## Current State",
            "## Relevant Details", "## Next Step")),
    }
    monkeypatch.setattr("row_bot.threads.load_validated_summary_state",
                        lambda thread_id, mode, messages=None: dict(summary) if mode == "agent" else None)
    state = env.new_turn(state, "Carry on.", "t3")
    request = env.call(state, "t3")
    third = _block(request)

    assert any("<HISTORICAL_CONTEXT" in str(message.content) for message in request)
    assert "Current date and time:" in third
    assert "Approval level for this chat: Ask." in third
    assert "Available tool groups for this turn include:" in third
    assert "Current model: gpt-4.1" in third
    assert "[id=mem_a]" in third  # its first showing is summarised away, so it is shown again
    assert _contexts(state)[-1].additional_kwargs[turn_context.METADATA_KEY]["full"] is True


def test_a_compaction_later_in_the_turn_appends_a_full_snapshot(env, monkeypatch):
    env.memories = [MEMORY_A]
    state = env.new_turn(None, "Summarise notes.txt.", "t1")
    env.call(state, "t1")
    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    state = env.new_turn(state, "And the totals?", "t2")
    env.call(state, "t2")
    _step(state, "call_1", "totals")
    monkeypatch.setattr(env.agent, "_agent_summary_marker", lambda messages: ("3:dddd", 3))

    messages, created = env.agent._turn_context_after_compaction(
        list(state["messages"]), state["execution_budget"], [])

    assert len(created) == 1 and messages[-1] is created[0]  # appended after the latest tool result
    snapshot = str(created[0].content)
    assert "Current date and time:" in snapshot and "Approval level" in snapshot
    assert created[0].id.endswith("-1")
    assert env.recalls == 2  # the turn's recall is reused, not run again


def test_skill_usage_is_recorded_once_per_turn_not_once_per_call(env, monkeypatch):
    from row_bot.skill_discovery import SkillRecord

    record = SkillRecord(canonical_id="report_writer", alias=None, display_name="Report writer", icon="R",
                         description="Writes short reports.", tags=(), activation={},
                         instructions="Write three bullets.", source="manual", root=None)
    recorded: list[list[str]] = []
    monkeypatch.setattr("row_bot.skills_activation.record_usage",
                        lambda thread_id, names, source="agent": recorded.append(list(names)))
    from row_bot.skills_activation import pin_skill

    pin_skill(THREAD_ID, "report_writer")
    state = env.new_turn(None, "Write the report.", "t1")
    for call_id in ("c1", "c2"):
        env.activate("t1")
        env.agent._current_authorized_skill_records_var.set((record,))
        update = env.agent._pre_model_trim(state)
        if "messages" in update:
            state["messages"] = add_messages(state["messages"], update["messages"])
        _step(state, call_id, "done")
    systems = [str(message.content) for message in update["llm_input_messages"] if message.type == "system"]

    assert recorded == [["report_writer"]]
    assert any("Write three bullets." in text for text in systems)  # a pinned skill stays in skills.manual


def test_skill_load_and_a_skill_mention_reach_the_model_without_editing_the_system_prompt(env):
    from row_bot.skill_discovery import SkillRecord, build_skill_discovery_tools

    record = SkillRecord(canonical_id="report_writer", alias=None, display_name="Report writer", icon="R",
                         description="Writes short reports.", tags=(), activation={},
                         instructions="1. Read the sources. 2. Write three bullets.", source="manual", root=None)
    _search, load = build_skill_discovery_tools((record,), thread_id=THREAD_ID, context_tokens=200_000)
    loaded = load.invoke({"name": "report_writer"})
    assert "1. Read the sources. 2. Write three bullets." in loaded  # the body comes back in the result

    env.activate("t1")
    env.agent._current_authorized_skill_records_var.set((record,))
    stable, turn_records = env.agent._split_active_skill_records()
    assert stable == [] and [item.canonical_id for item in turn_records] == ["report_writer"]

    # A conversation from before this change has the skill loaded but its body nowhere in view:
    # the next turn context delivers it once.
    env.agent._TURN_CONTEXT_VALUES.clear()
    state = env.new_turn(None, "Write the report.", "t1")
    env.activate("t1")
    env.agent._current_authorized_skill_records_var.set((record,))
    update = env.agent._pre_model_trim(state)
    block = _block(update["llm_input_messages"])
    assert "Report writer" in block and "Write three bullets" in block
    systems = [str(message.content) for message in update["llm_input_messages"] if message.type == "system"]
    assert not any("Write three bullets" in text for text in systems)
