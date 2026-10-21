"""Small-context profile (WP A5, F43): a model whose window is 32K or less runs Agent mode.

A 32K-native local model was refused Agent mode: the system prompt, every tool schema and every
tool guide (the fixed envelope) left so little of the window that the first web page it read pushed
the request past usable input, with nothing older to compact. These tests drive the real tool
assembly (``get_agent_graph`` over Row-Bot's own default tools) and the real request preparation
(``_pre_model_trim``) with a faked 32K Ollama model. Nothing reaches a provider.
"""
from __future__ import annotations

import contextvars
import importlib
import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

pytestmark = pytest.mark.subsystem

OPTIONAL_DEFAULT_MODULES = ("row_bot.tools.developer_tool", "row_bot.tools.chart_tool")
MODEL = "llama3.1:8b"
SMALL = 32_768
LARGE = 200_000
# Stand-ins of today's sizes, so the envelope never depends on the OS running the test.
STATIC_SELF_KNOWLEDGE = "ABOUT YOU (SELF-KNOWLEDGE): " + "Row-Bot runs on this computer and keeps its data here. " * 95
DYNAMIC_STATE = "Current state: " + "one model provider and two channels are configured. " * 25
PLATFORM = "System: the test machine. Shell: the test shell."


def _memory(index: int) -> dict:
    return {"id": f"mem_{index}", "entity_type": "fact", "subject": f"Fact {index}",
            "description": f"The user keeps quarterly report number {index} in the shared notes folder.",
            "score": 0.9}


def _page(tokens: int) -> str:
    """Distinct page text of about *tokens* tokens (four characters each)."""
    lines, size, index = [], 0, 0
    while size < tokens * 4:
        lines.append(f"Section {index}: the quarterly figures, their sources and the notes beside them.")
        size += len(lines[-1]) + 1
        index += 1
    return "\n".join(lines)


class LocalModel:
    """One conversation with a faked local Ollama model of a given window, over Row-Bot's default tools."""

    def __init__(self, agent, monkeypatch, enabled: list[str]) -> None:
        from row_bot import threads
        from row_bot.models import context_reserves

        self.agent = agent
        self.enabled = enabled
        self.window = SMALL
        self.thread_id = f"small-profile-{uuid.uuid4().hex[:8]}"
        threads.create_thread("Small profile", thread_id=self.thread_id, seed_default_skills=False)
        self.recalls: list[dict] = []

        def policy(*_args, **_kwargs):
            reply, summary = context_reserves(self.window, "combined")
            return SimpleNamespace(
                model_ref=MODEL, provider_id="ollama", native_limit_tokens=self.window,
                requested_limit_tokens=None, effective_limit_tokens=self.window,
                usable_input_tokens=self.window - reply, compact_at_tokens=self.window - reply - summary,
                capacity_source="test_metadata", capacity_state="ready", limit_kind="combined",
            )

        self.policy = policy
        readiness = SimpleNamespace(provider_id="ollama", runtime_model=MODEL, capability_source="test",
                                    confidence="high", transport="ollama")
        monkeypatch.setattr(agent, "get_context_size", lambda *args, **kwargs: self.window)
        monkeypatch.setattr(agent, "get_context_policy", policy)
        monkeypatch.setattr(agent, "get_current_model", lambda: MODEL)
        monkeypatch.setattr(agent, "is_cloud_model", lambda model: False)
        monkeypatch.setattr(agent, "get_cloud_provider", lambda model: None)
        monkeypatch.setattr(agent, "_ensure_agent_mode_ready", lambda model_name: readiness)
        monkeypatch.setattr(agent, "_get_llm_for_reasoning_plan", lambda *args, **kwargs: object())
        monkeypatch.setattr(agent, "create_react_agent", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(agent, "_has_code_folder", lambda: False)
        # Row-Bot's own tools only: no plugin or channel tools another test may have left registered.
        monkeypatch.setattr("row_bot.plugins.registry.get_langchain_tools",
                            lambda allow_names=None, refresh_mcp=True: [])
        monkeypatch.setattr("row_bot.plugins.registry.get_destructive_names", lambda allow_names=None: set())
        monkeypatch.setattr("row_bot.plugins.registry.get_skills_prompt", lambda *args, **kwargs: "")
        monkeypatch.setattr("row_bot.channels.registry.running_channels", lambda: [])
        monkeypatch.setattr("row_bot.prompts.get_platform_context", lambda: PLATFORM)
        monkeypatch.setattr("row_bot.self_knowledge.build_static_self_knowledge_block", lambda: STATIC_SELF_KNOWLEDGE)
        monkeypatch.setattr("row_bot.self_knowledge.build_dynamic_self_knowledge_block", lambda: DYNAMIC_STATE)
        monkeypatch.setattr("row_bot.memory_policy.build_auto_recall", lambda *args, **kwargs: SimpleNamespace(
            allowed=True, reason="selected", candidates_seen=5, selected=[_memory(i) for i in range(5)], trace={}))
        monkeypatch.setattr("row_bot.memory_policy.touch_selected_memories",
                            lambda decision: self.recalls.append(list(decision.selected)))
        monkeypatch.setattr("row_bot.memory_policy.record_recall_trace", lambda decision, **kwargs: None)

    def _activate(self) -> None:
        self.agent._set_active_runtime_context(
            thread_id=self.thread_id, runtime_surface="normal_chat", requested_runtime_mode="agent",
            selected_runtime_mode="agent", approval_mode="approve", generation_id=f"{self.thread_id}-turn",
            model_override=MODEL, enabled_tool_names=tuple(self.enabled),
        )

    def graph(self):
        """The real tool assembly for this window, run in its own context."""
        def build():
            self._activate()
            graph = self.agent.get_agent_graph(list(self.enabled))
            return graph, dict(self.agent._current_on_demand_tools_var.get())
        return contextvars.copy_context().run(build)

    def request(self, messages: list) -> SimpleNamespace:
        """One Agent model call's preparation: the bound tools, then the pre-model hook."""
        def prepare():
            self._activate()
            graph = self.agent.get_agent_graph(list(self.enabled))
            state = {"messages": list(messages),
                     "execution_budget": self.agent.new_execution_budget(f"{self.thread_id}-turn")}
            update = self.agent._pre_model_trim(state)
            prepared = list(update["llm_input_messages"])
            canonical = tuple(self.agent._current_canonical_tools_var.get())
            return SimpleNamespace(
                graph=graph, messages=prepared, tools=canonical,
                estimate=self.agent._count_prepared_tokens(prepared, canonical),
                system="\n".join(str(m.content) for m in prepared if isinstance(m, SystemMessage)),
                usable=self.policy().usable_input_tokens,
            )
        return contextvars.copy_context().run(prepare)


@pytest.fixture
def local_model(tmp_path, monkeypatch, reload_for_data_dir):
    agent, _activation, skills = reload_for_data_dir(
        tmp_path / "data", "row_bot.agent", "row_bot.skills_activation", "row_bot.skills")
    import row_bot.tools as tool_package
    from row_bot.tools import registry

    for name in OPTIONAL_DEFAULT_MODULES:
        importlib.import_module(name)
    registry.set_tool_config("filesystem", "workspace_root", str(tmp_path / "workspace"))
    skills.load_skills()
    # Row-Bot's own default tools, as a new installation enables them (accounts not connected).
    modules = set(tool_package._CORE_TOOL_MODULES) | set(OPTIONAL_DEFAULT_MODULES)
    enabled = [owner.name for owner in registry.get_all_tools()
               if owner.enabled_by_default and type(owner).__module__ in modules]
    agent.clear_agent_cache()
    yield LocalModel(agent, monkeypatch, enabled)
    agent.clear_agent_cache()


def _read_a_web_page(tokens: int) -> list:
    return [
        HumanMessage(content="Summarise https://example.test/q3-report for me.", id="person-1"),
        AIMessage(content="", id="ai-1", tool_calls=[{"id": "call_1", "name": "read_url", "type": "tool_call",
                                                     "args": {"url": "https://example.test/q3-report"}}]),
        ToolMessage(content=_page(tokens), name="read_url", tool_call_id="call_1", id="tool-1"),
    ]


def test_a_32k_local_model_gets_an_agent_request_under_its_usable_input(local_model):
    """The acceptance case: the first page read used to refuse the turn on a 32K-native model."""
    page = _read_a_web_page(6_000)

    prepared = local_model.request(page)

    assert prepared.estimate < prepared.usable
    assert "the quarterly figures, their sources" in json.dumps([str(m.content) for m in prepared.messages])
    # The envelope (system prompt and bound schemas) leaves most of the window to the conversation.
    envelope = local_model.agent._count_prepared_tokens(
        [m for m in prepared.messages if isinstance(m, SystemMessage)], prepared.tools)
    assert envelope < SMALL // 3


def test_a_small_window_binds_core_tools_and_offers_the_rest_through_tool_search(local_model):
    graph, on_demand = local_model.graph()
    bound = graph.tools.tools_by_name

    core = {"workspace_read_file", "run_command", "read_url", "duckduckgo", "save_memory", "search_memory",
            "task_create", "task_list"}
    bridges = {"tool_search", "tool_invoke", "skill_search", "skill_load", "read_tool_output"}
    assert core | bridges <= set(bound)
    for deferred in ("delegate_work", "row_bot_status", "create_chart", "tracker_log", "wiki_read", "calculate"):
        assert deferred not in bound
        assert deferred in on_demand  # offered on demand, called by its own name or through tool_invoke
    assert not any(name.startswith("developer_") for name in set(bound) | set(on_demand))  # no code folder
    assert "delegate_work" in bound["tool_search"].description  # the catalog names what is offered


def test_a_small_windows_tool_search_finds_tools_by_their_word_forms_and_group_names(local_model):
    """L2v: small models searched for "calculator" and got nothing back, so they never reached `calculate`."""
    graph, _on_demand = local_model.graph()
    search = graph.tools.tools_by_name["tool_search"]

    def first(query: str) -> str | None:
        results = json.loads(search.invoke({"query": query}))["results"]
        return results[0]["name"] if results else None

    assert first("calculator") == "calculate"
    assert first("transcripts") == "youtube_transcript"
    assert first("vision") == "analyze_image"  # its group's name in Settings ("👁️ Vision")
    assert first("file delivery") == "send_file"  # its group ("file_delivery")
    assert first("Mezze House") is None


def test_a_small_window_sends_guides_as_an_index_and_task_and_memory_guidance_with_their_tools(local_model):
    prepared = local_model.request([HumanMessage(content="hello", id="person-1")])
    root = str(next(m for m in prepared.messages if isinstance(m, SystemMessage)).content)

    assert "TASKS & REMINDERS:" not in root and "MEMORY GUIDELINES:" not in root  # tool-gated guides now
    assert "TASKS & REMINDERS:" in prepared.system and "MEMORY GUIDELINES:" in prepared.system
    assert "TOOL GUIDES (index)" in prepared.system and "skill_load(name)" in prepared.system
    assert "- filesystem_guide (filesystem):" in prepared.system
    assert "# Filesystem" not in prepared.system  # one line per guide, not its text
    assert STATIC_SELF_KNOWLEDGE[:40] not in prepared.system
    turn_context = str(prepared.messages[-1].content)
    assert "Current state:" not in turn_context  # Row-Bot's live state: row_bot_status answers it
    assert local_model.recalls == [[_memory(i) for i in range(3)]]  # at most three memories, all shown
    assert "mem_3" not in turn_context and "mem_2" in turn_context


def test_a_guide_left_as_an_index_line_reads_in_full_through_skill_load(local_model):
    graph, _on_demand = local_model.graph()
    bound = graph.tools.tools_by_name

    assert "filesystem_guide" in bound["skill_search"].description
    loaded = json.loads(bound["skill_load"].invoke(
        {"name": "filesystem_guide"}, config={"configurable": {"thread_id": local_model.thread_id}}))
    assert loaded["ok"] is True and loaded["instructions"].strip()


def test_a_window_above_32k_keeps_todays_prompt(local_model):
    local_model.window = LARGE
    prepared = local_model.request([HumanMessage(content="hello", id="person-1")])
    root = str(next(m for m in prepared.messages if isinstance(m, SystemMessage)).content)
    bound = prepared.graph.tools.tools_by_name

    from row_bot.prompts import _AGENT_GUIDELINES

    assert root.endswith(_AGENT_GUIDELINES)  # the whole root prompt, TASKS and MEMORY included
    assert STATIC_SELF_KNOWLEDGE[:40] in prepared.system
    assert "TOOL GUIDES (index)" not in prepared.system
    assert {"delegate_work", "row_bot_status", "create_chart", "tracker_log"} <= set(bound)
    assert "tool_search" not in bound  # nothing is deferred on a large window
    assert "Current state:" in str(prepared.messages[-1].content)
