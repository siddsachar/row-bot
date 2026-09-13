"""Initial edit authority uses an actual opaque read-issued token and fake data."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import stat
import uuid

import pytest

from tests.subsystem.developer.test_client_workspace_edits import domain as domain

pytestmark = pytest.mark.subsystem


def test_read_token_is_opaque_and_has_no_database_or_recovery_write(domain):
    d = domain
    (d.root / "hello.txt").write_bytes(b"reviewed bytes\n")
    first = d.read()
    second = d.read()
    assert first.status == "text" and first.review_token == second.review_token
    assert len(first.review_token) == 64 and int(first.review_token, 16) >= 0
    public = json.dumps(asdict(first))
    assert "parent_identity" not in public and "metadata_digest" not in public and str(d.root) not in public
    assert Path(d.tasks._DB_PATH).read_bytes() == d.initial_db_bytes
    assert not d.receipts and not list(d.root.glob(".row-bot-edit-recovery/*"))


def test_cold_missing_instance_key_does_not_initialize_store_or_issue_token(domain, monkeypatch, tmp_path):
    d = domain
    (d.root / "hello.txt").write_bytes(b"reviewed bytes\n")
    absent = tmp_path / "cold-never-created" / "tasks.db"
    monkeypatch.setattr(d.tasks, "_DB_PATH", str(absent))
    snapshot = d.read()
    assert snapshot.status == "denied" and snapshot.code == "command_metadata_unavailable"
    assert snapshot.review_token == "" and snapshot.content == ""
    assert not absent.parent.exists()


@pytest.mark.parametrize("change", ["identity", "metadata", "content"])
def test_original_read_rejects_changed_identity_metadata_or_bytes(domain, change):
    d = domain
    target = d.root / "hello.txt"
    target.write_bytes(b"old\n")
    snapshot = d.read()
    if change == "identity":
        target.rename(d.root / "retained-external.txt")
        target.write_bytes(b"old\n")
    elif change == "metadata":
        target.chmod(stat.S_IREAD)
    else:
        target.write_bytes(b"external\n")
    try:
        result = d.save("new\n", snapshot=snapshot)
        assert result.status == "conflict" and result.code in {"file_review_conflict", "file_revision_conflict"}
        assert target.read_bytes() == (b"external\n" if change == "content" else b"old\n")
        assert not d.receipts and not (d.root / ".row-bot-edit-recovery").exists()
        assert Path(d.tasks._DB_PATH).read_bytes() == d.initial_db_bytes
    finally:
        if change == "metadata":
            target.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("token", ["", "a" * 64, "0" * 63, "not-hex" * 20])
def test_unissued_or_missing_review_never_becomes_digest_only_write(domain, token):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    result = d.save(expected_review_token=token)
    assert result.status == "conflict" and result.code == "file_review_conflict"
    assert (d.root / "hello.txt").read_bytes() == b"old\n" and not d.receipts


def test_token_cannot_be_reused_for_another_file_with_identical_bytes(domain):
    d = domain
    (d.root / "first.txt").write_bytes(b"old\n")
    (d.root / "second.txt").write_bytes(b"old\n")
    first, second = d.read("first.txt"), d.read("second.txt")
    result = d.save(path="second.txt", snapshot=replace(second, review_token=first.review_token))
    assert result.status == "conflict" and result.code == "file_review_conflict"
    assert (d.root / "second.txt").read_bytes() == b"old\n" and not d.receipts


def test_token_is_bound_to_exact_conversation_and_binding(domain):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    first = d.read()
    with sqlite3.connect(d.threads.DB_PATH) as connection:
        connection.execute("INSERT INTO thread_meta(thread_id,name,approval_mode) VALUES ('other','Other','approve')")
    d.resources.bind("other", "workspace", d.workspace.id, expected_revision=0)
    other = d.service.get_workspace_editable_file(d.workspace.id, "other", "hello.txt")
    result = d.service.save_workspace_text(d.workspace.id, "other", "hello.txt", "new\n",
        expected_resource_revision=other.resource_revision, expected_binding_id=other.binding_id,
        expected_binding_revision=other.binding_revision, expected_digest=other.digest,
        expected_review_token=first.review_token, command_id=str(uuid.uuid4()), persist_recovery=d.receipts.append)
    assert result.status == "conflict" and result.code == "file_review_conflict" and not d.receipts


@pytest.mark.parametrize("when", ["before_save", "writer_admission"])
def test_exact_policy_change_rejects_even_when_both_modes_allow_ordinary_edits(domain, monkeypatch, when):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    snapshot = d.read()
    def change():
        with sqlite3.connect(d.threads.DB_PATH) as connection:
            connection.execute("UPDATE thread_meta SET approval_mode='allow_all' WHERE thread_id='chat'")
    if when == "before_save":
        change()
    else:
        acquire = d.runs.acquire_agent_write_lock
        def admit(*args, **kwargs):
            result = acquire(*args, **kwargs)
            change()
            return result
        monkeypatch.setattr(d.runs, "acquire_agent_write_lock", admit)
    result = d.save(snapshot=snapshot)
    assert result.code == "file_review_conflict", result
    assert (d.root / "hello.txt").read_bytes() == b"old\n" and not d.receipts
    assert d.runs.list_agent_write_locks() == []


@pytest.mark.parametrize("when", ["before_save", "publisher"])
@pytest.mark.parametrize("existing", [False, True])
def test_initial_missing_or_existing_target_binds_original_parent(domain, monkeypatch, when, existing):
    d = domain
    parent = d.root / "parent"
    parent.mkdir()
    if existing:
        (parent / "hello.txt").write_bytes(b"old\n")
    snapshot = d.read("parent/hello.txt")
    assert snapshot.status == ("text" if existing else "missing") and snapshot.review_token
    def swap():
        parent.rename(d.root / "retained-parent")
        parent.mkdir()
        if existing:
            (parent / "hello.txt").write_bytes(b"old\n")
    if when == "before_save":
        swap()
    else:
        publish = d.edits.publish_text_revision
        def swapped(*args, **kwargs):
            swap()
            return publish(*args, **kwargs)
        monkeypatch.setattr(d.edits, "publish_text_revision", swapped)
    result = d.save(path="parent/hello.txt", snapshot=snapshot)
    assert result.code in {"file_review_conflict", "folder_selection_denied"}, result
    assert not d.receipts and not (parent / ".row-bot-edit-recovery").exists()
    assert (parent / "hello.txt").read_bytes() == b"old\n" if existing else not list(parent.iterdir())


def test_instance_key_rotation_during_writer_wait_invalidates_original_review(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    snapshot = d.read()
    acquire = d.runs.acquire_agent_write_lock
    def admit(*args, **kwargs):
        result = acquire(*args, **kwargs)
        with sqlite3.connect(d.tasks._DB_PATH) as connection:
            connection.execute("UPDATE client_instance SET secret=? WHERE id=1", ('b' * 64,))
        return result
    monkeypatch.setattr(d.runs, "acquire_agent_write_lock", admit)
    result = d.save(snapshot=snapshot)
    assert result.code == "file_review_conflict" and not d.receipts
    assert (d.root / "hello.txt").read_bytes() == b"old\n" and d.runs.list_agent_write_locks() == []


def test_same_command_recovery_requires_original_read_token_and_parent(domain):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    snapshot, command = d.read(), str(uuid.uuid4())
    first = d.save(snapshot=snapshot, command_id=command)
    proof = d.receipts[0]
    assert first.status == "saved" and proof.review_token == snapshot.review_token and proof.parent_identity
    forged = d.save(snapshot=snapshot, command_id=command, recovery=proof, expected_review_token="a" * 64)
    assert forged.status == "partial" and forged.code == "edit_recovery_conflict"
    successful = d.save(snapshot=snapshot, command_id=command, recovery=proof)
    assert successful.status == "saved" and successful.change_set_id == first.change_set_id
    assert len(d.ledger.list_change_sets()) == 1


def test_shadow_initial_read_rejects_same_byte_replacement_without_host_or_pending_effects(domain):
    d = domain
    workspace = d.storage.get_workspace(d.workspace.id)
    workspace.execution_mode = "docker"
    workspace.touch()
    d.storage.save_workspace(workspace)
    (d.root / "hello.txt").write_bytes(b"host\n")
    shadow = d.sandbox.sandbox_shadow_path(workspace.id)
    shadow.mkdir(parents=True)
    target = shadow / "hello.txt"
    target.write_bytes(b"old\n")
    snapshot = d.read()
    target.rename(shadow / "retained-shadow.txt")
    target.write_bytes(b"old\n")
    result = d.save(snapshot=snapshot)
    assert result.code == "file_review_conflict" and not d.receipts
    assert (d.root / "hello.txt").read_bytes() == b"host\n" and target.read_bytes() == b"old\n"
    assert not d.sandbox.PENDING_CHANGES_PATH.exists()


def test_original_edit_recovery_retries_retained_writer_cleanup(domain, monkeypatch):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    snapshot, command = d.read(), str(uuid.uuid4())
    release = d.runs.release_agent_write_lock
    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic post-save writer release failure")
    monkeypatch.setattr(d.runs, "release_agent_write_lock", unavailable)
    first = d.save(snapshot=snapshot, command_id=command)
    assert first.status == "partial" and first.file_saved and d.runs.list_agent_write_locks()
    original_identity = (d.root / "hello.txt").stat().st_ino
    monkeypatch.setattr(d.runs, "release_agent_write_lock", release)
    recovered = d.save(snapshot=snapshot, command_id=command, recovery=d.receipts[0])
    assert recovered.status == "saved" and recovered.ledger_saved, recovered
    assert not d.runs.list_agent_write_locks()
    assert (d.root / "hello.txt").stat().st_ino == original_identity
    assert len(d.ledger.list_change_sets()) == 1


def test_same_command_id_never_adopts_or_releases_another_workspace_run(domain):
    d = domain
    (d.root / "hello.txt").write_bytes(b"old\n")
    command = str(uuid.uuid4())
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-edit:" + command).hex
    d.runs.create_agent_run(run_id=run_id, kind="workflow", status="running", thread_id="other",
        workspace_id="other-workspace", write_lock_key="developer:other-workspace",
        result_json={"command_id": command, "operation": "workspace.edit"})
    assert d.runs.acquire_agent_write_lock("developer:other-workspace", run_id,
        thread_id="other", workspace_id="other-workspace")
    try:
        result = d.save(command_id=command)
        assert result.code == "edit_recovery_conflict" and not d.receipts
        assert d.runs.get_agent_write_lock("developer:other-workspace")["run_id"] == run_id
        assert d.runs.get_agent_run(run_id)["status"] == "running"
    finally:
        d.runs.release_agent_write_lock(run_id=run_id)
