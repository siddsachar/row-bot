# ruff: noqa: F811 -- canonical isolated jobs/admission fixture.
from __future__ import annotations

import contextlib
import asyncio
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest

from tests.subsystem.knowledge_graph.test_document_job_commands import client  # noqa: F401


def run_async(function):
    @wraps(function)
    def run(*args,**kwargs):
        return asyncio.run(function(*args,**kwargs))
    return run


class Stream:
    def __init__(self,data: bytes):
        self.data,self.offset,self.calls = data,0,[]
    async def read(self,size):
        self.calls.append(size)
        result = self.data[self.offset:self.offset + size]
        self.offset += len(result)
        return result


def command(files=None):
    return {"command_id":str(uuid4()),"type":"document.upload","payload":{"files":files or [{"name":"example.txt","size_bytes":7}],"review_id":"reviewed"}}


async def execute(client,value,streams=None,**callbacks):
    from row_bot.application import document_upload_commands as api
    return await api.execute_document_upload(value,streams if streams is not None else [Stream(b"example")],service=client[1],
        key=value["command_id"],**client[2],**{"validate_action":lambda kind:None,"validate_review":lambda command,review:None,
        "disk_free":lambda path:10 * 1024**3,**callbacks})


def ids(value,sequence=0):
    identifier = UUID(value["command_id"])
    return "client_" + identifier.hex,"upload_" + uuid5(identifier,str(sequence)).hex


@run_async
async def test_streams_all_files_into_paused_existing_job_owner(client):
    value = command([{"name":"one.txt","size_bytes":3},{"name":"two.md","size_bytes":3}])
    first,second = Stream(b"one"),Stream(b"two")
    result = await execute(client,value,[first,second])
    assert result["status"] == "completed" and result["processing"] == "paused"
    batch,_ = ids(value)
    assert client[1].get_batch(batch).status == "paused"
    assert [Path(row.staged_path).read_bytes() for row in client[1].list_jobs(batch)] == [b"one",b"two"]
    assert max(first.calls + second.calls) == 1024**2
    assert client[1].claim_next("worker") is None


@run_async
async def test_strict_id_remains_unprocessable_when_proof_missing_or_corrupt(client):
    value = command()
    assert (await execute(client,value))["status"] == "completed"
    batch,_ = ids(value)
    service = client[1]
    with contextlib.closing(service._connect()) as conn,conn:
        conn.execute("DELETE FROM document_batch_admissions WHERE batch_id=?",(batch,))
        conn.execute("UPDATE document_batches SET status='queued',pause_requested=0 WHERE id=?",(batch,))
    assert service.claim_next("worker") is None
    assert batch not in service.finalizable_batches()
    assert service.list_jobs(batch)[0].status == "queued"


@run_async
async def test_duplicate_source_retains_both_copies_and_does_not_reprocess(client):
    service = client[1]
    first = command()
    assert (await execute(client,first))["status"] == "completed"
    _,job_id = ids(first)
    job = service.get_job(job_id)
    with contextlib.closing(service._connect()) as conn,conn:
        conn.execute("INSERT INTO document_records VALUES (?,?,?,?,?,?,?,?)",
            (job.id,job.original_name,job.stored_name,job.staged_path,job.content_sha256,job.size_bytes,"saved",""))
    second = command()
    result = await execute(client,second)
    assert result["files"][0]["status"] == "skipped_duplicate"
    assert Path(job.staged_path).read_bytes() == b"example"
    assert Path(service.get_job(ids(second)[1]).staged_path).read_bytes() == b"example"


@run_async
async def test_repeated_command_never_reads_stream_again(client,monkeypatch):
    value = command()
    expected = await execute(client,value)
    class Unreadable:
        async def read(self,*args):
            pytest.fail("repeated input read")
    assert await execute(client,value,[Unreadable()]) == expected
    assert len(client[1].list_jobs()) == 1


@run_async
async def test_lost_completed_ack_stays_partial_without_republication(client,monkeypatch):
    from row_bot.application import document_upload_commands as api
    value = command()
    monkeypatch.setattr(api.admissions,"complete_command",lambda *args:(_ for _ in ()).throw(OSError("lost ack")))
    assert (await execute(client,value))["status"] == "partial"
    stream = Stream(b"different")
    assert (await execute(client,value,[stream]))["status"] == "partial"
    assert stream.calls == [] and len(client[1].list_jobs()) == 1


@run_async
@pytest.mark.parametrize("data",[b"short",b"too much data"])
async def test_stream_size_mismatch_retains_candidate_without_recording_success(client,data):
    value = command()
    result = await execute(client,value,[Stream(data)])
    batch,job = ids(value)
    assert result["status"] == "partial"
    assert client[1].get_job(job).status == "staging"
    assert client[1].get_batch(batch).pause_requested
    assert list((client[1].staging_root / job).glob(".*.uploading"))
    assert client[1].claim_next("worker") is None


@run_async
async def test_revocation_after_chunk_keeps_written_bytes_and_stops_later_effects(client):
    service = client[1]
    value = command()
    revoked = False
    class Revoking:
        def __init__(self):
            self.calls=0
        async def read(self,size):
            nonlocal revoked
            self.calls += 1
            if self.calls == 1:
                return b"example"
            revoked = True
            return b""
    def validate_review(*args):
        if revoked:
            raise PermissionError("sentinel authority")
    with pytest.raises(PermissionError,match="sentinel authority"):
        await execute(client,value,[Revoking()],validate_review=validate_review)
    _,job = ids(value)
    assert [path.read_bytes() for path in (service.staging_root / job).glob(".*.uploading")] == [b"example"]
    assert service.get_job(job).status == "staging"


@run_async
async def test_cancel_during_stream_prevents_record_publication(client):
    service = client[1]
    value = command()
    batch,_ = ids(value)
    class Cancelling(Stream):
        async def read(self,size):
            result = await super().read(size)
            if not result:
                service.cancel_batch(batch)
            return result
    assert (await execute(client,value,[Cancelling(b"example")]))["status"] == "partial"
    assert service.get_job(ids(value)[1]).status == "cancelled"
    assert not service.list_document_records()


@run_async
async def test_existing_predicted_job_directory_is_preserved(client):
    service = client[1]
    value = command()
    _,job = ids(value)
    destination = service.staging_root / job
    destination.mkdir()
    (destination / "user.txt").write_text("never overwrite")
    stream = Stream(b"example")
    assert (await execute(client,value,[stream]))["status"] == "partial"
    assert stream.calls == [] and (destination / "user.txt").read_text() == "never overwrite"


@run_async
async def test_publication_collision_keeps_both_candidate_and_external_bytes(client,monkeypatch):
    import row_bot.developer.edits as edits
    value = command()
    original = edits._rename_edit_no_replace
    def collision(source,destination,**kwargs):
        if kwargs.get("dst_dir_fd") is None:
            Path(destination).write_bytes(b"external bytes")
        else:
            fd = os.open(destination,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600,dir_fd=kwargs["dst_dir_fd"])
            os.write(fd,b"external bytes")
            os.close(fd)
        return original(source,destination,**kwargs)
    monkeypatch.setattr(edits,"_rename_edit_no_replace",collision)
    assert (await execute(client,value))["status"] == "partial"
    path = Path(client[1].get_job(ids(value)[1]).staged_path)
    assert path.read_bytes() == b"external bytes"
    assert [item.read_bytes() for item in path.parent.glob(".*.uploading")] == [b"example"]


@run_async
async def test_disk_reserve_rejection_never_consumes_upload_stream(client):
    value = command()
    stream = Stream(b"example")
    assert (await execute(client,value,[stream],disk_free=lambda path:1))["status"] == "partial"
    assert stream.calls == []


def test_passive_upload_review_has_no_initialization_or_provider_work(tmp_path,monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR",str(tmp_path / "absent"))
    from row_bot.application import document_upload_commands as api
    result = api.read_document_upload_review([{"name":"example.txt","size_bytes":7}],validate=lambda:None)
    assert result["processing"] == "paused" and result["provider_work"] is False
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("files",[[{"name":"C:/private/input.txt","size_bytes":7}],
    [{"name":"example.txt","size_bytes":True}],[{"name":"example.exe","size_bytes":7}],
    [{"name":"example.txt","size_bytes":256*1024*1024+1}]])
def test_rejects_unsafe_or_unbounded_upload_metadata(files):
    from row_bot.application import document_upload_commands as api
    with pytest.raises(Exception,match="invalid_document_upload"):
        api.read_document_upload_review(files,validate=lambda:None)


@run_async
@pytest.mark.parametrize("name",["Document.PDF","a" * 200 + ".txt"])
async def test_safe_uppercase_and_long_display_names_preserve_upload_compatibility(client,name):
    from row_bot.application import document_job_commands as queue
    value = command([{"name":name,"size_bytes":7}])
    result = await execute(client,value)
    assert result["status"] == "completed" and result["files"][0]["name"] == name
    page = queue.read_document_queue(kind="jobs",batch_id=result["batch_id"],validate=lambda:None)
    assert page.availability == "available" and page.items[0].name == name


@run_async
async def test_read_receipt_is_passive_scoped_and_content_free(client,monkeypatch):
    from row_bot.application import document_upload_commands as api
    value = command()
    result = await execute(client,value)
    monkeypatch.setattr(api.admissions,"transaction",lambda *args:pytest.fail("passive initialization"))
    assert api.read_document_upload_command(command_id=value["command_id"],**client[2]) == result
    assert "sha256" not in json.dumps(result) and "staged_path" not in json.dumps(result)
    with pytest.raises(Exception,match="unavailable"):
        api.read_document_upload_command(command_id=value["command_id"],**{**client[2],"authority_id":"different"})


@run_async
async def test_uploaded_hash_matches_all_streamed_bytes(client):
    value = command()
    await execute(client,value)
    job = client[1].get_job(ids(value)[1])
    assert job.content_sha256 == hashlib.sha256(b"example").hexdigest()


@run_async
async def test_candidate_leaf_replacement_preserves_original_and_replacement(client,monkeypatch):
    import row_bot.developer.edits as edits
    value = command()
    original = edits._rename_edit_no_replace
    def replace(source,destination,**kwargs):
        descriptor = kwargs.get("src_dir_fd")
        retained = str(source) + ".original" if descriptor is None else str(source) + ".original"
        os.rename(source,retained,src_dir_fd=descriptor,dst_dir_fd=descriptor)
        fd = os.open(source,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600,dir_fd=descriptor)
        os.write(fd,b"foreign")
        os.close(fd)
        return original(source,destination,**kwargs)
    monkeypatch.setattr(edits,"_rename_edit_no_replace",replace)
    assert (await execute(client,value))["status"] == "partial"
    source = Path(client[1].get_job(ids(value)[1]).staged_path)
    assert source.read_bytes() == b"foreign"
    assert [path.read_bytes() for path in source.parent.glob("*.original")] == [b"example"]
    assert client[1].get_job(ids(value)[1]).status == "staging"


@run_async
async def test_directory_replacement_after_creation_checkpoint_is_not_adopted(client,monkeypatch):
    from row_bot.application import document_upload_commands as api
    value = command()
    original = api.admissions.command_progress
    _,job = ids(value)
    directory = client[1].staging_root / job
    def replace(owner,key,receipt):
        result = original(owner,key,receipt)
        if receipt.get("_document_upload",{}).get("phase") == "directory_created":
            directory.rename(directory.with_name(directory.name + ".original"))
            directory.mkdir()
            (directory / "external.txt").write_text("foreign directory")
        return result
    monkeypatch.setattr(api.admissions,"command_progress",replace)
    stream = Stream(b"example")
    assert (await execute(client,value,[stream]))["status"] == "partial"
    assert stream.calls == []
    assert list(directory.iterdir()) == [directory / "external.txt"]


@run_async
async def test_new_hardlink_after_saved_candidate_rejects_publication_receipt(client,monkeypatch):
    from row_bot.application import document_upload_commands as api
    value = command()
    original = api.admissions.command_progress
    directory = client[1].staging_root / ids(value)[1]
    def link(owner,key,receipt):
        result = original(owner,key,receipt)
        if receipt.get("_document_upload",{}).get("phase") == "candidate_saved":
            candidate = next(directory.glob(".*.uploading"))
            os.link(candidate,directory / "external-link")
        return result
    monkeypatch.setattr(api.admissions,"command_progress",link)
    assert (await execute(client,value))["status"] == "partial"
    assert (directory / "external-link").read_bytes() == b"example"
    assert client[1].get_job(ids(value)[1]).status == "staging"
