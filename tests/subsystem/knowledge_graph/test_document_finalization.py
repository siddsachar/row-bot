"""Fault boundaries in the existing document job owner, with isolated real files/SQL."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    from row_bot import document_jobs
    jobs = importlib.reload(document_jobs)
    service = jobs.DocumentJobService(tmp_path / "data", now=lambda: "2026-09-10T00:00:00+00:00")
    batch = service.create_batch()
    job = service.create_staging_job(batch, 0, "synthetic.txt")
    source = Path(job.staged_path)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"Synthetic finalization source")
    service.complete_staging(job.id, hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source)
    service.finish_batch_staging(batch)
    service.transition_job(job.id, "indexing")
    return jobs, service, service.get_job(job.id), source


def assert_complete(service, job_id):
    job = service.get_job(job_id)
    expected = service.completed_root / job.id / job.stored_name
    assert job.status == "completed" and job.stage == "finalize"
    assert job.staged_path == str(expected) and expected.is_file()
    assert not (service.staging_root / job.id / job.stored_name).exists()
    record = next(item for item in service.list_document_records() if item["document_id"] == job_id)
    for key in ("original_name", "stored_name", "staged_path", "content_sha256", "size_bytes", "searchable_at", "completed_at"):
        assert record[key] == getattr(job, key)
    assert job.searchable_at and job.completed_at and not job.error_code
    return job


class FaultConnection:
    def __init__(self, connection, fault):
        self.connection, self.fault = connection, fault
    def __enter__(self):
        self.connection.__enter__()
        return self
    def __exit__(self, *args):
        return self.connection.__exit__(*args)
    def __getattr__(self, key):
        return getattr(self.connection, key)
    def execute(self, sql, *args):
        self.fault(sql)
        return self.connection.execute(sql, *args)
    def commit(self):
        self.fault("COMMIT")
        return self.connection.commit()


@pytest.mark.parametrize("boundary", ["INSERT INTO document_records", "UPDATE document_jobs SET status=", "COMMIT"])
def test_searchable_state_and_record_commit_together(owner, monkeypatch, boundary):
    _jobs, service, job, _source = owner
    connect = service._connect
    def fault(sql):
        if boundary in sql:
            raise OSError("synthetic transaction boundary")
    monkeypatch.setattr(service, "_connect", lambda: FaultConnection(connect(), fault))
    with pytest.raises(OSError):
        service.mark_searchable(job.id)
    monkeypatch.setattr(service, "_connect", connect)
    assert service.get_job(job.id).status == "indexing"
    assert not service.list_document_records()
    service.mark_searchable(job.id)
    assert service.get_job(job.id).status == "searchable"
    assert len(service.list_document_records()) == 1


@pytest.mark.parametrize("boundary", ["link", "retain", "retained", "record", "job", "commit"])
def test_finalization_fault_restarts_without_repeating_ingestion(owner, monkeypatch, boundary):
    jobs, service, job, source = owner
    service.mark_searchable(job.id)
    service.transition_job(job.id, "extracting", stage="knowledge_commit")
    connect, link, retain = service._connect, os.link, jobs._rename_source_no_replace
    def fault(sql):
        selected = {"record": "INSERT INTO document_records", "job": "UPDATE document_jobs SET status=", "commit": "COMMIT"}.get(boundary)
        if selected and selected in sql:
            raise OSError("synthetic finalization transaction fault")
    def fail_link(*_args, **_kwargs):
        raise OSError("synthetic source link fault")
    def fail_retention(source_path, destination):
        if boundary == "retained":
            retain(source_path, destination)
        raise OSError("synthetic source retention fault")
    monkeypatch.setattr(service, "_connect", lambda: FaultConnection(connect(), fault))
    if boundary == "link":
        monkeypatch.setattr(os, "link", fail_link)
    elif boundary in {"retain", "retained"}:
        monkeypatch.setattr(jobs, "_rename_source_no_replace", fail_retention)
    with pytest.raises(OSError):
        service.mark_completed(job.id)
    monkeypatch.setattr(service, "_connect", connect)
    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(jobs, "_rename_source_no_replace", retain)
    failed = service.get_job(job.id)
    assert failed.status != "completed" and failed.stage == "finalize"
    assert source.is_file() or (service.completed_root / job.id / job.stored_name).is_file()
    resumed = jobs.DocumentJobService(service.data_dir)
    result = resumed.recover_unfinished()
    assert result["finalization_recovered"] == 1
    assert result["indexing_restarted"] == result["extraction_resumed"] == 0
    completed = assert_complete(resumed, job.id)
    assert resumed.mark_completed(job.id) == completed
    assert resumed.recover_unfinished()["finalization_recovered"] == 0


@pytest.mark.parametrize("legacy_state", ["state_only", "moved", "job_path", "missing_record"])
def test_legacy_completed_inconsistency_reconciles_both_locations(owner, legacy_state):
    _jobs, service, job, source = owner
    service.mark_searchable(job.id)
    destination = service.completed_root / job.id / job.stored_name
    if legacy_state != "state_only":
        destination.parent.mkdir(parents=True)
        os.rename(source, destination)
    connection = service._connect()
    try:
        connection.execute("UPDATE document_jobs SET status='completed',stage='finalize',completed_at='legacy-complete' WHERE id=?", (job.id,))
        if legacy_state == "job_path":
            connection.execute("UPDATE document_jobs SET staged_path=? WHERE id=?", (str(destination), job.id))
        if legacy_state == "missing_record":
            connection.execute("DELETE FROM document_records WHERE document_id=?", (job.id,))
        connection.commit()
    finally:
        connection.close()
    assert service.recover_unfinished()["finalization_recovered"] == 1
    assert assert_complete(service, job.id).completed_at == "legacy-complete"


@pytest.mark.parametrize("problem", ["edited", "destination_collision", "ambiguous", "missing", "foreign_path"])
def test_recovery_preserves_uncertain_sources_and_clear_finished_keeps_job(owner, problem, tmp_path):
    _jobs, service, job, source = owner
    service.mark_searchable(job.id)
    destination = service.completed_root / job.id / job.stored_name
    original = source.read_bytes()
    foreign = tmp_path / "foreign.txt"
    foreign.write_bytes(original)
    if problem == "edited":
        source.write_bytes(b"User edited bytes")
    elif problem in {"destination_collision", "ambiguous"}:
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"User destination bytes" if problem == "destination_collision" else original)
    elif problem == "missing":
        source.unlink()
    connection = service._connect()
    try:
        connection.execute("UPDATE document_jobs SET status='completed', stage='finalize', completed_at='legacy' WHERE id=?", (job.id,))
        if problem == "foreign_path":
            connection.execute("UPDATE document_jobs SET staged_path=? WHERE id=?", (str(foreign), job.id))
        connection.commit()
    finally:
        connection.close()
    before = {str(path): path.read_bytes() for path in (source, destination, foreign) if path.exists()}
    result = service.recover_unfinished()
    assert result["finalization_incomplete"] == 1
    failed = service.get_job(job.id)
    assert failed.status == "failed" and failed.error_code == "finalization_incomplete"
    service.finalize_batch(job.batch_id)
    assert service.clear_finished() == 0
    assert service.get_job(job.id).status == "failed"
    assert {str(path): path.read_bytes() for path in (source, destination, foreign) if path.exists()} == before


def test_failed_finalization_retry_uses_destination_without_extraction(owner, monkeypatch):
    _jobs, service, job, _source = owner
    service.mark_searchable(job.id)
    publish = service._publish_job_record
    monkeypatch.setattr(service, "_publish_job_record", lambda *_args: (_ for _ in ()).throw(OSError("post-move commit")))
    with pytest.raises(OSError):
        service.mark_completed(job.id)
    monkeypatch.setattr(service, "_publish_job_record", publish)
    retried = service.retry_failed(job.id)
    assert retried.status == "completed"
    assert_complete(service, job.id)
    assert service.claim_next("synthetic-owner") is None


def test_completed_removal_intent_prevents_recovery_resurrection(owner):
    _jobs, service, job, source = owner
    service.mark_searchable(job.id)
    connection = service._connect()
    try:
        connection.execute("UPDATE document_jobs SET status='completed',stage='finalize' WHERE id=?", (job.id,))
        connection.execute("DELETE FROM document_records WHERE document_id=?", (job.id,))
        connection.commit()
    finally:
        connection.close()
    service.begin_removal(job.id, {}, {"status": "complete"}, removal_id="a" * 32)
    assert service.recover_unfinished()["finalization_recovered"] == 0
    assert source.is_file() and not service.list_document_records()


def test_legacy_searchable_missing_record_is_repaired(owner):
    _jobs, service, job, _source = owner
    service.transition_job(job.id, "searchable")
    assert not service.list_document_records()
    result = service.recover_unfinished()
    assert result["finalization_recovered"] == 1
    assert service.get_job(job.id).status == "searchable"
    assert service.list_document_records()[0]["document_id"] == job.id


def test_fresh_process_recovers_post_link_interruption(owner, monkeypatch):
    jobs, service, job, _source = owner
    service.mark_searchable(job.id)
    def fail(*_args):
        raise OSError("synthetic interruption after link")
    monkeypatch.setattr(jobs, "_rename_source_no_replace", fail)
    with pytest.raises(OSError):
        service.mark_completed(job.id)
    script = "from row_bot.document_jobs import DocumentJobService; import json; print(json.dumps(DocumentJobService().recover_unfinished()))"
    child = subprocess.run([sys.executable, "-c", script], env=dict(os.environ, ROW_BOT_DATA_DIR=str(service.data_dir)),
                           capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(child.stdout)["finalization_recovered"] == 1
    assert_complete(service, job.id)


def test_restart_acknowledges_removed_worker_without_republishing(owner):
    _jobs, service, job, source = owner
    service.begin_removal(job.id, {}, {"status": "pending"}, removal_id="b" * 32)
    service.cancel_job(job.id)
    assert service.get_job(job.id).status == "indexing"
    result = service.recover_unfinished()
    assert result["cancellations_acknowledged"] == 1
    assert service.get_job(job.id).status == "cancelled"
    assert source.is_file() and not service.list_document_records()
    assert result["indexing_restarted"] == 0


def test_changed_completed_bytes_before_sql_publication_are_preserved(owner, monkeypatch):
    _jobs, service, job, _source = owner
    service.mark_searchable(job.id)
    publish = service._publish_job_record
    destination = service.completed_root / job.id / job.stored_name
    def interleave(current, status, path):
        path.write_bytes(b"New externally edited completed source")
        return publish(current, status, path)
    monkeypatch.setattr(service, "_publish_job_record", interleave)
    with pytest.raises(Exception, match="source changed"):
        service.mark_completed(job.id)
    assert service.get_job(job.id).status == "failed"
    assert destination.read_bytes() == b"New externally edited completed source"
    assert service.list_document_records()[0]["completed_at"] == ""


def test_legacy_completed_batch_cancel_does_not_prevent_source_reconciliation(owner):
    _jobs, service, job, _source = owner
    service.mark_searchable(job.id)
    service.transition_job(job.id, "completed", stage="finalize")
    service.cancel_batch(job.batch_id)
    assert service.recover_unfinished()["finalization_recovered"] == 1
    assert_complete(service, job.id)


def test_clear_finished_preserves_legacy_source_owned_by_pending_removal(owner):
    _jobs, service, job, source = owner
    service.mark_searchable(job.id)
    service.transition_job(job.id, "completed", stage="finalize")
    service.finalize_batch(job.batch_id)
    service.begin_removal(job.id, {}, {"status": "partial"}, removal_id="c" * 32)
    assert service.clear_finished() == 0
    assert source.is_file() and service.get_job(job.id).status == "completed"


def test_searchable_record_repair_failure_cannot_complete_unextracted_job(owner, monkeypatch):
    _jobs, service, job, _source = owner
    service.transition_job(job.id, "searchable")
    publish = service._publish_job_record
    monkeypatch.setattr(service, "_publish_job_record", lambda *_args: (_ for _ in ()).throw(OSError("record repair failed")))
    result = service.recover_unfinished()
    assert result["finalization_incomplete"] == 1
    assert service.get_job(job.id).status == "searchable"
    assert service.get_job(job.id).stage != "finalize"
    monkeypatch.setattr(service, "_publish_job_record", publish)
    service.recover_unfinished()
    assert service.get_job(job.id).status == "searchable"
    assert service.list_document_records()[0]["completed_at"] == ""


def test_recovery_destination_collision_is_never_overwritten(owner, monkeypatch):
    jobs, service, job, source = owner
    service.mark_searchable(job.id)
    retain = jobs._rename_source_no_replace
    collisions = []
    def interleave(staged, destination):
        destination.write_bytes(b"Independent existing recovery entry")
        collisions.append(destination)
        retain(staged, destination)
    monkeypatch.setattr(jobs, "_rename_source_no_replace", interleave)
    with pytest.raises(FileExistsError):
        service.mark_completed(job.id)
    assert source.read_bytes() == b"Synthetic finalization source"
    assert collisions[0].read_bytes() == b"Independent existing recovery entry"
    monkeypatch.setattr(jobs, "_rename_source_no_replace", retain)
    with pytest.raises(jobs.DocumentJobError):
        service.mark_completed(job.id)
    assert len(list(collisions[0].parent.iterdir())) == 1
    assert collisions[0].read_bytes() == b"Independent existing recovery entry"


@pytest.mark.parametrize("platform, symbol, flag", [("linux", "renameat2", 1), ("darwin", "renamex_np", 4)])
def test_unix_retention_requests_exclusive_native_rename(owner, monkeypatch, platform, symbol, flag):
    import ctypes
    from types import SimpleNamespace
    jobs, _service, _job, source = owner
    calls = []
    class Rename:
        def __call__(self, *arguments):
            calls.append(arguments)
            return 0
    rename = Rename()
    monkeypatch.setattr(jobs, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace(**{symbol: rename}))
    destination = source.with_name("retained.txt")
    jobs._rename_source_no_replace(source, destination)
    assert calls[0][-1] == flag
    assert os.fsencode(source.absolute()) in calls[0]
    assert os.fsencode(destination.absolute()) in calls[0]
    assert rename.restype is ctypes.c_int


def test_unavailable_exclusive_rename_preserves_source(owner, monkeypatch):
    import ctypes
    from types import SimpleNamespace
    jobs, _service, _job, source = owner
    monkeypatch.setattr(jobs, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: SimpleNamespace())
    with pytest.raises(jobs.DocumentJobError, match="unavailable"):
        jobs._rename_source_no_replace(source, source.with_name("retained.txt"))
    assert source.read_bytes() == b"Synthetic finalization source"
