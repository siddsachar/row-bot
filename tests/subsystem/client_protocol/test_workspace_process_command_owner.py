"""Real canonical command/run records with isolated disposable process effects."""
# ruff: noqa: F811 -- imported pytest fixture name.
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import sqlite3
import sys
from types import SimpleNamespace
import threading
import uuid

import pytest

from row_bot.application import workspace_process_commands as commands
from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions
from tests.subsystem.developer.test_client_workspace_processes import domain, _command  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Client-platform local process containment is supported on Windows and Linux",
)]


@pytest.fixture
def owner(domain):
    d = domain
    instance = admissions.instance_identity()
    def validate():
        pass
    def body(text=None, identifier=None):
        identifier = identifier or str(uuid.uuid4())
        review = commands.get_workspace_process_review(d.workspace.id, "chat", identifier,
            text or _command("print('synthetic result')"), validate=validate)
        return {"type": "workspace.process.start", "command_id": identifier, "client_session_id": "session",
            "expected_revision": review["conversation_revision"], "payload": {
                "target": {"kind": "workspace", **{name: review[name] for name in
                    ("resource_id", "resource_revision", "binding_id", "binding_revision")}},
                "command": review["command"], "policy_revision": review["policy_revision"],
                "action_digest": review["action_digest"], "nonce": "private-approved-nonce"}}
    def execute(value, **kwargs):
        return commands.execute_workspace_process_command(None, value, "chat", owner_id=instance,
            key=value["command_id"], validate=validate,
            **({"validate_approval": lambda review: None} | kwargs))
    def cleanup(value, process_id, kind="stop"):
        return {**value, "type": "workspace.process." + kind, "command_id": str(uuid.uuid4()),
                "payload": {"target": value["payload"]["target"], "process_id": process_id}}
    return SimpleNamespace(d=d, instance=instance, body=body, execute=execute, cleanup=cleanup)


def test_passive_review_reads_existing_policy_without_schema_or_process_effects(owner, monkeypatch):
    d = owner.d
    with sqlite3.connect(d.tasks._DB_PATH) as conn:
        before = conn.execute("SELECT COUNT(*) FROM client_commands").fetchone()[0]
    with monkeypatch.context() as patch:
        patch.setattr(d.threads, "_ensure_thread_db", lambda: pytest.fail("Read initialized schema"))
        patch.setattr(admissions, "transaction", lambda: pytest.fail("Read mutated admission store"))
        patch.setattr(d.runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("Read started a process"))
        value = owner.body()
    assert value["payload"]["policy_revision"] and value["payload"]["action_digest"]
    with sqlite3.connect(d.tasks._DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM client_commands").fetchone()[0] == before
    assert not d.runtime.tracked_processes(d.workspace.path)


def test_original_ids_are_durable_before_domain_start_and_exact_retry_never_reexecutes(owner, monkeypatch):
    d, body = owner.d, owner.body()
    original, calls = d.service.start_workspace_process, []
    def start(*args, **kwargs):
        saved = admissions.receipt(owner.instance, body["command_id"])
        assert saved["workspace_process_phase"] == "start_reserved"
        assert saved["workspace_process_id"] == body["command_id"]
        assert saved["workspace_process_run_id"] == commands._run_id(body["command_id"])
        calls.append(saved)
        return original(*args, **kwargs)
    monkeypatch.setattr(d.service, "start_workspace_process", start)
    outcome = owner.execute(body)
    assert d.state(body["command_id"]).done.wait(10)
    assert owner.execute(body) == outcome and len(calls) == 1
    assert outcome["workspace_process"]["command"] == ""
    with sqlite3.connect(d.tasks._DB_PATH) as conn:
        serialized = conn.execute("SELECT result_json FROM client_commands WHERE command_id=?", (body["command_id"],)).fetchone()[0]
    assert body["payload"]["command"] not in serialized and body["payload"]["nonce"] not in serialized


@pytest.mark.parametrize("failure", ["approval", "policy", "revision", "binding"])
def test_current_review_and_approval_fail_before_process_effect(owner, failure):
    d, body = owner.d, owner.body()
    if failure == "policy":
        with sqlite3.connect(d.threads.DB_PATH) as conn:
            conn.execute("UPDATE thread_meta SET approval_mode='block' WHERE thread_id='chat'")
    elif failure == "revision":
        body["expected_revision"] = "wrong"
    elif failure == "binding":
        body["payload"]["target"]["binding_revision"] = "wrong"
    callback = None if failure == "approval" else lambda _: None
    with pytest.raises(ClientPlatformError):
        owner.execute(body, validate_approval=callback)
    assert not d.runtime.tracked_processes(d.workspace.path) and not d.runs.list_agent_write_locks()


def test_consumed_nonce_is_bound_to_exact_review_and_second_authority_check(owner):
    d, body = owner.d, owner.body()
    def approval(review):
        assert review["command_id"] == body["command_id"]
        assert review["action_digest"] == body["payload"]["action_digest"]
        with sqlite3.connect(d.threads.DB_PATH) as conn:
            conn.execute("UPDATE thread_meta SET approval_mode='block' WHERE thread_id='chat'")
    with pytest.raises(ClientPlatformError, match="process_review_stale"):
        owner.execute(body, validate_approval=approval)
    assert not d.runtime.tracked_processes(d.workspace.path)


def test_uncertain_reserved_start_after_reload_is_never_redispatched(owner, monkeypatch):
    d, body = owner.d, owner.body()
    calls = []
    def lost(*a, **kw):
        calls.append(1)
        raise RuntimeError("lost before or after unknown effect")
    monkeypatch.setattr(d.service, "start_workspace_process", lost)
    first = owner.execute(body)
    assert first["status"] == "partial"
    importlib.reload(commands)
    second = owner.execute(body)
    assert second["status"] == "partial" and second["workspace_process_id"] == body["command_id"]
    assert calls == [1]


def test_lost_completed_command_receipt_recovers_only_after_exact_run_quiescence(owner, monkeypatch):
    d, body = owner.d, owner.body()
    original, calls = d.service.start_workspace_process, []
    def start(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(d.service, "start_workspace_process", start)
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("receipt unavailable")))
    first = owner.execute(body)
    assert first["status"] == "partial"
    assert d.state(body["command_id"]).done.wait(10)
    monkeypatch.setattr(admissions, "complete_command", complete)
    monkeypatch.setattr(d.runtime, "_ACTIVE_PROCESSES", {})
    recovered = owner.execute(body)
    assert recovered["status"] == "completed" and recovered["workspace_process"]["quiesced"]
    assert calls == [1]


def test_duplicate_start_and_owned_stop_do_not_wait_for_the_pending_start(owner, monkeypatch):
    d, body = owner.d, owner.body(_command("import threading;threading.Event().wait()"))
    entered, release = threading.Event(), threading.Event()
    original, calls = d.service.start_workspace_process, []
    def start(*a, **kw):
        result = original(*a, **kw)
        calls.append(result)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(d.service, "start_workspace_process", start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(owner.execute, body)
        assert entered.wait(10)
        try:
            duplicate = pool.submit(owner.execute, body).result(timeout=3)
            assert duplicate["status"] == "partial" and len(calls) == 1
            stopped = pool.submit(owner.execute, owner.cleanup(body, body["command_id"])).result(timeout=3)
            assert stopped["workspace_process_id"] == body["command_id"]
            assert d.state(body["command_id"]).done.wait(10)
        finally:
            release.set()
        first.result(timeout=5)
    assert not d.runs.list_agent_write_locks()


def test_chat_revision_advance_does_not_revoke_process_but_cleanup_ignores_new_start_policy(owner):
    d, body = owner.d, owner.body(_command("import threading;threading.Event().wait()"))
    owner.execute(body)
    state = d.state(body["command_id"])
    with sqlite3.connect(d.threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET client_revision=client_revision+1 WHERE thread_id='chat'")
    state.guard()
    with sqlite3.connect(d.threads.DB_PATH) as conn:
        conn.execute("UPDATE thread_meta SET approval_mode='block' WHERE thread_id='chat'")
    stopped = owner.execute(owner.cleanup(body, body["command_id"]))
    assert stopped["workspace_process_id"] == body["command_id"]
    assert state.done.wait(10) and not d.runs.list_agent_write_locks()


def test_hmac_rejects_changed_command_or_nonce_with_same_identity(owner):
    body = owner.body()
    owner.execute(body)
    assert owner.d.state(body["command_id"]).done.wait(10)
    for field in ("command", "nonce"):
        changed = {**body, "payload": {**body["payload"], field: "changed"}}
        with pytest.raises(ClientPlatformError, match="idempotency_mismatch"):
            owner.execute(changed)


def test_passive_durable_discovery_survives_registry_loss_and_redacts_private_fields(owner, monkeypatch):
    d, body = owner.d, owner.body(_command("import threading;threading.Event().wait()"))
    owner.execute(body)
    state = d.state(body["command_id"])
    target = body["payload"]["target"]
    with monkeypatch.context() as patch:
        patch.setattr(d.runtime, "_ACTIVE_PROCESSES", {})
        patch.setattr(d.runs, "ensure_agent_run_schema", lambda: pytest.fail("Passive schema mutation"))
        patch.setattr(d.runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("Passive process probe"))
        result = commands.list_durable_workspace_processes(d.workspace.id, "chat", binding_id=target["binding_id"],
            binding_revision=target["binding_revision"], validate=lambda: None)
    assert result["items"][0]["process_id"] == body["command_id"]
    assert not result["items"][0]["quiesced"] and result["items"][0]["command"] == ""
    assert str(d.root) not in json.dumps(result) and "launcher_pid" not in json.dumps(result)
    d.runtime.stop_tracked_process(state)
    assert state.done.wait(10)


def test_missing_durable_store_read_never_creates_it(domain, monkeypatch, tmp_path):
    d = domain
    missing = tmp_path / "missing-tasks.db"
    monkeypatch.setattr(d.tasks, "_DB_PATH", str(missing))
    snapshot = d.snapshot()
    page = commands.list_durable_workspace_processes(d.workspace.id, "chat", binding_id=snapshot.binding_id,
        binding_revision=snapshot.binding_revision, validate=lambda: None)
    assert page == {"items": [], "next_cursor": None} and not missing.exists()


def test_stop_before_domain_admission_is_durable_and_prevents_late_launch(owner, monkeypatch):
    d, body = owner.d, owner.body()
    entered, release = threading.Event(), threading.Event()
    original = d.service.start_workspace_process
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(d.service, "start_workspace_process", delayed)
    monkeypatch.setattr(d.runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("Cancelled reserved Start executed"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(owner.execute, body)
        assert entered.wait(10)
        try:
            control = owner.cleanup(body, body["command_id"])
            stopped = pool.submit(owner.execute, control).result(timeout=3)
            assert stopped["status"] == "partial"
            assert admissions.receipt(owner.instance, control["command_id"])["workspace_process_phase"] == "cleanup_requested"
        finally:
            release.set()
        result = pending.result(timeout=5)
    assert result["workspace_process"]["code"] == "process_revoked"
    assert result["workspace_process"]["quiesced"]
    assert not d.runtime.tracked_processes(d.workspace.path) and not d.runs.list_agent_write_locks()


def test_real_nonce_owner_rejection_remains_typed_and_effect_free(owner):
    from row_bot.api.v1.security import ClientSecurity, ClientSession
    body = owner.body()
    security = ClientSecurity("isolated", clock=lambda: 1, policy=lambda: {})
    session = ClientSession("session", "group", "binding", "csrf", 100)
    def approval(review):
        security.consume_nonce(session, review["command_id"], review["resource_revision"],
            review["action_digest"], "missing-proof", review["command_id"])
    with pytest.raises(ClientPlatformError, match="approval_expired"):
        owner.execute(body, validate_approval=approval)
    assert not owner.d.runtime.tracked_processes(owner.d.workspace.path)
    assert admissions.receipt(owner.instance, body["command_id"])["code"] == "approval_expired"


def _saved_run(owner, identifier, *, quiesced=False, binding=None):
    d, target = owner.d, owner.body()["payload"]["target"]
    identity = {"operation": "workspace.process", "command_id": identifier, "run_id": commands._run_id(identifier),
        "resource_id": d.workspace.id, "conversation_id": "chat", "binding_id": binding or target["binding_id"],
        "binding_revision": target["binding_revision"], "quiesced": quiesced, "private_path": str(d.root)}
    d.runs.create_agent_run(run_id=identity["run_id"], kind="workflow", status="running", thread_id="chat",
        parent_thread_id="chat", workspace_id=d.workspace.id, workspace_path=str(d.root),
        display_name="Synthetic recovery record", prompt="", result_json=identity)
    return identity


def test_durable_paging_filters_full_saved_history_and_binds_cursor(owner):
    identifiers = [str(uuid.uuid4()) for _ in range(35)]
    for identifier in identifiers:
        _saved_run(owner, identifier)
    _saved_run(owner, str(uuid.uuid4()), quiesced=True)
    _saved_run(owner, str(uuid.uuid4()), binding="other-binding")
    target = owner.body()["payload"]["target"]
    def page(**kwargs):
        return commands.list_durable_workspace_processes(owner.d.workspace.id, "chat", binding_id=target["binding_id"],
            binding_revision=target["binding_revision"], validate=lambda: None, **kwargs)
    first = page()
    second = page(cursor=first["next_cursor"])
    assert [item["process_id"] for item in first["items"] + second["items"]] == identifiers[::-1]
    assert len(first["items"]) == 32 and second["next_cursor"] is None
    for cursor in ("bad", "x" * 100, first["next_cursor"].replace(":", ":0"), "0" * 32 + ":1"):
        with pytest.raises(ClientPlatformError, match="cursor_expired"):
            page(cursor=cursor)


@pytest.mark.parametrize("malformation", ["view", "malformed_identity", "oversized"])
def test_malformed_durable_history_fails_without_partial_or_private_output(owner, malformation):
    identity = _saved_run(owner, str(uuid.uuid4()))
    with sqlite3.connect(owner.d.tasks._DB_PATH) as conn:
        if malformation == "view":
            conn.execute("ALTER TABLE agent_runs RENAME TO source_runs")
            conn.execute("CREATE VIEW agent_runs AS SELECT * FROM source_runs")
        else:
            if malformation == "malformed_identity":
                identity["command_id"] = "private-user-path"
            else:
                identity["private_path"] = "x" * (1024 * 1024)
            conn.execute("UPDATE agent_runs SET result_json=?", (json.dumps(identity),))
    target = owner.body()["payload"]["target"]
    with pytest.raises(ClientPlatformError, match="process_history_unavailable"):
        commands.list_durable_workspace_processes(owner.d.workspace.id, "chat", binding_id=target["binding_id"],
            binding_revision=target["binding_revision"], validate=lambda: None)


def test_revoked_authority_cannot_claim_or_read_owned_receipts(owner):
    body = owner.body()
    def revoked():
        raise ClientPlatformError("session_revoked")
    with pytest.raises(ClientPlatformError, match="session_revoked"):
        commands.execute_workspace_process_command(None, body, "chat", owner_id=owner.instance,
            key=body["command_id"], validate=revoked, validate_approval=lambda _: None)
    assert admissions.receipt(owner.instance, body["command_id"]) is None


def test_failed_writer_release_is_never_reported_as_completed_cleanup(owner, monkeypatch):
    d, body = owner.d, owner.body(_command("import threading;threading.Event().wait()"))
    owner.execute(body)
    state = d.state(body["command_id"])
    original = d.runs.release_agent_write_lock
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic locked writer store")
    monkeypatch.setattr(d.runs, "release_agent_write_lock", unavailable)
    try:
        owner.execute(owner.cleanup(body, body["command_id"]))
        assert state.done.wait(10)
        assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)
        settled = owner.execute(owner.cleanup(body, body["command_id"]))
        assert settled["status"] == "partial"
        assert not settled["workspace_process"]["quiesced"]
        assert state.quiesced, "The retained original OS proof must survive the ledger failure"
        monkeypatch.setattr(d.runs, "release_agent_write_lock", original)
        monkeypatch.setattr(d.runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("Ledger recovery launched a process"))
        recovered = owner.execute(owner.cleanup(body, body["command_id"], "recover"))
        assert recovered["status"] == "completed" and recovered["workspace_process"]["quiesced"]
        assert not d.runs.list_agent_write_locks()
        assert d.runs.get_agent_run(commands._run_id(body["command_id"]))["result_json"]["quiesced"] is True
    finally:
        monkeypatch.setattr(d.runs, "release_agent_write_lock", original)
        original(run_id=commands._run_id(body["command_id"]))


def test_second_stage_history_failure_retains_original_diagnostic_and_retries_only_ledger(owner, monkeypatch):
    d, body = owner.d, owner.body(_command("import threading;threading.Event().wait()"))
    owner.execute(body)
    state = d.state(body["command_id"])
    original = d.runs.finish_agent_run
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic failed final record")
    monkeypatch.setattr(d.runs, "finish_agent_run", unavailable)
    with state.lock:
        state.code = "process_revoked"
    owner.execute(owner.cleanup(body, body["command_id"]))
    assert state.done.wait(10) and state.quiesced
    assert not d.runs.list_agent_write_locks()
    incomplete = owner.execute(owner.cleanup(body, body["command_id"], "recover"))
    assert incomplete["status"] == "partial" and not incomplete["workspace_process"]["quiesced"]
    monkeypatch.setattr(d.runs, "finish_agent_run", original)
    monkeypatch.setattr(d.runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("Ledger recovery launched a process"))
    recovered = owner.execute(owner.cleanup(body, body["command_id"], "recover"))
    assert recovered["status"] == "completed" and recovered["workspace_process"]["quiesced"]
    assert recovered["workspace_process"]["code"] == "process_revoked"
    assert d.runs.get_agent_run(commands._run_id(body["command_id"]))["result_json"]["code"] == "process_revoked"


def test_preexecution_failure_does_not_hide_an_unreleased_writer_from_recovery_history(owner, monkeypatch):
    d, body = owner.d, owner.body()
    original = d.runs.release_agent_write_lock
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic locked writer store")
    monkeypatch.setattr(d.runs, "release_agent_write_lock", unavailable)
    monkeypatch.setattr(d.runtime, "launch_tracked_process", lambda *a, **kw: (_ for _ in ()).throw(ValueError("process_bootstrap_failed")))
    try:
        result = owner.execute(body)
        assert result["status"] == "partial" and not result["workspace_process"]["quiesced"]
        saved = d.runs.get_agent_run(commands._run_id(body["command_id"]))["result_json"]
        assert saved.get("quiesced") is not True
        assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)
    finally:
        monkeypatch.setattr(d.runs, "release_agent_write_lock", original)
        original(run_id=commands._run_id(body["command_id"]))


def test_capacity_and_bulk_stop_retain_failed_finalization_until_explicit_recovery(owner, monkeypatch):
    import sys
    d, body = owner.d, owner.body(_command("import threading;threading.Event().wait()"))
    owner.execute(body)
    state = d.state(body["command_id"])
    original = d.runs.release_agent_write_lock
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic locked writer store")
    monkeypatch.setattr(d.runs, "release_agent_write_lock", unavailable)
    try:
        owner.execute(owner.cleanup(body, body["command_id"]))
        assert state.done.wait(10) and state.quiesced and not state.finalization_complete
        monkeypatch.setattr(d.runtime, "_PROCESS_LIMIT", 1)
        with pytest.raises(ValueError, match="process_limit"):
            d.runtime.launch_tracked_process(d.root, [sys.executable, "-c", "print('must not run')"], "synthetic capacity")
        d.runtime.stop_workspace_processes(str(d.root))
        assert d.state(body["command_id"]) is state
        monkeypatch.setattr(d.runs, "release_agent_write_lock", original)
        recovered = owner.execute(owner.cleanup(body, body["command_id"], "recover"))
        assert recovered["workspace_process"]["quiesced"] and state.finalization_complete
        later = d.runtime.launch_tracked_process(d.root, [sys.executable, "-c", "print('legacy compatible')"], "synthetic capacity")
        assert later.done.wait(10) and later.quiesced
        assert state not in d.runtime.tracked_processes(str(d.root))
    finally:
        monkeypatch.setattr(d.runs, "release_agent_write_lock", original)
        original(run_id=commands._run_id(body["command_id"]))


def test_nonce_expiry_while_waiting_for_domain_admission_prevents_late_execution(owner, monkeypatch):
    from row_bot.api.v1.security import ClientSecurity, ClientSession
    d, body = owner.d, owner.body()
    now, approved = [1.0], threading.Event()
    security = ClientSecurity("isolated", clock=lambda: now[0], policy=lambda: {})
    session = ClientSession("session", "group", "binding", "csrf", 1000)
    target = body["payload"]["target"]
    nonce = security.approval_nonce(session, body["command_id"], target["resource_revision"], body["payload"]["action_digest"])
    body["payload"]["nonce"] = nonce
    def approval(review):
        security.consume_nonce(session, review["command_id"], review["resource_revision"],
            review["action_digest"], nonce, review["command_id"])
        approved.set()
    admission_lock, waiting = d.service._START_LOCK, threading.Event()
    class BarrierLock:
        def __enter__(self):
            waiting.set()
            admission_lock.acquire()
        def __exit__(self, *_args):
            admission_lock.release()
    monkeypatch.setattr(d.service, "_START_LOCK", BarrierLock())
    monkeypatch.setattr(d.runtime.subprocess, "Popen", lambda *a, **kw: pytest.fail("Expired nonce started a process"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with admission_lock:
            future = pool.submit(owner.execute, body, validate_approval=approval)
            assert approved.wait(5)
            assert waiting.wait(5)
            now[0] = 302
        result = future.result(timeout=10)
    assert result["workspace_process"]["quiesced"] and result["workspace_process"]["state"] == "failed"
    assert not d.runtime.tracked_processes(d.workspace.path) and not d.runs.list_agent_write_locks()


def test_admission_failure_with_failed_callback_retains_finished_os_owner_for_recovery(owner, monkeypatch):
    d, body = owner.d, owner.body(_command("from pathlib import Path;Path('must-not-exist').write_text('unexpected')"))
    append, release = d.runs.append_agent_event, d.runs.release_agent_write_lock
    def rejected_event(run_id, event_type, *args, **kwargs):
        if event_type == "workspace.process.owner":
            raise ValueError("synthetic owner admission denied")
        return append(run_id, event_type, *args, **kwargs)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic locked writer store")
    monkeypatch.setattr(d.runs, "append_agent_event", rejected_event)
    monkeypatch.setattr(d.runs, "release_agent_write_lock", unavailable)
    try:
        result = owner.execute(body)
        state = d.state(body["command_id"])
        assert result["status"] == "partial" and not result["workspace_process"]["quiesced"]
        assert state.done.is_set() and state.quiesced and not state.finalization_complete
        assert not (d.root / "must-not-exist").exists()
        monkeypatch.setattr(d.runs, "append_agent_event", append)
        monkeypatch.setattr(d.runs, "release_agent_write_lock", release)
        recovered = owner.execute(owner.cleanup(body, body["command_id"], "recover"))
        assert recovered["workspace_process"]["quiesced"] and state.finalization_complete
        assert recovered["workspace_process"]["code"] == "process_admission_failed"
    finally:
        monkeypatch.setattr(d.runs, "append_agent_event", append)
        monkeypatch.setattr(d.runs, "release_agent_write_lock", release)
        release(run_id=commands._run_id(body["command_id"]))
