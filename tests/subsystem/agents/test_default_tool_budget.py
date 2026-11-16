"""The tools a plain chat sends with every model call stay within a budget (B1).

Every bound tool's schema goes with every model call: a local model reads all of it before the first
word and a cloud call pays for it. These tests build Row-Bot's real tool assembly (``get_agent_graph``
over its own default tools, the browser and chart modules included, and its bundled skills) for a plain
chat: a 200K cloud model, Ask, no code folder, no apps. Nothing reaches a provider.

The tools offered on demand stay reachable: by name, or through tool_search and tool_invoke, with the
same approvals. Raising the budget is a deliberate change: edit ``BUDGET`` below, so a reviewer sees it.
"""
from __future__ import annotations

import contextvars
import importlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from tests.helpers.snapshots import assert_matches_snapshot

pytestmark = pytest.mark.subsystem

BUDGET = 32_000  # provider-facing characters: about 8,000 tokens at four characters a token
SNAPSHOT = Path(__file__).resolve().parents[2] / "snapshots" / "tools" / "plain_chat_tool_set.txt"
OPTIONAL_DEFAULT_MODULES = ("row_bot.tools.developer_tool", "row_bot.tools.chart_tool", "row_bot.tools.browser_tool")
MODEL = "anthropic:claude-test"


def _chars(tool) -> int:
    return len(json.dumps(convert_to_openai_tool(tool), ensure_ascii=False, sort_keys=True))


class PlainChat:
    """One conversation on a 200K cloud model over Row-Bot's default tools, under an approval level."""

    def __init__(self, agent, monkeypatch, enabled: list[str]) -> None:
        self.agent = agent
        self.enabled = enabled
        self.approval = "approve"
        self.allowlist: tuple[str, ...] | None = None
        self.thread_id = f"plain-chat-{uuid.uuid4().hex[:8]}"
        self.asked: list[str] = []
        readiness = SimpleNamespace(provider_id="anthropic", runtime_model="claude-test", capability_source="test",
                                    confidence="high", transport="anthropic")
        monkeypatch.setattr(agent, "get_context_size", lambda *args, **kwargs: 200_000)
        monkeypatch.setattr(agent, "get_current_model", lambda: MODEL)
        monkeypatch.setattr(agent, "is_cloud_model", lambda model: True)
        monkeypatch.setattr(agent, "get_cloud_provider", lambda model: "anthropic")
        monkeypatch.setattr(agent, "_ensure_agent_mode_ready", lambda model_name: readiness)
        monkeypatch.setattr(agent, "_get_llm_for_reasoning_plan", lambda *args, **kwargs: object())
        monkeypatch.setattr(agent, "create_react_agent", lambda **kwargs: SimpleNamespace(**kwargs))
        monkeypatch.setattr(agent, "_has_code_folder", lambda: False)
        # Row-Bot's own tools only: no plugin, channel or app tools another test may have left registered.
        monkeypatch.setattr("row_bot.plugins.registry.get_langchain_tools",
                            lambda allow_names=None, refresh_mcp=True: [])
        monkeypatch.setattr("row_bot.plugins.registry.get_destructive_names", lambda allow_names=None: set())
        monkeypatch.setattr("row_bot.channels.registry.running_channels", lambda: [])
        monkeypatch.setattr("row_bot.mcp_client.runtime.get_langchain_tools",
                            lambda allow_names=None, refresh=True: [])
        # The approval card: recorded and answered yes, as the person would.
        monkeypatch.setattr(agent, "interrupt", lambda payload: self.asked.append(payload["tool"]) or True)

    def run(self, work):
        """*work(graph, on_demand)* inside this chat's turn, on its own context."""
        def turn():
            self.agent._set_active_runtime_context(
                thread_id=self.thread_id, runtime_surface="normal_chat", requested_runtime_mode="agent",
                selected_runtime_mode="agent", approval_mode=self.approval, generation_id=f"{self.thread_id}-turn",
                model_override=MODEL, enabled_tool_names=tuple(self.enabled), tool_allowlist=self.allowlist,
            )
            graph = self.agent.get_agent_graph(list(self.enabled), tool_allowlist=self.allowlist)
            return work(graph, dict(self.agent._current_on_demand_tools_var.get()))
        return contextvars.copy_context().run(turn)

    def graph(self):
        return self.run(lambda graph, on_demand: (graph.tools.tools_by_name, on_demand))


@pytest.fixture
def plain_chat(tmp_path, monkeypatch, reload_for_data_dir):
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
    yield PlainChat(agent, monkeypatch, enabled)
    agent.clear_agent_cache()


def test_a_plain_chats_tool_set_is_on_record_and_within_the_budget(plain_chat):
    bound, on_demand = plain_chat.graph()
    sizes = {name: _chars(tool) for name, tool in sorted(bound.items())}
    total = sum(sizes.values())
    lines = [f"{name}: {chars}" for name, chars in sizes.items()]
    lines.append(f"TOTAL ({len(sizes)} tools): {total}")
    lines.append(f"ON DEMAND ({len(on_demand)} tools): {', '.join(sorted(on_demand))}")

    assert_matches_snapshot(SNAPSHOT, "\n".join(lines) + "\n")
    assert total <= BUDGET, f"The plain-chat tool block is {total} characters, over its {BUDGET} budget."


def test_every_tool_offered_on_demand_is_named_once_in_tool_searchs_catalog(plain_chat):
    bound, on_demand = plain_chat.graph()
    catalog = bound["tool_search"].description.split("\nRow-Bot's tools, by group:\n", 1)[1]
    named = [name.strip() for line in catalog.splitlines() for name in line.split(": ", 1)[-1].split(",")]

    assert sorted(named) == sorted(on_demand)
    assert not set(on_demand) & set(bound)
    assert plain_chat.graph()[0]["tool_search"].description == bound["tool_search"].description  # turn to turn


def test_a_tool_offered_on_demand_runs_when_called_by_its_own_name(plain_chat):
    """F11: a deferred tool (speak, in the sound group) is reached by its name without tool_invoke."""
    seen: list[str] = []

    def call_by_name(graph, on_demand):
        assert "speak" in on_demand and "speak" not in graph.tools.tools_by_name
        request = SimpleNamespace(
            tool=None,
            tool_call={"name": "speak", "args": {"text": "Hello"}, "id": "call-1"},
            override=lambda **change: SimpleNamespace(tool=change["tool"], tool_call=request.tool_call),
        )
        plain_chat.agent._wrap_platform_tool_call(request, lambda req: seen.append(req.tool.name) or "ran")

    plain_chat.run(call_by_name)

    assert seen == ["speak"]


def test_a_destructive_tool_found_by_search_asks_on_the_same_card(plain_chat):
    """Security: deferring a tool never removes its approval. task_delete, found with tool_search and run
    through tool_invoke, asks the person exactly as it did when it was bound."""
    def search_and_invoke(graph, on_demand):
        bound = graph.tools.tools_by_name
        found = json.loads(bound["tool_search"].invoke({"query": "delete a task", "limit": 5}))["results"]
        assert "task_delete" in [result["name"] for result in found]
        return bound["tool_invoke"].invoke({"name": "task_delete", "arguments": {"task_id": "missing"}})

    result = plain_chat.run(search_and_invoke)

    assert plain_chat.asked == ["task_delete"]  # the card came before the tool ran
    assert "not found" in str(result)


def test_under_block_a_destructive_tool_offered_on_demand_is_refused(plain_chat):
    plain_chat.approval = "block"

    def search_and_invoke(graph, on_demand):
        bound = graph.tools.tools_by_name
        assert "task_delete" not in on_demand and "task_delete" not in bound
        found = json.loads(bound["tool_search"].invoke({"query": "delete a task", "limit": 5}))["results"]
        assert "task_delete" not in [result["name"] for result in found]
        return json.loads(bound["tool_invoke"].invoke({"name": "task_delete", "arguments": {"task_id": "x"}}))

    refused = plain_chat.run(search_and_invoke)

    assert refused["ok"] is False and refused["error"]["code"] == "unknown_tool"
    assert plain_chat.asked == []


def test_a_profile_with_its_own_tool_list_binds_exactly_that_list(plain_chat):
    plain_chat.allowlist = ("sound", "task")
    bound, on_demand = plain_chat.graph()

    assert {"speak", "make_music", "transcribe", "task_create", "task_delete", "task_run_now"} <= set(bound)
    assert not on_demand
