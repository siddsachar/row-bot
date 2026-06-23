from __future__ import annotations

import asyncio
import sys

import pytest

from tests.fixtures.mcp import FakeAsyncContext, FakeClientSession


pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


def test_mcp_config_normalizes_http_aliases_and_invalid_transports() -> None:
    from row_bot.mcp_client.config import normalize_server_config

    assert normalize_server_config("web", {"transport": "http", "url": "http://127.0.0.1/mcp"})["transport"] == "streamable_http"
    assert normalize_server_config("bad", {"transport": "banana"})["transport"] == "stdio"


def test_stdio_command_resolution_reports_missing_command() -> None:
    from row_bot.mcp_client.runtime import McpStdioCommandNotFound, _resolve_stdio_command

    assert _resolve_stdio_command(sys.executable, {}) == sys.executable
    with pytest.raises(McpStdioCommandNotFound, match="not found"):
        _resolve_stdio_command("row-bot-definitely-missing-command", {})


def test_streamable_http_transport_connects_with_fake_sdk(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    calls: list[tuple[str, dict]] = []

    def fake_http_client(url: str, *, headers: dict):
        calls.append((url, headers))
        return FakeAsyncContext(("read", "write", "session"))

    monkeypatch.setattr(runtime, "streamablehttp_client", fake_http_client)
    monkeypatch.setattr(runtime, "ClientSession", FakeClientSession)

    server = runtime.McpServerRuntime(
        "http-fake",
        {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp", "headers": {"X-Test": "1"}, "connect_timeout": 1},
    )

    asyncio.run(server._connect())

    assert calls == [("http://127.0.0.1:9/mcp", {"X-Test": "1"})]
    assert server.session.initialized is True
    asyncio.run(server.close())


def test_sse_transport_connects_with_fake_sdk(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    calls: list[tuple[str, dict]] = []

    def fake_sse_client(url: str, *, headers: dict):
        calls.append((url, headers))
        return FakeAsyncContext(("read", "write"))

    monkeypatch.setattr(runtime, "sse_client", fake_sse_client)
    monkeypatch.setattr(runtime, "ClientSession", FakeClientSession)

    server = runtime.McpServerRuntime(
        "sse-fake",
        {"transport": "sse", "url": "http://127.0.0.1:9/sse", "headers": {"X-Test": "2"}, "connect_timeout": 1},
    )

    asyncio.run(server._connect())

    assert calls == [("http://127.0.0.1:9/sse", {"X-Test": "2"})]
    assert server.session.initialized is True
    asyncio.run(server.close())


def test_probe_server_returns_dependency_missing_when_sdk_unavailable(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    monkeypatch.setattr(runtime, "ClientSession", None)

    result = runtime.probe_server("missing-sdk", {"transport": "stdio", "command": sys.executable})

    assert result == {"ok": False, "error": "Python package 'mcp' is not installed", "tools": []}
