from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from tests.fixtures.mcp import FakeCallResult, FakeContentBlock, FakeMcpTool


pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


@pytest.fixture(autouse=True)
def clean_runtime_state():
    from row_bot.mcp_client import runtime

    with runtime._runtime_lock:
        runtime._catalog.clear()
        runtime._servers.clear()
        runtime._statuses.clear()
    yield
    runtime.shutdown()
    with runtime._runtime_lock:
        runtime._catalog.clear()
        runtime._servers.clear()
        runtime._statuses.clear()


@dataclass
class FakeResource:
    uri: str
    name: str = ""
    description: str = ""


@dataclass
class FakeTextContent:
    text: str


@dataclass
class FakePrompt:
    name: str
    description: str = ""


@dataclass
class FakePromptMessage:
    role: str
    content: Any


class FakeSession:
    async def initialize(self) -> None:
        return None

    async def list_tools(self):
        return type("ToolList", (), {"tools": [FakeMcpTool("read_file", "Read", {"type": "object"})]})()

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        return FakeCallResult([FakeContentBlock("text", f"{name}:{arguments['path']}")])

    async def list_resources(self):
        return type("ResourceList", (), {"resources": [FakeResource("file://demo", "Demo", "A demo resource")]})()

    async def read_resource(self, uri: str):
        return type("ResourceRead", (), {"contents": [FakeTextContent(f"content:{uri}")]} )()

    async def list_prompts(self):
        return type("PromptList", (), {"prompts": [FakePrompt("summarize", "Summarize input")]})()

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None):
        return type(
            "PromptResult",
            (),
            {
                "description": f"prompt:{name}",
                "messages": [FakePromptMessage("user", FakeTextContent(str(arguments or {})))],
            },
        )()


class FakeFuture:
    def __init__(self, value):
        self.value = value

    def result(self, timeout=None):
        if asyncio.iscoroutine(self.value):
            return asyncio.run(self.value)
        return self.value


def test_server_runtime_discovers_tools_and_exposes_resource_prompt_methods() -> None:
    from row_bot.mcp_client import runtime

    server = runtime.McpServerRuntime("fake", {"tool_timeout": 1, "output_limit": 50})
    server.session = FakeSession()

    asyncio.run(server._discover_tools())

    assert runtime.get_status_summary()["servers"]["fake"]["tool_count"] == 1
    assert asyncio.run(server.call_tool("read_file", {"path": "a.txt"})) == "read_file:a.txt"
    assert "Demo: file://demo" in asyncio.run(server.list_resources())
    assert asyncio.run(server.read_resource("file://demo")) == "content:file://demo"
    assert "summarize: Summarize input" in asyncio.run(server.list_prompts())
    assert "[user] {'topic': 'tests'}" in asyncio.run(server.get_prompt("summarize", {"topic": "tests"}))


def test_discover_stop_refresh_and_shutdown_use_fake_runtime(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    started = []
    stopped = []

    class FakeRuntime:
        def __init__(self, name: str, cfg: dict[str, Any]):
            self.name = name
            self.cfg = cfg

        async def start(self):
            started.append(self.name)

        async def stop(self):
            stopped.append(self.name)

    monkeypatch.setattr(runtime, "McpServerRuntime", FakeRuntime)
    monkeypatch.setattr(runtime, "sdk_available", lambda: True)
    monkeypatch.setattr(
        runtime.mcp_config,
        "get_config",
        lambda: {"enabled": True, "servers": {"fake": {"enabled": True, "transport": "stdio"}}},
    )
    def immediate_schedule(value):
        if asyncio.iscoroutine(value):
            return FakeFuture(asyncio.run(value))
        return FakeFuture(value)

    monkeypatch.setattr(runtime, "_schedule", immediate_schedule)

    runtime.discover_enabled_servers()
    assert started == ["fake"]
    assert "fake" in runtime._servers

    runtime.refresh_server("fake")
    assert stopped == ["fake"]
    assert started == ["fake", "fake"]

    runtime.shutdown()
    assert stopped[-1] == "fake"


def test_probe_reports_success_and_failure_without_real_transport(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    class ProbeRuntime:
        def __init__(self, name: str, cfg: dict[str, Any]):
            self.name = name
            self.cfg = cfg
            self.session = FakeSession()

        async def _connect(self):
            if self.cfg.get("fail"):
                raise RuntimeError("connect failed")

        async def close(self):
            return None

    monkeypatch.setattr(runtime, "McpServerRuntime", ProbeRuntime)

    ok = asyncio.run(runtime.probe_server_async("fake", {}))
    failed = asyncio.run(runtime.probe_server_async("fake", {"fail": True}))

    assert ok["ok"] is True
    assert ok["tool_count"] == 1
    assert failed == {"ok": False, "error": "connect failed", "tools": []}


def test_tool_resource_and_prompt_wrappers_call_fake_runtime(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    server = runtime.McpServerRuntime("fake", {"tool_timeout": 1})
    server.session = FakeSession()
    with runtime._runtime_lock:
        runtime._servers["fake"] = server

    monkeypatch.setattr(runtime, "_schedule", lambda value: FakeFuture(value))

    assert runtime._make_tool_func("fake", "read_file")(path="demo.txt") == "read_file:demo.txt"
    assert "MCP resources:" in runtime._make_resource_list_func("fake")()
    assert runtime._make_resource_read_func("fake")("file://demo") == "content:file://demo"
    assert "MCP prompts:" in runtime._make_prompt_list_func("fake")()
    assert "prompt:summarize" in runtime._make_prompt_get_func("fake")("summarize", {"topic": "tests"})

    with pytest.raises(RuntimeError, match="not running"):
        runtime._make_tool_func("missing", "read_file")(path="demo.txt")


def test_langchain_wrappers_allow_filters_destructive_names_and_status(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    server_cfg = {
        "enabled": True,
        "transport": "stdio",
        "tools": {
            "enabled": {"read_file": True, "delete_file": True},
            "resources_enabled": True,
            "prompts_enabled": True,
        },
    }
    server = runtime.McpServerRuntime("fake", {"tool_timeout": 1})
    server.session = FakeSession()

    monkeypatch.setattr(runtime.mcp_config, "is_globally_enabled", lambda: True)
    monkeypatch.setattr(runtime.mcp_config, "get_config", lambda: {"enabled": True, "servers": {"fake": server_cfg}})
    monkeypatch.setattr(runtime, "discover_enabled_servers", lambda: None)
    monkeypatch.setattr(runtime, "_schedule", lambda value: FakeFuture(value))

    with runtime._runtime_lock:
        runtime._servers["fake"] = server
        runtime._statuses["fake"] = runtime.McpServerStatus(name="fake", enabled=True, status="connected")
        runtime._catalog["fake"] = runtime._normalize_tools(
            "fake",
            server_cfg,
            [FakeMcpTool("read_file", "Read", {"type": "object"}), FakeMcpTool("delete_file", "Delete", {"type": "object"})],
        )

    assert runtime._allow_names_set(["", "mcp", "mcp_fake_read_file"]) == {"mcp", "mcp_fake_read_file"}
    assert runtime._mcp_runtime_name_allowed("mcp_fake_read_file", {"mcp_fake_read_file"}) is True
    assert runtime._mcp_runtime_name_allowed("mcp_fake_delete_file", {"mcp_fake_read_file"}) is False

    tools = runtime.get_langchain_tools(allow_names=["mcp_fake_read_file", "mcp_fake_list_resources", "mcp_fake_get_prompt"])
    assert [tool.name for tool in tools] == ["mcp_fake_read_file", "mcp_fake_list_resources", "mcp_fake_get_prompt"]

    destructive = runtime.get_destructive_tool_names()
    assert destructive == {"mcp_fake_delete_file"}

    summary = runtime.get_status_summary()
    assert summary["connected_server_count"] == 1
    assert summary["tool_count"] == 2
    assert summary["destructive_tool_count"] == 1


def test_plugin_mcp_runtime_filters_tools_by_plugin_source(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    plugin_cfg = {
        "enabled": True,
        "transport": "stdio",
        "source": {
            "kind": "plugin",
            "plugin_id": "office-plugin",
            "plugin_name": "Office Plugin",
            "server_id": "office",
        },
        "tools": {"enabled": {"search_mail": True, "delete_mail": True}},
    }
    other_cfg = {
        "enabled": True,
        "transport": "stdio",
        "source": {
            "kind": "plugin",
            "plugin_id": "other-plugin",
            "plugin_name": "Other Plugin",
            "server_id": "other",
        },
        "tools": {"enabled": {"search_mail": True}},
    }
    monkeypatch.setattr(
        runtime,
        "_get_effective_config",
        lambda: {
            "enabled": True,
            "servers": {
                "plugin_office_plugin_office": plugin_cfg,
                "plugin_other_plugin_other": other_cfg,
            },
        },
    )
    monkeypatch.setattr(runtime, "discover_enabled_servers", lambda: None)

    with runtime._runtime_lock:
        runtime._servers["plugin_office_plugin_office"] = runtime.McpServerRuntime(
            "plugin_office_plugin_office",
            {"tool_timeout": 1},
        )
        runtime._servers["plugin_other_plugin_other"] = runtime.McpServerRuntime(
            "plugin_other_plugin_other",
            {"tool_timeout": 1},
        )
        runtime._catalog["plugin_office_plugin_office"] = runtime._normalize_tools(
            "plugin_office_plugin_office",
            plugin_cfg,
            [
                FakeMcpTool("search_mail", "Search mail", {"type": "object"}),
                FakeMcpTool("delete_mail", "Delete mail", {"type": "object"}),
            ],
        )
        runtime._catalog["plugin_other_plugin_other"] = runtime._normalize_tools(
            "plugin_other_plugin_other",
            other_cfg,
            [FakeMcpTool("search_mail", "Search mail", {"type": "object"})],
        )

    tools = runtime.get_plugin_langchain_tools("office-plugin")
    destructive = runtime.get_plugin_destructive_tool_names("office-plugin")
    records = runtime.get_plugin_tool_records("office-plugin")

    assert [tool.name for tool in tools] == [
        "mcp_plugin_office_plugin_office_search_mail",
        "mcp_plugin_office_plugin_office_delete_mail",
    ]
    assert destructive == {"mcp_plugin_office_plugin_office_delete_mail"}
    assert [record["plugin_id"] for record in records] == ["office-plugin", "office-plugin"]
    assert all(record["source"] == "mcp" for record in records)
