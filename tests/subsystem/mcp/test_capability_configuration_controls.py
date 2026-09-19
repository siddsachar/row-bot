"""MCP configuration uses isolated saved data and no external server activity."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot.application import capability_configuration_controls as controls
from row_bot.mcp_client import config

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", None)
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    return tmp_path


def saved(servers, **extra):
    config.CONFIG_PATH.write_text(json.dumps({"version": 1, "enabled": True, "servers": servers, **extra}), encoding="utf-8")


def intent(name="Synthetic", **fields):
    return {"operation": "add", "fields": {"name": name, "transport": "stdio", "command": "synthetic-command", **fields}}


def command(value=None):
    return {"command_id": str(uuid4()), "type": "mcp.configuration.save", "expected_revision": "0",
            "payload": {"configuration_revision": controls.read_mcp_configuration().revision,
                        "intent": intent() if value is None else value}}


def execute(value, **options):
    return controls.execute_mcp_configuration_command(owner_id="synthetic-owner", key=value["command_id"], command=value,
        validate=options.get("validate", lambda: None), validate_review=options.get("validate_review", lambda _: None))


def test_original_receipt_rehashes_current_bytes_after_saved_snapshot(owner, monkeypatch):
    from row_bot.runtime import admissions
    value = command()
    checkpoint = admissions.command_progress
    def fail_completed_checkpoint(owner_id, key, result):
        if result.get("status") == "completed":
            raise OSError("lost receipt")
        return checkpoint(owner_id, key, result)
    monkeypatch.setattr(admissions, "command_progress", fail_completed_checkpoint)
    # Leave only the prepublication proof, with no confirmed saved outcome.
    with pytest.raises(OSError, match="lost receipt"):
        execute(value)
    original = config.read_saved_configuration
    captured = []
    def raced_read():
        result = original()
        if not captured:
            captured.append(result.identity)
            changed = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
            changed["servers"]["Synthetic"]["enabled"] = True
            config.CONFIG_PATH.write_text(json.dumps(changed), encoding="utf-8")
        return result
    monkeypatch.setattr(admissions, "command_progress", checkpoint)
    monkeypatch.setattr(config, "read_saved_configuration", raced_read)
    assert execute(value)["status"] == "partial"
    assert original().document["servers"]["Synthetic"]["enabled"] is True


@pytest.mark.parametrize("operation", ["edit", "rename"])
def test_saved_disabled_revokes_actual_retained_tool_before_schedule(owner, monkeypatch, operation):
    from row_bot.mcp_client import runtime
    saved({"Synthetic": {"enabled": True, "command": "synthetic-command", "tools": {"enabled": {"read": True}}, "unknown": {"retain": 1}}})
    monkeypatch.setattr(runtime, "_get_effective_config", config.get_config)
    monkeypatch.setattr(runtime, "_catalog", {"Synthetic": {"read": runtime.McpToolInfo("Synthetic", "read", "mcp_synthetic_read", enabled=True)}})
    monkeypatch.setattr(runtime, "_servers", {"Synthetic": runtime.McpServerRuntime("Synthetic", {"tool_timeout": 1})})
    bound = runtime._make_tool_func("Synthetic", "read", enforce_policy=True)
    monkeypatch.setattr(runtime, "_schedule", lambda *_a, **_k: pytest.fail("Revoked tool scheduled"))
    identifier = controls.read_mcp_configuration().items[0].server_id
    fields = {"name": "Renamed"} if operation == "rename" else {"args": ["one two", "--exact"]}
    result = execute(command({"operation": operation, "server_id": identifier, "fields": fields}))
    assert result["mcp_configuration"]["saved_disabled"] is True
    with pytest.raises(RuntimeError):
        bound()
    document = config.read_saved_configuration().document
    name = "Renamed" if operation == "rename" else "Synthetic"
    assert document["servers"][name]["unknown"] == {"retain": 1}
    assert document["servers"][name]["tools"] == {"enabled": {"read": True}}


def test_import_collision_is_atomic_and_valid_import_preserves_unknowns(owner):
    saved({"Existing": {"command": "synthetic", "enabled": True}}, future={"keep": 1})
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(controls.CapabilityConfigurationError, match="mcp_server_collision"):
        execute(command({"operation": "import", "import_json": json.dumps({"mcpServers": {
            "New": {"command": "synthetic"}, "Existing": {"command": "other"}}})}))
    assert config.CONFIG_PATH.read_bytes() == before
    result = execute(command({"operation": "import", "import_json": json.dumps({"mcpServers": {
        "New": {"command": "synthetic", "enabled": True, "future": {"keep": 2}}}})}))
    assert result["status"] == "completed"
    current = config.read_saved_configuration().document
    assert current["future"] == {"keep": 1}
    assert current["servers"]["New"]["future"] == {"keep": 2}
    assert current["servers"]["New"]["enabled"] is False
    assert current["servers"]["Existing"]["enabled"] is True


def test_response_loss_reconciles_owned_publication_without_new_write(owner, monkeypatch):
    from row_bot.runtime import admissions
    original_progress = admissions.command_progress
    value = command()
    def lose_after_publication(owner_id, key, result):
        if result.get("status") == "completed":
            raise OSError("synthetic response loss")
        return original_progress(owner_id, key, result)
    monkeypatch.setattr(admissions, "command_progress", lose_after_publication)
    with pytest.raises(OSError, match="response loss"):
        execute(value)
    current = config.CONFIG_PATH.read_bytes()
    inode = config.CONFIG_PATH.stat().st_ino
    assert controls.read_mcp_configuration().availability == "recovery_required"
    monkeypatch.setattr(admissions, "command_progress", original_progress)
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("No publication replay"))
    result = execute(value, validate_review=lambda _: pytest.fail("Read-only reconciliation needs no new effect approval"))
    assert result["status"] == "completed" and "_mcp_configuration" not in result
    assert config.CONFIG_PATH.read_bytes() == current and config.CONFIG_PATH.stat().st_ino == inode
    assert controls.read_mcp_configuration().availability == "available"


def test_equal_bytes_replacement_never_proves_uncertain_publication(owner, monkeypatch):
    from row_bot.runtime import admissions
    original_progress = admissions.command_progress
    value = command()
    def lose(owner_id, key, result):
        if result.get("status") == "completed":
            raise OSError("response lost")
        return original_progress(owner_id, key, result)
    monkeypatch.setattr(admissions, "command_progress", lose)
    with pytest.raises(OSError):
        execute(value)
    data = config.CONFIG_PATH.read_bytes()
    other = owner / "replacement.json"
    other.write_bytes(data)
    os.replace(other, config.CONFIG_PATH)
    monkeypatch.setattr(admissions, "command_progress", original_progress)
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("No blind retry"))
    result = execute(value)
    assert result["status"] == "partial" and result["mcp_configuration"]["saved_disabled"] is None
    assert config.CONFIG_PATH.read_bytes() == data
    with pytest.raises(config.McpConfigurationError, match="recovery_required"):
        config.set_server_enabled("Synthetic", True)


def test_failed_publication_retains_durable_proof_and_blocks_missing_recreation(owner, monkeypatch):
    from row_bot.developer import edits
    from row_bot.runtime import admissions
    saved({"Original": {"command": "synthetic", "env": {"VALUE": "private-retained"}}})
    before = config.CONFIG_PATH.read_bytes()
    original_rename = edits._rename_edit_no_replace
    def crash_after_retirement(source, destination, **kwargs):
        if str(source).endswith("previous"):
            raise OSError("synthetic restore failure")
        original_rename(source, destination, **kwargs)
        raise OSError("synthetic crash after retirement")
    monkeypatch.setattr(edits, "_rename_edit_no_replace", crash_after_retirement)
    with pytest.raises(edits.FileEditError):
        config.set_server_enabled("Original", False)
    rows = admissions.read_unfinished_target_commands("settings:mcp")
    assert len(rows["items"]) == 1 and rows["items"][0]["type"] == "mcp.configuration.legacy_save"
    row = rows["items"][0]
    receipt = admissions.receipt(row["owner_id"], row["command_id"])
    assert "private-retained" not in json.dumps(receipt)
    proof = receipt["_mcp_configuration"]["publication"]
    retained = owner / ".row-bot-edit-recovery" / proof["command_id"] / "previous"
    assert retained.read_bytes() == before and not config.CONFIG_PATH.exists()
    assert controls.read_mcp_configuration().availability == "recovery_required"
    with pytest.raises(config.McpConfigurationError, match="recovery_required"):
        config.upsert_server("New", {"command": "new"})
    assert not config.CONFIG_PATH.exists() and retained.read_bytes() == before


def test_final_authority_recheck_retains_original_when_nonce_expires(owner):
    saved({"Original": {"command": "synthetic"}})
    before = config.CONFIG_PATH.read_bytes()
    calls = []
    def authority(review):
        calls.append(review)
        if len(calls) >= 3:
            raise PermissionError("synthetic expired nonce")
    result = execute(command(), validate_review=authority)
    assert result["status"] == "partial"
    assert config.CONFIG_PATH.read_bytes() == before
    assert len(calls) >= 3 and all(review == calls[0] for review in calls)


def test_unknown_pending_overflow_and_query_failure_never_means_safe_empty(owner, monkeypatch):
    from row_bot.runtime import admissions
    monkeypatch.setattr(admissions, "read_unfinished_target_commands", lambda *_a, **_k: {"items": [], "overflow": True})
    assert controls.read_mcp_configuration().availability == "recovery_required"
    with pytest.raises(config.McpConfigurationError, match="recovery_required"):
        config.save_config({})
    def unavailable(*_a, **_k):
        raise admissions.AdmissionError("command_metadata_unavailable")
    monkeypatch.setattr(admissions, "read_unfinished_target_commands", unavailable)
    assert controls.read_mcp_configuration().availability == "unavailable"
    with pytest.raises(config.McpConfigurationError, match="recovery_unavailable"):
        config.save_config({})
    assert not config.CONFIG_PATH.exists()


@pytest.mark.parametrize("fields", [{"transport": []}, {"args": "--not-an-array"},
    {"env": {"KEY": 1}}, {"headers": []}, {"connect_timeout": True}, {"tool_timeout": float("inf")},
    {"output_limit": 1.5}, {"unknown_wire_field": "no"}, {"command": "bad\0command"}])
def test_invalid_typed_fields_never_reach_publication(owner, monkeypatch, fields):
    monkeypatch.setattr(config, "publish_saved_configuration", lambda *_a, **_k: pytest.fail("Invalid input publication"))
    with pytest.raises(controls.CapabilityConfigurationError, match="invalid_command"):
        execute(command(intent(**fields)))
    assert not config.CONFIG_PATH.exists()


def test_config_wire_and_page_budgets_remain_distinct(owner):
    # An existing multi-megabyte library is editable without raising the command budget.
    saved({"Original": {"command": "synthetic", "unknown_future": "x" * (1024 * 1024 + 1)}})
    identifier = controls.read_mcp_configuration().items[0].server_id
    result = execute(command({"operation": "edit", "server_id": identifier, "fields": {"args": ["exact"]}}))
    assert result["status"] == "completed"
    assert len(config.read_saved_configuration().document["servers"]["Original"]["unknown_future"]) == 1024 * 1024 + 1
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(controls.CapabilityConfigurationError, match="invalid_command"):
        execute(command({"operation": "import", "import_json": "x" * (128 * 1024)}))
    assert config.CONFIG_PATH.read_bytes() == before
    saved({f"Synthetic {i:05}": {"command": "synthetic", "env": {"VALUE": "x" * 32}} for i in range(205)})
    assert len(json.dumps(asdict(controls.read_mcp_configuration(limit=50))).encode()) < 256 * 1024
    config.CONFIG_PATH.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    assert controls.read_mcp_configuration().availability == "unavailable"


def test_malformed_loaded_status_is_bounded_unknown(owner, monkeypatch):
    from types import SimpleNamespace
    from row_bot.mcp_client import runtime
    saved({"Synthetic": {"command": "synthetic", "enabled": False}})
    monkeypatch.setattr(runtime, "_statuses", {"Synthetic": SimpleNamespace(status=["connected"], tool_count=2**128)})
    monkeypatch.setattr(runtime, "_servers", {})
    item = controls.read_mcp_configuration().items[0]
    assert item.runtime_status == "unknown" and item.tool_count is None
    assert item.connection_present is False and item.enabled is False


def test_nonce_revocation_after_durable_checkpoint_prevents_retirement(owner, monkeypatch):
    from row_bot.runtime import admissions
    saved({"Original": {"command": "synthetic"}})
    before = config.CONFIG_PATH.read_bytes()
    original = admissions.command_progress
    checkpoints = []
    def checkpoint(owner_id, key, result):
        original(owner_id, key, result)
        proof = result.get("_mcp_configuration", {}).get("publication")
        if proof:
            checkpoints.append(proof)
    monkeypatch.setattr(admissions, "command_progress", checkpoint)
    def authority(_review):
        if checkpoints:
            raise PermissionError("synthetic final nonce revocation")
    result = execute(command(), validate_review=authority)
    assert result["status"] == "partial" and checkpoints
    assert config.CONFIG_PATH.read_bytes() == before
    directory = owner / ".row-bot-edit-recovery" / checkpoints[0]["command_id"]
    assert (directory / "candidate").exists() and not (directory / "previous").exists()


def test_concurrent_legacy_save_forces_new_client_revision_review(owner, monkeypatch):
    import threading
    saved({"Original": {"command": "synthetic", "enabled": True}}, future={"keep": True})
    value = command()
    entered, release = threading.Event(), threading.Event()
    original = config.publish_saved_configuration
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test publication release missing"
        return original(*args, **kwargs)
    monkeypatch.setattr(config, "publish_saved_configuration", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        legacy = pool.submit(config.set_server_enabled, "Original", False)
        assert entered.wait(5), "test legacy publication not entered"
        client = pool.submit(execute, value)
        release.set()
        legacy.result(timeout=10)
        with pytest.raises(controls.CapabilityConfigurationError, match="revision_conflict"):
            client.result(timeout=10)
    document = config.read_saved_configuration().document
    assert document["servers"]["Original"]["enabled"] is False
    assert "Synthetic" not in document["servers"] and document["future"] == {"keep": True}


def test_cold_missing_read_has_no_effectful_import_or_directory_creation(tmp_path):
    target = tmp_path / "absent"
    script = '''
import pathlib,sys
def forbidden(*args,**kwargs): raise AssertionError("no directory mutation")
pathlib.Path.mkdir=forbidden
from row_bot.application.capability_configuration_controls import read_mcp_configuration
page=read_mcp_configuration()
assert page.availability=="missing" and page.total==0
assert "row_bot.mcp_client.runtime" not in sys.modules
assert "mcp" not in sys.modules and "row_bot.secret_store" not in sys.modules
assert not pathlib.Path(sys.argv[1]).exists()
print("cold_read_ok")
'''
    environment = {**os.environ, "ROW_BOT_DATA_DIR": str(target)}
    result = subprocess.run([sys.executable, "-c", script, str(target)], env=environment,
        capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "cold_read_ok"
    assert not target.exists()


def test_saved_status_redacts_launch_values_and_never_refreshes_runtime(owner, monkeypatch):
    from row_bot.mcp_client import runtime
    for name in ("get_status_summary", "_get_effective_config", "_sync_catalog_from_config", "discover_enabled_servers", "probe_server", "refresh_server"):
        monkeypatch.setattr(runtime, name, lambda *_a, **_k: pytest.fail("No runtime refresh/probe"))
    saved({"Synthetic": {"transport": "stdio", "enabled": False,
        "command": "private-command-value", "args": ["private-argument-value"],
        "cwd": str(owner / "private-path-value"), "env": {"ORDINARY": "private-env-value"},
        "headers": {"Ordinary": "private-header-value"}, "url": "https://private-url.invalid/token",
        "tools": {"catalog": {"read": {"description": "private-description-value"}}}}})
    before = config.CONFIG_PATH.read_bytes()
    page = controls.read_mcp_configuration()
    public = json.dumps(asdict(page))
    assert "private-" not in public and str(owner) not in public
    assert page.items[0].tool_count == 1 and page.items[0].enabled is False
    assert set(page.items[0].configured_fields) == {"command", "args", "cwd", "url", "env", "headers"}
    assert config.CONFIG_PATH.read_bytes() == before and config._config_cache is None


@pytest.mark.parametrize("raw", ['{broken', '{"servers":[]}', '{"servers":{"x":null}}',
    '{"enabled":NaN}', '{"servers":{},"servers":{}}', '{"version":2}'])
def test_corruption_is_unavailable_and_never_replaced_by_defaults(owner, raw):
    config.CONFIG_PATH.write_text(raw, encoding="utf-8")
    page = controls.read_mcp_configuration()
    assert page.availability == "unavailable" and page.total is None and page.revision is None
    with pytest.raises(config.McpConfigurationError):
        config.save_config({"servers": {}})
    assert config.CONFIG_PATH.read_text(encoding="utf-8") == raw


def test_full_library_pages_and_filter_bind_cursor_to_current_snapshot(owner):
    saved({f"Server {index:04}": {"command": "synthetic", "enabled": False} for index in range(205)})
    first = controls.read_mcp_configuration(limit=50)
    seen = list(first.items)
    cursor = first.next_cursor
    while cursor:
        page = controls.read_mcp_configuration(limit=50, cursor=cursor)
        seen.extend(page.items)
        cursor = page.next_cursor
    assert len(seen) == len({item.server_id for item in seen}) == first.total == 205
    assert controls.read_mcp_configuration(query="0204").items[0].name == "Server 0204"
    with pytest.raises(controls.CapabilityConfigurationError, match="cursor_expired"):
        controls.read_mcp_configuration(query="different", limit=50, cursor=first.next_cursor)
    document = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    document["enabled"] = False
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(controls.CapabilityConfigurationError, match="cursor_expired"):
        controls.read_mcp_configuration(limit=50, cursor=first.next_cursor)


def test_malformed_saved_labels_and_toggles_are_not_authority(owner):
    saved({"../private-path": {"enabled": "true", "command": "synthetic"},
           "\ud800": {"enabled": 1, "command": "synthetic"}})
    page = controls.read_mcp_configuration()
    assert len(page.items) == 2
    assert all(item.name == "MCP server" and item.enabled is None for item in page.items)


def test_canonical_legacy_concurrent_setters_preserve_unrelated_unknown_fields(owner):
    saved({"Synthetic": {"command": "synthetic", "unknown": {"keep": True}}}, legacy={"keep": True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: config.set_tool_enabled("Synthetic", *pair), [("one", True), ("two", False)]))
    result = config.read_saved_configuration().document
    assert result["legacy"] == {"keep": True}
    assert result["servers"]["Synthetic"]["unknown"] == {"keep": True}
    assert result["servers"]["Synthetic"]["tools"]["enabled"] == {"one": True, "two": False}


def test_explicit_large_config_publication_keeps_default_editor_budget(owner):
    from row_bot.developer import edits
    data = "a" * (edits.CLIENT_EDIT_BYTE_LIMIT + 1)
    path = owner / "large.txt"
    proofs = []
    with pytest.raises(edits.FileEditError, match="file_too_large"):
        edits.publish_text_revision(owner, path.name, data, expected_digest="missing", command_id=str(uuid4()), persist_recovery=proofs.append)
    assert not path.exists() and proofs == []
    result = edits.publish_text_revision(owner, path.name, data, expected_digest="missing", command_id=str(uuid4()), persist_recovery=proofs.append, max_bytes=8 * 1024 * 1024)
    assert result.digest == hashlib.sha256(data.encode()).hexdigest() and path.read_bytes() == data.encode()
    with pytest.raises(edits.FileEditError, match="file_too_large"):
        edits.read_edit_bytes(owner, path.name)
    assert edits.read_edit_bytes(owner, path.name, max_bytes=8 * 1024 * 1024)[0] == data.encode()


@pytest.mark.parametrize("limit", [0, -1, True, 8 * 1024 * 1024 + 1])
def test_publication_budget_invalid_before_effect(owner, limit):
    from row_bot.developer import edits
    with pytest.raises(edits.FileEditError, match="invalid_edit"):
        edits.publish_text_revision(owner, "never.txt", "value", expected_digest="missing", command_id=str(uuid4()), persist_recovery=lambda _: pytest.fail("No receipt"), max_bytes=limit)
    assert not (owner / "never.txt").exists()


def test_save_disabled_creates_exact_argv_once_and_omitted_edit_values_are_retained(owner):
    value = command(intent(args=["two words", "", "literal'quote"], env={"ORDINARY": "synthetic-secret"}))
    result = execute(value)
    assert execute(value) == result
    server = config.get_servers()["Synthetic"]
    assert server["args"] == ["two words", "", "literal'quote"] and server["enabled"] is False
    identity = controls.read_mcp_configuration().items[0].server_id
    execute(command({"operation": "edit", "server_id": identity, "fields": {"tool_timeout": 35}}))
    assert config.get_servers()["Synthetic"]["env"] == {"ORDINARY": "synthetic-secret"}
    assert "synthetic-secret" not in json.dumps(result)
