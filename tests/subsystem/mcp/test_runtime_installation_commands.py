"""Managed-install commands use synthetic archives and isolated canonical receipts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import threading
from uuid import uuid4
import zipfile

import psutil
import pytest

from row_bot.application import mcp_runtime_installation as controls
from row_bot.mcp_client import requirements
from row_bot.runtime import admissions

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]
_DOWNLOAD = requirements._download


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from row_bot import tasks
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", tmp_path / "runtimes")
    monkeypatch.setattr(controls, "_OPERATIONS", {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("bundle/node.exe", b"fake runtime never executed")
    data = output.getvalue()
    calls = []
    def resolve(runtime_id, *, validate, cancelled):
        validate()
        calls.append("resolve")
        return requirements.make_archive_runtime_plan(runtime_id, version="1.2.3",
            url="https://example.invalid/node.zip", sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data), asset_name="node.zip", executable_candidates=("node.exe",))
    def download(url, destination, progress=None, *, validate=lambda: None):
        validate()
        calls.append("download")
        destination.write_bytes(data)
    monkeypatch.setattr(requirements, "resolve_managed_runtime_plan", resolve)
    monkeypatch.setattr(requirements, "_download", download)
    service = controls.McpRuntimeInstallationService()
    yield service, calls, tmp_path
    assert service.close(timeout=5)


def command(service, operation="resolve", source=None, runtime_id="node", policy=lambda _: {}):
    revision = requirements.runtime_install_revision(runtime_id)
    review = service.review(owner_id="owner", runtime_id=runtime_id, operation=operation,
        resource_revision=revision, source_command_id=source, validate=lambda: None, read_policy=policy)
    return {"command_id": str(uuid4()), "type": "mcp.runtime." + operation, "payload": {
        "runtime_id": runtime_id, "source_command_id": source, "resource_revision": revision,
        "action_digest": review.action_digest}}


def execute(service, value, **kwargs):
    return service.execute(value, owner_id="owner", key=value["command_id"], validate=kwargs.get("validate", lambda: None),
        read_policy=kwargs.get("read_policy", lambda _: {}), validate_review=kwargs.get("validate_review", lambda _: None))


def settled(service, value):
    active = controls._OPERATIONS.get(value["payload"]["runtime_id"])
    if active:
        active.thread.join(10)
        assert not active.thread.is_alive()
    return service.receipt(owner_id="owner", runtime_id=value["payload"]["runtime_id"],
        command_id=value["command_id"], validate=lambda: None)


def test_passive_snapshot_never_creates_runtime_or_resolves(owner):
    service, calls, root = owner
    state = service.snapshot("node", validate=lambda: None)
    assert state.availability == "missing" and state.installed is False
    assert not calls and not (root / "runtimes").exists() and not (root / "tasks.db").exists()


def test_explicit_resolve_and_pinned_install_real_publication_and_original_replay(owner):
    service, calls, _ = owner
    resolve = command(service)
    execute(service, resolve)
    resolved = settled(service, resolve)
    assert resolved["status"] == "completed" and resolved["installation"]["stage"] == "resolved"
    assert calls == ["resolve"]
    install = command(service, "install", resolve["command_id"])
    execute(service, install)
    installed = settled(service, install)
    assert installed["status"] == "completed" and installed["installation"]["installed"] is True
    assert installed["installation"]["quiesced"] is True and calls == ["resolve", "download"]
    assert execute(service, install) == installed
    assert execute(service, resolve) == resolved and calls == ["resolve", "download"]
    private = admissions.read_command_receipt("owner", install["command_id"])[controls._PRIVATE]
    assert requirements.read_runtime_installation_proof("node", install["command_id"], private)
    assert "_runtime_installation" not in json.dumps(installed)


def test_cancel_blocked_metadata_read_does_not_release_owner_or_repeat(owner, monkeypatch):
    service, calls, _ = owner
    entered, release = threading.Event(), threading.Event()
    original = requirements.resolve_managed_runtime_plan
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(requirements, "resolve_managed_runtime_plan", blocked)
    value = command(service)
    execute(service, value)
    assert entered.wait(5)
    cancel = {"command_id": str(uuid4()), "type": "mcp.runtime.install.cancel", "payload": {
        "runtime_id": "node", "source_command_id": value["command_id"]}}
    try:
        ack = execute(service, cancel)
        assert ack["installation"]["quiesced"] is False
        assert service.close(timeout=0) is False
        with pytest.raises(controls.RuntimeInstallationError, match="pending"):
            execute(service, command(service))
        assert not calls
    finally:
        release.set()
    result = settled(service, value)
    assert result["installation"]["stage"] == "cancelled" and result["installation"]["quiesced"] is True
    assert execute(service, cancel) == ack


def test_current_policy_withdrawal_before_download_prevents_bytes(owner):
    service, calls, _ = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    policy = {"allowed": True}
    install = command(service, "install", resolve["command_id"], policy=lambda _: policy)
    def review(_):
        policy["allowed"] = False
    # The accepted review must not silently capture a different policy after callback.
    with pytest.raises(controls.RuntimeInstallationError, match="runtime_review_changed"):
        execute(service, install, read_policy=lambda _: policy, validate_review=review)
    assert calls == ["resolve"]
    assert admissions.read_command_metadata("owner", install["command_id"]) is None


@pytest.mark.parametrize("mode,dead", [("missing", True), ("reused", True), ("same", False), ("same-jitter", False), ("denied", False)])
def test_dead_owner_requires_exact_process_birth_and_access_denied_is_unknown(owner, monkeypatch, mode, dead):
    class Process:
        def __init__(self, pid):
            if mode == "missing":
                raise psutil.NoSuchProcess(pid)
            if mode == "denied":
                raise psutil.AccessDenied(pid)
        def create_time(self):
            return 23.0 if mode == "reused" else 12.0005 if mode == "same-jitter" else 12.0
    monkeypatch.setattr(controls.psutil, "Process", Process)
    assert controls._dead_owner({"owner_pid": 123, "owner_birth": 12.0}) is dead


def test_same_byte_replacement_cannot_recover_manifest_publication(owner):
    service, _, _ = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    install = command(service, "install", resolve["command_id"])
    execute(service, install)
    settled(service, install)
    private = admissions.read_command_receipt("owner", install["command_id"])[controls._PRIVATE]
    path = requirements.RUNTIMES_DIR / "node/manifest.json"
    original = path.read_bytes()
    path.rename(path.with_name("retained-original.json"))
    path.write_bytes(original)
    assert not requirements.read_runtime_installation_proof("node", install["command_id"], private)


def test_exclusive_target_claim_uses_independent_connections_and_preserves_replay(owner, monkeypatch):
    admissions.instance_identity()
    # Remove only the process-local serialization: SQLite BEGIN IMMEDIATE owns exclusion.
    monkeypatch.setattr(admissions, "_LOCK", nullcontext())
    barrier = threading.Barrier(2)
    values = [{"command_id": str(uuid4()), "type": "mcp.runtime.resolve"} for _ in range(2)]
    def claim(index):
        barrier.wait()
        value = values[index]
        try:
            admissions.claim_command(str(index), value["command_id"], value, "same", exclusive_target=True)
            return "accepted"
        except admissions.AdmissionError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sorted(results) == ["accepted", "operation_pending"]
    winner = results.index("accepted")
    value = values[winner]
    with pytest.raises(admissions.AdmissionError, match="operation_uncertain"):
        admissions.claim_command(str(winner), value["command_id"], value, "same", exclusive_target=True)
    other = {"command_id": str(uuid4()), "type": "mcp.runtime.resolve"}
    assert admissions.claim_command("third", other["command_id"], other, "other", exclusive_target=True) is None


def test_live_owner_without_registry_never_claims_quiescence_or_restarts(owner, monkeypatch):
    service, calls, _ = owner
    monkeypatch.setattr(service, "_finish", lambda _: None)
    entered, release = threading.Event(), threading.Event()
    original = requirements.resolve_managed_runtime_plan

    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(requirements, "resolve_managed_runtime_plan", blocked)
    value = command(service)
    try:
        execute(service, value)
        assert entered.wait(5)
    finally:
        release.set()
    active = controls._OPERATIONS.get("node")
    assert active is not None
    active.thread.join(5)
    assert not active.thread.is_alive()
    # Emulate lost in-memory ownership after the worker wrote its private return,
    # without claiming that the still-alive host process died.
    controls._OPERATIONS.clear()
    receipt = service.receipt(owner_id="owner", runtime_id="node", command_id=value["command_id"], validate=lambda: None)
    if receipt["status"] == "completed":
        pytest.fail("Fixture needs a receipt before host-observed completion")
    assert receipt["installation"]["quiesced"] is None
    assert service.snapshot("node", owner_id="owner", validate=lambda: None).active_command_id == value["command_id"]
    assert execute(service, value)["status"] == "partial" and calls == ["resolve"]


def test_dead_owner_recovers_durable_resolved_plan_without_network(owner, monkeypatch):
    service, calls, _ = owner
    monkeypatch.setattr(service, "_finish", lambda _: None)
    value = command(service)
    execute(service, value)
    controls._OPERATIONS["node"].thread.join(5)
    controls._OPERATIONS.clear()
    monkeypatch.setattr(controls, "_dead_owner", lambda _: True)
    result = execute(service, value)
    assert result["status"] == "completed" and result["installation"]["stage"] == "resolved"
    assert result["installation"]["quiesced"] is True and calls == ["resolve"]
    install = command(service, "install", value["command_id"])
    assert install["payload"]["source_command_id"] == value["command_id"]


def test_post_manifest_fault_recovers_only_exact_publication_after_worker_returns(owner, monkeypatch):
    service, calls, _ = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    original = requirements._write_manifest
    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("private path deliberately must not escape")
    monkeypatch.setattr(requirements, "_write_manifest", lost_ack)
    install = command(service, "install", resolve["command_id"])
    execute(service, install)
    result = settled(service, install)
    assert result["status"] == "completed" and result["installation"]["installed"] is True
    assert calls == ["resolve", "download"] and "private path" not in json.dumps(result)


def test_manifest_checkpoint_fault_fails_finally_never_republishes_and_a_retry_installs(owner, monkeypatch):
    service, calls, _ = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    original = controls._merge
    def fault(owner, identity, changes, private=None, **kwargs):
        if private and "publication" in private:
            raise OSError("checkpoint unavailable")
        return original(owner, identity, changes, private, **kwargs)
    monkeypatch.setattr(controls, "_merge", fault)
    install = command(service, "install", resolve["command_id"])
    execute(service, install)
    result = settled(service, install)
    assert result["status"] == "partial" and result["installation"]["stage"] == "failed"
    assert result["installation"]["quiesced"] is True
    runtime = requirements.RUNTIMES_DIR / "node"
    # The unpublished generation is never advertised or left to block a retry.
    assert not (runtime / "manifest.json").exists() and not (runtime / "1.2.3").exists()
    assert not list(runtime.glob(".install-*"))
    assert execute(service, install) == result and calls == ["resolve", "download"]
    monkeypatch.setattr(controls, "_merge", original)
    again = command(service)
    execute(service, again)
    settled(service, again)
    retry = command(service, "install", again["command_id"])
    execute(service, retry)
    assert settled(service, retry)["installation"]["installed"] is True


def test_private_completion_merge_never_loses_generation_proof(owner):
    value = {"command_id": str(uuid4()), "type": "mcp.runtime.install"}
    admissions.claim_command("owner", value["command_id"], value, "settings:mcp-runtime:node")
    controls._merge("owner", value["command_id"], {"status": "accepted"}, {"generation": {"identity": "test"}})
    controls._merge("owner", value["command_id"], {"status": "partial"}, {"finished_outcome": {"stage": "needs_attention"}})
    receipt = admissions.read_command_receipt("owner", value["command_id"])
    assert receipt[controls._PRIVATE]["generation"] == {"identity": "test"}
    controls._merge("owner", value["command_id"], {"status": "completed"}, terminal=True)
    controls._merge("owner", value["command_id"], {"status": "partial"}, {"publication": "stale"})
    assert admissions.read_command_receipt("owner", value["command_id"])["status"] == "completed"


def test_no_installation_proof_read_while_actual_worker_still_alive(owner, monkeypatch):
    service, _, _ = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    entered, release = threading.Event(), threading.Event()
    original = requirements._write_manifest
    def blocked(*args, **kwargs):
        original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(requirements, "_write_manifest", blocked)
    monkeypatch.setattr(requirements, "read_runtime_installation_proof", lambda *_a, **_k: pytest.fail("Worker is still live"))
    install = command(service, "install", resolve["command_id"])
    execute(service, install)
    assert entered.wait(5)
    try:
        result = service.receipt(owner_id="owner", runtime_id="node", command_id=install["command_id"], validate=lambda: None)
        assert result["installation"]["quiesced"] is False
    finally:
        release.set()
    settled(service, install)


def test_original_cannot_be_replayed_with_changed_payload_or_owner(owner):
    service, calls, _ = owner
    value = command(service)
    execute(service, value)
    settled(service, value)
    changed = {**value, "payload": {**value["payload"], "action_digest": "forged"}}
    with pytest.raises(admissions.AdmissionError, match="idempotency_mismatch"):
        execute(service, changed)
    with pytest.raises(controls.RuntimeInstallationError, match="command_unavailable"):
        service.receipt(owner_id="other", runtime_id="node", command_id=value["command_id"], validate=lambda: None)
    assert calls == ["resolve"]


def test_cancel_racing_already_observed_completion_reports_exact_quiescence(owner):
    service, calls, _ = owner
    value = command(service)
    execute(service, value)
    settled(service, value)
    cancel = {"command_id": str(uuid4()), "type": "mcp.runtime.install.cancel", "payload": {
        "runtime_id": "node", "source_command_id": value["command_id"]}}
    receipt = execute(service, cancel)
    assert receipt["installation"]["stage"] == "already_quiesced"
    assert receipt["installation"]["quiesced"] is True
    assert execute(service, cancel) == receipt and calls == ["resolve"]


def test_cold_passive_read_import_does_not_load_runtime_sdk_or_create_data(tmp_path):
    data = tmp_path / "cold-data"
    source = """
import pathlib, sys, urllib.request
urllib.request.OpenerDirector.open = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from row_bot.application.mcp_runtime_installation import McpRuntimeInstallationService
result = McpRuntimeInstallationService().snapshot('node', owner_id='owner', validate=lambda: None)
assert result.installed is False
assert 'row_bot.mcp_client.runtime' not in sys.modules
assert 'mcp' not in sys.modules
assert not pathlib.Path(sys.argv[1]).exists()
"""
    env = {**os.environ, "ROW_BOT_DATA_DIR": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", source, str(data)], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_actual_download_rechecks_authority_after_each_blocking_chunk(owner, monkeypatch):
    _service, _calls, root = owner
    allowed = True
    class Response:
        headers = {}
        count = 0
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read1(self, _size):
            nonlocal allowed
            self.count += 1
            if self.count == 2:
                allowed = False
            return b"chunk"
        read = read1
    monkeypatch.setattr(requirements, "_request", lambda _: Response())
    def validate():
        if not allowed:
            raise PermissionError("revoked")
    destination = root / "download"
    with pytest.raises(PermissionError, match="revoked"):
        _DOWNLOAD("https://example.invalid/archive.zip", destination, validate=validate)
    assert destination.read_bytes() == b"chunk"


def test_corrupt_durable_identifier_remains_recovery_required_without_path_disclosure(owner):
    service, _, _ = owner
    value = {"command_id": str(uuid4()), "type": "mcp.runtime.install"}
    admissions.claim_command("owner", value["command_id"], value, "settings:mcp-runtime:node")
    with admissions.transaction() as conn:
        conn.execute("UPDATE client_commands SET command_id=?", (r"C:\private\not-a-command",))
    snapshot = service.snapshot("node", owner_id="owner", validate=lambda: None)
    assert snapshot.availability == "recovery_required"
    assert snapshot.active_command_id is None and snapshot.quiesced is None


def installed_through(service):
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    install = command(service, "install", resolve["command_id"])
    execute(service, install)
    return settled(service, install)


def test_connection_changes_and_nonce_expiry_mid_install_do_not_abort_it(owner, monkeypatch):
    service, calls, _ = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    policy = {"connections": 1}
    approvals = []
    def approval(review):
        approvals.append(review)
        if len(approvals) > 1:
            raise PermissionError("approval_expired")
    download = requirements._download
    def download_while_a_server_connects(url, destination, progress=None, *, validate=lambda: None):
        policy["connections"] += 1  # an MCP server connects or its catalog changes meanwhile
        download(url, destination, progress, validate=validate)
    monkeypatch.setattr(requirements, "_download", download_while_a_server_connects)
    install = command(service, "install", resolve["command_id"], policy=lambda _: policy)
    execute(service, install, read_policy=lambda _: policy, validate_review=approval)
    result = settled(service, install)
    assert result["status"] == "completed" and result["installation"]["installed"] is True
    # The approval was checked once, when the install was accepted.
    assert len(approvals) == 1 and calls == ["resolve", "download"]


def test_a_failed_install_is_final_logged_without_paths_and_a_retry_is_accepted(owner, monkeypatch, caplog):
    service, calls, root = owner
    resolve = command(service)
    execute(service, resolve)
    settled(service, resolve)
    digest = requirements._generation_digest
    def unreadable(*_args, **_kwargs):
        raise OSError(str(root / "private" / "generation"))
    monkeypatch.setattr(requirements, "_generation_digest", unreadable)
    install = command(service, "install", resolve["command_id"])
    with caplog.at_level(logging.INFO, logger=controls.__name__):
        execute(service, install)
        failed = settled(service, install)
    assert failed["status"] == "partial" and failed["code"] == "runtime_installation_failed"
    assert failed["installation"]["stage"] == "failed" and failed["installation"]["quiesced"] is True
    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert any("OSError" in message and "stage extracting" in message for message in warnings), warnings
    assert str(root) not in caplog.text and "private" not in caplog.text
    # The staging folder is removed and the install lock released.
    assert not list((requirements.RUNTIMES_DIR / "node").glob(".install-*"))
    state = service.snapshot("node", owner_id="owner", validate=lambda: None)
    assert state.availability == "missing" and state.active_command_id is None and state.quiesced is True
    assert execute(service, install) == failed
    monkeypatch.setattr(requirements, "_generation_digest", digest)
    assert installed_through(service)["installation"]["installed"] is True
    assert calls == ["resolve", "download", "resolve", "download"]


def test_an_admitting_install_left_by_a_dead_process_is_recovered_at_start(owner):
    service, calls, _ = owner
    identity = str(uuid4())
    admissions.claim_command("owner", identity, {"command_id": identity, "type": "mcp.runtime.install"},
                             "settings:mcp-runtime:node", exclusive_target=True)
    stopped = {"runtime_id": "node", "operation": "install", "stage": "needs_attention", "cancel_requested": False,
               "quiesced": True, "installed": None, "plan": None}
    # The saved owner is this PID with another birth time: a process that has since exited.
    controls._merge("owner", identity, {"command_id": identity, "status": "partial",
        "code": "runtime_installation_unconfirmed", "installation": stopped},
        {"runtime_id": "node", "operation": "install", "owner_pid": os.getpid(), "owner_birth": 1.0,
         "finished_outcome": {**stopped, "quiesced": False}})
    staging = requirements.RUNTIMES_DIR / "node" / ".install-0123456789abcdef"
    (staging / "extracted").mkdir(parents=True)
    (staging / "extracted" / "node.exe").write_bytes(b"partial runtime never executed")
    assert service.snapshot("node", owner_id="owner", validate=lambda: None).availability == "recovery_required"
    admissions.recover("next-process")
    receipt = service.receipt(owner_id="owner", runtime_id="node", command_id=identity, validate=lambda: None)
    assert receipt["status"] == "partial" and receipt["installation"]["stage"] == "failed"
    assert not staging.exists()
    state = service.snapshot("node", owner_id="owner", validate=lambda: None)
    assert state.availability == "missing" and state.active_command_id is None
    assert installed_through(service)["installation"]["installed"] is True


def test_a_manifest_left_by_an_old_data_folder_is_reported_cleanly_and_replaced(owner, tmp_path):
    service, _, _ = owner
    elsewhere = tmp_path / "old-home" / ".thoth" / "runtimes" / "node" / "v20.0.0"
    runtime = requirements.RUNTIMES_DIR / "node"
    runtime.mkdir(parents=True)
    (runtime / "manifest.json").write_text(json.dumps({"installed": True, "version": "v20.0.0",
        "root": str(elsewhere), "bin_dir": str(elsewhere), "executable_path": str(elsewhere / "node.exe")}),
        encoding="utf-8")
    state = service.snapshot("node", owner_id="owner", validate=lambda: None)
    assert state.availability == "missing" and state.installed is False and state.version is None
    assert ".thoth" not in json.dumps(asdict(state))
    assert installed_through(service)["installation"]["installed"] is True
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    assert "previous_manifest" not in manifest and ".thoth" not in json.dumps(manifest)
    assert service.snapshot("node", owner_id="owner", validate=lambda: None).version == "1.2.3"


def test_snapshot_says_whether_a_system_runtime_is_on_the_path(owner, monkeypatch):
    service, calls, _ = owner
    found = set()
    monkeypatch.setattr(requirements.shutil, "which", lambda name, **_: name if name in found else None)
    assert service.snapshot("node", validate=lambda: None).system_available is False
    found.update({"node", "npm"})
    assert service.snapshot("node", validate=lambda: None).system_available is False
    found.add("npx")
    assert service.snapshot("node", validate=lambda: None).system_available is True
    assert calls == []


def _archive(count):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as handle:
        handle.writestr("bundle/node.exe", b"fake runtime never executed")
        for index in range(count):
            handle.writestr(f"bundle/lib/{index % 40}/file{index}.js", b"//")
    return output.getvalue()


def test_the_unpacking_time_budget_grows_with_the_number_of_files(tmp_path):
    def late():
        # The first read starts the budget; every later check sees 124 seconds gone.
        reads = iter([0.0])
        return lambda: next(reads, 124.0)
    with requirements._owned_directory(tmp_path / "many", create=True) as root:
        requirements._extract_into(_archive(60), root, clock=late())
    with requirements._owned_directory(tmp_path / "few", create=True) as root:
        with pytest.raises(RuntimeError, match="time budget"):
            requirements._extract_into(_archive(3), root, clock=late())


@pytest.mark.slow
def test_session_checks_are_throttled_while_a_2000_file_runtime_unpacks(owner, monkeypatch):
    service, _, _ = owner
    data = _archive(2000)
    def resolve(runtime_id, *, validate, cancelled):
        validate()
        return requirements.make_archive_runtime_plan(runtime_id, version="1.2.3",
            url="https://example.invalid/node.zip", sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data), asset_name="node.zip", executable_candidates=("node.exe",))
    def download(url, destination, progress=None, *, validate=lambda: None):
        validate()
        destination.write_bytes(data)
    monkeypatch.setattr(requirements, "resolve_managed_runtime_plan", resolve)
    monkeypatch.setattr(requirements, "_download", download)
    source = command(service)
    execute(service, source)
    settled(service, source)
    checks = []
    install = command(service, "install", source["command_id"])
    execute(service, install, validate=lambda: checks.append(1))
    controls._OPERATIONS["node"].thread.join(300)
    assert settled(service, install)["installation"]["installed"] is True
    # About one session check a second, not one per file or megabyte.
    assert len(checks) < 100, len(checks)
