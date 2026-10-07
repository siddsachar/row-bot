"""Actual temporary MCP Test ownership, saved catalogs and no retest on acceptance."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import capability_catalog_controls as controls
from row_bot.application import capability_configuration_controls as configuration
from row_bot.application import capability_runtime_controls as lifecycle
from row_bot.mcp_client import config
from row_bot.mcp_client.safety import schema_digest
from row_bot.runtime import admissions

pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks, tool_configuration
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(tool_configuration, "configuration_path", lambda: tmp_path / "tools_config.json")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", None)
    for name in ("_servers", "_catalog", "_statuses", "_stderr_tails"):
        monkeypatch.setattr(runtime, name, {})
    monkeypatch.setattr(runtime, "_loop", None)
    monkeypatch.setattr(runtime, "_thread", None)
    monkeypatch.setattr(runtime, "sdk_available", lambda: True)
    monkeypatch.setattr(config, "clear_agent_cache_if_loaded", lambda: None)
    calls, tools = [], [{"name": "get_record", "description": "Read synthetic data", "inputSchema": {"type": "object"}},
        {"name": "delete_record", "description": "Delete synthetic data", "inputSchema": {}},
        {"name": "unrecognized", "description": "Unknown operation", "inputSchema": {}}]
    async def list_tools():
        calls.append("list_tools")
        return SimpleNamespace(tools=tools)
    async def connect(server):
        calls.append("connect")
        server.session = SimpleNamespace(list_tools=list_tools)
    monkeypatch.setattr(runtime.McpServerRuntime, "_connect", connect)
    document = {"enabled": False, "future": {"keep": 1}, "servers": {"Synthetic": {
        "enabled": False, "command": "synthetic", "env": {"PRIVATE": "synthetic-secret"}, "future": [1],
        "tools": {"future": {"keep": 2}}}}}
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    yield SimpleNamespace(runtime=runtime, calls=calls, tools=tools, document=document)
    runtime.shutdown()


def test_request():
    page = configuration.read_mcp_configuration()
    return {"type": "mcp.runtime.control", "command_id": str(uuid4()), "expected_revision": "0",
        "payload": {"resource_revision": page.revision, "server_id": page.items[0].server_id,
                    "operation": "test", "expected_runtime_id": None}}


test_request.__test__ = False


def run_test():
    request = test_request()
    result = lifecycle.execute_mcp_runtime_command(owner_id="synthetic-owner", key=request["command_id"], command=request,
        validate=lambda: None, validate_review=lambda _: None)
    assert result["status"] == "completed" and result["mcp_runtime"]["state"] == "tested", result
    return request


def tools(request, owner_id="synthetic-owner"):
    return {row["name"]: row for row in controls.tested_tools(owner_id=owner_id,
        server_id=request["payload"]["server_id"], test_command_id=request["command_id"])}


def command(test):
    return {"command_id": str(uuid4()), "type": "mcp.catalog.accept", "expected_revision": "0",
        "payload": {"configuration_revision": configuration.read_mcp_configuration().revision,
            "server_id": test["payload"]["server_id"], "test_command_id": test["command_id"]}}


def execute(request, **callbacks):
    return controls.execute_mcp_catalog_command(owner_id=callbacks.get("owner_id", "synthetic-owner"),
        key=request["command_id"], command=request, validate=callbacks.get("validate", lambda: None),
        validate_review=callbacks.get("validate_review", lambda _: None))


def test_actual_test_metadata_survives_cleanup_and_explicit_acceptance_never_retests(owner, monkeypatch):
    before = config.CONFIG_PATH.read_bytes()
    tested = run_test()
    assert config.CONFIG_PATH.read_bytes() == before and not owner.runtime._servers and not owner.runtime._catalog
    rows = tools(tested)
    assert len(rows) == 3 and not rows["get_record"]["requires_approval"]
    assert rows["delete_record"]["requires_approval"] and rows["unrecognized"]["requires_approval"]
    request = command(tested)
    reviewed = controls.review_mcp_catalog_command(owner_id="synthetic-owner", **request["payload"], validate=lambda: None)
    assert reviewed["tool_count"] == 3
    monkeypatch.setattr(owner.runtime, "launch_server_owned", lambda *_a, **_k: pytest.fail("Acceptance retested server"))
    saved = execute(request)
    assert saved["status"] == "completed" and saved["mcp_configuration"]["saved_disabled"] is None
    assert saved["mcp_configuration"]["runtime_cleanup"] == "not_requested"
    current = config.read_saved_configuration().document
    assert current["enabled"] is False and current["servers"]["Synthetic"]["enabled"] is False
    assert current["servers"]["Synthetic"]["tools"]["enabled"] == {"get_record": True, "delete_record": False, "unrecognized": False}
    assert current["future"] == owner.document["future"]
    assert current["servers"]["Synthetic"]["env"] == owner.document["servers"]["Synthetic"]["env"]
    assert current["servers"]["Synthetic"]["tools"]["future"] == {"keep": 2}
    assert current["servers"]["Synthetic"]["tools"]["catalog"]["get_record"]["input_schema_digest"] == schema_digest({"type": "object"})
    assert owner.calls == ["connect", "list_tools"]
    assert execute(request) == saved
    assert "synthetic-secret" not in json.dumps([rows, reviewed, saved])


def test_existing_explicit_choices_and_unknown_fields_survive_acceptance(owner):
    tools = owner.document["servers"]["Synthetic"]["tools"]
    tools.update(enabled={"get_record": False, "delete_record": True, "older": True},
        require_approval=["get_record"], catalog={"get_record": {"future": 7}, "older": {"description": "Saved older tool"}})
    config.CONFIG_PATH.write_text(json.dumps(owner.document), encoding="utf-8")
    tested = run_test()
    assert execute(command(tested))["status"] == "completed"
    current = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]
    assert current["enabled"] == {"get_record": False, "delete_record": True, "older": True, "unrecognized": False}
    assert current["catalog"]["get_record"]["future"] == 7 and "older" in current["catalog"]
    assert {"get_record", "delete_record", "unrecognized"}.issubset(current["require_approval"])


def test_an_overlap_with_row_bot_is_a_note_and_risky_tools_still_ask(owner):
    owner.document["servers"]["Synthetic"]["source"] = {"overlaps_native": ["memory"]}
    config.CONFIG_PATH.write_text(json.dumps(owner.document), encoding="utf-8")
    tested = run_test()
    rows = tools(tested)
    assert rows["delete_record"]["requires_approval"] and rows["unrecognized"]["requires_approval"]
    assert execute(command(tested))["status"] == "completed"
    enabled = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]["enabled"]
    assert enabled["get_record"] is True  # Lookups are not held back by the overlap.


def test_changed_configuration_expires_test_capture_without_retest(owner):
    tested = run_test()
    changed = config.read_saved_configuration().document
    changed["future"] = "changed"
    config.CONFIG_PATH.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(controls.Error, match="mcp_catalog_stale"):
        execute(command(tested))
    assert owner.calls == ["connect", "list_tools"]


def test_other_owner_and_unknown_original_cannot_accept_tested_metadata(owner):
    tested = run_test()
    with pytest.raises(controls.Error, match="mcp_catalog_unavailable"):
        execute(command(tested), owner_id="other")
    unknown = {**tested, "command_id": str(uuid4())}
    with pytest.raises(controls.Error, match="mcp_catalog_unavailable"):
        tools(unknown)


@pytest.mark.parametrize("kind", ["count", "bytes", "duplicate-runtime-name"])
def test_unavailable_catalog_never_blocks_actual_test_transport_cleanup(owner, kind):
    if kind == "count":
        owner.tools[:] = [{"name": f"get_{index}"} for index in range(1001)]
    elif kind == "bytes":
        owner.tools[:] = [{"name": f"get_{index}", "description": "x" * 16384} for index in range(12)]
    else:
        owner.tools[:] = [{"name": "get-a"}, {"name": "get_a"}]
    tested = run_test()
    assert not owner.runtime._servers
    with pytest.raises(controls.Error, match="mcp_catalog_unavailable"):
        tools(tested)
    receipt = admissions.read_command_receipt("synthetic-owner", tested["command_id"])
    assert len(json.dumps(receipt).encode()) < 256 * 1024
    assert owner.calls == ["connect", "list_tools"]




def test_original_acceptance_reconciles_lost_response_without_repeat_save(owner, monkeypatch):
    tested = run_test()
    request = command(tested)
    progress = admissions.command_progress
    def lost(owner_id, key, receipt):
        if receipt.get("status") == "completed":
            raise OSError("lost completion")
        return progress(owner_id, key, receipt)
    monkeypatch.setattr(admissions, "command_progress", lost)
    with pytest.raises(OSError, match="lost completion"):
        execute(request)
    saved = config.read_saved_configuration()
    monkeypatch.setattr(admissions, "command_progress", progress)
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("Recovery resaved catalog"))
    assert execute(request)["status"] == "completed"
    assert config.read_saved_configuration().identity == saved.identity
    assert owner.calls == ["connect", "list_tools"]








def test_approval_is_rechecked_at_final_publication_authority(owner):
    tested = run_test()
    request = command(tested)
    before = config.CONFIG_PATH.read_bytes()
    approvals = []
    def validate_review(review):
        approvals.append(review)
        if len(approvals) == 3:
            raise PermissionError("expired")
    result = execute(request, validate_review=validate_review)
    assert result["status"] == "partial"
    assert len(approvals) == 3 and all(review == approvals[0] for review in approvals)
    assert config.CONFIG_PATH.read_bytes() == before
    assert owner.calls == ["connect", "list_tools"]


def test_checkpoint_failure_keeps_config_and_does_not_blindly_reaccept(owner, monkeypatch):
    tested = run_test()
    request = command(tested)
    before = config.CONFIG_PATH.read_bytes()
    original = admissions.command_progress
    def fail_proof(owner_id, key, receipt):
        if receipt.get("_mcp_configuration", {}).get("publication"):
            raise OSError("checkpoint")
        return original(owner_id, key, receipt)
    monkeypatch.setattr(admissions, "command_progress", fail_proof)
    assert execute(request)["status"] == "partial"
    monkeypatch.setattr(admissions, "command_progress", original)
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("Blind retry"))
    assert execute(request)["status"] == "partial"
    assert config.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize("change", ["operation", "scope", "proof", "metadata", "missing_capture"])
def test_inconsistent_retained_test_is_not_accepted(owner, change):
    tested = run_test()
    receipt = admissions.receipt("synthetic-owner", tested["command_id"])
    private = receipt["_mcp_runtime"]
    if change == "operation":
        private["operation"] = "connect"
    elif change == "scope":
        private["server_id"] = "b" * 64
    elif change == "proof":
        private["completion"]["operation"] = "disconnect"
    elif change == "metadata":
        private["tested_catalog"]["tools"][0]["requires_approval"] = "false"
    else:
        private.pop("configuration_digest")
    admissions.complete_command("synthetic-owner", tested["command_id"], receipt)
    with pytest.raises(controls.Error):
        tools(tested)
    with pytest.raises(controls.Error):
        execute(command(tested))


def test_required_approval_cannot_be_lowered_by_new_catalog_metadata(owner):
    owner.document["servers"]["Synthetic"]["tools"].update(catalog={"get_record": {
        "description": "Read", "destructive": True, "requires_approval": True}}, enabled={"get_record": True})
    config.CONFIG_PATH.write_text(json.dumps(owner.document), encoding="utf-8")
    tested = run_test()
    reviewed = tools(tested)["get_record"]
    assert reviewed["requires_approval"] is True and reviewed["destructive"] is True
    assert execute(command(tested))["status"] == "completed"
    current = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]
    assert current["catalog"]["get_record"]["destructive"] is True
    assert current["catalog"]["get_record"]["requires_approval"] is True
    assert "get_record" in current["require_approval"]


@pytest.mark.parametrize("change", ["added", "schema", "description", "removed_returned"])
def test_remote_catalog_change_requires_renewed_acceptance_before_exposure(owner, monkeypatch, change):
    tested = run_test()
    assert execute(command(tested))["status"] == "completed"
    accepted = config.read_saved_configuration().document
    accepted["enabled"] = True
    accepted["servers"]["Synthetic"]["enabled"] = True
    config.CONFIG_PATH.write_text(json.dumps(accepted), encoding="utf-8")
    if change == "added":
        owner.tools.append({"name": "get_new_record", "description": "Read newly deployed records", "inputSchema": {}})
        name = "get_new_record"
    elif change == "schema":
        owner.tools[0]["inputSchema"] = {"type": "object", "properties": {"destination": {"type": "string"}}}
        name = "get_record"
    elif change == "description":
        owner.tools[0]["description"] = "Read records from a newly selected destination"
        name = "get_record"
    else:
        # A saved preference cannot reactivate a tool removed from the last accepted deployment.
        accepted["servers"]["Synthetic"]["tools"]["accepted_names"].remove("get_record")
        config.CONFIG_PATH.write_text(json.dumps(accepted), encoding="utf-8")
        name = "get_record"
    cfg = config.read_saved_configuration().document
    monkeypatch.setattr(owner.runtime, "_get_effective_config", lambda: cfg)
    owner.runtime._catalog["Synthetic"] = owner.runtime._normalize_tools("Synthetic", cfg["servers"]["Synthetic"], owner.tools)
    tools = owner.runtime.get_langchain_tools(refresh=False)
    assert not any(tool.name.endswith("_" + name) for tool in tools)
    assert next(row for row in owner.runtime.get_catalog_snapshot()["Synthetic"] if row["name"] == name)["enabled"] is False
    # The next explicit test/acceptance records exactly these capabilities.
    owner.runtime._catalog.clear()
    tested = run_test()
    assert execute(command(tested))["status"] == "completed"
    cfg = config.read_saved_configuration().document
    owner.runtime._catalog["Synthetic"] = owner.runtime._normalize_tools("Synthetic", cfg["servers"]["Synthetic"], owner.tools)
    assert any(tool.name.endswith("_" + name) for tool in owner.runtime.get_langchain_tools(refresh=False))


def _policy_rows(tested):
    from row_bot.application import capability_policy_controls as policy
    return {row.name: row for row in policy.read_mcp_policy(server_id=tested["payload"]["server_id"]).items}


def test_annotations_reach_the_saved_catalog_so_read_only_tools_run_and_destructive_ones_stay_locked(owner):
    """B307: what a server declares about its tools is weighed after acceptance too, not just while testing."""
    owner.tools[:] = [
        {"name": "check_stock", "description": "Execute a stock check", "inputSchema": {},
         "annotations": {"readOnlyHint": True, "title": "Check stock"}},
        {"name": "lookup", "description": "Look a record up", "inputSchema": {}, "annotations": {"destructiveHint": True}},
        {"name": "save_purchase_orders", "description": "Save purchase orders", "inputSchema": {},
         "annotations": {"readOnlyHint": False}}]
    tested = run_test()
    found = tools(tested)
    assert found["check_stock"]["effect"] == "read_only" and not found["check_stock"]["requires_approval"]
    assert found["lookup"]["destructive"] and found["save_purchase_orders"]["effect"] == "mutation"
    assert execute(command(tested))["status"] == "completed"
    saved = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]
    assert saved["catalog"]["check_stock"]["annotations"] == {"readOnlyHint": True}  # Only the hints Row-Bot weighs.
    assert saved["enabled"] == {"check_stock": True, "lookup": False, "save_purchase_orders": False}
    rows = _policy_rows(tested)
    assert rows["check_stock"].enabled is True and rows["check_stock"].requires_approval is False
    assert rows["check_stock"].approval_locked is False
    assert rows["lookup"].approval_locked is True and rows["lookup"].destructive is True
    assert rows["save_purchase_orders"].requires_approval is True and rows["save_purchase_orders"].approval_locked is False


def test_a_hint_the_server_drops_does_not_linger_to_relax_its_tool(owner):
    owner.tools[:] = [{"name": "check_stock", "description": "Check stock", "inputSchema": {},
                       "annotations": {"readOnlyHint": True}}]
    assert execute(command(run_test()))["status"] == "completed"
    del owner.tools[0]["annotations"]
    tested = run_test()
    assert execute(command(tested))["status"] == "completed"
    saved = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]["catalog"]["check_stock"]
    assert "annotations" not in saved
    assert _policy_rows(tested)["check_stock"].approval_locked is True  # Unknown again: it always asks.


def test_a_deeply_nested_schema_is_kept_as_its_digest_and_never_grows_the_record(owner):
    schema = {}
    for _ in range(30):
        schema = {"nested": schema}
    owner.tools[0]["inputSchema"] = schema
    tested = run_test()
    assert tools(tested)[owner.tools[0]["name"]]["input_schema_digest"] == schema_digest(schema)
    assert len(json.dumps(admissions.read_command_receipt("synthetic-owner", tested["command_id"])).encode()) < 256 * 1024


def test_tools_with_very_large_schemas_can_be_accepted_and_only_their_digests_are_kept(owner):
    """Notion's tools carry about 170 KB of input schemas: what is agreed to stays exact without keeping them."""
    big = {"type": "object", "properties": {f"field_{n}": {"type": "string", "description": "d" * 200} for n in range(300)}}
    owner.tools[:] = [{"name": f"get_{n}", "description": "Read records", "inputSchema": big} for n in range(3)]
    tested = run_test()
    assert set(tools(tested)) == {"get_0", "get_1", "get_2"}
    assert execute(command(tested))["status"] == "completed"
    saved = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]["catalog"]
    assert all(row["input_schema_digest"] == schema_digest(big) and "input_schema" not in row for row in saved.values())
