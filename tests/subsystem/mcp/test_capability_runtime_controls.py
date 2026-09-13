"""Reviewed MCP lifecycle uses fake transports and canonical isolated receipts."""
from dataclasses import asdict
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import capability_configuration_controls as configuration
from row_bot.application import capability_runtime_controls as controls
from row_bot.mcp_client import config

pytestmark = [pytest.mark.subsystem, pytest.mark.mcp_transport]


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", None)
    monkeypatch.setattr(runtime, "_servers", {})
    monkeypatch.setattr(runtime, "_catalog", {})
    monkeypatch.setattr(runtime, "_statuses", {})
    monkeypatch.setattr(runtime, "_loop", None)
    monkeypatch.setattr(runtime, "_thread", None)
    monkeypatch.setattr(runtime, "sdk_available", lambda: True)
    monkeypatch.setattr(runtime.mcp_config, "clear_agent_cache_if_loaded", lambda: None)
    calls = []
    async def tools():
        calls.append("list_tools")
        return SimpleNamespace(tools=[])
    async def connect(server):
        calls.append("connect")
        server.session = SimpleNamespace(list_tools=tools)
    monkeypatch.setattr(runtime.McpServerRuntime, "_connect", connect)
    config.CONFIG_PATH.write_text(json.dumps({"enabled": True, "servers": {"Synthetic": {
        "enabled": True, "command": "synthetic", "env": {"VALUE": "private-fixture-secret"}}}}), encoding="utf-8")
    yield runtime, calls
    runtime.shutdown()


def request(operation="test", runtime_id=None):
    page = configuration.read_mcp_configuration()
    revision = controls.read_mcp_runtime_state(page.items[0].server_id).cleanup_revision if operation == "disconnect" else page.revision
    return {"type": "mcp.runtime.control", "command_id": str(uuid4()), "expected_revision": "0", "payload": {
        "resource_revision": revision, "server_id": page.items[0].server_id,
        "operation": operation, "expected_runtime_id": runtime_id}}


def execute(command, **options):
    return controls.execute_mcp_runtime_command(owner_id="synthetic-owner", key=command["command_id"], command=command,
        validate=options.get("validate", lambda: None), validate_review=options.get("validate_review", lambda _: None),
        validate_admitted_review=options.get("validate_admitted_review"),
        observe_seconds=options.get("observe_seconds", 5))


def test_passive_read_and_review_never_connect_or_expose_launch_values(owner):
    _, calls = owner
    value = request()
    payload = value["payload"]
    state = controls.read_mcp_runtime_state(payload["server_id"])
    reviewed = controls.review_mcp_runtime_command(**payload, validate=lambda: None)
    assert state.state == "missing" and state.session_quiesced is None
    assert calls == [] and "private-fixture-secret" not in json.dumps([asdict(state), reviewed])


def test_connect_stop_original_replays_do_not_repeat_transport(owner):
    runtime, calls = owner
    connect = request("connect")
    result = execute(connect)
    assert result["status"] == "completed" and result["mcp_runtime"]["state"] == "connected"
    assert result["mcp_runtime"]["session_quiesced"] is False
    identity = result["mcp_runtime"]["runtime_id"]
    assert execute(connect, validate_review=lambda _: pytest.fail("No replay approval")) == result
    stopped_command = request("disconnect", identity)
    stopped = execute(stopped_command)
    assert stopped["status"] == "completed" and stopped["mcp_runtime"]["session_quiesced"] is True
    assert execute(stopped_command) == stopped and calls == ["connect", "list_tools"]
    assert not runtime._servers


def test_temporary_test_leaves_saved_enablement_and_catalog_unchanged(owner):
    runtime, calls = owner
    before = config.CONFIG_PATH.read_bytes()
    command = request()
    tested = execute(command)
    assert tested["status"] == "completed" and tested["mcp_runtime"]["state"] == "tested"
    assert tested["mcp_runtime"]["session_quiesced"] is True
    assert execute(command) == tested and calls == ["connect", "list_tools"]
    assert config.CONFIG_PATH.read_bytes() == before and not runtime._catalog and not runtime._servers


def test_uncertain_new_launch_is_not_repeated_by_a_new_command(owner, monkeypatch):
    runtime, calls = owner
    def uncertain(*_a, **kwargs):
        kwargs["before_start"](str(uuid4()))
        raise RuntimeError("synthetic schedule uncertainty")
    monkeypatch.setattr(runtime, "launch_server_owned", uncertain)
    original = request()
    result = execute(original)
    assert result["status"] == "partial"
    assert execute(original)["status"] == "partial"
    with pytest.raises(controls.CapabilityRuntimeError, match="recovery_required"):
        execute(request())
    assert calls == []


def test_stop_uses_owned_identity_even_after_saved_config_is_corrupt(owner):
    _, calls = owner
    connected = execute(request("connect"))
    identity = connected["mcp_runtime"]["runtime_id"]
    stop = request("disconnect", identity)
    config.CONFIG_PATH.write_text("{corrupt", encoding="utf-8")
    stopped = execute(stop)
    assert stopped["mcp_runtime"]["session_quiesced"] is True and calls == ["connect", "list_tools"]
    assert config.CONFIG_PATH.read_text(encoding="utf-8") == "{corrupt"


def test_expired_final_nonce_prevents_transport(owner):
    _, calls = owner
    checks = []
    def nonce(review):
        checks.append(review)
        if len(checks) >= 3:
            raise PermissionError("synthetic expired review")
    result = execute(request(), validate_review=nonce)
    assert result["status"] == "partial" and calls == []
    assert checks and all(review == checks[0] for review in checks)


def test_test_result_response_loss_replays_durable_proof_after_owner_release(owner, monkeypatch):
    runtime, calls = owner
    original = controls._persist_progress
    def lose(owner_id, key, result):
        if result.get("status") == "completed":
            raise OSError("synthetic response loss")
        return original(owner_id, key, result)
    monkeypatch.setattr(controls, "_persist_progress", lose)
    command = request()
    with pytest.raises(OSError, match="response loss"):
        execute(command)
    assert not runtime._servers
    monkeypatch.setattr(controls, "_persist_progress", original)
    monkeypatch.setattr(runtime, "launch_server_owned", lambda *_a, **_k: pytest.fail("No repeated Test"))
    result = execute(command)
    assert result["mcp_runtime"]["state"] == "tested" and result["status"] == "completed"
    assert calls == ["connect", "list_tools"] and "_mcp_runtime" not in result


def test_failed_completion_checkpoint_retains_owner_until_original_receipt_retry(owner, monkeypatch):
    runtime, calls = owner
    original = controls._persist_progress
    def lose(owner_id, key, result):
        if result.get("_mcp_runtime", {}).get("completion"):
            raise OSError("synthetic completion checkpoint failure")
        return original(owner_id, key, result)
    monkeypatch.setattr(controls, "_persist_progress", lose)
    command = request()
    result = execute(command)
    assert result["status"] == "partial" and result["mcp_runtime"]["session_quiesced"] is True
    assert "Synthetic" in runtime._servers
    monkeypatch.setattr(controls, "_persist_progress", original)
    monkeypatch.setattr(runtime, "launch_server_owned", lambda *_a, **_k: pytest.fail("No reconnect on receipt retry"))
    result = execute(command)
    assert result["status"] == "completed" and result["mcp_runtime"]["state"] == "tested"
    assert not runtime._servers and calls == ["connect", "list_tools"]


def test_stop_checkpoints_original_uncertain_connect_before_owner_release(owner, monkeypatch):
    runtime, calls = owner
    original = controls._persist_progress
    connect = request("connect")
    def lose(owner_id, key, result):
        if key == connect["command_id"] and result.get("status") == "completed":
            raise OSError("synthetic original connect response loss")
        return original(owner_id, key, result)
    monkeypatch.setattr(controls, "_persist_progress", lose)
    with pytest.raises(OSError, match="response loss"):
        execute(connect)
    identity = runtime._servers["Synthetic"].runtime_id
    stopped = execute(request("disconnect", identity))
    assert stopped["mcp_runtime"]["session_quiesced"] is True and not runtime._servers
    monkeypatch.setattr(controls, "_persist_progress", original)
    monkeypatch.setattr(runtime, "launch_server_owned", lambda *_a, **_k: pytest.fail("No reconnect"))
    result = execute(connect)
    assert result["status"] == "completed" and result["mcp_runtime"]["state"] == "stopped"
    assert calls == ["connect", "list_tools"]


def test_disabled_saved_server_can_only_be_tested_explicitly(owner):
    _, calls = owner
    data = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    data["servers"]["Synthetic"]["enabled"] = False
    config.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(controls.CapabilityRuntimeError, match="disabled"):
        execute(request("connect"))
    assert not calls
    assert execute(request())["mcp_runtime"]["state"] == "tested"
    assert json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))["servers"]["Synthetic"]["enabled"] is False


def test_new_disconnect_review_works_when_config_is_unavailable(owner):
    _, _calls = owner
    connected = execute(request("connect"))
    server_id = connected["mcp_runtime"]["server_id"]
    config.CONFIG_PATH.write_text("{corrupt", encoding="utf-8")
    state = controls.read_mcp_runtime_state(server_id)
    assert state.configuration_revision is None and state.cleanup_revision is not None
    command = {"type": "mcp.runtime.control", "command_id": str(uuid4()), "payload": {
        "resource_revision": state.cleanup_revision, "server_id": server_id,
        "operation": "disconnect", "expected_runtime_id": state.runtime_id}}
    reviewed = controls.review_mcp_runtime_command(**command["payload"], validate=lambda: None)
    assert reviewed["resource_revision"] == state.cleanup_revision
    assert execute(command)["mcp_runtime"]["session_quiesced"] is True


def test_runtime_state_cold_read_never_creates_data_or_loads_runtime(tmp_path):
    target = tmp_path / "absent"
    script = '''
import pathlib,sys
def forbidden(*a,**k): raise AssertionError("cold read mkdir")
pathlib.Path.mkdir=forbidden
from row_bot.application.capability_runtime_controls import read_mcp_runtime_state
value=read_mcp_runtime_state("a"*64)
assert value.state=="missing" and value.availability=="missing"
assert "row_bot.tasks" not in sys.modules and "row_bot.mcp_client.runtime" not in sys.modules
assert not pathlib.Path(sys.argv[1]).exists()
'''
    completed = subprocess.run([sys.executable, "-c", script, str(target)],
        env={**os.environ, "ROW_BOT_DATA_DIR": str(target)}, capture_output=True, text=True,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert completed.returncode == 0, completed.stderr


def test_completed_transport_proof_recovers_in_fresh_process_without_runtime_import(owner, monkeypatch, tmp_path):
    runtime, calls = owner
    original = controls._persist_progress
    def lose(owner_id, key, value):
        if value.get("status") == "completed":
            raise OSError("synthetic reply loss")
        return original(owner_id, key, value)
    monkeypatch.setattr(controls, "_persist_progress", lose)
    command = request()
    with pytest.raises(OSError, match="reply loss"):
        execute(command)
    assert not runtime._servers
    script = '''
import json,sys
from row_bot.application.capability_runtime_controls import execute_mcp_runtime_command
command=json.loads(sys.argv[1])
def forbidden(*a,**k): raise AssertionError("Recovery must not approve another launch")
result=execute_mcp_runtime_command(owner_id="synthetic-owner",key=command["command_id"],command=command,
    validate=lambda:None,validate_review=forbidden)
assert result["status"]=="completed" and result["mcp_runtime"]["state"]=="tested"
assert result["mcp_runtime"]["session_quiesced"] is True and "_mcp_runtime" not in result
assert "row_bot.mcp_client.runtime" not in sys.modules
'''
    completed = subprocess.run([sys.executable, "-c", script, json.dumps(command)],
        env={**os.environ, "ROW_BOT_DATA_DIR": str(tmp_path)}, capture_output=True, text=True,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert completed.returncode == 0, completed.stderr
    assert calls == ["connect", "list_tools"]


def test_reconciliation_never_stops_replacement_owner(owner, monkeypatch):
    runtime, calls = owner
    def uncertain(*_args, **kwargs):
        kwargs["before_start"](str(uuid4()))
        raise OSError("synthetic launch uncertainty")
    monkeypatch.setattr(runtime, "launch_server_owned", uncertain)
    command = request()
    assert execute(command)["status"] == "partial"
    newer = runtime.McpServerRuntime("Synthetic", {})
    runtime._servers["Synthetic"] = newer
    assert execute(command)["status"] == "partial"
    assert runtime._servers["Synthetic"] is newer and not newer._stop_requested.is_set() and not calls


def test_quiescent_owner_without_matching_completion_proof_remains_uncertain(owner, monkeypatch):
    runtime, _calls = owner
    server = runtime.McpServerRuntime("Synthetic", {})
    server.cleanup_complete = True
    server._finished.set()
    runtime._servers["Synthetic"] = server
    command = request()
    from row_bot.runtime import admissions
    mapped = {**command, "wire_expected_revision": command["expected_revision"],
              "expected_revision": command["payload"]["resource_revision"]}
    admissions.claim_command("synthetic-owner", command["command_id"], mapped,
        "settings:mcp-runtime:" + command["payload"]["server_id"])
    admissions.command_progress("synthetic-owner", command["command_id"], {
        "command_id": command["command_id"], "status": "partial", "_mcp_runtime": {
            "runtime_id": server.runtime_id, "operation": "test"}})
    checked = []
    def no_proof(*_args):
        checked.append(True)
        return {"receipt_confirmed": True, "session_quiesced": True}
    monkeypatch.setattr(runtime, "reconcile_server_release_owned", no_proof)
    assert execute(command)["status"] == "partial" and checked == [True]


@pytest.mark.parametrize("field,value", [("extra", "unknown"), ("operation", ["test"]), ("resource_revision", "bad"), ("expected_runtime_id", "bad")])
def test_unknown_or_malformed_payload_cannot_reach_a_transport(owner, field, value):
    _runtime, calls = owner
    command = request()
    command["payload"][field] = value
    with pytest.raises(controls.CapabilityRuntimeError, match="invalid_command"):
        execute(command)
    assert calls == []


@pytest.mark.parametrize("complete_before_response", [False, True])
def test_late_partial_response_cannot_overwrite_completed_transport_proof(owner, monkeypatch, complete_before_response):
    runtime, calls = owner
    command = request()
    created = []
    def delayed_launch(name, cfg, **kwargs):
        server = runtime.McpServerRuntime(name, cfg)
        server.state = "connecting"
        runtime._servers[name] = server
        kwargs["before_start"](server.runtime_id)
        server._before_release = kwargs["before_release"]
        server._release_confirmed = False
        created.append(server)
        return server
    monkeypatch.setattr(runtime, "launch_server_owned", delayed_launch)
    original = controls._persist_progress
    interleaved = []
    def late_progress(owner_id, key, value):
        if value.get("mcp_runtime", {}).get("state") == "connecting" and not interleaved:
            interleaved.append(True)
            server = created[0]
            server._probe_result = {"ok": True}
            server.cleanup_complete = True
            server._finished.set()
            assert server._confirm_release()
            runtime._servers.pop(server.name)
            if complete_before_response:
                assert execute(command)["status"] == "completed"
        return original(owner_id, key, value)
    monkeypatch.setattr(controls, "_persist_progress", late_progress)
    assert execute(command, observe_seconds=0)["status"] == ("completed" if complete_before_response else "partial")
    assert not runtime._servers
    result = execute(command)
    assert result["status"] == "completed" and result["mcp_runtime"]["state"] == "tested"
    assert len(created) == 1 and calls == [] and interleaved == [True]


def test_admitted_approval_observes_exact_checkpoint_and_never_reuses_prelaunch_policy(owner):
    from row_bot.runtime import admissions
    runtime, calls = owner
    command = request()
    prelaunch, admitted = [], []
    def ordinary(review):
        assert not runtime._servers
        prelaunch.append(review)
    def after_reservation(review, identity):
        assert runtime._servers["Synthetic"].runtime_id == identity
        stored = admissions.receipt("synthetic-owner", command["command_id"])
        assert stored["_mcp_runtime"]["runtime_id"] == identity
        assert review == prelaunch[0]
        admitted.append(identity)
    result = execute(command, validate_review=ordinary, validate_admitted_review=after_reservation)
    assert result["status"] == "completed" and len(prelaunch) == 2
    assert len(admitted) >= 2 and len(set(admitted)) == 1 and calls == ["connect", "list_tools"]


def test_admitted_approval_expiry_after_handshake_blocks_discovery(owner, monkeypatch):
    runtime, calls = owner
    revoked = []
    async def handshake(server):
        calls.append("connect")
        revoked.append(True)
        server.session = SimpleNamespace(list_tools=lambda: pytest.fail("Expired review must not discover tools"))
    monkeypatch.setattr(runtime.McpServerRuntime, "_connect", handshake)
    def admitted(_review, identity):
        assert runtime._servers["Synthetic"].runtime_id == identity
        if revoked:
            raise PermissionError("synthetic expired admitted nonce")
    result = execute(request(), validate_admitted_review=admitted)
    assert result["status"] == "completed" and result["mcp_runtime"]["state"] == "failed"
    assert result["mcp_runtime"]["session_quiesced"] is True and calls == ["connect"]
