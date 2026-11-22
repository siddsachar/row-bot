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
from pathlib import Path
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
        self.device_zone = ""
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
            enabled_tool_names=self.groups, device_zone=self.device_zone,
        )
        self.agent._current_authorized_skill_records_var.set(())

    def call(self, state: dict, turn: str, *, config: dict | None = None) -> list:
        """One model call through the pre-model hook; its ``messages`` update applied as LangGraph does."""
        self.activate(turn)
        update = self.agent._pre_model_trim(state, config)
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


def test_a_device_in_another_zone_is_named_so_local_time_means_its_zone(env, monkeypatch):
    """M10: asked from a London browser, "your local time" was the Mac's Los Angeles. The person's device zone is
    named when it isn't the computer's, once, and again only when it changes."""
    from row_bot import schedule_zones

    monkeypatch.setattr(schedule_zones, "local_zone_name", lambda: "America/Los_Angeles")
    env.device_zone = "Europe/London"
    state = env.new_turn(None, "Every weekday at 8, tell me Sydney's weather.", "t1")
    assert ("The person's device is in Europe/London (UTC+01:00), not this computer's time zone: their "
            "\"local time\" is that zone's, and a new workflow schedule keeps it.") in _block(env.call(state, "t1"))

    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    state = env.new_turn(state, "Thanks.", "t2")
    assert "device is in" not in _block(env.call(state, "t2"))  # unchanged, so not repeated

    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    env.device_zone = "America/Los_Angeles"
    state = env.new_turn(state, "Now from the computer itself.", "t3")
    assert "The person's device is in this computer's time zone." in _block(env.call(state, "t3"))


SPOKEN = {"configurable": {"thread_id": THREAD_ID, "voice_mode": True}}


def test_a_spoken_turn_is_answered_for_listening_and_the_system_prompt_stays_the_same(env):
    """VD7: a Talk or Realtime turn's context says to answer for listening, with what's better read kept on
    screen; a typed turn after it says to write as usual. The line sits in the turn's own context message,
    never in the person's words, and the system prompt is byte-identical, so the prompt cache holds."""
    spoken_line = env.agent.VOICE_TURN_LINE
    state = env.new_turn(None, "What's on my calendar today?", "t1")
    typed_first = env.call(env.new_turn(None, "What's on my calendar today?", "t0"), "t0")
    assert spoken_line not in _block(typed_first)

    spoken = env.call(state, "t1", config=SPOKEN)
    assert spoken_line in _block(spoken)
    person = [message for message in state["messages"] if isinstance(message, HumanMessage)
              and not turn_context.is_turn_context_message(message)]
    assert [message.content for message in person] == ["What's on my calendar today?"]
    assert spoken_line in str(_contexts(state)[0].content)  # saved in the turn's context, so replayed as is
    assert spoken[0].content == typed_first[0].content  # the cached system prefix

    _step(state, "call-1", "Stand-up at 10.")
    later = env.call(state, "t1", config=SPOKEN)
    assert len(_contexts(state)) == 1 and _block(later) == _block(spoken)

    state["messages"] = [*state["messages"], AIMessage(content="You have stand-up at ten.")]
    state = env.new_turn(state, "And tomorrow?", "t2")
    assert spoken_line not in _block(env.call(state, "t2", config=SPOKEN))  # unchanged, so not repeated

    state["messages"] = [*state["messages"], AIMessage(content="Nothing tomorrow.")]
    state = env.new_turn(state, "Thanks, typing now.", "t3")
    typed = env.call(state, "t3", config={"configurable": {"thread_id": THREAD_ID}})
    assert env.agent.TYPED_TURN_LINE in _block(typed)
    assert typed[0].content == spoken[0].content


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


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The person's home folder, named after their account, with no workspace chosen yet."""
    folder = tmp_path / "jane-account"
    folder.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: folder))
    monkeypatch.delenv("ROW_BOT_WORKSPACE", raising=False)
    return folder


def test_the_turn_context_names_the_workspace_under_home_without_the_account_name(env, home):
    state = env.new_turn(None, "Make a packing list.", "t1")
    first = _block(env.call(state, "t1"))

    assert ("Workspace folder: ~/Documents/Row-Bot. File tools and the shell start here; relative paths are "
            "inside it, and a file tool asks the person before it uses a full path elsewhere.") in first
    assert "jane-account" not in first and str(home) not in first

    state["messages"] = [*state["messages"], AIMessage(content="Done.")]
    state = env.new_turn(state, "Add sun cream.", "t2")
    assert "Workspace folder" not in _block(env.call(state, "t2"))  # unchanged, so not repeated


def test_a_workspace_chosen_outside_home_is_named_as_it_is(env, home, tmp_path):
    from row_bot.tools import registry

    chosen = tmp_path / "Work"
    chosen.mkdir()
    registry.set_tool_config("filesystem", "workspace_root", str(chosen))
    state = env.new_turn(None, "Make a packing list.", "t1")

    assert f"Workspace folder: {chosen}. File tools" in _block(env.call(state, "t1"))


def test_a_bound_code_folder_is_named_with_the_workspace_for_other_files(env, home, reload_for_data_dir):
    threads, storage = reload_for_data_dir(None, "row_bot.threads", "row_bot.developer.storage")
    repo = home / "code" / "app"
    repo.mkdir(parents=True)
    code_folder = storage.add_or_update_local_workspace(str(repo))
    threads.create_thread("Fix the build", thread_id=THREAD_ID, thread_type="code",
                          developer_workspace_id=code_folder.id, project_workspace_id=code_folder.id)
    state = env.new_turn(None, "Why does the build fail?", "t1")
    block = _block(env.call(state, "t1"))

    assert ("Code folder: ~/code/app (the shell and Developer tools start here). Workspace folder for other "
            "files: ~/Documents/Row-Bot.") in block
    assert "jane-account" not in block
