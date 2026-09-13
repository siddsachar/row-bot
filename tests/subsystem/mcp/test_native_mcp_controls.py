"""Native exposure uses the original registry/file and exact durable receipts."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot import tool_configuration as configuration
from row_bot.application import native_mcp_controls as controls

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.tools import registry
    from row_bot.tools.mcp_tool import McpTool
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(registry, "_active_config_path", tmp_path / "tools_config.json")
    monkeypatch.setattr(registry, "_tools", {"mcp": McpTool()})
    monkeypatch.setattr(registry, "_enabled", {"mcp": False})
    monkeypatch.setattr(registry, "_tool_configs", {})
    monkeypatch.setattr(registry, "_global_config", {})
    monkeypatch.setattr(registry, "_invalidate_agent_cache", lambda: None)
    document = {"tools": {"mcp": False, "other": True}, "tool_configs": {"other": {"private": "synthetic-secret"}},
                "global": {"future": 3}, "future": {"kept": [1]}}
    configuration.configuration_path().write_text(json.dumps(document), encoding="utf-8")
    return registry, document


def command(enabled=True):
    return {"command_id": str(uuid4()), "type": "mcp.facade.control", "expected_revision": "0",
        "payload": {"resource_revision": controls.read_native_mcp_state().resource_revision, "enabled": enabled}}


def execute(value, **callbacks):
    return controls.execute_native_mcp_command(owner_id="synthetic-owner", key=value["command_id"], command=value,
        validate=callbacks.get("validate", lambda: None), validate_review=callbacks.get("validate_review", lambda _: None))


def test_actual_native_facade_exposes_connected_tools_without_start_or_execution(owner, monkeypatch):
    from langchain_core.tools import StructuredTool
    from row_bot.mcp_client import runtime
    calls = []
    tool = StructuredTool.from_function(lambda: calls.append("executed"), name="mcp_synthetic_read", description="Read synthetic data")
    monkeypatch.setattr(runtime, "get_langchain_tools", lambda: [tool])
    for name in ("discover_enabled_servers", "probe_server", "stop_server", "_schedule"):
        monkeypatch.setattr(runtime, name, lambda *_a, **_k: pytest.fail("Native switch started transport"))
    registry, original = owner
    assert registry.get_langchain_tools() == []
    value = command()
    reviewed = controls.review_native_mcp_command(**value["payload"], validate=lambda: None)
    result = execute(value)
    assert result["status"] == "completed" and reviewed["enabled"] is True
    assert [item.name for item in registry.get_langchain_tools()] == [tool.name]
    assert calls == []
    current = configuration.read_saved().document
    for field in ("tool_configs", "global", "future"):
        assert current[field] == original[field]
    assert "synthetic-secret" not in json.dumps([result, reviewed, asdict(controls.read_native_mcp_state())])
    assert execute(command(False))["status"] == "completed"
    assert registry.get_langchain_tools() == []


@pytest.mark.parametrize("flat", [False, True])
def test_legacy_setters_preserve_format_unknown_fields_and_share_one_publisher(owner, monkeypatch, flat):
    registry, original = owner
    if flat:
        original.update(original.pop("tools"))
    configuration.configuration_path().write_text(json.dumps(original), encoding="utf-8")
    publish = configuration.publish_saved
    publications = []
    def counted(*args, **kwargs):
        publications.append(kwargs["command_id"])
        return publish(*args, **kwargs)
    monkeypatch.setattr(configuration, "publish_saved", counted)
    registry.set_enabled("mcp", True)
    registry.set_tool_config("other", "new", "Row-Bot ⚡")
    registry.set_global_config("new", 4)
    current = configuration.read_saved().document
    assert ("tools" in current) is not flat
    assert configuration.tools_map(current)["mcp"] is True
    assert current["future"] == original["future"] and current["global"] == {"future": 3, "new": 4}
    assert current["tool_configs"]["other"] == {"private": "synthetic-secret", "new": "Row-Bot ⚡"}
    assert len(set(publications)) == 3
    assert "Row-Bot ⚡".encode() in configuration.configuration_path().read_bytes()


def test_concurrent_legacy_setters_preserve_independent_fields(owner):
    registry, _ = owner
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(registry.set_enabled, "mcp", True),
            pool.submit(registry.set_tool_config, "other", "new", 2),
            pool.submit(registry.set_global_config, "new", 3)]
        for future in futures:
            future.result()
    current = configuration.read_saved().document
    assert current["tools"]["mcp"] is True and current["tools"]["other"] is True
    assert current["tool_configs"]["other"] == {"private": "synthetic-secret", "new": 2}
    assert current["global"] == {"future": 3, "new": 3}


@pytest.mark.parametrize("raw", ['{"tools":{"mcp":true},"tools":{}}', '{"tools":null}', '{"tools":{"mcp":NaN}}', 'corrupt'])
def test_corruption_refuses_writes_and_never_advances_cached_authority(owner, raw):
    registry, _ = owner
    path = configuration.configuration_path()
    path.write_text(raw, encoding="utf-8")
    assert controls.read_native_mcp_state().availability == "unavailable"
    with pytest.raises(configuration.ToolConfigurationError):
        registry.set_enabled("mcp", True)
    assert path.read_text(encoding="utf-8") == raw and registry._enabled["mcp"] is False


def test_stale_saved_snapshot_and_registration_replacement_refuse_before_effect(owner):
    registry, _ = owner
    first = command()
    registry.set_global_config("changed", True)
    with pytest.raises(controls.NativeMcpError, match="revision_conflict"):
        execute(first)
    second = command()
    registry._tools["mcp"] = object()
    with pytest.raises(controls.NativeMcpError, match="revision_conflict"):
        execute(second)
    assert registry._enabled["mcp"] is False


def test_final_authority_revocation_preserves_saved_state(owner):
    registry, _ = owner
    before = configuration.configuration_path().read_bytes()
    calls = []
    def approval(_):
        calls.append(1)
        if len(calls) == 3:
            raise ValueError("revoked")
    with pytest.raises(ValueError, match="revoked"):
        execute(command(), validate_review=approval)
    assert len(calls) == 3 and configuration.configuration_path().read_bytes() == before
    assert registry._enabled["mcp"] is False


def test_original_retry_confirms_owned_publication_without_resaving(owner, monkeypatch):
    from row_bot.runtime import admissions
    value = command()
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("response lost")))
    assert execute(value)["status"] == "partial"
    assert controls.read_native_mcp_state().availability == "recovery_required"
    with pytest.raises(configuration.ToolConfigurationError, match="recovery_required"):
        owner[0].set_global_config("new", 1)
    saved = configuration.read_saved()
    monkeypatch.setattr(admissions, "complete_command", complete)
    monkeypatch.setattr(configuration, "publish_saved", lambda *_a, **_k: pytest.fail("Original replay resaved file"))
    assert execute(value)["status"] == "completed"
    assert execute(value)["status"] == "completed"
    assert configuration.read_saved().identity == saved.identity


def test_equal_replacement_bytes_never_become_owned_publication(owner, monkeypatch):
    from row_bot.runtime import admissions
    value = command()
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("response lost")))
    assert execute(value)["status"] == "partial"
    path = configuration.configuration_path()
    replacement = path.with_name("replacement.json")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    monkeypatch.setattr(admissions, "complete_command", complete)
    assert execute(value)["status"] == "partial"


def test_uncertain_cache_invalidation_retries_original_without_file_write(owner, monkeypatch):
    value = command()
    invalidate = controls._invalidate_loaded
    monkeypatch.setattr(controls, "_invalidate_loaded", lambda: (_ for _ in ()).throw(RuntimeError("cache")))
    assert execute(value)["status"] == "partial"
    owner[0]._enabled["mcp"] = False
    monkeypatch.setattr(controls, "_invalidate_loaded", invalidate)
    monkeypatch.setattr(configuration, "publish_saved", lambda *_a, **_k: pytest.fail("Recovery published again"))
    assert execute(value)["status"] == "completed"
    assert owner[0]._enabled["mcp"] is True


def test_missing_publication_checkpoint_never_retries_effect(owner, monkeypatch):
    from row_bot.runtime import admissions
    original = admissions.command_progress
    def fault(owner_id, key, value):
        if value.get("_native_tool_configuration", {}).get("publication"):
            raise OSError("checkpoint")
        return original(owner_id, key, value)
    monkeypatch.setattr(admissions, "command_progress", fault)
    before = configuration.configuration_path().read_bytes()
    value = command()
    assert execute(value)["status"] == "partial"
    assert configuration.configuration_path().read_bytes() == before
    monkeypatch.setattr(admissions, "command_progress", original)
    assert execute(value)["status"] == "partial"


def test_cold_read_never_constructs_tools_or_creates_data(tmp_path):
    target = tmp_path / "missing"
    script = '''
import pathlib,sys
def forbidden(*args,**kwargs): raise AssertionError("directory mutation")
pathlib.Path.mkdir=forbidden
from row_bot.application.native_mcp_controls import read_native_mcp_state
state=read_native_mcp_state()
assert state.availability=="registration_unavailable" and state.saved_enabled is False
assert not any(name in sys.modules for name in ("row_bot.tools", "row_bot.tasks", "row_bot.mcp_client.runtime", "row_bot.secret_store"))
assert not pathlib.Path(sys.argv[1]).exists()
'''
    result = subprocess.run([sys.executable, "-c", script, str(target)], capture_output=True, text=True, timeout=20,
        env={**os.environ, "ROW_BOT_DATA_DIR": str(target)}, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate stream metadata")
def test_native_and_legacy_writes_preserve_alternate_stream_by_prewrite_refusal(owner):
    path = configuration.configuration_path()
    stream = Path(str(path) + ":retained-test-stream")
    stream.write_bytes(b"retained synthetic stream")
    before, identity = path.read_bytes(), path.stat().st_ino
    for action in (lambda: execute(command()), lambda: owner[0].set_enabled("mcp", True)):
        with pytest.raises(ValueError, match="file_metadata_unavailable"):
            action()
        assert path.read_bytes() == before and path.stat().st_ino == identity
        assert stream.read_bytes() == b"retained synthetic stream" and owner[0]._enabled["mcp"] is False


@pytest.mark.parametrize("value", [None, 0, "true", [], {}])
def test_native_toggle_requires_exact_boolean(owner, value):
    request = command()
    request["payload"]["enabled"] = value
    with pytest.raises(controls.NativeMcpError, match="invalid_command"):
        execute(request)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("revocation", ["disable", "replace", "scope"])
@pytest.mark.parametrize("collector", [False, True])
def test_retained_toolnode_preserves_schema_but_refuses_revoked_native_effect(owner, monkeypatch, tmp_path, asynchronous, revocation, collector):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.prebuilt import ToolNode
    from langgraph.runtime import Runtime
    from row_bot.mcp_client import runtime
    registry, _ = owner
    effects = []
    def effect(value: str = "original") -> str:
        """Read synthetic data."""
        effects.append(value)
        return value
    async def async_effect(value: str = "original") -> str:
        return effect(value)
    original = StructuredTool.from_function(effect, coroutine=async_effect, name="mcp_synthetic_read",
        metadata={"source": "mcp", "requires_approval": True}, tags=["synthetic"])
    snapshots = []
    def tools_snapshot(**kwargs):
        snapshots.append(kwargs)
        return [original]
    monkeypatch.setattr(runtime, "get_langchain_tools", tools_snapshot)
    assert execute(command())["status"] == "completed"
    if collector:
        from row_bot import agent
        from row_bot.plugins import registry as plugins
        monkeypatch.setattr(runtime, "get_destructive_tool_names", lambda **_k: set())
        monkeypatch.setattr(plugins, "get_langchain_tools", lambda **_k: [])
        monkeypatch.setattr(plugins, "get_enabled_plugin_tool_records", lambda: [])
        monkeypatch.setattr(plugins, "get_destructive_names", lambda **_k: set())
        _, external, _ = agent._collect_agent_tool_candidates(["mcp"], {"mcp"})
        bound = external[0]["tool"]
        assert snapshots == [{"allow_names": {"mcp"}, "refresh": False}]
    else:
        bound = registry.get_langchain_tools()[0]
    assert bound.args_schema == original.args_schema and bound.metadata == original.metadata and bound.tags == original.tags
    import inspect
    assert inspect.signature(bound.func) == inspect.signature(original.func)
    assert inspect.signature(bound.coroutine) == inspect.signature(original.coroutine)
    node = ToolNode([bound], handle_tool_errors=False)
    message = {"messages": [AIMessage(content="", tool_calls=[{"id": "synthetic", "name": bound.name,
        "args": {"value": "requested"}, "type": "tool_call"}])]}
    invocation = {"configurable": {"__pregel_runtime": Runtime()}}
    if asynchronous:
        asyncio.run(node.ainvoke(message, invocation))
    else:
        node.invoke(message, invocation)
    assert effects == ["requested"]
    if revocation == "disable":
        assert execute(command(False))["status"] == "completed"
    elif revocation == "replace":
        from row_bot.tools.mcp_tool import McpTool
        registry.register(McpTool())
    else:
        monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "new-scope"))
    with pytest.raises(RuntimeError, match="Native MCP capability was revoked"):
        if asynchronous:
            asyncio.run(node.ainvoke(message, invocation))
        else:
            node.invoke(message, invocation)
    assert effects == ["requested"]


@pytest.mark.skipif(os.name != "nt", reason="Windows restricted DACL semantics")
def test_restricted_windows_configuration_keeps_permissions_before_write(owner):
    import ctypes
    from ctypes import wintypes
    from row_bot.developer.edits import _windows_edit_metadata
    path = configuration.configuration_path()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    set_security = advapi.SetFileSecurityW
    set_security.argtypes, set_security.restype = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    assert convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None), ctypes.get_last_error()
    try:
        assert set_security(str(path), 4 | 0x80000000, descriptor), ctypes.get_last_error()
    finally:
        kernel.LocalFree(descriptor)
    metadata, before, identity = _windows_edit_metadata(path), path.read_bytes(), path.stat().st_ino
    with pytest.raises(ValueError, match="file_metadata_unavailable"):
        execute(command())
    assert path.read_bytes() == before and path.stat().st_ino == identity
    assert _windows_edit_metadata(path) == metadata and owner[0]._enabled["mcp"] is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX extended attribute semantics")
def test_posix_configuration_extended_attributes_are_not_discarded(owner):
    path = configuration.configuration_path()
    before = path.read_bytes()
    os.setxattr(path, "user.row_bot_native_review", b"retained")
    with pytest.raises(ValueError, match="file_metadata_unavailable"):
        execute(command())
    assert path.read_bytes() == before and os.getxattr(path, "user.row_bot_native_review") == b"retained"


def test_in_place_edit_after_snapshot_is_not_confirmed_as_original_publication(owner, monkeypatch):
    from row_bot.runtime import admissions
    value = command()
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("lost")))
    assert execute(value)["status"] == "partial"
    saved = configuration.read_saved()
    private = admissions.receipt("synthetic-owner", value["command_id"])["_native_tool_configuration"]
    changed = saved.document
    changed["tools"]["mcp"] = False
    configuration.configuration_path().write_text(json.dumps(changed), encoding="utf-8")
    assert not configuration.confirmed_publication(saved, private["publication"], owner_id="synthetic-owner",
        key=value["command_id"], command_id=value["command_id"])
    monkeypatch.setattr(admissions, "complete_command", complete)
    assert execute(value)["status"] == "partial"


def test_atomic_publication_failure_does_not_advance_legacy_cache(owner, monkeypatch):
    from row_bot.developer import edits
    before = configuration.configuration_path().read_bytes()
    monkeypatch.setattr(edits, "_rename_edit_no_replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("retirement failed")))
    with pytest.raises(ValueError, match="file_publication_incomplete"):
        owner[0].set_enabled("mcp", True)
    assert owner[0]._enabled["mcp"] is False
    assert configuration.configuration_path().read_bytes() == before
    assert controls.read_native_mcp_state().availability == "recovery_required"


def test_concurrent_original_requests_publish_once(owner, monkeypatch):
    original = configuration.publish_saved
    effects = []
    def publish(*args, **kwargs):
        effects.append(kwargs["command_id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(configuration, "publish_saved", publish)
    value = command()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(execute, [value, value]))
    assert all(result["status"] == "completed" for result in results)
    assert effects == [value["command_id"]]


def test_replacement_cannot_receive_old_uncertain_cache_mutation(owner, monkeypatch):
    value = command()
    monkeypatch.setattr(controls, "_invalidate_loaded", lambda: (_ for _ in ()).throw(OSError("cache")))
    assert execute(value)["status"] == "partial"
    owner[0]._tools["mcp"] = object()
    owner[0]._enabled["mcp"] = False
    monkeypatch.setattr(controls, "_invalidate_loaded", lambda: None)
    assert execute(value)["status"] == "partial"
    assert owner[0]._enabled["mcp"] is False
    # A later app-owned registration loading the saved state may be observed.
    owner[0]._enabled["mcp"] = True
    assert execute(value)["status"] == "completed"


def test_unloaded_registration_read_is_passive_and_write_is_rejected(owner):
    owner[0]._tools.clear()
    state = controls.read_native_mcp_state()
    assert state.availability == "registration_unavailable" and state.effective_enabled is None
    before = configuration.configuration_path().read_bytes()
    with pytest.raises(controls.NativeMcpError, match="native_mcp_unavailable"):
        execute(command())
    assert configuration.configuration_path().read_bytes() == before
