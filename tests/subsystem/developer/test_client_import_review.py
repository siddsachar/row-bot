"""Independent D4 recovery and current-authority probes over disposable files."""
from __future__ import annotations

import copy
import uuid

import pytest

from tests.subsystem.developer.test_client_workspace_imports import imports as imports
from tests.subsystem.developer.test_client_workspace_edits import domain as domain

pytestmark = pytest.mark.subsystem


def test_recovery_does_not_adopt_same_bytes_from_replacement_inode(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "before\n"}, {"file.txt": "after\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    mark = d.sandbox.mark_pending_change_imported
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", lambda *_a, **_k: (_ for _ in ()).throw(OSError("marker")))
    assert d.apply(review, command_id=command_id).status == "partial"
    proof = copy.deepcopy(d.import_receipts[-1])
    target = d.root / "file.txt"
    target.rename(d.root / "owned-after.txt")
    target.write_bytes(b"after\n")
    replacement = target.stat().st_ino
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", mark)
    result = d.apply(review, command_id=command_id, recovery=proof)
    assert result.status == "partial" and not result.imported
    assert result.code == "file_revision_conflict"
    assert target.read_bytes() == b"after\n" and target.stat().st_ino == replacement
    assert not d.sandbox.read_pending_import_rows(d.workspace.id, "chat")[0]["imported"]


@pytest.mark.parametrize("revoke", ["authority", "writer_stop"])
def test_mid_import_revocation_preserves_partial_progress_and_remaining_file(imports, monkeypatch, revoke):
    d = imports
    pending = d.pending({"a.txt": "old-a\n", "b.txt": "old-b\n"}, {"a.txt": "new-a\n", "b.txt": "new-b\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    original = d.edits.publish_text_revision
    revoked = False
    def publish(*args, **kwargs):
        nonlocal revoked
        result = original(*args, **kwargs)
        revoked = True
        if revoke == "writer_stop":
            run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-import:" + command_id).hex
            d.runs.stop_agent_run(run_id)
        return result
    monkeypatch.setattr(d.edits, "publish_text_revision", publish)
    def validate():
        if revoked and revoke == "authority":
            raise ValueError("capability_revoked")
    result = d.apply(review, command_id=command_id, validate=validate)
    assert result.status == "partial" and result.files_applied == ("a.txt",)
    assert result.code == ("capability_revoked" if revoke == "authority" else "edit_cancelled")
    assert not result.imported and not result.ledger_saved
    assert (d.root / "a.txt").read_bytes() == b"new-a\n"
    assert (d.root / "b.txt").read_bytes() == b"old-b\n"
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id) is None


def test_completed_import_release_failure_is_reported_as_partial(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "before\n"}, {"file.txt": "after\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    release = d.runs.release_agent_write_lock
    command_id = str(uuid.uuid4())
    monkeypatch.setattr(d.runs, "release_agent_write_lock", lambda **_k: (_ for _ in ()).throw(OSError("lease")))
    result = d.apply(review, command_id=command_id)
    assert result.status == "partial" and result.imported and result.ledger_saved
    assert result.code == "sandbox_import_unconfirmed"
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id) is not None
    monkeypatch.setattr(d.runs, "release_agent_write_lock", release)
    retried = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    assert retried.status == "imported"
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id) is None


def test_original_recovery_retries_failed_run_finalization_without_republishing(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "before\n"}, {"file.txt": "after\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    finish = d.runs.finish_agent_run
    monkeypatch.setattr(d.runs, "finish_agent_run", lambda *_a, **_k: (_ for _ in ()).throw(OSError("finalization")))
    partial = d.apply(review, command_id=command_id)
    assert partial.status == "partial" and partial.imported
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id) is None
    before = (d.root / "file.txt").stat()
    monkeypatch.setattr(d.runs, "finish_agent_run", finish)
    monkeypatch.setattr(d.edits, "publish_text_revision", lambda *_a, **_k: pytest.fail("repeated host publication"))
    result = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-import:" + command_id).hex
    assert result.status == "imported" and d.runs.get_agent_run(run_id)["status"] == "completed"
    after = (d.root / "file.txt").stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
