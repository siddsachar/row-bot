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

    def fake_http_client(url: str, *, headers: dict, httpx_client_factory):
        assert callable(httpx_client_factory)
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


def test_a_connection_logs_where_it_connected_but_never_a_key_in_its_address_or_arguments(monkeypatch, caplog) -> None:
    import logging
    from row_bot.mcp_client import runtime

    monkeypatch.setattr(runtime, "streamablehttp_client",
                        lambda url, *, headers, httpx_client_factory: FakeAsyncContext(("read", "write", "session")))
    monkeypatch.setattr(runtime, "ClientSession", FakeClientSession)
    server = runtime.McpServerRuntime("keyed", {
        "transport": "streamable_http", "connect_timeout": 1, "args": ["--token", "arg-secret"],
        "url": "http://127.0.0.1:9/k/path-secret/mcp?api_key=query-secret"})

    with caplog.at_level(logging.INFO, logger="row_bot.mcp"):
        asyncio.run(server._connect())
    asyncio.run(server.close())

    [line] = [record.getMessage() for record in caplog.records if "mcp.server.connected" in record.getMessage()]
    assert '"host": "http://127.0.0.1"' in line and '"args": 2' in line
    for secret in ("path-secret", "query-secret", "arg-secret", "--token"):
        assert secret not in caplog.text


def test_sse_transport_connects_with_fake_sdk(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    calls: list[tuple[str, dict]] = []

    def fake_sse_client(url: str, *, headers: dict, httpx_client_factory):
        assert callable(httpx_client_factory)
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


def test_the_mcp_sdk_never_logs_a_server_session_id(caplog) -> None:
    import logging
    from types import SimpleNamespace

    from mcp.client.streamable_http import StreamableHTTPTransport

    from row_bot.mcp_client import runtime  # noqa: F401 - loading the MCP client sets its SDK logging

    transport = StreamableHTTPTransport("http://127.0.0.1:9/mcp")
    with caplog.at_level(logging.INFO):
        transport._maybe_extract_session_id_from_response(
            SimpleNamespace(headers={"mcp-session-id": "synthetic-session-4f2a"})
        )
    assert transport.session_id == "synthetic-session-4f2a"
    assert "synthetic-session-4f2a" not in caplog.text


def test_a_stdio_programs_stderr_keeps_only_its_last_lines() -> None:
    from row_bot.mcp_client.runtime import _StderrTail

    tail = _StderrTail()
    tail.stream.write("".join(f"line {n}\n" for n in range(100)) + "x" * 5000 + "\n")
    tail.stream.flush()
    tail.release()
    lines = tail.lines(wait=10)
    assert len(lines) == 40 and lines[-3] == "line 99"
    assert lines[-2:] == ["x" * 300, "x" * 300]  # A long line is read in bounded pieces, each cut short.


@pytest.mark.slow
def test_a_stdio_server_that_fails_to_start_leaves_its_last_lines() -> None:
    from row_bot.mcp_client import runtime

    script = ("import sys\nfor n in range(50): print(f'starting {n}', file=sys.stderr)\n"
              "raise AttributeError(\"'Server' object has no attribute 'list_resources'\")\n")
    try:
        result = runtime.probe_server("broken", {"transport": "stdio", "command": sys.executable, "args": ["-c", script],
                                                 "connect_timeout": 20})
        assert result["ok"] is False
        lines = runtime.stderr_tails()["broken"]
        assert len(lines) == 40 and lines[-1] == "AttributeError: 'Server' object has no attribute 'list_resources'"
    finally:
        runtime.shutdown()


def test_a_server_that_refuses_the_key_says_so_and_lets_go() -> None:
    """Found live (GitHub): the SDK raises a refused key's HTTP error as the connection closes. That is how it
    ended, not a connection still open: the check says why, and nothing is left to refuse the next try."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from row_bot.mcp_client import runtime

    class Refuses(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(401)
            self.send_header("content-length", "0")
            self.end_headers()

        do_GET = do_DELETE = do_POST

        def log_message(self, *args):
            pass

    web = ThreadingHTTPServer(("127.0.0.1", 0), Refuses)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    cfg = {"transport": "streamable_http", "url": f"http://127.0.0.1:{web.server_address[1]}/mcp", "connect_timeout": 10}
    try:
        for _ in range(2):
            result = runtime.probe_server("refuses", cfg)
            assert result["ok"] is False and "401 Unauthorized" in result["error"], result
    finally:
        web.shutdown()
        runtime.shutdown()


@pytest.mark.slow
def test_a_server_that_never_answers_the_end_of_its_session_still_lets_go() -> None:
    import socket
    import threading
    import time

    import anyio
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    from row_bot.mcp_client import runtime

    server = FastMCP("silent-at-close")

    @server.tool()
    async def ping() -> str:
        return "pong"

    app = server.streamable_http_app()

    async def silent_delete(scope, receive, send):  # Answers everything but the session's DELETE.
        if scope["type"] == "http" and scope["method"] == "DELETE":
            await anyio.sleep_forever()
        await app(scope, receive, send)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    web = uvicorn.Server(uvicorn.Config(silent_delete, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    try:
        while not web.started:
            time.sleep(0.05)
        started = time.monotonic()
        result = runtime.probe_server("silent", {"transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp",
                                                 "connect_timeout": 10})
        assert result["ok"] is True and [tool["name"] for tool in result["tools"]] == ["ping"]
        assert time.monotonic() - started < 10  # Not the library's five-minute wait.
    finally:
        web.should_exit = True
        thread.join(10)
        runtime.shutdown()
