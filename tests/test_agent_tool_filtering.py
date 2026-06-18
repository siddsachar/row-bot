from __future__ import annotations

from types import SimpleNamespace

from langchain_core.tools import StructuredTool


def _lc_tool(name: str) -> StructuredTool:
    def _run(query: str = "") -> str:
        return f"{name}:{query}"

    return StructuredTool.from_function(
        func=_run,
        name=name,
        description=f"{name} test tool",
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
