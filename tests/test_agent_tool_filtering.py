from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.providers.models import TransportMode
from row_bot.providers.tool_schema import ToolSchemaCompatibilityError


def _lc_tool(name: str) -> StructuredTool:
    def _run(query: str = "") -> str:
        return f"{name}:{query}"

    return StructuredTool.from_function(
        func=_run,
        name=name,
        description=f"{name} test tool",
    )


def _malformed_array_tool(name: str) -> StructuredTool:
    class _Args(BaseModel):
        values: list[Any] = Field(default_factory=list)

    def _run(values: list[Any] | None = None) -> str:
        return f"{name}:{values or []}"

    return StructuredTool.from_function(
        func=_run,
        name=name,
        description=f"{name} malformed schema test tool",
        args_schema=_Args,
    )


def _prepare_graph(monkeypatch):
    import row_bot.agent as agent

    agent.clear_agent_cache()
    monkeypatch.setattr(agent, "get_current_model", lambda: "model:test")
    monkeypatch.setattr(agent, "get_llm", lambda: object())
    monkeypatch.setattr(agent, "get_context_size", lambda model_name=None: 32_768)
    monkeypatch.setattr(agent, "get_agent_system_prompt", lambda: "system")
    monkeypatch.setattr(
        agent,
        "_ensure_agent_mode_ready",
        lambda model_name: SimpleNamespace(
            provider_id="test",
            runtime_model="test",
            capability_source="test",
            confidence="high",
        ),
    )
    monkeypatch.setattr(agent, "create_react_agent", lambda **kwargs: SimpleNamespace(**kwargs))
    return agent


def test_get_agent_graph_without_allowlist_keeps_plugin_and_channel_tools(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    core_tool = _lc_tool("filesystem")
    plugin_tool = _lc_tool("plugin_lookup")
    channel_tool = _lc_tool("channel_send")
    plugin_allow_args = []

    monkeypatch.setattr(
        agent.tool_registry,
        "get_tool",
        lambda name: SimpleNamespace(
            as_langchain_tools=lambda: [core_tool],
            destructive_tool_names=set(),
        ) if name == "filesystem" else None,
    )

    from row_bot.plugins import registry as plugin_registry
    from row_bot.channels import registry as channel_registry
    from row_bot.channels import tool_factory

    def fake_plugin_tools(allow_names=None):
        plugin_allow_args.append(allow_names)
        return [plugin_tool]

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", fake_plugin_tools)
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())
    monkeypatch.setattr(channel_registry, "running_channels", lambda: [SimpleNamespace(name="sms")])
    monkeypatch.setattr(tool_factory, "create_channel_tools", lambda channel: [channel_tool])

    graph = agent.get_agent_graph(["filesystem"])

    assert [tool.name for tool in graph.tools] == ["filesystem", "plugin_lookup", "channel_send"]
    assert plugin_allow_args == [None]


def test_get_agent_graph_with_allowlist_filters_plugin_and_channel_tools(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    core_tool = _lc_tool("filesystem")
    plugin_tool = _lc_tool("plugin_lookup")
    plugin_allow_args = []

    monkeypatch.setattr(
        agent.tool_registry,
        "get_tool",
        lambda name: SimpleNamespace(
            as_langchain_tools=lambda: [core_tool],
            destructive_tool_names=set(),
        ) if name == "filesystem" else None,
    )

    from row_bot.plugins import registry as plugin_registry
    from row_bot.channels import registry as channel_registry
    from row_bot.channels import tool_factory

    def fake_plugin_tools(allow_names=None):
        allow = set(allow_names or [])
        plugin_allow_args.append(allow)
        return [plugin_tool] if "plugin_lookup" in allow else []

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", fake_plugin_tools)
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())
    monkeypatch.setattr(channel_registry, "running_channels", lambda: (_ for _ in ()).throw(AssertionError("channels should not bind")))
    monkeypatch.setattr(tool_factory, "create_channel_tools", lambda channel: (_ for _ in ()).throw(AssertionError("channels should not bind")))

    graph = agent.get_agent_graph(
        ["filesystem"],
        tool_allowlist=["filesystem", "plugin_lookup"],
    )

    assert [tool.name for tool in graph.tools] == ["filesystem", "plugin_lookup"]
    assert plugin_allow_args == [{"filesystem", "plugin_lookup"}]


def test_get_agent_graph_with_allowlist_filters_individual_mcp_tools(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    mcp_tool = _lc_tool("mcp_local_echo")
    allow_args = []

    monkeypatch.setattr(
        agent.tool_registry,
        "get_tool",
        lambda name: SimpleNamespace(
            as_langchain_tools=lambda: [_lc_tool("mcp_fallback")],
            destructive_tool_names={"mcp_fallback"},
        ) if name == "mcp" else None,
    )

    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import registry as plugin_registry

    def fake_mcp_tools(allow_names=None):
        allow = set(allow_names or [])
        allow_args.append(allow)
        return [mcp_tool] if "mcp_local_echo" in allow else []

    monkeypatch.setattr(mcp_runtime, "get_langchain_tools", fake_mcp_tools)
    monkeypatch.setattr(mcp_runtime, "get_destructive_tool_names", lambda allow_names=None: set())
    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())

    graph = agent.get_agent_graph(["mcp"], tool_allowlist=["mcp_local_echo"])

    assert [tool.name for tool in graph.tools] == ["mcp_local_echo"]
    assert allow_args == [{"mcp_local_echo"}]


def test_get_agent_graph_memory_allowlist_exposes_normal_memory_tools(monkeypatch):
    agent = _prepare_graph(monkeypatch)

    from row_bot.plugins import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())

    graph = agent.get_agent_graph(["memory"], tool_allowlist=["memory"])
    names = {tool.name for tool in graph.tools}

    assert {
        "save_memory",
        "search_memory",
        "list_memories",
        "update_memory",
        "delete_memory",
        "link_memories",
        "explore_connections",
    } <= names


def test_get_agent_graph_parent_mcp_allowlist_includes_all_mcp_tools(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    mcp_tools = [_lc_tool("mcp_local_echo"), _lc_tool("mcp_other_list")]
    allow_args = []

    monkeypatch.setattr(
        agent.tool_registry,
        "get_tool",
        lambda name: SimpleNamespace(
            as_langchain_tools=lambda: [_lc_tool("mcp_fallback")],
            destructive_tool_names=set(),
        ) if name == "mcp" else None,
    )

    from row_bot.mcp_client import runtime as mcp_runtime
    from row_bot.plugins import registry as plugin_registry

    def fake_mcp_tools(allow_names=None):
        allow_args.append(set(allow_names or []))
        return mcp_tools

    monkeypatch.setattr(mcp_runtime, "get_langchain_tools", fake_mcp_tools)
    monkeypatch.setattr(mcp_runtime, "get_destructive_tool_names", lambda allow_names=None: set())
    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())

    graph = agent.get_agent_graph(["mcp"], tool_allowlist=["mcp"])

    assert [tool.name for tool in graph.tools] == ["mcp_local_echo", "mcp_other_list"]
    assert allow_args == [{"mcp"}]


def test_get_agent_graph_cache_key_includes_allowlist(monkeypatch):
    agent = _prepare_graph(monkeypatch)

    monkeypatch.setattr(
        agent.tool_registry,
        "get_tool",
        lambda name: SimpleNamespace(
            as_langchain_tools=lambda: [_lc_tool(name)],
            destructive_tool_names=set(),
        ),
    )

    from row_bot.plugins import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())

    first = agent.get_agent_graph(["filesystem", "row_bot_status"], tool_allowlist=["filesystem"])
    second = agent.get_agent_graph(["filesystem", "row_bot_status"], tool_allowlist=["row_bot_status"])
    repeated = agent.get_agent_graph(["filesystem", "row_bot_status"], tool_allowlist=["filesystem"])

    assert first is repeated
    assert first is not second


def test_gemini_final_boundary_isolates_malformed_mcp_plugin_and_channel_tools(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    core_tool = _lc_tool("filesystem")
    malformed_mcp = _malformed_array_tool("mcp_bad_array")
    malformed_plugin = _malformed_array_tool("plugin_bad_array")
    malformed_channel = _malformed_array_tool("channel_bad_array")

    monkeypatch.setattr(
        agent,
        "_ensure_agent_mode_ready",
        lambda model_name: SimpleNamespace(
            provider_id="google",
            runtime_model="gemini-test",
            capability_source="test",
            confidence="high",
            transport=TransportMode.GOOGLE_GENAI,
        ),
    )

    def fake_core_tool(name):
        if name == "filesystem":
            return SimpleNamespace(as_langchain_tools=lambda: [core_tool], destructive_tool_names=set())
        if name == "mcp":
            return SimpleNamespace(as_langchain_tools=lambda: [malformed_mcp], destructive_tool_names=set())
        return None

    monkeypatch.setattr(agent.tool_registry, "get_tool", fake_core_tool)

    from row_bot.plugins import registry as plugin_registry
    from row_bot.channels import registry as channel_registry
    from row_bot.channels import tool_factory

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [malformed_plugin])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())
    monkeypatch.setattr(channel_registry, "running_channels", lambda: [SimpleNamespace(name="sms")])
    monkeypatch.setattr(tool_factory, "create_channel_tools", lambda channel: [malformed_channel])
    monkeypatch.setattr(tool_factory, "destructive_channel_tool_names", lambda channel: set())

    graph = agent.get_agent_graph(["filesystem", "mcp"])

    assert [tool.name for tool in graph.tools] == ["filesystem"]


def test_gemini_explicit_allowlist_fails_for_malformed_tool(monkeypatch):
    agent = _prepare_graph(monkeypatch)
    malformed = _malformed_array_tool("malformed")
    monkeypatch.setattr(
        agent,
        "_ensure_agent_mode_ready",
        lambda model_name: SimpleNamespace(
            provider_id="google",
            runtime_model="gemini-test",
            capability_source="test",
            confidence="high",
            transport=TransportMode.GOOGLE_GENAI,
        ),
    )
    monkeypatch.setattr(
        agent.tool_registry,
        "get_tool",
        lambda name: SimpleNamespace(
            as_langchain_tools=lambda: [malformed],
            destructive_tool_names=set(),
        ),
    )

    from row_bot.plugins import registry as plugin_registry

    monkeypatch.setattr(plugin_registry, "get_langchain_tools", lambda allow_names=None: [])
    monkeypatch.setattr(plugin_registry, "get_destructive_names", lambda allow_names=None: set())

    with pytest.raises(ToolSchemaCompatibilityError, match=r"malformed.*values\.items"):
        agent.get_agent_graph(["malformed"], tool_allowlist=["malformed"])
