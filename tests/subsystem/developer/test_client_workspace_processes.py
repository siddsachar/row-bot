from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Client-platform local process containment is supported on Windows and Linux",
)]


def _command(code):
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


@pytest.fixture
def domain(tmp_path, monkeypatch):
    from row_bot import threads, tasks, agent_runs, conversation_resources
    from row_bot.developer import client_processes as service, runtime, storage, sandbox_runtime
    from row_bot.developer.state import DeveloperWorkspace
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(agent_runs, "_SCHEMA_READY_PATH", None)
    monkeypatch.setattr(threads, "_thread_write_blocked", lambda _: False)
    threads._ensure_thread_db()
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("INSERT INTO thread_meta(thread_id,name,approval_mode) VALUES ('chat','Chat','approve')")
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "registry")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "registry/workspaces.json")
    monkeypatch.setattr(sandbox_runtime, "SANDBOX_ROOT", tmp_path / "sandboxes")
    monkeypatch.setattr(runtime, "_ACTIVE_PROCESSES", {})
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = DeveloperWorkspace(id="workspace-fixture", name="Fixture", path=str(root))
    storage.save_workspace(workspace)
    conversation_resources.bind("chat", "workspace", workspace.id, expected_revision=0)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Implicit Git/container/provider subprocess")
    monkeypatch.setattr(runtime.subprocess, "run", forbidden)
    monkeypatch.setattr(sandbox_runtime, "ensure_docker_sandbox", forbidden)
    def snapshot():
        return service.list_workspace_processes(workspace.id, "chat")
    def start(code="print('done')", *, command=None, command_id=None, confirmed=True, captured=None, **kwargs):
        value = captured or snapshot()
        return service.start_workspace_process(workspace.id, "chat", command or _command(code),
            command_id=command_id or str(uuid.uuid4()), expected_resource_revision=value.resource_revision,
            expected_binding_id=value.binding_id, expected_binding_revision=value.binding_revision,
            confirmed=confirmed, **kwargs)
    def state(process_id):
        return next(item for item in runtime.tracked_processes(str(root)) if item.process_id == process_id)
    fixture = SimpleNamespace(service=service, runtime=runtime, root=root, workspace=workspace,
        start=start, state=state, snapshot=snapshot, runs=agent_runs, tasks=tasks, threads=threads,
        resources=conversation_resources, storage=storage, sandbox=sandbox_runtime)
    yield fixture
    for value in runtime.tracked_processes(str(root)):
        runtime.stop_tracked_process(value)
        assert value.done.wait(10), "owned process cleanup did not return"


def test_passive_list_and_unapproved_start_have_no_process_or_run_effect(domain):
    d = domain
    assert d.snapshot().processes == () and not Path(d.tasks._DB_PATH).exists()
    result = d.start(confirmed=False)
    assert result.code == "process_approval_required" and result.quiesced
    assert d.snapshot().processes == () and not Path(d.tasks._DB_PATH).exists()


def test_actual_process_captures_channels_and_releases_registered_writer_on_exit(domain):
    d = domain
    started = d.start("import sys; print('stdout'); sys.stderr.write('stderr')")
    state = d.state(started.process_id)
    assert state.done.wait(10)
    result = d.snapshot().processes[0]
    assert result.state == "exited" and result.exit_code == 0 and result.quiesced
    output = d.service.get_workspace_process_output(d.workspace.id, "chat", result.process_id)
    assert "stdout" in "".join(item.text for item in output.entries if item.channel == "stdout")
    assert "stderr" in "".join(item.text for item in output.entries if item.channel == "stderr")
    assert d.runs.list_agent_write_locks() == []
    assert d.runs.get_agent_run(result.run_id)["status"] == "completed"


def test_active_writer_is_held_until_exact_owned_stop(domain):
    d = domain
    started = d.start("import threading; print('ready', flush=True); threading.Event().wait()")
    state = d.state(started.process_id)
    with state.changed:
        assert state.changed.wait_for(lambda: any("ready" in item[2] for item in state.output), timeout=5)
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)["run_id"] == started.run_id
    with pytest.raises(ValueError, match="process_unavailable"):
        d.service.stop_workspace_process(d.workspace.id, "chat", str(uuid.uuid4()))
    assert not state.quiesced
    d.service.stop_workspace_process(d.workspace.id, "chat", started.process_id)
    assert state.done.wait(10) and state.quiesced
    assert d.runs.list_agent_write_locks() == []


def test_response_retry_never_starts_second_process_and_rejects_changed_intent(domain):
    d = domain
    command_id = str(uuid.uuid4())
    first = d.start(command_id=command_id)
    assert d.state(first.process_id).done.wait(10)
    retry = d.start(command_id=command_id)
    assert retry.process_id == first.process_id and retry.quiesced
    assert len(d.runtime.tracked_processes(str(d.root))) == 1
    changed = d.start("print('changed')", command_id=command_id)
    assert changed.code == "process_identity_conflict"


def test_full_output_is_drained_but_tail_and_json_pages_are_bounded(domain):
    d = domain
    started = d.start("import sys; sys.stdout.write('x'*2000000); sys.stdout.write('TAIL'); sys.stderr.write('ERR')")
    state = d.state(started.process_id)
    assert state.done.wait(15), "verbose subprocess blocked on a full pipe"
    assert state.output_bytes <= 256 * 1024 and len(state.output) <= 1024
    cursor, text, saw_gap = 0, "", False
    while True:
        page = d.service.get_workspace_process_output(d.workspace.id, "chat", started.process_id, cursor)
        assert len(json.dumps(asdict(page), ensure_ascii=True).encode()) <= 16 * 1024
        saw_gap |= page.truncated
        text += "".join(item.text for item in page.entries)
        if page.next_cursor == cursor:
            break
        assert page.next_cursor > cursor
        cursor = page.next_cursor
    assert saw_gap and "TAIL" in text and "ERR" in text


def test_tiny_output_entries_have_bounded_overhead_and_invalid_cursor_rejects(domain):
    d = domain
    first = d.start()
    state = d.state(first.process_id)
    assert state.done.wait(10)
    for _ in range(5000):
        state.append("stdout", "\x01")
    assert len(state.output) <= 1024
    page = d.service.get_workspace_process_output(d.workspace.id, "chat", first.process_id)
    assert len(json.dumps(asdict(page), ensure_ascii=True).encode()) <= 16 * 1024
    for cursor in (-1, True, state.sequence + 1):
        with pytest.raises(ValueError, match="process_cursor_invalid"):
            d.service.get_workspace_process_output(d.workspace.id, "chat", first.process_id, cursor)


def test_live_capability_revocation_stops_owned_process(domain):
    d = domain
    revoked = threading.Event()
    def validate():
        if revoked.is_set():
            raise ValueError("capability_revoked")
    started = d.start("import threading; threading.Event().wait()", validate=validate)
    state = d.state(started.process_id)
    revoked.set()
    assert state.done.wait(10) and state.quiesced and state.code == "process_revoked"
    assert d.runs.list_agent_write_locks() == []


def test_detach_readd_cannot_revive_stale_start_and_does_not_release_other_writer(domain):
    d = domain
    before = d.snapshot()
    detached = d.resources.unbind("chat", before.binding_id,
        expected_revision=d.resources.list_bindings("chat").bindings_revision)
    d.resources.bind("chat", "workspace", d.workspace.id, expected_revision=detached.bindings_revision)
    result = d.start(captured=before)
    assert result.code == "resource_revision_conflict" and d.snapshot().processes == ()
    assert not Path(d.tasks._DB_PATH).exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows descendant handle verification")
def test_job_owns_spawned_descendant_until_it_is_dead(domain):
    import ctypes
    from ctypes import wintypes
    d = domain
    child = "import threading; threading.Event().wait()"
    code = "import subprocess,sys,threading; p=subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print('CHILD:'+str(p.pid),flush=True); threading.Event().wait()"
    started = d.start(code)
    state = d.state(started.process_id)
    with state.changed:
        assert state.changed.wait_for(lambda: any("CHILD:" in item[2] for item in state.output), timeout=5)
        text = "".join(item[2] for item in state.output)
    pid = int(text.split("CHILD:")[1].splitlines()[0])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    assert handle
    try:
        assert kernel.WaitForSingleObject(handle, 0) == 258
        d.service.stop_workspace_process(d.workspace.id, "chat", started.process_id)
        assert state.done.wait(10) and state.quiesced
        assert kernel.WaitForSingleObject(handle, 0) == 0
        assert d.runs.list_agent_write_locks() == []
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows pre-execution Job assignment")
def test_containment_assignment_failure_never_executes_command(domain, monkeypatch):
    from row_bot.plugins import worker_ownership
    d = domain
    def fail(_process):
        raise RuntimeError("injected assignment unavailable")
    monkeypatch.setattr(worker_ownership, "WindowsJob", fail)
    marker = d.root / "must-not-exist"
    result = d.start("from pathlib import Path; Path('must-not-exist').write_text('bad')")
    assert result.state == "failed" and result.code == "process_admission_failed"
    assert result.quiesced and not marker.exists() and d.runs.list_agent_write_locks() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows Job quiescence refusal")
def test_failed_quiescence_retains_writer_and_prevents_replacement(domain, monkeypatch):
    from row_bot.plugins import worker_ownership
    d = domain
    original = worker_ownership.WindowsJob
    class UnconfirmedJob:
        def __init__(self, process):
            self.actual = original(process)
        def close(self):
            assert self.actual.close()  # Kill actual disposable children safely.
            return False  # An owner must not turn this into a positive claim.
    monkeypatch.setattr(worker_ownership, "WindowsJob", UnconfirmedJob)
    first = d.start()
    state = d.state(first.process_id)
    assert state.done.wait(10)
    assert not state.quiesced and state.state == "cleanup_incomplete"
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)["run_id"] == first.run_id
    second = d.start()
    assert second.code == "workspace_writer_busy"
    assert len(d.runtime.tracked_processes(str(d.root))) == 1


def test_startup_timeout_terminates_actual_owned_bootstrap(domain, monkeypatch, tmp_path):
    d = domain
    bootstrap = tmp_path / "process_worker.py"
    bootstrap.write_text("import threading; threading.Event().wait()\n")
    monkeypatch.setattr(d.runtime, "__file__", str(tmp_path / "runtime.py"))
    state = d.runtime.launch_tracked_process(d.root, [sys.executable, "-V"], "fixture", startup_timeout=0.05)
    assert state.code == "process_start_timeout"
    assert state.done.wait(10) and state.process.poll() is not None
    assert state.host_quiesced
    if sys.platform.startswith("linux"):
        # This fake supervisor never supplies the required descendant receipt.
        assert not state.quiesced and not state.remote_done.is_set()
    else:
        assert state.quiesced


def test_postcommit_admission_event_failure_releases_only_own_writer(domain, monkeypatch):
    d = domain
    original = d.runs.append_agent_event
    def fail(run_id, event_type, *args, **kwargs):
        if event_type == "write_lock.acquired":
            raise RuntimeError("private event failure")
        return original(run_id, event_type, *args, **kwargs)
    monkeypatch.setattr(d.runs, "append_agent_event", fail)
    result = d.start()
    assert result.state == "failed" and d.snapshot().processes == ()
    assert d.runs.list_agent_write_locks() == []


def test_revoked_attempt_cannot_release_preexisting_canonical_run(domain):
    d = domain
    command_id = str(uuid.uuid4())
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-process:" + command_id).hex
    d.runs.create_agent_run(run_id=run_id, kind="workflow", status="running", thread_id="foreign")
    assert d.runs.acquire_agent_write_lock("developer:foreign", run_id)
    def revoked():
        raise ValueError("capability_revoked")
    result = d.start(command_id=command_id, validate=revoked)
    assert result.state == "failed"
    assert d.runs.get_agent_write_lock("developer:foreign")["run_id"] == run_id
    assert d.runs.get_agent_run(run_id)["status"] == "running"
    d.runs.release_agent_write_lock(run_id=run_id)


def test_missing_executable_finishes_without_private_bootstrap_diagnostics(domain):
    d = domain
    result = d.start(command="row-bot-disposable-nonexistent-executable")
    state = d.state(result.process_id)
    assert state.done.wait(10) and state.quiesced
    assert state.code == "process_start_failed" and d.runs.list_agent_write_locks() == []
    output = d.service.get_workspace_process_output(d.workspace.id, "chat", result.process_id)
    assert output.entries == ()


@pytest.fixture
def docker(domain, tmp_path, monkeypatch):
    d = domain
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    monkeypatch.setattr(d.sandbox, "detect_container_runtime", lambda: SimpleNamespace(available=True, binary="fake-docker"))
    monkeypatch.setattr(d.runtime, "_REMOTE_STOP_TIMEOUT", 0.05)
    receipt_path = tmp_path / "fake-remote-receipt.json"
    script = tmp_path / "fake-docker-transport.py"
    script.write_text('''import json,hmac,hashlib,sys,os
p=json.loads(sys.stdin.buffer.readline())
mode=sys.argv[1]
path=sys.argv[2]
key=bytes.fromhex(p['key'])
seq=0
def signed(value):
    data=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
    return {'payload':value,'signature':hmac.new(key,data,hashlib.sha256).hexdigest()}
def emit(event,**fields):
    global seq
    seq+=1
    sys.stdout.write(json.dumps({'version':1,'sequence':seq,'event':event,**fields})+'\\n')
    sys.stdout.flush()
value={'version':1,'owner_id':p['owner_id'],'container_id':p['container_id'],'supervisor_pid':1234,'start_time':'5678','state':'running','quiesced':False,'exit_code':None,'output_incomplete':False}
receipt=signed(value)
with open(path,'w') as f: json.dump(receipt,f)
emit('started',pid=1235,receipt=receipt)
if mode=='lost': os._exit(0)
if mode=='hold': sys.stdin.buffer.readline()
emit('output',channel='stdout',text='Docker fixture output\\n')
value.update(state='complete',quiesced=True,exit_code=0,output_incomplete=False)
if mode=='foreign-owner': value['owner_id']='00000000-0000-0000-0000-000000000000'
if mode=='incomplete': value['output_incomplete']=True
receipt=signed(value)
if mode=='bad-signature': receipt['signature']='0'*64
with open(path,'w') as f: json.dump(receipt,f)
emit('exited',exit_code=0,output_incomplete=value['output_incomplete'],receipt=receipt)
''', encoding="utf-8")
    options = {"mode": "complete", "inspect_changes": {}, "recovery": "complete"}
    queries = []
    def query(argv, payload=None, **kwargs):
        queries.append((argv, payload))
        if argv[1] == "inspect":
            return json.dumps({"id": "c" * 64, "running": True, "network": "none", "image": workspace.sandbox_image,
                "workspace_id": workspace.id, "mounts": [{"Destination": "/workspace", "Source": str(shadow), "RW": True}],
                **options["inspect_changes"]}).encode()
        assert payload["action"] in {"read", "stop"} and "key" not in payload
        receipt = json.loads(receipt_path.read_text())
        if payload["action"] == "stop":
            assert payload["supervisor_pid"] == receipt["payload"]["supervisor_pid"]
            assert payload["start_time"] == receipt["payload"]["start_time"]
            run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-process:" + receipt["payload"]["owner_id"]).hex
            identity = d.runs.get_agent_run(run_id)["result_json"]
            receipt["payload"].update(state="complete", quiesced=True, exit_code=130)
            key = d.service._receipt_key(identity)
            from row_bot.developer.process_worker import _signed_receipt
            receipt = _signed_receipt(receipt["payload"], key)
            if options["recovery"] == "tamper":
                receipt["signature"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt))
        return json.dumps({"receipt": receipt}).encode()
    def bootstrap(binary, container_id, *, recover=False):
        assert binary == "fake-docker" and container_id == "c" * 64
        return (["fake-docker", "recover"] if recover else
                [sys.executable, "-I", "-S", "-B", str(script), options["mode"], str(receipt_path)])
    monkeypatch.setattr(d.runtime, "run_process_control_query", query)
    monkeypatch.setattr(d.service, "_docker_bootstrap", bootstrap)
    return SimpleNamespace(d=d, options=options, queries=queries, receipt_path=receipt_path, shadow=shadow)


def test_prepared_docker_completion_requires_valid_signed_proof_and_keeps_key_private(docker):
    d = docker.d
    result = d.start(command="python3 -V")
    state = d.state(result.process_id)
    assert state.done.wait(10) and state.quiesced
    run = d.runs.get_agent_run(result.run_id)
    assert run["result_json"]["completion_receipt"]["payload"]["quiesced"]
    key = d.service._receipt_key(state.metadata).hex()
    assert key not in json.dumps(run) and key not in json.dumps(asdict(d.snapshot()))
    assert d.runs.list_agent_write_locks() == []
    assert docker.queries[0][0][1] == "inspect"


@pytest.mark.parametrize("mode", ["bad-signature", "foreign-owner", "incomplete"])
def test_remote_invalid_completion_never_releases_writer(docker, mode):
    d = docker.d
    docker.options["mode"] = mode
    result = d.start(command="python3 -V")
    state = d.state(result.process_id)
    assert state.done.wait(10) and not state.quiesced
    assert state.state == "cleanup_incomplete"
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)["run_id"] == result.run_id


def test_remote_transport_eof_is_not_completion_and_exact_owner_recovery_works(docker):
    d = docker.d
    docker.options["mode"] = "lost"
    result = d.start(command="python3 -V")
    state = d.state(result.process_id)
    assert state.done.wait(10) and not state.quiesced
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)
    recovered = d.service.recover_workspace_process(d.workspace.id, "chat", result.process_id)
    assert recovered.quiesced and recovered.exit_code == 130
    assert d.runs.list_agent_write_locks() == []
    assert any(payload and payload["action"] == "stop" for _, payload in docker.queries)


def test_recovery_after_registry_reload_uses_canonical_run_and_verified_dead_launcher(docker, monkeypatch):
    d = docker.d
    docker.options["mode"] = "lost"
    result = d.start(command="python3 -V")
    state = d.state(result.process_id)
    assert state.done.wait(10) and not state.quiesced
    monkeypatch.setattr(d.runtime, "_ACTIVE_PROCESSES", {})
    recovered = d.service.recover_workspace_process(d.workspace.id, "chat", result.process_id)
    assert recovered.quiesced and d.runs.list_agent_write_locks() == []


def test_recovery_rejects_container_replacement_before_signalling_any_owner(docker):
    d = docker.d
    docker.options["mode"] = "lost"
    result = d.start(command="python3 -V")
    assert d.state(result.process_id).done.wait(10)
    docker.options["inspect_changes"] = {"id": "d" * 64}
    recovered = d.service.recover_workspace_process(d.workspace.id, "chat", result.process_id)
    assert not recovered.quiesced and d.runs.list_agent_write_locks()
    assert not any(payload and payload["action"] == "stop" for _, payload in docker.queries)


def test_recovery_rejects_bad_signature_and_preserves_lease(docker):
    d = docker.d
    docker.options.update(mode="lost", recovery="tamper")
    result = d.start(command="python3 -V")
    assert d.state(result.process_id).done.wait(10)
    recovered = d.service.recover_workspace_process(d.workspace.id, "chat", result.process_id)
    assert not recovered.quiesced and d.runs.list_agent_write_locks()


@pytest.mark.parametrize("changes", [
    {"running": False}, {"workspace_id": "other-workspace"}, {"image": "foreign-image"},
    {"network": "bridge"}, {"mounts": []},
    {"mounts": [{"Destination": "/workspace", "Source": "//foreign-server/share", "RW": True}]},
])
def test_prepared_container_mismatch_never_starts_command_or_creates_owner(docker, changes):
    d = docker.d
    docker.options["inspect_changes"] = changes
    result = d.start(command="python3 -V")
    assert result.quiesced and result.state == "failed"
    assert not docker.receipt_path.exists() and not d.runtime.tracked_processes(d.workspace.path)
    assert d.runs.list_agent_write_locks() == []


def test_remote_explicit_stop_waits_for_signed_completion(docker):
    d = docker.d
    docker.options["mode"] = "hold"
    result = d.start(command="python3 -V")
    state = d.state(result.process_id)
    assert state.ready.is_set() and not state.quiesced
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)
    d.service.stop_workspace_process(d.workspace.id, "chat", result.process_id)
    assert state.done.wait(10) and state.quiesced
    assert state.remote_receipt["payload"]["quiesced"]
    assert not d.runs.list_agent_write_locks()


def test_remote_receipt_key_is_bound_to_run_owner_and_container(docker):
    d = docker.d
    result = d.start(command="python3 -V")
    state = d.state(result.process_id)
    assert state.done.wait(10)
    key = d.service._receipt_key(state.metadata)
    for field in ("run_id", "command_id", "container_id"):
        identity = {**state.metadata, field: "changed"}
        assert d.service._receipt_key(identity) != key
        with pytest.raises(ValueError, match="process_receipt_invalid"):
            d.service._verify_receipt(state.remote_receipt, identity, d.service._receipt_key(identity))


@pytest.mark.parametrize("code", [
    "import sys;sys.stdout.write('x'*10000)",
    "import sys;sys.stderr.write('x'*10000)",
    "import threading;threading.Event().wait(60)",
])
def test_real_control_query_bounds_output_and_terminates_on_timeout(domain, code):
    with pytest.raises(ValueError, match="process_control_unavailable"):
        domain.runtime.run_process_control_query([sys.executable, "-I", "-S", "-B", "-c", code], timeout=0.15)


def test_remote_recovery_pid_reuse_refuses_signal_and_closes_handle(monkeypatch, tmp_path):
    import io
    from row_bot.developer import process_worker as worker
    receipt = {"payload": {"state": "running"}}
    output, closed, signalled = io.BytesIO(), [], []
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setattr(worker.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(worker, "_owner_directory", lambda owner_id: tmp_path)
    monkeypatch.setattr(worker, "_read_receipt", lambda path: receipt)
    monkeypatch.setattr(worker, "_process_identity", lambda pid: (1, "replacement-start-time"))
    monkeypatch.setattr(worker.os, "pidfd_open", lambda pid: 12345, raising=False)
    monkeypatch.setattr(worker.os, "close", closed.append)
    monkeypatch.setattr(worker.signal, "pidfd_send_signal", lambda *args: signalled.append(args), raising=False)
    result = worker._recover_owner({"action": "stop", "owner_id": str(uuid.uuid4()),
                                    "supervisor_pid": 1234, "start_time": "original-start-time"})
    assert result == 125 and closed == [12345] and signalled == []
    assert json.loads(output.getvalue()) == {"error": "process_recovery_unavailable"}


def test_prepared_supervisor_fails_before_command_when_kernel_pidfds_unavailable(monkeypatch):
    import ctypes
    from row_bot.developer import process_worker as worker
    emitted = []
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setattr(worker, "_emit", lambda event, **fields: emitted.append((event, fields)))
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(prctl=lambda *args: 0))
    def unavailable(*args):
        raise OSError("kernel has no pidfd")
    monkeypatch.setattr(worker.os, "pidfd_open", unavailable, raising=False)
    monkeypatch.setattr(worker.signal, "pidfd_send_signal", lambda *args: None, raising=False)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No command before containment"))
    assert worker._remote_process(["never"], {}) == 126
    assert emitted == [("failed", {"code": "process_containment_unavailable"})]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Native Linux subreaper and pidfd descendant proof")
def test_local_linux_detached_descendant_is_reaped_before_writer_release(domain):
    import psutil
    d = domain
    code = ("import subprocess,sys,threading; "
        "child=subprocess.Popen([sys.executable,'-c','import threading; threading.Event().wait()'], start_new_session=True); "
        "print('DESCENDANT='+str(child.pid),flush=True); threading.Event().wait()")
    result = d.start(code)
    state = d.state(result.process_id)
    with state.changed:
        assert state.changed.wait_for(lambda: any("DESCENDANT=" in row[2] for row in state.output), timeout=5)
    text = "".join(row[2] for row in state.output)
    pid = int(text.split("DESCENDANT=", 1)[1].splitlines()[0])
    child = psutil.Process(pid)
    assert child.is_running() and state.metadata["process_target"] == "local-linux"
    d.service.stop_workspace_process(d.workspace.id, "chat", result.process_id)
    assert state.done.wait(10) and state.quiesced
    assert not child.is_running() and not d.runs.list_agent_write_locks()
    assert state.remote_receipt["payload"]["quiesced"]


def test_unsupported_local_containment_rejects_before_any_command(domain, monkeypatch):
    from types import SimpleNamespace
    d = domain
    # Change only the service's platform view, never global pathlib/os behavior.
    monkeypatch.setattr(d.service, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(d.service, "os", SimpleNamespace(name="posix"))
    result = d.start()
    assert result.code == "process_containment_unavailable" and result.quiesced
    assert d.runtime.tracked_processes(d.workspace.path) == ()
    assert not d.runs.list_agent_write_locks()


def test_legacy_runtime_does_not_inherit_new_client_platform_refusal(domain, monkeypatch):
    d = domain
    monkeypatch.setattr(d.runtime, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))
    state = d.runtime.launch_tracked_process(d.root, [sys.executable, "-c", "print('legacy')"], "legacy fixture")
    assert state.done.wait(10)
    assert state.exit_code == 0 and any("legacy" in row[2] for row in state.output)


def test_output_frame_must_fit_a_single_bounded_json_page(domain, tmp_path):
    d = domain
    script = tmp_path / "oversized-output-frame.py"
    script.write_text("import json,sys\nsys.stdin.buffer.readline()\n"
        "print(json.dumps({'version':1,'sequence':1,'event':'started','pid':1}),flush=True)\n"
        "print(json.dumps({'version':1,'sequence':2,'event':'output','channel':'stdout','text':'\\U0001f600'*2048}),flush=True)\n",
        encoding="utf-8")
    state = d.runtime.launch_tracked_process(d.root, ["unused"], "fake transport",
        bootstrap_argv=[sys.executable, "-I", "-S", "-B", str(script)])
    assert state.done.wait(10) and state.quiesced
    assert state.code == "process_output_invalid" and not state.output
