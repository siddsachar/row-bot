"""Real LangChain dispatch with deterministic effect and approval sentinels."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "isolated"))
    from row_bot import agent
    from row_bot import agent_budget

    # The graph repeat guard is owned by a separate module. Give each synthetic
    # logical turn its own real budget rather than inheriting another test's.
    budget_token = agent_budget._ACTIVE_BUDGET_ID.set("")
    budget = agent_budget.new_execution_budget("synthetic-profile-test")
    agent_budget.activate_execution_budget(budget)

    previous = [
        (variable, variable.set(variable.get(None)))
        for variable in {
            value for value in vars(agent).values() if isinstance(value, ContextVar)
        }
    ]
    variables = [
        agent._current_agent_profile_snapshot_var,
        agent._current_agent_profile_id_var,
        agent._current_agent_profile_frozen_var,
        agent._current_thread_id_var,
        agent._current_tool_allowlist_var,
        agent._current_tool_allowlist_active_var,
    ]
    values = [
        {
            "id": "synthetic",
            "enabled": True,
            "tool_policy_json": {"capability": "read_only"},
        },
        "synthetic",
        True,
        "",
        (),
        False,
    ]
    tokens = [variable.set(value) for variable, value in zip(variables, values)]
    yield agent
    for variable, token in reversed(list(zip(variables, tokens))):
        variable.reset(token)
    for variable, token in reversed(previous):
        variable.reset(token)
    agent_budget.clear_execution_budget_runtime(budget["budget_id"])
    agent_budget._ACTIVE_BUDGET_ID.reset(budget_token)


@pytest.mark.parametrize(
    "name,parent",
    [
        ("developer_run_command", "developer"),
        ("developer_commit_changes", "developer"),
        ("developer_push_current_branch", "developer"),
        ("workspace_delete_file", "filesystem"),
        ("save_memory", "memory"),
        ("row_bot_update_setting", "row_bot_status"),
        ("browser_click", "browser"),
        ("unknown_action", "developer"),
    ],
)
def test_toolnode_blocks_effect_and_unknown_before_the_original_callback(
    runtime, name, parent
):
    calls = []

    def effect(command: str = "") -> str:
        calls.append(command)
        return "effect ran"

    tool = StructuredTool.from_function(
        effect, name=name, description="Synthetic effect"
    )
    bound = runtime._bind_profile_tool(tool, source="core", parent=parent)
    node = ToolNode([bound])
    result = node.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "call", "name": name, "args": {"command": "synthetic"}}
                    ],
                )
            ]
        },
        {"configurable": {"__pregel_runtime": Runtime()}},
    )
    assert "read-only" in result["messages"][0].content
    assert calls == []


@pytest.mark.parametrize(
    "command",
    [
        "pip install example",
        "curl https://example.invalid",
        "rm data.txt",
        "git commit -m synthetic",
        "git push",
        "gh pr create",
        "git branch new",
        "date -s now",
        "find . -delete",
        "python -c pass",
    ],
)
def test_shell_prefix_or_explicit_allowlist_never_grants_read_only_execution(
    runtime, command
):
    runtime._current_tool_allowlist_active_var.set(True)
    runtime._current_tool_allowlist_var.set(("shell",))
    calls = []
    tool = StructuredTool.from_function(
        lambda command: calls.append(command) or "executed",
        name="run_command",
        description="Synthetic shell",
    )
    assert "read-only" in runtime._bind_profile_tool(
        tool, source="core", parent="shell"
    ).invoke({"command": command})
    assert calls == []


@pytest.mark.parametrize(
    "name,parent",
    [
        ("workspace_read_file", "filesystem"),
        ("search_memory", "memory"),
        ("developer_read_file", "developer"),
        ("row_bot_status", "row_bot_status"),
        ("browser_snapshot", "browser"),
    ],
)
def test_known_read_halves_remain_usable(runtime, name, parent):
    calls = []
    tool = StructuredTool.from_function(
        lambda query="": calls.append(query) or "read result",
        name=name,
        description="Synthetic read",
    )
    assert (
        runtime._bind_profile_tool(tool, source="core", parent=parent).invoke(
            {"query": "synthetic"}
        )
        == "read result"
    )
    assert calls == ["synthetic"]


def test_composite_action_argument_is_checked_for_positional_and_keyword_calls(runtime):
    calls = []

    def tabs(action: str = "list") -> str:
        calls.append(action)
        return "read tabs"

    bound = runtime._bind_profile_tool(
        StructuredTool.from_function(tabs, name="browser_tab", description="Tabs"),
        source="core",
        parent="browser",
    )
    assert bound.func("list") == "read tabs"
    assert "read-only" in bound.func("new")
    assert "read-only" in bound.invoke({"action": "close"})
    assert calls == ["list"]


def test_async_and_discovery_targets_cannot_bypass_profile_guard(runtime):
    from row_bot.tools.discovery import ExternalToolRecord, build_tool_discovery_tools

    calls = []

    async def effect(action: str) -> str:
        calls.append(action)
        return "executed"

    tool = StructuredTool.from_function(
        coroutine=effect, name="plugin_action", description="Synthetic async"
    )
    bound = runtime._bind_profile_tool(tool, source="plugin:demo", parent="demo")
    assert "read-only" in asyncio.run(bound.ainvoke({"action": "install"}))
    _search, invoke = build_tool_discovery_tools(
        [ExternalToolRecord.from_tool(bound, source="plugin:demo", parent="demo")],
        context_tokens=8192,
    )
    assert "read-only" in asyncio.run(
        invoke.ainvoke({"name": bound.name, "arguments": {"action": "send"}})
    )
    assert calls == []


def test_frozen_profile_and_runtime_allowlist_are_checked_on_each_retained_call(
    runtime,
):
    calls = []
    tool = StructuredTool.from_function(
        lambda query: calls.append(query) or "read",
        name="workspace_read_file",
        description="Synthetic",
    )
    bound = runtime._bind_profile_tool(tool, source="core", parent="filesystem")
    assert bound.invoke({"query": "first"}) == "read"
    runtime._current_tool_allowlist_active_var.set(True)
    runtime._current_tool_allowlist_var.set(())
    assert "allowlist" in bound.invoke({"query": "revoked"})
    runtime._current_tool_allowlist_active_var.set(False)
    runtime._current_agent_profile_snapshot_var.set(
        {"id": "synthetic", "enabled": False}
    )
    assert "unavailable" in bound.invoke({"query": "disabled"})
    assert calls == ["first"]


def test_external_name_cannot_claim_builtin_read_provenance(runtime):
    calls = []
    tool = StructuredTool.from_function(
        lambda: calls.append(1) or "foreign",
        name="workspace_read_file",
        description="Synthetic spoof",
    )
    assert "read-only" in runtime._bind_profile_tool(
        tool, source="plugin:spoof", parent="filesystem"
    ).invoke({})
    assert calls == []


def test_write_capable_and_unprofiled_calls_preserve_original_approval_callback_and_metadata(
    runtime,
):
    calls = []

    def gated(action: str) -> str:
        calls.append(action)
        return "Existing approval denied"

    tool = StructuredTool.from_function(
        gated,
        name="developer_run_command",
        description="Original schema",
        tags=["retained"],
        metadata={"synthetic": True},
    )
    bound = runtime._bind_profile_tool(tool, source="core", parent="developer")
    assert (
        bound.args_schema is tool.args_schema
        and bound.tags == tool.tags
        and bound.metadata == tool.metadata
    )
    runtime._current_agent_profile_snapshot_var.set(
        {"tool_policy_json": {"capability": "write_capable"}}
    )
    assert bound.invoke({"action": "run"}) == "Existing approval denied"
    runtime._current_agent_profile_snapshot_var.set({})
    runtime._current_agent_profile_id_var.set("")
    assert bound.invoke({"action": "run"}) == "Existing approval denied"
    assert calls == ["run", "run"]


def test_readonly_child_and_workflow_explicit_allowlists_do_not_widen_parent_filter(
    runtime,
):
    from row_bot import agent_runner, tasks

    profile = {
        "tool_policy_json": {
            "capability": "read_only",
            "allow_tools": ["image_gen", "gmail", "filesystem"],
        }
    }
    for filter_tools in (
        agent_runner._filter_child_tools,
        tasks._filter_workflow_tools_for_profile,
    ):
        assert filter_tools(["image_gen", "gmail", "filesystem"], profile) == [
            "filesystem"
        ]


def test_existing_mcp_effect_owner_must_confirm_read_only(runtime, monkeypatch):
    from row_bot.mcp_client import runtime as mcp

    info = SimpleNamespace(
        prefixed_name="mcp_demo_query", destructive=False, effect="read_only"
    )
    monkeypatch.setattr(mcp, "_catalog", {"demo": {"query": info}})
    calls = []
    tool = StructuredTool.from_function(
        lambda: calls.append(1) or "read",
        name=info.prefixed_name,
        description="MCP read",
    )
    bound = runtime._bind_profile_tool(tool, source="mcp", parent="mcp")
    assert bound.invoke({}) == "read"
    info.effect = "interaction"
    assert "read-only" in bound.invoke({})
    assert calls == [1]


@pytest.mark.parametrize(
    "external,asynchronous", [(False, False), (True, False), (True, True)]
)
def test_real_graph_guard_precedes_approval_and_preserves_write_profile_gate(
    runtime, monkeypatch, external, asynchronous
):
    from tests.test_agent_tool_filtering import _prepare_graph

    agent = _prepare_graph(monkeypatch)
    agent._approval_mode_var.set("approve")
    calls, approvals = [], []

    def effect(command: str) -> str:
        calls.append(command)
        return "executed"

    async def async_effect(command: str) -> str:
        return effect(command)

    original = StructuredTool.from_function(
        func=effect,
        coroutine=async_effect if asynchronous else None,
        name="developer_run_command",
        description="Synthetic graph effect",
    )
    entry = {
        "tool": original,
        "source": "plugin:synthetic" if external else "core",
        "parent": "synthetic" if external else "developer",
    }
    monkeypatch.setattr(
        agent,
        "_collect_agent_tool_candidates",
        lambda *_: (
            [] if external else [dict(entry)],
            [dict(entry)] if external else [],
            {original.name},
        ),
    )
    monkeypatch.setattr(
        agent.tool_registry, "get_external_tool_loading_mode", lambda: "auto"
    )
    monkeypatch.setattr(
        agent, "interrupt", lambda request: approvals.append(request) or False
    )
    graph = agent.get_agent_graph(["synthetic"])
    target = graph.tools.tools_by_name["tool_invoke" if external else original.name]
    arguments = (
        {"name": original.name, "arguments": {"command": "git push"}}
        if external
        else {"command": "git push"}
    )
    invoke = (
        (lambda: asyncio.run(target.ainvoke(arguments)))
        if asynchronous
        else lambda: target.invoke(arguments)
    )
    assert "read-only" in invoke()
    assert calls == approvals == []
    agent._current_agent_profile_snapshot_var.set(
        {"tool_policy_json": {"capability": "write_capable"}}
    )
    assert "cancelled" in invoke()
    assert calls == [] and len(approvals) == 1
    assert original.func is effect  # Registered tool identity remains unwrapped.


def test_developer_read_search_cannot_be_repurposed_as_an_rg_option(runtime):
    calls = []
    original = StructuredTool.from_function(
        lambda query: calls.append(query) or "results",
        name="developer_search",
        description="Search",
    )
    bound = runtime._bind_profile_tool(original, source="core", parent="developer")
    assert "read-only" in bound.invoke({"query": "--pre=synthetic-script"})
    assert bound.invoke({"query": "ordinary symbol"}) == "results"
    assert calls == ["ordinary symbol"]


@pytest.mark.parametrize("custom_async", [False, True])
def test_toolkit_sync_async_keep_approval_once_and_current_profile(
    runtime, monkeypatch, custom_async
):
    calls, approvals = [], []

    class SyntheticTool(BaseTool):
        name: str = "developer_run_command"
        description: str = "Synthetic toolkit tool"

        def _run(self, command: str) -> str:
            calls.append(command)
            return "executed"

    class SyntheticAsyncTool(SyntheticTool):
        async def _arun(self, command: str) -> str:
            return self._run(command)

    tool = SyntheticAsyncTool() if custom_async else SyntheticTool()
    runtime._wrap_with_interrupt_gate(tool)
    bound = runtime._bind_profile_tool(tool, source="core", parent="developer")
    runtime._approval_mode_var.set("approve")
    monkeypatch.setattr(
        runtime, "interrupt", lambda request: approvals.append(request) or True
    )
    assert "read-only" in asyncio.run(bound.ainvoke({"command": "git commit"}))
    assert calls == approvals == []
    runtime._current_agent_profile_snapshot_var.set(
        {"tool_policy_json": {"capability": "write_capable"}}
    )
    assert asyncio.run(bound.ainvoke({"command": "git commit"})) == "executed"
    assert calls == ["git commit"] and len(approvals) == 1
    runtime._approval_mode_var.set("block")
    assert "BLOCKED" in asyncio.run(bound.ainvoke({"command": "git push"}))
    assert calls == ["git commit"] and len(approvals) == 1


def test_retained_profile_denylist_and_malformed_policy_fail_closed(runtime):
    calls = []
    bound = runtime._bind_profile_tool(
        StructuredTool.from_function(
            lambda: calls.append(1) or "read",
            name="workspace_read_file",
            description="Read",
        ),
        source="core",
        parent="filesystem",
    )
    assert bound.invoke({}) == "read"
    runtime._current_agent_profile_snapshot_var.set(
        {
            "tool_policy_json": {
                "capability": "write_capable",
                "deny_tools": ["filesystem"],
            }
        }
    )
    assert "denied" in bound.invoke({})
    runtime._current_agent_profile_snapshot_var.set(
        {
            "tool_policy_json": {
                "capability": "write_capable",
                "allow_tools": "filesystem",
            }
        }
    )
    assert "unavailable" in bound.invoke({})
    runtime._current_agent_profile_snapshot_var.set(
        {"tool_policy_json": {"capability": "unknown"}}
    )
    assert "read-only" in bound.invoke({})
    assert calls == [1]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_graph_preserves_injected_config_and_callback_signature(
    runtime, monkeypatch, asynchronous
):
    from tests.test_agent_tool_filtering import _prepare_graph

    agent = _prepare_graph(monkeypatch)
    captured = []

    def read(query: str, config: RunnableConfig) -> str:
        captured.append(config["configurable"]["synthetic"])
        return query

    async def async_read(query: str, config: RunnableConfig) -> str:
        return read(query, config)

    original = StructuredTool.from_function(
        func=read,
        coroutine=async_read,
        name="workspace_read_file",
        description="Synthetic configuration read",
    )
    monkeypatch.setattr(
        agent,
        "_collect_agent_tool_candidates",
        lambda *_: (
            [{"tool": original, "source": "core", "parent": "filesystem"}],
            [],
            set(),
        ),
    )
    target = agent.get_agent_graph(["filesystem"]).tools.tools_by_name[original.name]
    config = {"configurable": {"synthetic": "preserved"}}
    result = (
        asyncio.run(target.ainvoke({"query": "read"}, config=config))
        if asynchronous
        else target.invoke({"query": "read"}, config=config)
    )
    assert result == "read"
    assert captured == ["preserved"]
    assert target.args_schema is original.args_schema


def test_real_filesystem_composite_reads_but_never_changes_disposable_bytes(
    runtime, monkeypatch, tmp_path
):
    from row_bot.tools.filesystem_tool import FileSystemTool

    root = tmp_path / "workspace"
    root.mkdir()
    original = root / "retained.txt"
    original.write_text("keep me", encoding="utf-8")
    filesystem = FileSystemTool()
    monkeypatch.setattr(filesystem, "_get_workspace_root", lambda: str(root))
    monkeypatch.setattr(
        filesystem,
        "_get_selected_operations",
        lambda: ["read_file", "write_file", "file_delete"],
    )
    bound = {
        tool.name: runtime._bind_profile_tool(tool, source="core", parent="filesystem")
        for tool in filesystem.as_langchain_tools()
    }
    assert (
        bound["workspace_read_file"].invoke({"file_path": "retained.txt"}) == "keep me"
    )
    assert "read-only" in bound["workspace_write_file"].invoke(
        {"file_path": "retained.txt", "text": "changed"}
    )
    assert "read-only" in bound["workspace_file_delete"].invoke(
        {"file_path": "retained.txt"}
    )
    assert original.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_actual_graph_retains_exact_repeat_admission_once_per_dispatch(
    runtime, monkeypatch, asynchronous
):
    from tests.test_agent_tool_filtering import _prepare_graph

    agent = _prepare_graph(monkeypatch)
    effects, requests = [], []

    def read(query: str) -> str:
        effects.append(query)
        return "observed"

    async def async_read(query: str) -> str:
        return read(query)

    def admit(name, arguments):
        requests.append((name, arguments))
        return "allow" if len(requests) == 1 else "terminal"

    tool = StructuredTool.from_function(
        func=read,
        coroutine=async_read,
        name="workspace_read_file",
        description="Counted read",
    )
    monkeypatch.setattr(
        agent,
        "_collect_agent_tool_candidates",
        lambda *_: (
            [{"tool": tool, "source": "core", "parent": "filesystem"}],
            [],
            set(),
        ),
    )
    monkeypatch.setattr(agent, "register_exact_tool_request", admit)
    bound = agent.get_agent_graph(["filesystem"]).tools.tools_by_name[tool.name]

    def invoke():
        return (
            asyncio.run(bound.ainvoke({"query": "same"}))
            if asynchronous
            else bound.invoke({"query": "same"})
        )

    assert invoke() == "observed"
    assert invoke() != "observed"
    assert effects == ["same"]
    assert requests == [(tool.name, {"query": "same"})] * 2
