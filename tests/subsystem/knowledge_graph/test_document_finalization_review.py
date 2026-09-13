"""Independent file ownership and recovery checks for document finalization."""
from __future__ import annotations

import os

import pytest

from tests.subsystem.knowledge_graph.test_document_finalization import owner as owner


def test_atomic_replacement_of_staging_name_during_finalization_is_preserved(owner, monkeypatch):
    jobs, service, job, source = owner
    service.mark_searchable(job.id)
    destination = service.completed_root / job.id / job.stored_name
    source_hash = service._source_hash
    replaced = []

    def interleave(path):
        digest = source_hash(path)
        if path == destination and source.exists() and not replaced:
            replacement = source.with_name("external-edit.txt")
            replacement.write_bytes(b"User replacement staged during finalization")
            os.replace(replacement, source)
            replaced.append(True)
        return digest

    monkeypatch.setattr(service, "_source_hash", interleave)
    try:
        service.mark_completed(job.id)
    except Exception:
        pass
    assert replaced == [True]
    candidates = [source, *(service.root / "recovery_orphans").rglob("*")]
    assert any(path.is_file() and path.read_bytes() == b"User replacement staged during finalization"
               for path in candidates), "Finalization deleted an independently replaced staging file"
    assert destination.read_bytes() == b"Synthetic finalization source"
    with pytest.raises(jobs.DocumentJobError):
        service.mark_completed(job.id)


def test_legacy_searchable_repair_rejects_foreign_source(owner, tmp_path):
    _jobs, service, job, source = owner
    foreign = tmp_path / "foreign-source.txt"
    foreign.write_bytes(source.read_bytes())
    service.transition_job(job.id, "searchable")
    connection = service._connect()
    try:
        connection.execute("UPDATE document_jobs SET staged_path=? WHERE id=?", (str(foreign), job.id))
        connection.commit()
    finally:
        connection.close()
    result = service.recover_unfinished()
    assert not service.list_document_records(), "Recovery published an unowned source path"
    assert result["finalization_incomplete"] == 1
    assert foreign.read_bytes() == source.read_bytes()


def test_finalization_intent_is_committed_before_source_link(owner, monkeypatch):
    _jobs, service, job, source = owner
    service.mark_searchable(job.id)
    link = os.link
    observed = []

    def inspect(source_path, destination, *args, **kwargs):
        connection = service._connect()
        try:
            saved = connection.execute("SELECT status,stage FROM document_jobs WHERE id=?", (job.id,)).fetchone()
            observed.append((saved["status"], saved["stage"]))
        finally:
            connection.close()
        return link(source_path, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", inspect)
    completed = service.mark_completed(job.id)
    assert observed == [("searchable", "finalize")]
    assert completed.status == "completed" and not source.exists()


def test_explicit_finalization_retry_can_resume_cancelled_batch(owner, monkeypatch):
    _jobs, service, job, _source = owner
    service.mark_searchable(job.id)
    link = os.link

    def fail(*_args, **_kwargs):
        raise OSError("Temporary source placement failure")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(OSError):
        service.mark_completed(job.id)
    monkeypatch.setattr(os, "link", link)
    service.cancel_batch(job.batch_id)
    assert service.get_job(job.id).status == "failed"
    assert service.retry_failed(job.id).status == "completed"
    assert service.claim_next("independent-review") is None
