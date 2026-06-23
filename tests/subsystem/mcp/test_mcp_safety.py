from __future__ import annotations

import pytest

from tests.fixtures.mcp import FakeMcpTool


pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


def test_mcp_safety_classifies_browser_tools_as_safe_but_mutations_destructive() -> None:
    from row_bot.mcp_client.safety import is_destructive_tool, prefixed_tool_name, sanitize_name_component, tool_enabled_by_default

    assert sanitize_name_component("File Server!") == "file_server"
    assert prefixed_tool_name("File Server!", "Read File") == "mcp_file_server_read_file"
    assert is_destructive_tool("browser_click", "click a browser target") is False
    assert is_destructive_tool("delete_file", "remove a file") is True
    assert is_destructive_tool("inspect", "read-only", type("Tool", (), {"annotations": {"readOnlyHint": True}})()) is False
    assert tool_enabled_by_default(False) is True
    assert tool_enabled_by_default(True) is False


def test_mcp_status_summary_counts_enabled_destructive_tools(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    server_cfg = {"enabled": True, "transport": "stdio", "tools": {"enabled": {"delete_file": True}}}
    monkeypatch.setattr(runtime.mcp_config, "get_config", lambda: {"enabled": True, "servers": {"fake": server_cfg}})
    with runtime._runtime_lock:
        runtime._catalog.clear()
        runtime._statuses.clear()
        runtime._catalog["fake"] = runtime._normalize_tools(
            "fake",
            server_cfg,
            [FakeMcpTool("delete_file", "Delete a file", {"type": "object"})],
        )

    summary = runtime.get_status_summary()

    assert summary["tool_count"] == 1
    assert summary["enabled_tool_count"] == 1
    assert summary["destructive_tool_count"] == 1
    assert runtime.get_destructive_tool_names() == {"mcp_fake_delete_file"}

    with runtime._runtime_lock:
        runtime._catalog.clear()
        runtime._statuses.clear()
