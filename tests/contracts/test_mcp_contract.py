from __future__ import annotations

import pytest

from tests.fixtures.mcp import FakeCallResult, FakeContentBlock, FakeMcpTool


pytestmark = pytest.mark.contract


def test_mcp_tool_normalization_applies_prefixes_and_destructive_defaults() -> None:
    from row_bot.mcp_client import runtime

    tools = runtime._normalize_tools(
        "File Server",
        {"tools": {"enabled": {"delete_file": True}, "require_approval": ["read_file"]}},
        [
            FakeMcpTool("read_file", "Read file", {"type": "object", "properties": {"path": {"type": "string"}}}),
            FakeMcpTool("delete_file", "Delete file", {"type": "object"}),
        ],
    )

    assert tools["read_file"].prefixed_name == "mcp_file_server_read_file"
    assert tools["read_file"].enabled is True
    assert tools["read_file"].requires_approval is True
    assert tools["delete_file"].destructive is True
    assert tools["delete_file"].enabled is True
    assert tools["delete_file"].requires_approval is True


def test_mcp_result_normalization_handles_errors_structured_content_and_truncation() -> None:
    from row_bot.mcp_client.results import normalize_call_result

    result = FakeCallResult(
        content=[FakeContentBlock("text", "hello world")],
        structuredContent={"ok": True, "value": 3},
        isError=True,
    )

    text = normalize_call_result(result, output_limit=50)

    assert text.startswith("MCP tool error: hello world")
    assert "STRUCTURED_CONTENT" in text
    assert "[Truncated MCP output at 50 characters]" in text


def test_mcp_langchain_wrappers_are_built_from_injected_catalog(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    server_cfg = {"enabled": True, "transport": "stdio", "tools": {"enabled": {"read_file": True}}}
    monkeypatch.setattr(runtime.mcp_config, "is_globally_enabled", lambda: True)
    monkeypatch.setattr(runtime.mcp_config, "get_config", lambda: {"enabled": True, "servers": {"fake": server_cfg}})
    monkeypatch.setattr(runtime, "discover_enabled_servers", lambda: None)

    with runtime._runtime_lock:
        runtime._catalog.clear()
        runtime._servers.clear()
        runtime._catalog["fake"] = runtime._normalize_tools(
            "fake",
            server_cfg,
            [FakeMcpTool("read_file", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}})],
        )

    tools = runtime.get_langchain_tools()

    assert [tool.name for tool in tools] == ["mcp_fake_read_file"]
    assert runtime.get_destructive_tool_names() == set()

    with runtime._runtime_lock:
        runtime._catalog.clear()
        runtime._servers.clear()
