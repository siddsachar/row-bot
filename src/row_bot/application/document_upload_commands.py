"""Reviewed streaming upload into paused canonical document batches."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.document_jobs import DocumentJobService

from collections.abc import Callable, Sequence
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid5

from row_bot.application import knowledge_commands as common
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.runtime import admissions

_EXTENSIONS = {".pdf",".doc",".docx",".txt",".md",".html",".htm",".epub"}
_MAX_BYTES = 256 * 1024 * 1024


def _files(values):
    from row_bot.document_jobs import is_safe_document_name
    if not isinstance(values,list) or not 1 <= len(values) <= 50:
        raise common._error("invalid_document_upload")
    result = []
    for item in values:
        if (not isinstance(item,dict) or item.keys() != {"name","size_bytes"} or not isinstance(item["name"],str)
                or not is_safe_document_name(item["name"])
                or Path(item["name"]).suffix.lower() not in _EXTENSIONS
                or type(item["size_bytes"]) is not int or not 0 < item["size_bytes"] <= _MAX_BYTES):
            raise common._error("invalid_document_upload")
        result.append(dict(item))
    return result


def read_document_upload_review(files: list[dict], *, validate: Callable[[],None]) -> dict:
    validate()
    files = _files(deepcopy(files))
    result = {"schema_version":1,"action":"document.upload","files":files,"file_count":len(files),
        "total_bytes":sum(item["size_bytes"] for item in files),"intent_digest":common._digest(files),
        "processing":"paused","provider_work":False}
    validate()
    return result


def _scope(owner,authority,*,read_only=False):
    if not isinstance(authority,str) or not 1 <= len(authority) <= 256:
        raise common._error("action_denied")
    return admissions.keyed_digest({"document_upload_authority":authority,"owner":owner},read_only=read_only)


def read_document_upload_command(*, owner_id: str, authority_id: str, command_id: str,
                                 validate: Callable[[],None]) -> dict:
    validate()
    command_id = common._uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id,command_id)
    saved = admissions.read_command_receipt(owner_id,command_id)
    if (not metadata or metadata["type"] != "document.upload" or metadata["target"] != "document-uploads"
            or not saved or saved.get("command_id") != command_id or not isinstance(saved.get("_document_upload"),dict)
            or saved["_document_upload"].get("scope") != _scope(owner_id,authority_id,read_only=True)):
        raise common._error("document_upload_unavailable")
    result = {"command_id":command_id,"status":"partial","code":"document_upload_uncertain"}
    if saved.get("status") == "completed":
        batch_id = saved.get("batch_id")
        files = saved.get("files")
        if (batch_id != "client_" + UUID(command_id).hex or saved.get("processing") != "paused"
                or not isinstance(files,list) or not 1 <= len(files) <= 50):
            raise common._error("document_upload_unavailable")
        public = []
        for sequence,item in enumerate(files):
            if (not isinstance(item,dict) or item.get("id") != "upload_" + uuid5(UUID(command_id),str(sequence)).hex
                    or item.get("status") not in {"queued","skipped_duplicate"}):
                raise common._error("document_upload_unavailable")
            checked = _files([{key:item.get(key) for key in ("name","size_bytes")}])[0]
            public.append({**checked,"id":item["id"],"status":item["status"]})
        result = {"command_id":command_id,"status":"completed","batch_id":batch_id,"files":public,"processing":"paused"}
    validate()
    return result


async def execute_document_upload(command: dict, streams: Sequence, *, service: DocumentJobService | None, owner_id: str,
        authority_id: str, key: str, validate: Callable[[],None], validate_action: Callable[[str],None],
        validate_review: Callable[[dict,dict],None], disk_free: Callable[[Path], int] | None = None) -> dict:
    validate()
    command = deepcopy(command)
    command_id = common._uuid(command.get("command_id"))
    payload = command.get("payload")
    if command.get("type") != "document.upload" or not isinstance(payload,dict) or payload.keys() != {"files","review_id"}:
        raise common._error("invalid_document_upload")
    def receipt() -> dict:
        return read_document_upload_command(owner_id=owner_id,authority_id=authority_id,command_id=command_id,validate=validate)
    if admissions.read_command_metadata(owner_id,command_id) is not None:
        try:
            admissions.claim_command(owner_id,key,command,"document-uploads")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise common._error(str(error)) from error
        return receipt()  # Repeated request never consumes or republishes bytes.
    review = read_document_upload_review(payload["files"],validate=validate)
    if not isinstance(streams,Sequence) or len(streams) != review["file_count"]:
        raise common._error("invalid_document_upload")
    authority_error = None
    def authority() -> None:
        nonlocal authority_error
        try:
            validate()
            validate_action("document.upload")
            validate_review(command,review)
        except Exception as error:
            authority_error = error
            raise
    authority()
    expected = get_row_bot_data_dir(create=False) / "document_ingestion" / "jobs.db"
    if service.db_path.absolute() != expected.absolute():
        raise common._error("document_upload_unavailable")
    try:
        prior = admissions.claim_command(owner_id,key,command,"document-uploads")
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise common._error(str(error)) from error
        return receipt()
    if prior is not None:
        return receipt()
    batch_id = "client_" + UUID(command_id).hex
    proof = {"scope":_scope(owner_id,authority_id),"phase":"effect_started"}
    def checkpoint(stage: str, details: dict) -> None:
        proof.update(phase=stage,details=details)
        admissions.command_progress(owner_id,key,{"command_id":command_id,"status":"effect_started","_document_upload":deepcopy(proof)})
    checkpoint("effect_started",{})
    files = []
    try:
        service.create_batch(batch_id=batch_id,client_admission={"owner_id":owner_id,"command_id":command_id},validate=authority)
        checkpoint("batch_created",{"batch_id":batch_id})
        from row_bot.document_uploads import stage_upload
        for sequence,item in enumerate(review["files"]):
            authority()
            options = {"disk_free":disk_free} if disk_free is not None else {}
            job = await stage_upload(service,batch_id,sequence,item["name"],streams[sequence],
                job_id="upload_" + uuid5(UUID(command_id),str(sequence)).hex,declared_size=item["size_bytes"],
                validate=authority,checkpoint=checkpoint,**options)
            files.append({**item,"id":job.id,"status":job.status})
        service.finish_batch_staging(batch_id,paused=True,validate=authority)
        checkpoint("batch_paused",{"batch_id":batch_id})
    except Exception:
        if authority_error is not None:
            raise authority_error
        return receipt()
    saved = {"command_id":command_id,"status":"completed","batch_id":batch_id,"files":files,
        "processing":"paused","_document_upload":proof}
    try:
        admissions.complete_command(owner_id,key,saved)
    except Exception:
        # Staged bytes may be durable even when their final acknowledgement is
        # lost. Read only the exact original, rechecking current authority.
        return receipt()
    return receipt()
