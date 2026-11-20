"""Compiled agent graphs are kept for the newest few configurations only (P5).

Every workflow configuration and fingerprint change made a graph of its own, a few MB with its tools'
schemas, and none was ever dropped: part of the heap a long-running app grew to. Fakes only.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.subsystem.agents.test_agent_tool_filtering import (  # noqa: F401 -- shared harness
    _lc_tool,
    _prepare_graph,
    _restore_agent_context_after_filtering_test,
)

pytestmark = pytest.mark.subsystem


def test_twenty_configurations_keep_only_the_newest_graphs_and_reuse_a_kept_one(monkeypatch) -> None:
    agent = _prepare_graph(monkeypatch)
    tool = _lc_tool("filesystem")
    monkeypatch.setattr(agent.tool_registry, "get_tool", lambda name: SimpleNamespace(
        as_langchain_tools=lambda: [tool], destructive_tool_names=set()) if name == "filesystem" else None)
    model = {"name": ""}
    monkeypatch.setattr(agent, "get_current_model", lambda: model["name"])

    def graph(index: int):
        model["name"] = f"model:test-{index}"  # each model is a configuration of its own
        return agent.get_agent_graph(["filesystem"])

    graphs = [graph(index) for index in range(20)]

    assert agent.agent_graph_cache_size() == agent.AGENT_CACHE_SIZE < 20
    newest = range(20 - agent.AGENT_CACHE_SIZE, 20)
    assert all(graph(index) is graphs[index] for index in newest), "the newest are kept and reused"
    assert graph(0) is not graphs[0], "the oldest was dropped and is built again"
    assert agent.agent_graph_cache_size() == agent.AGENT_CACHE_SIZE
