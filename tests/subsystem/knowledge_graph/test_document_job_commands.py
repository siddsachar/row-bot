from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR",str(tmp_path / "data"))
    from row_bot import document_jobs as jobs
    from row_bot.application import document_job_commands as api
    monkeypatch.setattr(jobs,"_wake_supervisor",lambda:None)
    service = jobs.DocumentJobService(tmp_path / "data")
    return api,service,{"owner_id":"test-owner","authority_id":"test-session","validate":lambda:None}


def queued(client,name="example.txt",*,finish=True):
    _,service,_ = client
    batch = service.create_batch()
    job = service.create_staging_job(batch,0,name)
    path = Path(job.staged_path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b"example document")
    service.complete_staging(job.id,hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_size,path)
    if finish:
        service.finish_batch_staging(batch)
    return batch,service.get_job(job.id)


def command(client,kind,target=None,targets=None):
    api,_,_ = client
    if kind == "document.jobs.clear_finished":
        payload = {"targets":[{"id":value,"revision":api.common._digest(api._find("batch",value))} for value in targets]}
    else:
        payload = {"target_id":target,"revision":api.common._digest(api._find("job" if kind.startswith("document.job.") else "batch",target))}
    review = api.read_document_control_review(kind,payload,validate=lambda:None)
    return {"command_id":str(uuid4()),"type":kind,"payload":{**payload,"review_id":"reviewed"}},review


def execute(client,value,**callbacks):
    api,service,identity = client
    return api.execute_document_control(value,service=service,key=value["command_id"],**identity,
        **{"validate_action":lambda action:None,"validate_review":lambda value,review:None,**callbacks})


def snapshot(service,batch):
    from row_bot.document_jobs import document_control_snapshot
    with contextlib.closing(service._connect()) as conn:
        return document_control_snapshot(conn,[batch])


def test_pause_resume_use_saved_queue_and_explicit_policy(client):
    _,service,_ = client
    batch,_ = queued(client)
    calls = []
    value,_ = command(client,"document.batch.pause",batch)
    assert execute(client,value,validate_action=calls.append)["outcome"] == "paused"
    assert service.get_batch(batch).pause_requested
    value,review = command(client,"document.batch.resume",batch)
    assert review["provider_work"]
    assert execute(client,value,validate_action=calls.append)["saved_status"] == "queued"
    assert not service.get_batch(batch).pause_requested
    assert "document.batch.resume" in calls


def test_active_job_cancel_is_requested_until_worker_ack(client):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    value,_ = command(client,"document.job.cancel",job.id)
    result = execute(client,value)
    assert result["outcome"] == "cancellation_requested" and result["saved_status"] == "indexing"
    assert service.get_job(job.id).cancel_requested


def test_batch_cancel_preserves_completed_jobs(client):
    _,service,_ = client
    batch,job = queued(client,finish=False)
    second = service.create_staging_job(batch,1,"another.txt")
    source = Path(second.staged_path)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"another document")
    service.complete_staging(second.id,hashlib.sha256(source.read_bytes()).hexdigest(),source.stat().st_size,source)
    service.finish_batch_staging(batch)
    service.claim_next("worker")
    service.mark_searchable(job.id)
    service.mark_completed(job.id)
    value,_ = command(client,"document.batch.cancel",batch)
    assert execute(client,value)["outcome"] == "cancellation_requested"
    assert service.get_job(job.id).status == "completed"
    assert service.get_job(second.id).status == "cancelled"


def test_scope_changed_after_review_before_writer_preserves_all_rows(client,monkeypatch):
    _,service,_ = client
    batch,job = queued(client,finish=False)
    value,_ = command(client,"document.batch.pause",batch)
    original = service.pause_batch
    def changed(*args,**kwargs):
        service.create_staging_job(batch,1,"late.txt")
        return original(*args,**kwargs)
    monkeypatch.setattr(service,"pause_batch",changed)
    assert execute(client,value)["status"] == "partial"
    assert not service.get_batch(batch).pause_requested and service.get_job(job.id).status == "queued"


def test_revoke_before_commit_rolls_back_batch_and_jobs(client):
    _,service,_ = client
    batch,job = queued(client)
    captured = snapshot(service,batch)
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError,match="revoked"):
        service.cancel_batch(batch,expected_snapshot=captured,validate=validate)
    assert snapshot(service,batch) == captured


def test_retry_preserves_work_and_resets_only_failed_job(client):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_failed(job.id,"parse_failed","private path",stage="parse")
    work = service.work_root / job.id
    work.mkdir()
    (work / "intermediate.json").write_text("retained bytes")
    value,_ = command(client,"document.job.retry",job.id)
    result = execute(client,value)
    assert result["retained_work"] and result["saved_status"] == "queued"
    assert not work.exists()
    assert [path.read_text() for path in service.work_root.glob(".retry-*/intermediate.json")] == ["retained bytes"]
    assert Path(service.get_job(job.id).staged_path).read_bytes() == b"example document"


def test_retry_refuses_new_removal_at_writer_boundary(client,monkeypatch):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_failed(job.id,"failed","",stage="parse")
    value,_ = command(client,"document.job.retry",job.id)
    original = service.retry_failed
    def changed(*args,**kwargs):
        service.begin_removal(job.id,{"document_id":job.id},{"status":"pending"},removal_id=uuid4().hex)
        return original(*args,**kwargs)
    monkeypatch.setattr(service,"retry_failed",changed)
    assert execute(client,value)["status"] == "partial"
    assert service.get_job(job.id).status == "failed"


def test_clear_only_reviewed_terminal_rows_retains_work_and_source(client):
    api,service,_ = client
    batch,job = queued(client)
    service.cancel_batch(batch)
    other,otherjob = queued(client,"other.txt")
    service.cancel_batch(other)
    work = service.work_root / job.id
    work.mkdir()
    (work / "unrecognized.txt").write_text("retained")
    value,_ = command(client,"document.jobs.clear_finished",targets=[batch])
    result = execute(client,value)
    assert result["count"] == 1 and result["retained_work"]
    assert api.read_document_queue(validate=lambda:None).total == 1
    assert service.get_job(otherjob.id).id == otherjob.id
    assert Path(job.staged_path).exists() and (work / "unrecognized.txt").read_text() == "retained"


def test_clear_never_recovers_or_deletes_active_job(client,monkeypatch):
    _,service,_ = client
    batch,job = queued(client)
    monkeypatch.setattr(service,"_recover_finalizations",lambda:pytest.fail("unexpected recovery"))
    with pytest.raises(Exception,match="not safely finished"):
        service.clear_finished(expected_snapshot=snapshot(service,batch),validate=lambda:None)
    assert service.get_job(job.id).status == "queued"


def test_unknown_receipt_never_retries_queue_effect(client,monkeypatch):
    api,service,identity = client
    batch,_ = queued(client)
    value,_ = command(client,"document.batch.pause",batch)
    monkeypatch.setattr(api.admissions,"complete_command",lambda *args:(_ for _ in ()).throw(OSError("lost acknowledgement")))
    assert execute(client,value)["status"] == "partial"
    monkeypatch.setattr(service,"pause_batch",lambda *args,**kw:pytest.fail("repeated effect"))
    assert execute(client,value)["status"] == "partial"
    assert api.read_document_control_command(command_id=value["command_id"],**identity)["code"] == "document_outcome_uncertain"


def test_completed_receipt_read_is_passive_and_auth_scoped(client,monkeypatch):
    api,_,identity = client
    batch,_ = queued(client)
    value,_ = command(client,"document.batch.pause",batch)
    result = execute(client,value)
    monkeypatch.setattr(api.admissions,"transaction",lambda *args:pytest.fail("read initialized admissions"))
    assert api.read_document_control_command(command_id=value["command_id"],**identity) == result
    with pytest.raises(Exception,match="unavailable"):
        api.read_document_control_command(command_id=value["command_id"],**{**identity,"authority_id":"another"})


def test_queue_paginates_and_never_exposes_paths_or_raw_error(client):
    api,service,_ = client
    for index in range(53):
        batch,job = queued(client,f"{index}.txt")
    page = api.read_document_queue(validate=lambda:None)
    assert page.total == 53 and len(page.items) == 50 and page.next_cursor
    second = api.read_document_queue(cursor=page.next_cursor,validate=lambda:None)
    assert len(second.items) == 3
    jobs = api.read_document_queue(kind="jobs",batch_id=batch,validate=lambda:None)
    assert jobs.items[0].id == job.id
    assert "staged_path" not in json.dumps(jobs.items[0].__dict__)
    service.pause_batch(batch)
    with pytest.raises(Exception,match="cursor_expired"):
        api.read_document_queue(cursor=page.next_cursor,validate=lambda:None)


def test_cold_reads_do_not_initialize_job_service(tmp_path,monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR",str(tmp_path / "absent"))
    from row_bot.application import document_job_commands as api
    from row_bot import document_jobs
    monkeypatch.setattr(document_jobs.DocumentJobService,"__init__",lambda *args:pytest.fail("initialized"))
    assert api.read_document_queue(validate=lambda:None).availability == "missing"
    assert not (tmp_path / "absent").exists()


def test_retry_finalization_does_not_repeat_parse_or_embedding(client,monkeypatch):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_searchable(job.id)
    service.mark_failed(job.id,"finalization_incomplete","",stage="finalize")
    service.cancel_batch(batch)
    value,_ = command(client,"document.job.retry",job.id)
    result = execute(client,value)
    assert result["saved_status"] == "completed"
    assert service.get_job(job.id).stage == "finalize"
    assert service.list_document_records()[0]["completed_at"]


def test_retry_revocation_before_retention_leaves_work_and_job_unchanged(client):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_failed(job.id,"failed","",stage="parse")
    work = service.work_root / job.id
    work.mkdir()
    captured = snapshot(service,batch)
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError):
        service.retry_failed(job.id,expected_snapshot=captured,validate=validate)
    assert work.exists() and snapshot(service,batch) == captured


def test_retry_changed_source_preserves_current_bytes_and_work(client):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_failed(job.id,"failed","",stage="parse")
    work = service.work_root / job.id
    work.mkdir()
    Path(job.staged_path).write_bytes(b"user replacement")
    captured = snapshot(service,batch)
    with pytest.raises(Exception,match="source changed"):
        service.retry_failed(job.id,expected_snapshot=captured,validate=lambda:None)
    assert Path(job.staged_path).read_bytes() == b"user replacement"
    assert work.exists() and snapshot(service,batch) == captured


def test_finalization_authority_loss_after_link_preserves_both_sources_and_original_error(client,monkeypatch):
    import os
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_searchable(job.id)
    service.mark_failed(job.id,"original_finalization_error","retained original error",stage="finalize")
    original = os.link
    revoked = False
    def link(*args,**kwargs):
        nonlocal revoked
        original(*args,**kwargs)
        revoked = True
    def validate():
        if revoked:
            raise PermissionError("authority sentinel")
    monkeypatch.setattr(os,"link",link)
    with pytest.raises(PermissionError,match="authority sentinel"):
        service.retry_failed(job.id,expected_snapshot=snapshot(service,batch),validate=validate)
    assert Path(job.staged_path).read_bytes() == b"example document"
    assert (service.completed_root / job.id / job.stored_name).read_bytes() == b"example document"
    assert service.get_job(job.id).error_code == "original_finalization_error"
    assert service.get_job(job.id).error_message == "retained original error"


def test_clear_missing_completed_source_is_not_claimed_finished(client):
    _,service,_ = client
    batch,job = queued(client)
    service.claim_next("worker")
    service.mark_searchable(job.id)
    completed = service.mark_completed(job.id)
    service.finalize_batch(batch)
    Path(completed.staged_path).unlink()
    captured = snapshot(service,batch)
    with pytest.raises(Exception,match="source changed"):
        service.clear_finished(expected_snapshot=captured,validate=lambda:None)
    assert snapshot(service,batch) == captured


def test_policy_rejection_precedes_claim_and_preserves_queue(client):
    api,service,identity = client
    batch,_ = queued(client)
    value,_ = command(client,"document.batch.resume",batch)
    captured = snapshot(service,batch)
    def reject(_kind):
        raise PermissionError("blocked provider work")
    with pytest.raises(PermissionError,match="blocked provider work"):
        execute(client,value,validate_action=reject)
    assert api.admissions.read_command_metadata(identity["owner_id"],value["command_id"]) is None
    assert snapshot(service,batch) == captured


def test_passive_review_refuses_oversized_saved_rows_without_truncation(client):
    api,service,_ = client
    batch,job = queued(client)
    with contextlib.closing(service._connect()) as conn,conn:
        conn.execute("UPDATE document_jobs SET error_message=? WHERE id=?",("x" * (2 * 1024 * 1024 + 1),job.id))
    value = {"target_id":batch,"revision":api.common._digest(api._find("batch",batch))}
    with pytest.raises(Exception,match="unavailable"):
        api.read_document_control_review("document.batch.pause",value,validate=lambda:None)


def test_client_clear_does_not_erase_an_active_sibling_added_after_review(client):
    _,service,_ = client
    batch,job = queued(client)
    service.cancel_batch(batch)
    captured = snapshot(service,batch)
    with contextlib.closing(service._connect()) as conn,conn:
        conn.execute("UPDATE document_jobs SET status='indexing',cancel_requested=0 WHERE id=?",(job.id,))
    with pytest.raises(Exception,match="scope changed"):
        service.clear_finished(expected_snapshot=captured,validate=lambda:None)
    assert service.get_job(job.id).status == "indexing"


def test_forged_private_receipt_does_not_escape_wire(client):
    api,_,identity = client
    batch,_ = queued(client)
    value,_ = command(client,"document.batch.pause",batch)
    execute(client,value)
    api.admissions.complete_command(identity["owner_id"],value["command_id"],
        {"command_id":value["command_id"],"status":"completed","_document_queue":"not a proof"})
    with pytest.raises(Exception,match="unavailable"):
        api.read_document_control_command(command_id=value["command_id"],**identity)
