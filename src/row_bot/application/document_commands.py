"""Reviewed document removal over the existing K07 operation owner.

Passive reviews inspect canonical saved data without importing the initializing
knowledge/document owners. Root owns nonce issuance and current action policy.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlite3 import Row

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any
from uuid import UUID

from row_bot.application.client_platform import ClientPlatformError
from row_bot.data_paths import get_memory_db_path, get_row_bot_data_dir
from row_bot import knowledge_views
from row_bot.runtime import admissions

_REVIEW_BYTES = 16 * 1024 * 1024


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _target(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)):
        raise ClientPlatformError("invalid_document_target")
    return value


def _read_file(path: Path, *, limit: int = _REVIEW_BYTES, digest_only=False, deadline: float | None = None) -> bytes | str | None:
    """Bounded no-follow opened-identity read of a canonical metadata leaf."""
    root = get_row_bot_data_dir(create=False).absolute()
    if path.absolute() == root or root not in path.absolute().parents:
        raise ClientPlatformError("document_review_unavailable")
    parents = []
    for component in (*reversed(path.parents), path):
        if component != root and root not in component.parents:
            continue
        try:
            info = component.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ClientPlatformError("document_review_unavailable")
        if component != path:
            parents.append((component, info.st_dev, info.st_ino))
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or (not digest_only and before.st_size > limit):
        raise ClientPlatformError("document_review_unavailable")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise ClientPlatformError("document_review_unavailable")
        if digest_only:
            digest = hashlib.sha256()
            while block := stream.read(1024 * 1024):
                if deadline is not None and time.monotonic() > deadline:
                    raise ClientPlatformError("document_review_unavailable")
                digest.update(block)
            value = digest.hexdigest()
        else:
            value = stream.read(limit + 1)
            if len(value) > limit:
                raise ClientPlatformError("document_review_unavailable")
        after = os.fstat(stream.fileno())
    final = path.lstat()
    for component, device, inode in parents:
        current = component.lstat()
        if (not stat.S_ISDIR(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400
                or (current.st_dev, current.st_ino) != (device, inode)):
            raise ClientPlatformError("document_review_unavailable")
    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    # Windows path stat and descriptor stat expose different ctime semantics;
    # compare each lifetime internally, and cross-check shared identity/mtime.
    if (identity(before) != identity(final) or identity(opened) != identity(after)
            or identity(before)[:4] != identity(after)[:4] or final.st_nlink != 1):
        raise ClientPlatformError("document_review_unavailable")
    return value


def _json(path: Path, default):
    raw = _read_file(path)
    if raw is None:
        return deepcopy(default)
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise ClientPlatformError("document_review_unavailable") from None


@dataclass(frozen=True)
class _Source:
    id: str
    role: str
    identity: str
    active: bool


@dataclass(frozen=True)
class _Page:
    schema_version: int
    revision: str
    items: tuple[_Source, ...]
    total: int | None
    next_cursor: str | None
    availability: str


def _saved_rows(path, required, sql, params=()) -> tuple[_Source, ...]:
    used = 0
    def build(row: Row) -> _Source:
        nonlocal used
        identifier, role, identity = row["id"], row["role"], row["identity"]
        if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 4096 or "\0" in identifier
                or not isinstance(identity, str) or len(identity) > 16384):
            raise ValueError("Invalid source identity")
        used += len(json.dumps([identifier, role, identity]).encode())
        if used > _REVIEW_BYTES:
            raise ValueError("Source identity budget exceeded")
        return _Source(identifier, role, identity, bool(row["active"]))
    page = knowledge_views._read(path, required, sql, params, build, _Page, "document-removal", 0, None, 1_000_000)
    if page.availability == "unavailable" or page.next_cursor:
        raise ClientPlatformError("document_review_unavailable")
    return page.items


def _source_state(document_id: str | None) -> dict:
    """Complete bounded saved source identity set; progress is not identity."""
    root = get_row_bot_data_dir(create=False)
    rows = _saved_rows(root / "document_ingestion" / "jobs.db", {
        "document_jobs": "id original_name stored_name content_sha256 size_bytes created_at batch_id status",
        "document_records": "document_id original_name stored_name content_sha256 size_bytes",
    }, """SELECT substr(id,1,4097) id,role,
          json_array(substr(original_name,1,4097),substr(stored_name,1,4097),substr(content_sha256,1,129),size_bytes,
                     substr(created_at,1,129),substr(batch_id,1,129)) identity,active,1 matched
        FROM (
          SELECT id,'job' role,original_name,stored_name,content_sha256,size_bytes,created_at,batch_id,
            status NOT IN ('completed','failed','cancelled','skipped_duplicate') active FROM document_jobs
          UNION ALL
          SELECT document_id,'record',original_name,stored_name,content_sha256,size_bytes,'','',1 FROM document_records
        ) WHERE (? IS NULL OR id=?) ORDER BY id,role""", (document_id, document_id))
    sources = {}
    for row in rows:
        values = json.loads(row.identity)
        if (any(not isinstance(value, str) or len(value) > bound
                for value, bound in zip((values[0], values[1], values[2], values[4], values[5]), (4096, 4096, 128, 128, 128)))
                or type(values[3]) is not int or not 0 <= values[3] <= 2**53 - 1):
            raise ClientPlatformError("document_review_unavailable")
        item = sources.setdefault(row.id, {"identities": {}, "included": False})
        item["identities"][row.role] = values
        item["included"] = item["included"] or row.active
    derived = _saved_rows(get_memory_db_path(create_parent=False), {"entities": "source"},
        """SELECT substr(source,10,4097) id,'derived' role,'' identity,1 active,1 matched
           FROM entities WHERE source LIKE 'document:%' AND (? IS NULL OR source=?)
           GROUP BY source ORDER BY source""", (document_id, "document:" + document_id if document_id else None))
    for row in derived:
        sources.setdefault(row.id, {"identities": {}, "included": False})["included"] = True
    manifest = _json(root / "document_index" / "manifest.json", {"version": 1, "documents": []})
    markers = _json(root / "processed_files.json", [])
    if (not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("documents"), list)
            or not isinstance(markers, list) or any(not isinstance(value, str) or not 1 <= len(value) <= 4096 for value in markers)):
        raise ClientPlatformError("document_review_unavailable")
    ids = set(markers)
    for entry in manifest["documents"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("document_id"), str) or not 1 <= len(entry["document_id"]) <= 4096:
            raise ClientPlatformError("document_review_unavailable")
        ids.add(entry["document_id"])
        if document_id is None or entry["document_id"] == document_id:
            item = sources.setdefault(entry["document_id"], {"identities": {}, "included": False})
            # A manifest-only source still has an immutable publication identity.
            # Do not authorize a later same-ID replacement from an empty identity.
            item["identities"]["index"] = [_digest(entry)]
    for identifier in ids:
        if document_id is None or identifier == document_id:
            sources.setdefault(identifier, {"identities": {}, "included": False})["included"] = True
    legacy = None
    legacy_root = root / "vector_store"
    if document_id is None and legacy_root.exists():
        info = legacy_root.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ClientPlatformError("document_review_unavailable")
        files = {}
        deadline = time.monotonic() + 5
        for path in legacy_root.rglob("*"):
            if len(files) >= 10000 or time.monotonic() > deadline:
                raise ClientPlatformError("document_review_unavailable")
            relative = path.relative_to(legacy_root).as_posix()
            info_child = path.lstat()
            if stat.S_ISLNK(info_child.st_mode) or getattr(info_child, "st_file_attributes", 0) & 0x400:
                raise ClientPlatformError("document_review_unavailable")
            files[relative + "/" if stat.S_ISDIR(info_child.st_mode) else relative] = (
                "directory" if stat.S_ISDIR(info_child.st_mode) else _read_file(path, digest_only=True, deadline=deadline))
        legacy = {"device": info.st_dev, "inode": info.st_ino, "files": files}
    saved = {"sources": sources, "legacy_revision": legacy}
    if len(json.dumps(saved).encode()) > _REVIEW_BYTES:
        raise ClientPlatformError("document_review_unavailable")
    return saved


def read_document_removal_review(document_id: str | None, *, validate: Callable[[], None]) -> dict:
    """Root wraps this exact saved state in its session-bound review nonce."""
    validate()
    _target(document_id)
    state = _source_state(document_id)
    result = {"schema_version": 1, "document_id": document_id, "source_revision": _digest(state),
              "source_count": sum(value["included"] for value in state["sources"].values()), "retains_copies": True}
    validate()
    return result


def _scope(owner: str, authority: str) -> str:
    if not isinstance(authority, str) or not 1 <= len(authority) <= 256:
        raise ClientPlatformError("action_denied")
    return admissions.keyed_digest({"document_authority": authority, "owner": owner})


def _uuid(value) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise ClientPlatformError("invalid_document_command") from None


@dataclass(frozen=True)
class _Removal:
    id: str
    target: str
    snapshot: str
    result: str


def _saved_removal(removal_id: str) -> dict | None:
    if not re.fullmatch(r"[a-f0-9]{32}", removal_id):
        raise ClientPlatformError("document_operation_unavailable")
    def build(row: Row) -> _Removal:
        value = _Removal(*(row[key] for key in ("id", "target", "snapshot", "result")))
        if any(not isinstance(item, str) for item in (value.id, value.target, value.snapshot, value.result)):
            raise ValueError("Invalid saved removal")
        return value
    page = knowledge_views._read(get_row_bot_data_dir(create=False) / "document_ingestion" / "jobs.db",
        {"document_removals": "id target snapshot result"},
        "SELECT id,target,snapshot,result,1 matched FROM document_removals WHERE id=?", (removal_id,),
        build, _Page, "removal-receipt", 0, None, 1)
    if page.availability == "unavailable":
        raise ClientPlatformError("document_operation_unavailable")
    if not page.items:
        return None
    value = page.items[0]
    try:
        snapshot, result = json.loads(value.snapshot), json.loads(value.result)
        if not isinstance(snapshot, dict) or not isinstance(result, dict):
            raise ValueError
        if value.id != removal_id or result.get("removal_id") != removal_id or result.get("document_id") != value.target:
            raise ValueError
        if value.target != "*":
            _target(value.target)
        return {"target": value.target, "snapshot": snapshot, "result": result}
    except (ValueError, RecursionError):
        raise ClientPlatformError("document_operation_unavailable") from None


def _public_removal(result: dict) -> dict:
    if not isinstance(result.get("removal_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", result["removal_id"]):
        raise ClientPlatformError("document_operation_unavailable")
    if result.get("document_id") != "*":
        _target(result.get("document_id"))
        if result.get("document_id") is None:
            raise ClientPlatformError("document_operation_unavailable")
    status = result.get("status")
    if status not in {"complete", "pending", "partial"}:
        raise ClientPlatformError("document_operation_unavailable")
    retained = result.get("retained_copies", [])
    stages, failures = result.get("stages", {}), result.get("failures", [])
    if not isinstance(retained, list) or not isinstance(stages, dict) or not isinstance(failures, list):
        raise ClientPlatformError("document_operation_unavailable")
    known_kinds = {"raw_copy", "document_source", "document_index", "legacy_index", "externally_edited_raw",
        "wiki_external_and_recovery_copies", "interrupted_source_copies", "legacy_index_created_after_request"}
    kinds = sorted({item.get("kind") for item in retained if isinstance(item, dict) and item.get("kind") in known_kinds})
    counts = {name: sum(value == name for value in stages.values()) for name in ("complete", "pending", "partial")}
    known_stages = {"worker", "derived_snapshot", "index", "source", "raw_copy", "derived_knowledge", "markers", "record", "legacy_index", "bulk_removal"}
    stage_rows = [{"stage": name, "status": value} for name, value in stages.items()
                  if name in known_stages and value in counts]
    codes = sorted({str(item.get("code")) for item in failures if isinstance(item, dict)
        and item.get("code") in {"cancellation_pending", "bulk_removal_failed", *(name + "_failed" for name in known_stages)}})
    count = result.get("derived_entities_removed")
    if type(count) is not int or not 0 <= count <= 2**53 - 1 or type(result.get("removed")) is not bool:
        raise ClientPlatformError("document_operation_unavailable")
    return {"removal_id": result["removal_id"], "document_id": None if result["document_id"] == "*" else result["document_id"],
        "status": status, "removed": result["removed"], "derived_entities_removed": count,
        "retained_copy_count": len(retained), "retained_kinds": kinds, "stages": stage_rows,
        "stage_counts": counts, "failure_codes": codes}


def public_receipt(value: dict) -> dict:
    """Apply to generic command receipts too; private scope/proof never crosses wire."""
    return {key: deepcopy(item) for key, item in value.items() if key in {"command_id", "status", "code", "removal"}}


def _owned(owner: str, authority: str, command_id: str) -> dict:
    metadata = admissions.read_command_metadata(owner, _uuid(command_id))
    saved = admissions.receipt(owner, command_id)
    if (not metadata or metadata["target"] != "documents" or metadata["type"] not in {"document.remove", "document.removal.retry"}
            or not saved or saved.get("_document_removal", {}).get("scope") != _scope(owner, authority)):
        raise ClientPlatformError("document_operation_unavailable")
    return saved


def read_document_command(*, owner_id: str, authority_id: str, command_id: str, validate: Callable[[], None]) -> dict:
    validate()
    saved = _owned(owner_id, authority_id, command_id)
    if saved.get("status") == "rejected":
        validate()
        return public_receipt(saved)
    value = _saved_removal(saved["_document_removal"]["removal_id"])
    if value is None:
        result = {"command_id": command_id, "status": "partial", "code": "document_outcome_uncertain"}
    else:
        result = {"command_id": command_id, "status": "completed" if value["result"]["status"] == "complete" else "partial",
                  "removal": _public_removal(value["result"])}
    validate()
    return result


def read_document_retry_review(*, owner_id: str, authority_id: str, source_command_id: str,
                               validate: Callable[[], None]) -> dict:
    result = read_document_command(owner_id=owner_id, authority_id=authority_id, command_id=source_command_id, validate=validate)
    if "removal" not in result:
        raise ClientPlatformError("document_operation_unavailable")
    saved = _saved_removal(result["removal"]["removal_id"])
    if saved is None:
        raise ClientPlatformError("document_operation_unavailable")
    count = len(saved["snapshot"].get("documents", [])) if saved["target"] == "*" else 1
    validate()
    return {"schema_version": 1, "source_count": count, "retains_copies": True,
            "source_command_id": source_command_id, "removal_id": result["removal"]["removal_id"],
            "document_id": result["removal"]["document_id"], "status": result["removal"]["status"],
            "source_revision": _digest(result["removal"])}


def execute_document_command(command: dict, *, owner_id: str, authority_id: str, key: str,
        validate: Callable[[], None], validate_action: Callable[[str], None],
        validate_review: Callable[[dict, dict], None]) -> dict:
    """Only explicit retry commands continue saved K07 stages; replays just read."""
    validate()
    command = deepcopy(command)
    command_id = _uuid(command.get("command_id"))
    kind, payload = command.get("type"), command.get("payload")
    if kind not in {"document.remove", "document.removal.retry"} or not isinstance(payload, dict):
        raise ClientPlatformError("invalid_document_command")
    fields = {"document_id", "source_revision", "review_id"} if kind == "document.remove" else {"source_command_id", "review_id"}
    if payload.keys() != fields:
        raise ClientPlatformError("invalid_document_command")
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is not None:
        try:
            admissions.claim_command(owner_id, key, command, "documents")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise ClientPlatformError(str(error)) from error
        return read_document_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    if kind == "document.remove":
        target = _target(payload["document_id"])
        approved = _source_state(target)
        review = {"schema_version": 1, "document_id": target, "source_revision": _digest(approved),
                  "source_count": sum(item["included"] for item in approved["sources"].values()), "retains_copies": True}
        if payload["source_revision"] != review["source_revision"]:
            raise ClientPlatformError("document_review_changed")
        removal_id = UUID(command_id).hex
    else:
        source = _owned(owner_id, authority_id, payload["source_command_id"])
        removal_id = source["_document_removal"]["removal_id"]
        saved = _saved_removal(removal_id)
        if not saved:
            raise ClientPlatformError("document_operation_unavailable")
        target = None if saved["target"] == "*" else _target(saved["target"])
        approved = saved["snapshot"].get("client_review")
        if target is None and not isinstance(approved, dict):
            raise ClientPlatformError("document_review_unavailable")
        review = read_document_retry_review(owner_id=owner_id, authority_id=authority_id,
            source_command_id=payload["source_command_id"], validate=validate)
    def authority() -> None:
        validate()
        validate_action(kind)
        validate_review(command, review)
    authority()
    try:
        prior = admissions.claim_command(owner_id, key, command, "documents")
    except admissions.AdmissionError as error:
        if str(error) != "operation_uncertain":
            raise ClientPlatformError(str(error)) from error
        return read_document_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    if prior is not None:
        return read_document_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    admissions.command_progress(owner_id, key, {"command_id": command_id, "status": "admitting",
        "_document_removal": {"scope": _scope(owner_id, authority_id), "removal_id": removal_id}})

    def snapshot_guard(identifier: str, snapshot: dict) -> None:
        authority()
        if identifier == "*":
            if kind != "document.remove" or _digest(_source_state(None)) != review["source_revision"]:
                raise ClientPlatformError("document_review_changed")
            expected = {identity for identity, item in approved["sources"].items() if item["included"]}
            if set(snapshot["documents"]) != expected or snapshot["legacy_revision"] != approved["legacy_revision"]:
                raise ClientPlatformError("document_review_changed")
            snapshot["client_review"] = deepcopy(approved)
            return
        if not isinstance(approved, dict) or identifier not in approved["sources"]:
            raise ClientPlatformError("document_review_changed")
        if not snapshot["known"]:
            return
        expected = approved["sources"][identifier]["identities"]
        current = _source_state(identifier)["sources"].get(identifier, {}).get("identities", {})
        if any(current.get(role) != value for role, value in expected.items()):
            raise ClientPlatformError("document_review_changed")
        record = snapshot.get("record")
        if record:
            core = [record.get(name) for name in ("original_name", "stored_name", "content_sha256", "size_bytes")]
            if not any(value[:4] == core for role, value in expected.items() if role in {"job", "record"}):
                raise ClientPlatformError("document_review_changed")
    # Only now may initializing canonical mutation owners be imported.
    from row_bot import documents
    try:
        result = (documents.clear_documents_details if target is None else documents.remove_document_details)(
            *(() if target is None else (target,)), removal_id=removal_id, validate=authority, validate_snapshot=snapshot_guard)
    except Exception:
        try:
            missing = _saved_removal(removal_id) is None
        except (OSError, ValueError):
            missing = False
        if missing:
            saved = admissions.receipt(owner_id, command_id) or {}
            admissions.complete_command(owner_id, key, {**saved, "status": "rejected", "code": "document_action_rejected"})
        raise
    authority()
    public = {"command_id": command_id, "status": "completed" if result["status"] == "complete" else "partial",
              "removal": _public_removal(result)}
    saved = admissions.receipt(owner_id, command_id) or {}
    admissions.complete_command(owner_id, key, {**saved, **public})
    return public
