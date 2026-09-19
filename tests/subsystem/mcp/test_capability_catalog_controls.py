"""Actual temporary MCP Test ownership, saved catalogs and no retest on acceptance."""
from dataclasses import asdict
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import capability_catalog_controls as controls
from row_bot.application import capability_configuration_controls as configuration
from row_bot.application import capability_runtime_controls as lifecycle
from row_bot.mcp_client import config
from row_bot.runtime import admissions

pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", None)
    for name in ("_servers", "_catalog", "_statuses"):
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


def read(request, **query):
    return controls.read_tested_mcp_catalog(owner_id=query.pop("owner_id", "synthetic-owner"),
        server_id=request["payload"]["server_id"], test_command_id=request["command_id"], **query)


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
    page = read(tested)
    assert page.availability == "available" and page.total == 3
    rows = {item.name: item for item in page.items}
    assert rows["get_record"].enabled_after_accept is True
    assert rows["delete_record"].enabled_after_accept is False and rows["delete_record"].requires_approval
    assert rows["unrecognized"].enabled_after_accept is False and rows["unrecognized"].requires_approval
    request = command(tested)
    reviewed = controls.review_mcp_catalog_command(owner_id="synthetic-owner", **request["payload"], validate=lambda: None)
    assert reviewed["tool_count"] == 3 and reviewed["manual_selection_required"] is False
    monkeypatch.setattr(owner.runtime, "launch_server_owned", lambda *_a, **_k: pytest.fail("Acceptance retested server"))
    saved = execute(request)
    assert saved["status"] == "completed" and saved["mcp_configuration"]["saved_disabled"] is None
    assert saved["mcp_configuration"]["runtime_cleanup"] == "not_requested"
    current = config.read_saved_configuration().document
    assert current["enabled"] is False and current["servers"]["Synthetic"]["enabled"] is False
    assert current["future"] == owner.document["future"]
    assert current["servers"]["Synthetic"]["env"] == owner.document["servers"]["Synthetic"]["env"]
    assert current["servers"]["Synthetic"]["tools"]["future"] == {"keep": 2}
    assert current["servers"]["Synthetic"]["tools"]["catalog"]["get_record"]["input_schema"] == {"type": "object"}
    assert owner.calls == ["connect", "list_tools"]
    assert execute(request) == saved
    assert "synthetic-secret" not in json.dumps([asdict(page), reviewed, saved])


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


def test_overlap_requires_explicit_manual_tool_selection(owner):
    owner.document["servers"]["Synthetic"]["source"] = {"overlaps_native": ["memory"]}
    config.CONFIG_PATH.write_text(json.dumps(owner.document), encoding="utf-8")
    tested = run_test()
    assert read(tested).manual_selection_required is True
    assert all(item.enabled_after_accept is False for item in read(tested).items)
    assert execute(command(tested))["status"] == "completed"


def test_changed_configuration_expires_test_capture_without_retest(owner):
    tested = run_test()
    changed = config.read_saved_configuration().document
    changed["future"] = "changed"
    config.CONFIG_PATH.write_text(json.dumps(changed), encoding="utf-8")
    assert read(tested).availability == "stale"
    with pytest.raises(controls.Error, match="mcp_catalog_stale"):
        execute(command(tested))
    assert owner.calls == ["connect", "list_tools"]


def test_other_owner_and_unknown_original_cannot_accept_tested_metadata(owner):
    tested = run_test()
    assert read(tested, owner_id="other").availability == "unavailable"
    with pytest.raises(controls.Error, match="mcp_catalog_unavailable"):
        execute(command(tested), owner_id="other")
    unknown = {**tested, "command_id": str(uuid4())}
    assert read(unknown).total is None


@pytest.mark.parametrize("kind", ["count", "bytes", "depth", "duplicate-runtime-name"])
def test_unavailable_catalog_never_blocks_actual_test_transport_cleanup(owner, kind):
    if kind == "count":
        owner.tools[:] = [{"name": f"get_{index}"} for index in range(1001)]
    elif kind == "bytes":
        owner.tools[:] = [{"name": f"get_{index}", "description": "x" * 16384} for index in range(12)]
    elif kind == "depth":
        schema = {}
        for _ in range(30):
            schema = {"nested": schema}
        owner.tools[0]["inputSchema"] = schema
    else:
        owner.tools[:] = [{"name": "get-a"}, {"name": "get_a"}]
    tested = run_test()
    assert not owner.runtime._servers
    assert read(tested).availability == "unavailable"
    receipt = admissions.read_command_receipt("synthetic-owner", tested["command_id"])
    assert len(json.dumps(receipt).encode()) < 256 * 1024
    assert owner.calls == ["connect", "list_tools"]


def test_full_catalog_filter_and_pages_are_bounded_and_cursor_scope_exact(owner):
    owner.tools[:] = [{"name": f"get_{index:03d}"} for index in range(205)]
    tested = run_test()
    page = read(tested, limit=50)
    assert page.total == 205 and len(page.items) == 50 and page.next_cursor
    names = []
    while True:
        names.extend(item.name for item in page.items)
        if not page.next_cursor:
            break
        page = read(tested, limit=50, cursor=page.next_cursor)
    assert len(names) == len(set(names)) == 205
    filtered = read(tested, query="get_204")
    assert filtered.total == 1 and filtered.items[0].name == "get_204"
    with pytest.raises(controls.Error, match="cursor_expired"):
        read(tested, query="changed", limit=50, cursor=read(tested, limit=50).next_cursor)


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


def test_cold_unknown_test_read_never_initializes_database_runtime_or_data(tmp_path):
    target = tmp_path / "missing"
    script = '''
import pathlib,sys
def forbidden(*args,**kwargs): raise AssertionError("directory mutation")
pathlib.Path.mkdir=forbidden
from row_bot.application.capability_catalog_controls import read_tested_mcp_catalog
page=read_tested_mcp_catalog(owner_id="synthetic",server_id="a"*64,test_command_id="00000000-0000-0000-0000-000000000001")
assert page.availability=="unavailable" and page.total is None
assert not any(name in sys.modules for name in ("row_bot.tasks","row_bot.tools","row_bot.mcp_client.runtime","mcp"))
assert not pathlib.Path(sys.argv[1]).exists()
'''
    completed = subprocess.run([sys.executable, "-c", script, str(target)], capture_output=True, text=True, timeout=20,
        env={**os.environ, "ROW_BOT_DATA_DIR": str(target)}, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert completed.returncode == 0, completed.stderr


def test_saved_test_catalog_can_be_read_after_actual_process_restart(owner):
    tested = run_test()
    script = '''
import pathlib,sys
def forbidden(*args,**kwargs): raise AssertionError("directory mutation")
pathlib.Path.mkdir=forbidden
from row_bot.application.capability_catalog_controls import read_tested_mcp_catalog
page=read_tested_mcp_catalog(owner_id="synthetic-owner",server_id=sys.argv[1],test_command_id=sys.argv[2])
assert page.availability=="available" and page.total==3
assert not any(name in sys.modules for name in ("row_bot.tasks","row_bot.tools","row_bot.mcp_client.runtime","mcp"))
'''
    completed = subprocess.run([sys.executable, "-c", script, tested["payload"]["server_id"], tested["command_id"]],
        capture_output=True, text=True, timeout=20, env={**os.environ, "ROW_BOT_DATA_DIR": str(config.CONFIG_PATH.parent)},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert completed.returncode == 0, completed.stderr
    assert owner.calls == ["connect", "list_tools"]


def test_catalog_public_projection_never_exposes_private_names_schemas_or_descriptions(owner):
    owner.tools[:] = [{"name": "Bearer-synthetic-private", "description": "synthetic-private-description",
        "inputSchema": {"secret": "synthetic-private-schema"}}, {"name": "sk-synthetic-other", "description": ""}]
    tested = run_test()
    public = json.dumps(asdict(read(tested)))
    assert "synthetic-private" not in public and "sk-synthetic" not in public
    assert len({row.name for row in read(tested).items}) == 2
    assert read(tested, query="private").total == 0


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
    assert read(tested).availability in {"unavailable", "stale"}
    with pytest.raises(controls.Error):
        execute(command(tested))


def test_required_approval_cannot_be_lowered_by_new_catalog_metadata(owner):
    owner.document["servers"]["Synthetic"]["tools"].update(catalog={"get_record": {
        "description": "Read", "destructive": True, "requires_approval": True}}, enabled={"get_record": True})
    config.CONFIG_PATH.write_text(json.dumps(owner.document), encoding="utf-8")
    tested = run_test()
    reviewed = next(row for row in read(tested).items if row.name == "get_record")
    assert reviewed.requires_approval is True and reviewed.destructive is True
    assert execute(command(tested))["status"] == "completed"
    current = config.read_saved_configuration().document["servers"]["Synthetic"]["tools"]
    assert current["catalog"]["get_record"]["destructive"] is True
    assert current["catalog"]["get_record"]["requires_approval"] is True
    assert "get_record" in current["require_approval"]
