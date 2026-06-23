from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from tests.fixtures.mcp import FakeMcpTool


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


def test_stdio_resolution_uses_env_path_and_reports_missing(monkeypatch) -> None:
    from row_bot.mcp_client.runtime import McpStdioCommandNotFound, _resolve_stdio_command

    env = {"PATH": str(pathlib.Path(sys.executable).parent), **{k: v for k, v in os.environ.items() if k == "PATHEXT"}}
    resolved = _resolve_stdio_command(pathlib.Path(sys.executable).name, env)

    assert pathlib.Path(resolved).name.lower() == pathlib.Path(sys.executable).name.lower()
    with pytest.raises(McpStdioCommandNotFound, match="not found"):
        _resolve_stdio_command("row-bot-missing-mcp-command", {"PATH": str(pathlib.Path(sys.executable).parent)})


def test_loop_scheduler_runs_coroutines_and_shutdown_resets_state() -> None:
    from row_bot.mcp_client import runtime

    async def answer():
        return 42

    assert runtime._schedule(answer()).result(timeout=5) == 42
    assert runtime._loop is not None

    runtime.shutdown()

    assert runtime._loop is None
    assert runtime._thread is None


def test_update_status_and_catalog_sync_from_config(monkeypatch) -> None:
    from row_bot.mcp_client import runtime

    config = {
        "enabled": True,
        "servers": {
            "files": {
                "enabled": True,
                "tools": {"enabled": {"read_file": False, "delete_file": True}, "require_approval": ["read_file"]},
            }
        },
    }
    monkeypatch.setattr(runtime.mcp_config, "get_config", lambda: config)
    runtime._update_status("files", status="connected", tool_count=0)
    with runtime._runtime_lock:
        runtime._catalog["files"] = runtime._normalize_tools(
            "files",
            {"tools": {"enabled": {"read_file": True}}},
            [FakeMcpTool("read_file"), FakeMcpTool("delete_file")],
        )

    runtime._sync_catalog_from_config(config)

    snapshot = runtime.get_catalog_snapshot()
    status = runtime.get_status_summary()["servers"]["files"]

    assert {tool["name"]: tool["enabled"] for tool in snapshot["files"]} == {"read_file": False, "delete_file": True}
    assert status["tool_count"] == 2
    assert status["enabled_tool_count"] == 1
    assert status["destructive_tool_count"] == 1


def test_normalize_tools_handles_filters_duplicates_destructive_and_schema_shapes() -> None:
    from row_bot.mcp_client import runtime

    normalized = runtime._normalize_tools(
        "File Server",
        {
            "tools": {
                "include": ["read_file", "delete_file"],
                "exclude": ["ignored"],
                "enabled": {"read_file": True},
                "require_approval": ["read_file"],
            }
        },
        [
            FakeMcpTool("", "blank"),
            FakeMcpTool("ignored", "ignored"),
            FakeMcpTool("read_file", "first", {"type": "object", "properties": {"path": {"type": "string"}}}),
            FakeMcpTool("read_file", "second", {"type": "object", "properties": {"count": {"type": "integer"}}}),
            FakeMcpTool("delete_file", "Delete a file", {"type": "object"}),
        ],
    )

    assert set(normalized) == {"read_file", "delete_file"}
    assert normalized["read_file"].description == "second"
    assert normalized["read_file"].prefixed_name == "mcp_file_server_read_file"
    assert normalized["read_file"].requires_approval is True
    assert normalized["delete_file"].destructive is True
    assert normalized["delete_file"].enabled is False


@pytest.mark.parametrize(
    ("schema", "value", "expected_type"),
    [
        ({"type": "string"}, "x", str),
        ({"type": "integer"}, 1, int),
        ({"type": "number"}, 1.5, float),
        ({"type": "boolean"}, True, bool),
        ({"type": "object"}, {}, dict),
        ({"type": "array", "items": {"type": "integer"}}, [1], list),
        ({"type": ["null", "string"]}, "x", str),
        ({}, object(), Any),
    ],
)
def test_json_schema_type_maps_basic_types(schema, value, expected_type) -> None:
    from row_bot.mcp_client import runtime

    mapped = runtime._json_schema_type(schema)

    if expected_type is Any:
        assert mapped is Any
    else:
        origin = getattr(mapped, "__origin__", None)
        assert isinstance(value, origin or mapped)


def test_schema_to_model_validates_required_optional_and_falls_back_for_complex_fields() -> None:
    from row_bot.mcp_client import runtime

    info = runtime.McpToolInfo(
        server_name="files",
        name="read_file",
        prefixed_name="mcp_files_read_file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to read"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["path"],
        },
    )

    model = runtime._schema_to_model(info)
    parsed = model(path="demo.txt")

    assert parsed.path == "demo.txt"
    assert parsed.limit == 5
    with pytest.raises(ValidationError):
        model()

    bad = runtime.McpToolInfo(
        server_name="files",
        name="bad",
        prefixed_name="mcp_files_bad",
        input_schema={"type": "object", "properties": {"not-valid-name": {"type": "string"}}},
    )
    assert runtime._schema_to_model(bad) is runtime._GenericArgs
