"""Saved MCP authorization uses canonical files/receipts and no live services."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
import asyncio
from dataclasses import asdict
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot.application import capability_configuration_controls as configuration
from row_bot.application import capability_policy_controls as controls
from row_bot.mcp_client import config

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", None)
    monkeypatch.setattr(config, "clear_agent_cache_if_loaded", lambda: None)
    data = {"version": 1, "enabled": True, "future": {"retained": [1]}, "servers": {"Synthetic": {
        "command": "synthetic", "enabled": True, "future": {"retained": 2}, "env": {"KEY": "synthetic-private-secret"},
        "tools": {"enabled": {"read": True, "delete_record": False}, "catalog": {
            "read": {"description": "Read synthetic records", "destructive": False},
            "delete_record": {"description": "Delete a synthetic record", "destructive": True}},
            "resources_enabled": True, "prompts_enabled": True, "future": {"retained": 3}}}}}
    config.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")
    return data


def target():
    return configuration._server_id("Synthetic")


def command(operation="server_enabled", enabled=False, **fields):
    intent = {"operation": operation, "enabled": enabled, **fields}
    if operation != "global_enabled":
        intent["server_id"] = target()
    return {"command_id": str(uuid4()), "type": "mcp.configuration.control", "expected_revision": "0",
        "payload": {"configuration_revision": controls.read_mcp_policy().revision, "intent": intent}}


def execute(value, **callbacks):
    return controls.execute_mcp_policy_command(owner_id="synthetic-owner", key=value["command_id"], command=value,
        validate=callbacks.get("validate", lambda: None), validate_review=callbacks.get("validate_review", lambda _: None))


@pytest.mark.parametrize("operation,fields", [
    ("global_enabled", {}), ("server_enabled", {}),
    ("tool_enabled", {"tool_id": controls._tool_id(target(), "read")}),
    ("tool_approval", {"tool_id": controls._tool_id(target(), "read")}),
    ("utility_enabled", {"utility": "resources"}), ("utility_enabled", {"utility": "prompts"})])
def test_each_saved_policy_control_preserves_unknowns_and_never_runs_connections(owner, monkeypatch, operation, fields):
    from row_bot.mcp_client import runtime
    for name in ("discover_enabled_servers", "stop_server", "shutdown", "refresh_server", "probe_server", "_schedule"):
        monkeypatch.setattr(runtime, name, lambda *_a, **_k: pytest.fail("Saved policy must not run a connection effect"))
    value = command(operation, enabled=operation == "tool_approval", **fields)
    reviewed = controls.review_mcp_policy_command(**value["payload"], validate=lambda: None)
    result = execute(value)
    assert result["status"] == "completed" and result["mcp_configuration"]["saved_disabled"] is None
    assert result["mcp_configuration"]["runtime_cleanup"] == "not_requested"
    assert reviewed["saved_disabled"] is None and "synthetic-private-secret" not in json.dumps([result, reviewed])
    current = config.read_saved_configuration().document
    assert current["future"] == owner["future"]
    assert current["servers"]["Synthetic"]["future"] == owner["servers"]["Synthetic"]["future"]
    assert current["servers"]["Synthetic"]["tools"]["future"] == {"retained": 3}
    assert current["servers"]["Synthetic"]["env"] == owner["servers"]["Synthetic"]["env"]
    page = controls.read_mcp_policy(server_id=target())
    if operation == "global_enabled":
        assert page.global_enabled is False
    elif operation == "server_enabled":
        assert page.server_enabled is False
    elif operation == "tool_enabled":
        assert next(row for row in page.items if row.name == "read").enabled is False
    elif operation == "tool_approval":
        assert next(row for row in page.items if row.name == "read").requires_approval is True
    elif fields["utility"] == "resources":
        assert page.resources_enabled is False
    else:
        assert page.prompts_enabled is False


@pytest.mark.parametrize("operation,fields", [
    ("global_enabled", {}), ("server_enabled", {}),
    ("tool_enabled", {"tool_id": controls._tool_id(target(), "read")}),
    ("utility_enabled", {"utility": "resources"}), ("utility_enabled", {"utility": "prompts"})])
def test_saved_revocation_blocks_actual_bound_dispatch_before_schedule(owner, monkeypatch, operation, fields):
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(runtime, "_get_effective_config", config.get_config)
    monkeypatch.setattr(runtime, "_catalog", {"Synthetic": {"read": runtime.McpToolInfo("Synthetic", "read", "mcp_synthetic_read", enabled=True)}})
    monkeypatch.setattr(runtime, "_servers", {"Synthetic": runtime.McpServerRuntime("Synthetic", {})})
    monkeypatch.setattr(runtime, "_schedule", lambda *_a: pytest.fail("Revoked operation reached scheduling"))
    if operation == "utility_enabled":
        # Utility authority is deliberately checked inside its scheduled async
        # operation; execute only that fake loop, never a physical resource call.
        def schedule(coroutine):
            result = Future()
            try:
                result.set_result(asyncio.run(coroutine))
            except Exception as error:
                result.set_exception(error)
            return result
        monkeypatch.setattr(runtime, "_schedule", schedule)
        async def forbidden(*_args):
            pytest.fail("Revoked utility reached physical session")
        monkeypatch.setattr(runtime.McpServerRuntime, "list_resources", forbidden)
        monkeypatch.setattr(runtime.McpServerRuntime, "list_prompts", forbidden)
        factory = runtime._make_resource_list_func if fields["utility"] == "resources" else runtime._make_prompt_list_func
        bound = factory("Synthetic", enforce_policy=True)
    else:
        bound = runtime._make_tool_func("Synthetic", "read", enforce_policy=True)
    assert execute(command(operation, **fields))["status"] == "completed"
    with pytest.raises(RuntimeError):
        bound()


@pytest.mark.parametrize("metadata", [{"destructive": True}, {"destructive": None}, {"requires_approval": True}, {"requires_approval": "false"}, {"description": None}])
def test_destructive_or_unknown_safety_metadata_cannot_lower_approval(owner, metadata):
    owner["servers"]["Synthetic"]["tools"]["catalog"]["read"] = metadata
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    before = config.CONFIG_PATH.read_bytes()
    value = command("tool_approval", tool_id=controls._tool_id(target(), "read"))
    with pytest.raises(controls.Error, match="approval_required"):
        execute(value)
    assert config.CONFIG_PATH.read_bytes() == before


def test_destructive_name_cannot_be_overridden_by_saved_false(owner):
    owner["servers"]["Synthetic"]["tools"]["catalog"]["delete_record"] = {"destructive": False}
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(controls.Error, match="approval_required"):
        execute(command("tool_approval", tool_id=controls._tool_id(target(), "delete_record")))


def test_enabling_excluded_tool_removes_only_its_exclusion(owner):
    owner["servers"]["Synthetic"]["tools"]["exclude"] = ["read", "delete_record"]
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    assert execute(command("tool_enabled", True, tool_id=controls._tool_id(target(), "read")))["status"] == "completed"
    assert config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]["exclude"] == ["delete_record"]
    assert next(row for row in controls.read_mcp_policy(server_id=target()).items if row.name == "read").enabled is True


def test_compatible_read_tool_can_remove_its_optional_approval(owner):
    owner["servers"]["Synthetic"]["tools"]["require_approval"] = ["read", "delete_record"]
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    assert execute(command("tool_approval", tool_id=controls._tool_id(target(), "read")))["status"] == "completed"
    assert config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]["require_approval"] == ["delete_record"]


def test_unknown_effect_retains_mandatory_approval_and_read_default_matches_canonical_owner(owner):
    owner["servers"]["Synthetic"]["tools"]["enabled"] = {}
    owner["servers"]["Synthetic"]["tools"]["catalog"]["opaque_action"] = {"destructive": False}
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    rows = {row.name: row for row in controls.read_mcp_policy(server_id=target()).items}
    assert rows["read"].enabled is True
    assert rows["opaque_action"].enabled is False and rows["opaque_action"].requires_approval is True
    assert rows["opaque_action"].approval_locked is True
    with pytest.raises(controls.Error, match="approval_required"):
        execute(command("tool_approval", tool_id=controls._tool_id(target(), "opaque_action")))


def test_enable_updates_only_target_in_nonempty_include_filter(owner):
    from row_bot.mcp_client import runtime
    owner["servers"]["Synthetic"]["tools"]["include"] = ["delete_record"]
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    assert next(row for row in controls.read_mcp_policy(server_id=target()).items if row.name == "read").enabled is False
    assert execute(command("tool_enabled", True, tool_id=controls._tool_id(target(), "read")))["status"] == "completed"
    current = config.read_saved_configuration().document["servers"]["Synthetic"]
    assert current["tools"]["include"] == ["delete_record", "read"]
    actual = runtime._normalize_tools("Synthetic", current, [{"name": "read"}, {"name": "unrelated_read"}])
    assert actual["read"].enabled is True and "unrelated_read" not in actual


def test_saved_tool_search_is_full_scope_paged_and_cursor_bound(owner):
    owner["servers"]["Synthetic"]["tools"]["enabled"] = {f"read_{i:05}": True for i in range(2001)}
    owner["servers"]["Synthetic"]["tools"]["catalog"] = {}
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    page = controls.read_mcp_policy(server_id=target(), limit=50)
    assert page.total == 2001 and len(page.items) == 50 and page.next_cursor
    found = controls.read_mcp_policy(server_id=target(), query="read_02000")
    assert found.total == 1 and found.items[0].name == "read_02000"
    second = controls.read_mcp_policy(server_id=target(), cursor=page.next_cursor, limit=50)
    assert second.items[0].name == "read_00050"
    with pytest.raises(controls.Error, match="cursor_expired"):
        controls.read_mcp_policy(cursor=page.next_cursor, limit=50)
    execute(command("server_enabled"))
    with pytest.raises(controls.Error, match="cursor_expired"):
        controls.read_mcp_policy(server_id=target(), cursor=page.next_cursor, limit=50)


def test_labels_are_redacted_and_malformed_toggle_is_unknown(owner):
    owner["servers"]["Synthetic"]["tools"]["enabled"] = {"secret-token": True, "sk-second": True, "read": "false"}
    owner["servers"]["Synthetic"]["enabled"] = "true"
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    page = controls.read_mcp_policy(server_id=target())
    serialized = json.dumps(asdict(page))
    assert "secret-token" not in serialized and "sk-second" not in serialized and "synthetic-private-secret" not in serialized
    hidden = [row for row in page.items if row.name.startswith("MCP tool (")]
    assert len(hidden) == 2 and hidden[0].name != hidden[1].name and hidden[0].tool_id != hidden[1].tool_id
    assert page.server_enabled is None and next(row for row in page.items if row.name == "read").enabled is None


def test_corrupt_tool_policy_retains_explicit_publication_recovery_after_global_disable(owner, monkeypatch):
    owner["servers"]["Synthetic"]["tools"] = ["malformed"]
    config.CONFIG_PATH.write_text(json.dumps(owner), encoding="utf-8")
    page = controls.read_mcp_policy(server_id=target())
    assert page.availability == "partial" and page.total is None and not page.items
    value = command("global_enabled")
    # The legacy normalized cache cannot consume this malformed tools object.
    # Publication is preserved and its exact proof remains available for retry.
    assert execute(value)["status"] == "partial"
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("No repeated write"))
    assert execute(value)["status"] == "completed"
    assert config.read_saved_configuration().document["enabled"] is False
    assert config.read_saved_configuration().document["servers"]["Synthetic"]["tools"] == ["malformed"]


def test_original_saved_policy_recovery_never_republishes_after_response_loss(owner, monkeypatch):
    from row_bot.runtime import admissions
    original = admissions.command_progress
    def lose(owner_id, key, value):
        if value.get("status") == "completed":
            raise OSError("synthetic response loss")
        return original(owner_id, key, value)
    monkeypatch.setattr(admissions, "command_progress", lose)
    value = command()
    with pytest.raises(OSError):
        execute(value)
    assert controls.read_mcp_policy().availability == "recovery_required"
    monkeypatch.setattr(admissions, "command_progress", original)
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("No repeated write"))
    result = execute(value, validate_review=lambda _: pytest.fail("No effect approval for receipt recovery"))
    assert result["status"] == "completed" and result["mcp_configuration"]["saved_disabled"] is None
    assert "_mcp_configuration" not in result


def test_stale_policy_and_concurrent_duplicate_commands_do_not_repeat_changes(owner, monkeypatch):
    value = command()
    published = []
    original = config.publish_saved_configuration
    def record(*args, **kwargs):
        published.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(config, "publish_saved_configuration", record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, [value, value]))
    assert results[0] == results[1] and published == [True]
    stale = {**value, "command_id": str(uuid4())}
    with pytest.raises(controls.Error, match="revision_conflict"):
        execute(stale)
    assert published == [True]


def test_m1_interrupted_save_blocks_m2b_and_preserves_source(owner, monkeypatch):
    from row_bot.runtime import admissions
    original = admissions.command_progress
    def lose(owner_id, key, value):
        if value.get("status") == "completed":
            raise OSError("synthetic response loss")
        return original(owner_id, key, value)
    monkeypatch.setattr(admissions, "command_progress", lose)
    m1 = {"command_id": str(uuid4()), "type": "mcp.configuration.save", "payload": {
        "configuration_revision": configuration.read_mcp_configuration().revision,
        "intent": {"operation": "edit", "server_id": target(), "fields": {"args": ["exact"]}}}}
    with pytest.raises(OSError):
        configuration.execute_mcp_configuration_command(owner_id="other-owner", key=m1["command_id"], command=m1,
            validate=lambda: None, validate_review=lambda _: None)
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.McpConfigurationError, match="recovery_required"):
        execute(command())
    assert config.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize("patch", [{"enabled": 1}, {"extra": True}, {"operation": []}, {"server_id": "bad"}, {"operation": "delete"}])
def test_unknown_or_malformed_intent_is_rejected_before_write(owner, patch):
    value = command()
    value["payload"]["intent"].update(patch)
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(controls.Error, match="invalid_command"):
        execute(value)
    assert config.CONFIG_PATH.read_bytes() == before


def test_policy_cold_read_has_no_runtime_import_or_directory_creation(tmp_path):
    script = '''
import pathlib,sys
def forbidden(*a,**k): raise AssertionError("cold policy read mkdir")
pathlib.Path.mkdir=forbidden
from row_bot.application.capability_policy_controls import read_mcp_policy
value=read_mcp_policy()
assert value.availability=="missing" and value.global_enabled is False
assert "row_bot.tasks" not in sys.modules and "row_bot.mcp_client.runtime" not in sys.modules
assert not pathlib.Path(sys.argv[1]).exists()
'''
    target = tmp_path / "missing"
    result = subprocess.run([sys.executable, "-c", script, str(target)],
        env={**os.environ, "ROW_BOT_DATA_DIR": str(target)}, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr
