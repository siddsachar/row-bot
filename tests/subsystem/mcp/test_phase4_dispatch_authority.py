"""Current MCP execution cannot adopt a stale approval/schema/transport cut."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


@pytest.fixture
def authority(monkeypatch):
    from row_bot.mcp_client import runtime
    cfg = {"enabled": True, "servers": {"fixture": {"enabled": True, "command": "synthetic", "tools": {"enabled": {"read": True}, "resources_enabled": True, "prompts_enabled": True}}}}
    server = runtime.McpServerRuntime("fixture", {"tool_timeout": 1})
    info = runtime.McpToolInfo("fixture", "read", "mcp_fixture_read", enabled=True)
    monkeypatch.setattr(runtime, "_servers", {"fixture": server})
    monkeypatch.setattr(runtime, "_catalog", {"fixture": {"read": info}})
    monkeypatch.setattr(runtime, "_get_effective_config", lambda: cfg)
    return runtime, cfg, server, info


@pytest.mark.parametrize("change", ["approval", "transport", "schema", "effect"])
def test_bound_tool_rejects_policy_changes_before_scheduling(authority, monkeypatch, change):
    runtime, cfg, _, info = authority
    bound = runtime._make_tool_func("fixture", "read", enforce_policy=True)
    if change == "approval":
        cfg["servers"]["fixture"]["tools"]["require_approval"] = ["read"]
    elif change == "transport":
        cfg["servers"]["fixture"]["command"] = "different-synthetic"
    elif change == "schema":
        info.input_schema = {"type": "object", "required": ["target"]}
    else:
        info.destructive = True
    monkeypatch.setattr(runtime, "_schedule", lambda _: pytest.fail("Stale policy scheduled"))
    with pytest.raises(RuntimeError, match="policy changed"):
        bound()


def test_policy_edit_while_waiting_for_stateful_session_lock_never_dispatches(authority):
    runtime, cfg, server, _ = authority
    bound = runtime._bind_authority("fixture", "read")
    calls = []
    async def call_tool(*args):
        calls.append(args)
    server.session = SimpleNamespace(call_tool=call_tool)
    class Lock:
        async def __aenter__(self):
            cfg["servers"]["fixture"]["tools"]["require_approval"] = ["read"]
        async def __aexit__(self, *args):
            return False
    server._session_lock = Lock()
    with pytest.raises(RuntimeError, match="policy changed"):
        asyncio.run(server.call_tool("read", {}, validate=lambda: runtime._validate_bound_runtime("fixture", bound, tool_name="read")))
    assert calls == []


def test_unrelated_server_change_preserves_unchanged_bound_authority(authority):
    runtime, cfg, _, _ = authority
    bound = runtime._bind_authority("fixture", "read")
    cfg["servers"]["unrelated"] = {"enabled": False, "command": "other"}
    runtime._validate_bound_runtime("fixture", bound, tool_name="read")


@pytest.mark.parametrize("feature", ["resources_enabled", "prompts_enabled"])
def test_read_features_cannot_adopt_changed_transport(authority, feature):
    runtime, cfg, _, _ = authority
    bound = runtime._bind_authority("fixture")
    cfg["servers"]["fixture"]["command"] = "changed"
    with pytest.raises(RuntimeError, match="policy changed"):
        runtime._validate_bound_runtime("fixture", bound, feature=feature)


@pytest.mark.parametrize("name,annotations,effect", [
    ("read_file", None, "read_only"),
    ("browser_snapshot", None, "read_only"),
    ("browser_click", None, "interaction"),
    ("browser_click", {"destructiveHint": True, "readOnlyHint": True}, "mutation"),
    ("read_and_delete_file", {"readOnlyHint": True}, "mutation"),
    ("opaque_operation", None, "unknown"),
    ("opaque_operation", {"readOnlyHint": True}, "read_only"),
    ("get_and_process", {"readOnlyHint": False}, "unknown"),
])
def test_effect_classification_preserves_reads_and_identifies_unknown(name, annotations, effect):
    from row_bot.mcp_client.safety import classify_tool_effect
    assert classify_tool_effect(name, tool_obj={"annotations": annotations}) == effect


def test_unknown_effect_requires_explicit_enablement_and_approval_after_refresh(monkeypatch):
    from row_bot.mcp_client import runtime
    cfg = {"enabled": True, "servers": {"fixture": {"enabled": True, "tools": {}}}}
    catalog = runtime._normalize_tools("fixture", cfg["servers"]["fixture"], [
        {"name": "opaque_operation"}, {"name": "read_file"}, {"name": "browser_click"},
    ])
    monkeypatch.setattr(runtime, "_catalog", {"fixture": catalog})
    monkeypatch.setattr(runtime, "_statuses", {})
    runtime._sync_catalog_from_config(cfg)
    assert catalog["opaque_operation"].enabled is False
    assert catalog["opaque_operation"].requires_approval is True
    for name in ("read_file", "browser_click"):
        assert catalog[name].enabled is True
        assert catalog[name].requires_approval is False
    cfg["servers"]["fixture"]["tools"]["enabled"] = {"opaque_operation": True}
    runtime._sync_catalog_from_config(cfg)
    assert catalog["opaque_operation"].enabled is True
    assert catalog["opaque_operation"].requires_approval is True


def test_prepared_plugin_launch_does_not_inherit_unrelated_host_credentials(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from row_bot.mcp_client import runtime
    from row_bot.plugins import mcp, worker
    launch = SimpleNamespace(plugin_id="fixture", declared_env={"PLUGIN_SCOPED_SECRET": "synthetic-scoped"})
    monkeypatch.setattr(mcp, "resolve_prepared_plugin_mcp_launch", lambda *_: launch)
    monkeypatch.setattr(worker, "_run_directory", lambda _: tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-synthetic-secret")
    monkeypatch.setenv("ROW_BOT_PRIVATE_TEST_VALUE", "private-synthetic")
    monkeypatch.setattr(runtime, "_resolve_stdio_command", lambda command, _env: command)
    monkeypatch.setattr(runtime, "apply_managed_runtime_env", lambda *_: pytest.fail("prepared launch cannot adopt another environment"))
    captured = []

    @asynccontextmanager
    async def stdio(params):
        captured.append(params)
        yield (object(), object())

    class Session:
        def __init__(self, *_args):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        async def initialize(self):
            return None

    monkeypatch.setattr(runtime, "stdio_client", stdio)
    monkeypatch.setattr(runtime, "ClientSession", Session)
    server = runtime.McpServerRuntime("fixture", {"command": "synthetic-python", "args": ["-I", "-S", "-B"]})

    async def connect():
        await server._connect()
        await server.exit_stack.aclose()

    asyncio.run(connect())
    assert server._prepared_plugin_launch is launch
    environment = captured[0].env
    assert environment["PLUGIN_SCOPED_SECRET"] == "synthetic-scoped"
    assert "OPENAI_API_KEY" not in environment and "ROW_BOT_PRIVATE_TEST_VALUE" not in environment
    assert environment["HOME"] == str(tmp_path) and environment["USERPROFILE"] == str(tmp_path)


def test_changed_preparation_revokes_already_bound_plugin_tool(authority, monkeypatch):
    from row_bot.plugins import mcp
    runtime, _cfg, server, _info = authority
    prepared = [object()]
    monkeypatch.setattr(mcp, "resolve_prepared_plugin_mcp_launch", lambda *_: prepared[0])
    server._prepared_plugin_launch = prepared[0]
    bound = runtime._bind_authority("fixture", "read")
    runtime._validate_bound_runtime("fixture", bound, tool_name="read")
    prepared[0] = object()
    with pytest.raises(RuntimeError, match="plugin environment changed"):
        runtime._validate_bound_runtime("fixture", bound, tool_name="read")
