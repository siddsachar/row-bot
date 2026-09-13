from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def domain(tmp_path, monkeypatch):
    from row_bot import threads, tasks, agent_runs, conversation_resources
    from row_bot.developer import client_edits as service, storage, edits, change_ledger, sandbox_runtime
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
    monkeypatch.setattr(change_ledger, "DEVELOPER_DIR", tmp_path / "registry")
    monkeypatch.setattr(change_ledger, "LEDGER_PATH", tmp_path / "registry/change_ledger.json")
    monkeypatch.setattr(sandbox_runtime, "SANDBOX_ROOT", tmp_path / "sandboxes")
    monkeypatch.setattr(sandbox_runtime, "PENDING_CHANGES_PATH", tmp_path / "sandboxes/pending_changes.json")
    root = tmp_path / "selected"
    root.mkdir()
    from row_bot.developer.state import DeveloperWorkspace
    workspace = DeveloperWorkspace(id="workspace-fixture", name="Fixture", path=str(root))
    storage.save_workspace(workspace)
    conversation_resources.bind("chat", "workspace", workspace.id, expected_revision=0)
    # The authenticated host owns initial key setup. Passive editor reads must
    # use that existing key, never create a new store or initialization effect.
    from row_bot.runtime import admissions
    admissions.instance_identity()
    initial_db_bytes = Path(tasks._DB_PATH).read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("Editor invoked a runtime/provider/Git action")
    monkeypatch.setattr(sandbox_runtime, "ensure_docker_sandbox", forbidden)
    monkeypatch.setattr(sandbox_runtime, "detect_container_runtime", forbidden)
    original_run = edits.subprocess.run
    monkeypatch.setattr(edits.subprocess, "run", forbidden)
    receipts = []
    def read(path="hello.txt"):
        return service.get_workspace_editable_file(workspace.id, "chat", path)
    def save(content="new\n", path="hello.txt", snapshot=None, **kwargs):
        snapshot = snapshot or read(path)
        return service.save_workspace_text(workspace.id, "chat", path, content,
            expected_resource_revision=snapshot.resource_revision,
            expected_binding_id=snapshot.binding_id,
            expected_binding_revision=snapshot.binding_revision, expected_digest=snapshot.digest,
            expected_review_token=kwargs.pop("expected_review_token", snapshot.review_token),
            command_id=kwargs.pop("command_id", str(uuid.uuid4())),
            persist_recovery=kwargs.pop("persist_recovery", receipts.append), **kwargs)
    return SimpleNamespace(service=service, root=root, workspace=workspace, read=read, save=save,
        receipts=receipts, ledger=change_ledger, sandbox=sandbox_runtime, storage=storage,
        edits=edits, runs=agent_runs, threads=threads, tasks=tasks, resources=conversation_resources,
        original_run=original_run, initial_db_bytes=initial_db_bytes)


def test_read_is_inert_and_bounded_with_exact_newlines(domain):
    d = domain
    (d.root / "hello.txt").write_bytes(b"first\r\nsecond\n")
    first = d.read()
    assert first.status == "text" and first.content == "first\r\nsecond\n"
    assert first.digest == hashlib.sha256(b"first\r\nsecond\n").hexdigest()
    assert Path(d.tasks._DB_PATH).read_bytes() == d.initial_db_bytes and not d.ledger.LEDGER_PATH.exists()
    assert str(d.root) not in json.dumps(asdict(first))
    (d.root / "hello.txt").write_bytes(b"x" * (201 * 1024))
    large = d.read()
    assert large.status == "too_large" and large.content == "" and large.code == "inline_edit_too_large"
    assert len(json.dumps(asdict(large)).encode()) < 256 * 1024
    denied = d.save("short")
    assert denied.status == "denied" and (d.root / "hello.txt").stat().st_size == 201 * 1024


def test_save_records_real_run_writer_ledger_and_preserves_original(domain):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\r\n")
    result = d.save("new\r\n")
    assert result.status == "saved", result
    assert result.file_saved and result.ledger_saved and result.change_set_id
    assert (d.root / "hello.txt").read_bytes() == b"new\r\n"
    assert d.runs.list_agent_write_locks() == []
    with sqlite3.connect(d.tasks._DB_PATH) as connection:
        rows = connection.execute("SELECT kind,status,thread_id FROM agent_runs").fetchall()
    assert rows == [("workflow", "completed", "chat")]
    changes = d.ledger.list_change_sets(workspace_id=d.workspace.id)
    assert len(changes) == 1 and changes[0].files[0].before_text == "old\r\n"
    assert list(d.root.glob(".row-bot-edit-recovery/*/previous"))[0].read_bytes() == b"old\r\n"
    assert str(d.root) not in json.dumps(asdict(result))


def test_unchanged_and_stale_digest_have_no_effect(domain):
    d = domain
    (d.root / "hello.txt").write_text("old")
    read = d.read()
    assert d.save("old", snapshot=read).status == "unchanged"
    (d.root / "hello.txt").write_text("external")
    result = d.save("new", snapshot=read)
    assert result.status == "conflict" and result.code == "file_revision_conflict"
    assert (d.root / "hello.txt").read_text() == "external"
    assert Path(d.tasks._DB_PATH).read_bytes() == d.initial_db_bytes and d.receipts == []


def test_response_loss_reuses_same_saved_identity(domain):
    d = domain
    (d.root / "hello.txt").write_text("old")
    snapshot, command = d.read(), str(uuid.uuid4())
    first = d.save(snapshot=snapshot, command_id=command)
    second = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert first.status == second.status == "saved", (first, second)
    assert first.change_set_id == second.change_set_id
    assert len(d.ledger.list_change_sets()) == 1
    (d.root / "hello.txt").write_text("later")
    retry = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert retry.status == "partial" and not retry.file_saved
    assert (d.root / "hello.txt").read_text() == "later"


def test_ledger_failure_reports_saved_content_and_retries_only_history(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_text("old")
    snapshot, command = d.read(), str(uuid.uuid4())
    original = d.ledger._save
    monkeypatch.setattr(d.ledger, "_save", lambda _: (_ for _ in ()).throw(OSError("private")))
    first = d.save(snapshot=snapshot, command_id=command)
    assert first.status == "partial" and first.file_saved and not first.ledger_saved
    assert first.code == "change_ledger_incomplete"
    monkeypatch.setattr(d.ledger, "_save", original)
    retry = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert retry.status == "saved" and retry.ledger_saved
    assert len(d.ledger.list_change_sets()) == 1 and d.runs.list_agent_write_locks() == []


def test_existing_writer_is_never_released_by_editor(domain):
    d = domain
    (d.root / "hello.txt").write_text("old")
    d.runs.create_agent_run(run_id="existing", kind="subagent", status="running", thread_id="chat")
    key = "developer:" + d.workspace.id
    assert d.runs.acquire_agent_write_lock(key, "existing")
    try:
        result = d.save()
        assert result.status == "conflict" and result.code == "workspace_writer_busy"
        assert d.runs.get_agent_write_lock(key)["run_id"] == "existing"
        assert (d.root / "hello.txt").read_text() == "old"
    finally:
        d.runs.release_agent_write_lock(run_id="existing")


@pytest.mark.parametrize("path", ["../outside", "a/../../outside", ".git/config", "file:stream", "CON.txt", ".row-bot-edit-recovery/evil"])
def test_invalid_paths_cannot_write(domain, path):
    d = domain
    result = d.save(path=path)
    assert result.status == "denied" and result.code == "workspace_path_denied"
    assert list(d.root.iterdir()) == []


def test_docker_edit_stays_in_existing_shadow_and_creates_one_pending_import(domain):
    d = domain
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    (d.root / "hello.txt").write_text("host")
    assert d.read().status == "sandbox_unprepared"
    denied = d.save()
    assert denied.status == "denied" and denied.code == "sandbox_unprepared"
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    (shadow / "hello.txt").write_text("shadow")
    snapshot, command = d.read(), str(uuid.uuid4())
    first = d.save(snapshot=snapshot, command_id=command)
    assert first.status == "pending_import", first
    assert (d.root / "hello.txt").read_text() == "host"
    assert (shadow / "hello.txt").read_text() == "new\n"
    second = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert second.pending_change_id == first.pending_change_id
    assert len(d.sandbox.list_pending_changes(workspace_id=workspace.id)) == 1
    assert not d.ledger.LEDGER_PATH.exists()


def test_publication_fault_restores_original_and_same_command_recovers(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_text("old")
    snapshot, command = d.read(), str(uuid.uuid4())
    link = d.edits.os.link
    monkeypatch.setattr(d.edits.os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected")))
    result = d.save(snapshot=snapshot, command_id=command)
    assert result.status == "partial" and not result.file_saved
    assert (d.root / "hello.txt").read_text() == "old"
    monkeypatch.setattr(d.edits.os, "link", link)
    retried = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert retried.status == "saved", retried
    assert (d.root / "hello.txt").read_text() == "new\n"


def test_external_edit_between_capture_and_retirement_is_preserved(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_text("old")
    original = d.edits._rename_edit_no_replace
    def race(source, destination, **kwargs):
        if source == d.root / "hello.txt" or source == "hello.txt":
            (d.root / "hello.txt").write_text("external replacement")
        return original(source, destination, **kwargs)
    monkeypatch.setattr(d.edits, "_rename_edit_no_replace", race)
    result = d.save()
    assert result.status == "partial" and result.code == "file_revision_conflict"
    assert (d.root / "hello.txt").read_text() == "external replacement"
    assert not d.ledger.LEDGER_PATH.exists()


def test_external_creator_at_publication_keeps_both_versions(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_text("old")
    link = d.edits.os.link
    def race(source, destination, **kwargs):
        (d.root / "hello.txt").write_text("new creator")
        return link(source, destination, **kwargs)
    monkeypatch.setattr(d.edits.os, "link", race)
    result = d.save()
    assert result.status == "partial" and not result.file_saved
    assert (d.root / "hello.txt").read_text() == "new creator"
    assert list(d.root.glob(".row-bot-edit-recovery/*/previous"))[0].read_text() == "old"


def test_receipt_failure_changes_no_existing_file(domain):
    d = domain
    (d.root / "hello.txt").write_text("old")
    def fail(_):
        raise OSError("private path must not escape")
    result = d.save(persist_recovery=fail)
    assert result.status == "partial" and result.code == "edit_receipt_unconfirmed"
    assert not result.file_saved and not result.ledger_saved
    assert (d.root / "hello.txt").read_text() == "old"
    assert "private" not in json.dumps(asdict(result))


@pytest.mark.parametrize("change", ["binding", "read_only", "resource"])
def test_current_authority_is_rechecked_under_writer_admission(domain, change):
    d = domain
    (d.root / "hello.txt").write_text("old")
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls != 2:
            return
        if change == "resource":
            workspace = d.storage.get_workspace(d.workspace.id)
            workspace.name = "Changed"
            workspace.touch()
            d.storage.save_workspace(workspace)
        else:
            if change == "binding":
                bindings = d.resources.list_bindings("chat")
                detached = d.resources.unbind("chat", bindings.bindings[0].binding_id,
                    expected_revision=bindings.bindings_revision)
                d.resources.bind("chat", "workspace", d.workspace.id, expected_revision=detached.bindings_revision)
                return
            with sqlite3.connect(d.threads.DB_PATH) as connection:
                connection.execute("UPDATE thread_meta SET approval_mode='read_only' WHERE thread_id='chat'")
    result = d.save(validate=validate)
    assert result.status in {"denied", "conflict"}, result
    assert (d.root / "hello.txt").read_text() == "old" and d.receipts == []
    assert d.runs.list_agent_write_locks() == []


def test_postcommit_writer_admission_error_releases_owned_lock(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_text("old")
    append = d.runs.append_agent_event
    def fail(run_id, event_type, *args, **kwargs):
        if event_type == "write_lock.acquired":
            raise RuntimeError("private error")
        return append(run_id, event_type, *args, **kwargs)
    monkeypatch.setattr(d.runs, "append_agent_event", fail)
    result = d.save()
    assert result.status == "denied" and not result.file_saved
    assert d.runs.list_agent_write_locks() == [] and (d.root / "hello.txt").read_text() == "old"
    with sqlite3.connect(d.tasks._DB_PATH) as connection:
        assert connection.execute("SELECT status FROM agent_runs").fetchone()[0] == "failed"


@pytest.mark.parametrize("content", [b"binary\0bytes", b"\xff\xfe", b"x" * (1024 * 1024 + 1)],
                         ids=["nul", "invalid-utf8", "over-one-mib"])
def test_noneditable_files_remain_untouched(domain, content):
    d = domain
    (d.root / "hello.txt").write_bytes(content)
    result = d.save()
    assert result.status == "denied" and not result.file_saved
    assert (d.root / "hello.txt").read_bytes() == content
    assert d.receipts == []


def test_worst_case_json_expansion_stays_below_wire_budget(domain):
    d = domain
    (d.root / "hello.txt").write_text("\u0001" * 40000, encoding="utf-8")
    result = d.read()
    assert result.status == "too_large" and result.content == ""
    assert d.save("replacement").status == "denied"
    (d.root / "hello.txt").write_text("\u0001" * 30000, encoding="utf-8")
    result = d.read()
    assert result.status == "text"
    assert len(json.dumps(asdict(result), ensure_ascii=True).encode()) < 256 * 1024


def test_corrupt_ledger_is_retained_and_blocks_client_and_legacy_mutation(domain):
    d = domain
    (d.root / "hello.txt").write_text("old")
    d.ledger.LEDGER_PATH.write_bytes(b"{broken retained")
    result = d.save()
    assert result.status == "denied" and result.code == "change_ledger_unavailable"
    with pytest.raises(ValueError, match="change_ledger_unavailable"):
        d.ledger.record_change_set(workspace_id=d.workspace.id, thread_id="chat", summary="legacy", files=[])
    assert d.ledger.LEDGER_PATH.read_bytes() == b"{broken retained"
    assert (d.root / "hello.txt").read_text() == "old"


def test_concurrent_ledger_writers_preserve_each_record(domain):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    d = domain
    barrier = threading.Barrier(8)
    def write(index):
        barrier.wait(timeout=3)
        return d.ledger.record_change_set(workspace_id=d.workspace.id, thread_id="chat",
            summary=str(index), files=[]).id
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(write, range(8)))
    records = d.ledger.list_change_sets()
    assert {item.id for item in records} == set(identities) and len(records) == 8


@pytest.mark.parametrize("before,after,expected", [("old\n", "new\n", b"new\r\n"),
    ("old", "new", b"new"), (None, "", b""), ("caf\u00e9\n", "\u96ea\n", "\u96ea\r\n".encode())],
    ids=["canonical-crlf-policy", "no-final-newline", "create-empty", "unicode"])
def test_prepared_edits_import_through_existing_git_eol_policy(domain, monkeypatch, before, after, expected):
    d = domain
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    if before is not None:
        (shadow / "hello.txt").write_bytes(before.encode("utf-8"))
        (d.root / "hello.txt").write_bytes(before.encode("utf-8"))
    snapshot, command = d.read(), str(uuid.uuid4())
    saved = d.save(after, snapshot=snapshot, command_id=command)
    assert saved.status == "pending_import", saved
    pending = d.sandbox.get_pending_change(saved.pending_change_id)
    calls = []
    def local_apply_only(args, **kwargs):
        assert args[:2] == ["git", "apply"] and kwargs["cwd"] == str(d.root)
        calls.append(args)
        return d.original_run(args, **kwargs)
    monkeypatch.setattr(d.edits.subprocess, "run", local_apply_only)
    change, decision = d.edits.apply_patch_to_workspace(workspace_id=workspace.id, thread_id="chat",
        patch=pending.patch, approval_mode="approve", confirmed=True)
    assert change is not None and decision.decision == "allow" and len(calls) == 2
    assert (d.root / "hello.txt").read_bytes() == expected
    assert (shadow / "hello.txt").read_bytes() == after.encode("utf-8")
    d.sandbox.mark_pending_change_imported(pending.id)
    replay = d.save(after, snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert replay.status == "saved" and replay.code == "sandbox_change_already_imported"
    assert replay.pending_change_id == pending.id and len(calls) == 2


@pytest.mark.parametrize("before,after", [("line", "line\n"), ("line\n", "line"),
    ("line\r\n", "line\n"), ("line\n", "line\r\n")],
    ids=["add-newline", "remove-newline", "crlf-to-lf", "lf-to-crlf"])
def test_newline_only_sandbox_edit_is_explicitly_unavailable_before_any_write(domain, before, after):
    d = domain
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    (shadow / "hello.txt").write_bytes(before.encode())
    (d.root / "hello.txt").write_bytes(before.encode())
    result = d.save(after)
    assert result.status == "denied" and result.code == "sandbox_patch_unavailable"
    assert not result.file_saved and not result.ledger_saved and d.receipts == []
    assert (shadow / "hello.txt").read_bytes() == before.encode()
    assert (d.root / "hello.txt").read_bytes() == before.encode()


def test_retained_editor_copies_never_enter_later_sandbox_snapshots(domain):
    assert domain.sandbox._is_skipped_path(".row-bot-edit-recovery/receipt/previous")


def test_pending_write_fault_preserves_shadow_and_host_for_retry(domain, monkeypatch):
    d = domain
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    (shadow / "hello.txt").write_text("shadow")
    (d.root / "hello.txt").write_text("host")
    snapshot, command = d.read(), str(uuid.uuid4())
    original = d.sandbox._save_pending_payload
    monkeypatch.setattr(d.sandbox, "_save_pending_payload", lambda _: (_ for _ in ()).throw(OSError("private")))
    result = d.save(snapshot=snapshot, command_id=command)
    assert result.status == "partial" and result.file_saved and result.code == "sandbox_history_incomplete"
    assert (d.root / "hello.txt").read_text() == "host"
    monkeypatch.setattr(d.sandbox, "_save_pending_payload", original)
    retry = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert retry.status == "pending_import" and retry.ledger_saved
    assert len(d.sandbox.list_pending_changes(workspace_id=workspace.id)) == 1


def test_writer_release_event_failure_keeps_saved_result_truthful(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_text("old")
    original = d.runs.append_agent_event
    def fail(run_id, event_type, *args, **kwargs):
        if event_type == "write_lock.released":
            raise RuntimeError("private release event error")
        return original(run_id, event_type, *args, **kwargs)
    monkeypatch.setattr(d.runs, "append_agent_event", fail)
    result = d.save()
    assert result.status == "partial" and result.file_saved and result.ledger_saved
    assert (d.root / "hello.txt").read_bytes() == b"new\n"
    assert d.runs.list_agent_write_locks() == []
    assert "private" not in json.dumps(asdict(result))


def test_stop_request_before_retirement_keeps_original_and_releases_writer(domain):
    d = domain
    (d.root / "hello.txt").write_text("old")
    def persist(receipt):
        d.receipts.append(receipt)
        owner = d.runs.get_agent_write_lock("developer:" + d.workspace.id)
        d.runs.stop_agent_run(owner["run_id"])
    result = d.save(persist_recovery=persist)
    assert result.status == "partial" and result.code == "edit_cancelled" and not result.file_saved
    assert (d.root / "hello.txt").read_text() == "old"
    assert d.runs.list_agent_write_locks() == []


def test_corrupt_pending_history_blocks_writes_and_remains_unchanged(domain):
    d = domain
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    (shadow / "hello.txt").write_text("shadow")
    d.sandbox.PENDING_CHANGES_PATH.write_bytes(b"{retained malformed")
    result = d.save()
    assert result.status == "denied" and result.code == "sandbox_history_unavailable"
    assert (shadow / "hello.txt").read_text() == "shadow"
    with pytest.raises(ValueError, match="sandbox_history_unavailable"):
        d.sandbox.mark_pending_change_imported("unknown")
    assert d.sandbox.PENDING_CHANGES_PATH.read_bytes() == b"{retained malformed"


def test_pending_concurrent_save_and_import_preserve_both_changes(domain):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    d = domain
    first = d.sandbox._record_pending_change(d.workspace, "chat", "first", {"a.txt": "a"}, {"a.txt": "b"})
    barrier = threading.Barrier(2)
    def mark():
        barrier.wait(timeout=3)
        d.sandbox.mark_pending_change_imported(first.id)
    def add():
        barrier.wait(timeout=3)
        return d.sandbox._record_pending_change(d.workspace, "chat", "second", {"b.txt": "a"}, {"b.txt": "b"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        marked, added = pool.submit(mark), pool.submit(add)
        second = added.result(timeout=5)
        marked.result(timeout=5)
    assert d.sandbox.get_pending_change(first.id).imported
    assert not d.sandbox.get_pending_change(second.id).imported


@pytest.mark.skipif(os.name != "nt", reason="Windows explicit file DACL semantics")
def test_restricted_windows_file_is_not_replaced_with_inherited_permissions(domain):
    import ctypes
    from ctypes import wintypes
    d = domain
    target = d.root / "hello.txt"
    target.write_bytes(b"retained restricted data")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    set_security = advapi.SetFileSecurityW
    set_security.argtypes, set_security.restype = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    # Restricted to OWNER RIGHTS; this applies only to this disposable file.
    assert convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None), ctypes.get_last_error()
    try:
        assert set_security(str(target), 4 | 0x80000000, descriptor), ctypes.get_last_error()
    finally:
        kernel.LocalFree(descriptor)
    security_before = d.edits._windows_edit_metadata(target)
    identity_before = target.stat().st_ino
    result = d.save()
    assert result.status == "denied" and result.code == "file_metadata_unavailable", result
    assert not result.file_saved and d.receipts == []
    assert target.read_bytes() == b"retained restricted data" and target.stat().st_ino == identity_before
    assert d.edits._windows_edit_metadata(target) == security_before


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate data streams")
def test_windows_alternate_stream_is_retained_before_any_publication(domain):
    d = domain
    target = d.root / "hello.txt"
    target.write_bytes(b"main data")
    stream = Path(str(target) + ":retained-test-stream")
    stream.write_bytes(b"alternate data")
    identity = target.stat().st_ino
    result = d.save()
    assert result.status == "denied" and result.code == "file_metadata_unavailable", result
    assert target.read_bytes() == b"main data" and stream.read_bytes() == b"alternate data"
    assert target.stat().st_ino == identity and d.receipts == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX native xattr semantics")
def test_posix_extended_attributes_are_not_discarded(domain):
    d = domain
    target = d.root / "hello.txt"
    target.write_bytes(b"original")
    name = "user.row_bot_d2_review"
    os.setxattr(target, name, b"retained")
    result = d.save()
    assert result.status == "denied" and result.code == "file_metadata_unavailable"
    assert target.read_bytes() == b"original" and os.getxattr(target, name) == b"retained"


def test_metadata_change_during_retirement_restores_original(domain, monkeypatch):
    d = domain
    target = d.root / "hello.txt"
    target.write_bytes(b"old")
    original = d.edits.file_edit_metadata_digest
    def changed(path):
        value = original(path)
        retained = list(d.root.glob(".row-bot-edit-recovery/*/previous"))
        is_retained = (any(item.stat().st_ino == os.fstat(path).st_ino for item in retained)
                       if isinstance(path, int) else path.name == "previous")
        return "changed-metadata" if is_retained else value
    monkeypatch.setattr(d.edits, "file_edit_metadata_digest", changed)
    result = d.save()
    assert result.status == "partial" and result.code == "file_metadata_unavailable"
    assert not result.file_saved and target.read_bytes() == b"old"


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX dirfd parent replacement semantics")
@pytest.mark.parametrize("phase", ["receipt", "retirement", "publication"])
def test_posix_parent_replacement_cannot_redirect_publication(tmp_path, monkeypatch, phase):
    from row_bot.developer import edits
    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    parent = root / "selected"
    parent.mkdir()
    (parent / "file.txt").write_bytes(b"old")
    (outside / "file.txt").write_bytes(b"external")
    moved = root / "retained-parent"
    swapped = False
    def swap():
        nonlocal swapped
        if not swapped:
            parent.rename(moved)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
    rename, link = edits._rename_edit_no_replace, os.link
    def retire(source, destination, **kwargs):
        if phase == "retirement":
            swap()
        return rename(source, destination, **kwargs)
    def publish(source, destination, **kwargs):
        if phase == "publication":
            swap()
        return link(source, destination, **kwargs)
    monkeypatch.setattr(edits, "_rename_edit_no_replace", retire)
    monkeypatch.setattr(edits.os, "link", publish)
    receipts = []
    def persist(receipt):
        receipts.append(receipt)
        if phase == "receipt":
            swap()
    result = edits.publish_text_revision(root, "selected/file.txt", "new", expected_digest=hashlib.sha256(b"old").hexdigest(),
        command_id=str(uuid.uuid4()), persist_recovery=persist)
    assert swapped and result.changed
    assert (outside / "file.txt").read_bytes() == b"external"
    assert list(outside.iterdir()) == [outside / "file.txt"]
    assert (moved / "file.txt").read_bytes() == b"new"
    assert list(moved.glob(".row-bot-edit-recovery/*/previous"))[0].read_bytes() == b"old"


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX dirfd no-replace rename")
def test_posix_relative_rename_retains_collision_and_rejects_escape(tmp_path):
    from row_bot.developer import edits
    (tmp_path / "source").write_bytes(b"source")
    (tmp_path / "destination").write_bytes(b"destination")
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            edits._rename_edit_no_replace("source", "destination", src_dir_fd=descriptor, dst_dir_fd=descriptor)
        for name in ("../escape", "/escape", "..", "", "a/b"):
            with pytest.raises(edits.FileEditError, match="workspace_path_denied"):
                edits._rename_edit_no_replace(name, "target", src_dir_fd=descriptor, dst_dir_fd=descriptor)
        edits._rename_edit_no_replace("source", "renamed", src_dir_fd=descriptor, dst_dir_fd=descriptor)
        assert (tmp_path / "renamed").read_bytes() == b"source"
        assert (tmp_path / "destination").read_bytes() == b"destination"
    finally:
        os.close(descriptor)
