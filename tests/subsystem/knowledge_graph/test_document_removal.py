"""Removal recovery uses only isolated SQLite/filesystem data and fake projections."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.subsystem


@pytest.fixture
def stack(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data))
    from row_bot import document_index, document_jobs, documents, knowledge_graph, wiki_vault
    jobs = importlib.reload(document_jobs)
    index = importlib.reload(document_index)
    docs = importlib.reload(documents)
    kg = importlib.reload(knowledge_graph)
    wiki = importlib.reload(wiki_vault)
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    wiki.set_vault_path(str(tmp_path / "vault"))
    wiki.set_enabled(True)
    service = jobs.DocumentJobService(data)
    return {"jobs": jobs, "index": index, "docs": docs, "kg": kg, "wiki": wiki, "service": service}


def document(stack, name="notes.txt", *, active=False):
    service, index, docs, kg, wiki = (stack[key] for key in ("service", "index", "docs", "kg", "wiki"))
    batch = service.create_batch()
    job = service.create_staging_job(batch, 0, name)
    content = f"Synthetic document {name} {job.id}".encode()
    path = Path(job.staged_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    service.complete_staging(job.id, hashlib.sha256(content).hexdigest(), len(content), path)
    service.finish_batch_staging(batch)
    service.transition_job(job.id, "indexing")
    service.mark_searchable(job.id)
    if active:
        service.transition_job(job.id, "extracting")
    else:
        service.mark_completed(job.id)
    index.initialize_index(docs.DOCUMENT_INDEX_DIR)
    manifest = index.read_corpus_manifest(docs.DOCUMENT_INDEX_DIR)
    manifest["documents"].append({"document_id": job.id, "generation": "generation-original"})
    index._atomic_write_json(docs.DOCUMENT_INDEX_DIR / index.CORPUS_MANIFEST_NAME, manifest)
    live = docs.DOCUMENT_INDEX_DIR / "documents" / job.id / "generation-original"
    live.mkdir(parents=True)
    (live / "synthetic-vector-data").write_bytes(b"retained vector bytes")
    raw = wiki.get_vault_path() / "raw" / job.stored_name
    raw.write_bytes(content)
    entity = kg.save_entity("fact", f"Derived {job.id}", "Derived synthetic document knowledge.", source=f"document:{job.id}")
    article = wiki.export_entity(entity)
    return {"job": service.get_job(job.id), "raw": raw, "content": content, "entity": entity, "article": article, "live": live.parent}


def test_complete_removal_and_duplicate_calls_preserve_retired_copies(stack):
    doc = document(stack)
    docs, service, kg = (stack[key] for key in ("docs", "service", "kg"))
    result = docs.remove_document_details(doc["job"].id)
    assert result["status"] == "complete" and result["removed"]
    assert result["derived_entities_removed"] == 1
    assert not doc["live"].exists() and not doc["raw"].exists() and not doc["article"].exists()
    assert not service.list_document_records()
    assert kg.get_entity(doc["entity"]["id"]) is None
    copies = result["retained_copies"]
    assert {row["kind"] for row in copies} >= {"document_source", "document_index", "raw_copy"}
    assert all(Path(row["path"]).exists() for row in copies)
    assert any(Path(row["path"]).read_bytes() == doc["content"] for row in copies if row["kind"] == "document_source")
    assert docs.remove_document_details(doc["job"].id) == result
    assert docs.remove_document_details(doc["job"].id, removal_id=result["removal_id"]) == result


def test_concurrent_duplicate_requests_share_one_operation(stack):
    doc = document(stack)
    start = threading.Barrier(2)
    def remove():
        start.wait(timeout=10)
        return stack["docs"].remove_document_details(doc["job"].id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(remove), pool.submit(remove)
        left, right = first.result(timeout=10), second.result(timeout=10)
    assert left == right and left["status"] == "complete"
    connection = stack["service"]._connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM document_removals").fetchone()[0] == 1
    finally:
        connection.close()


def test_shard_rename_failure_after_manifest_commit_retries(stack, monkeypatch):
    doc = document(stack)
    index, docs = stack["index"], stack["docs"]
    original_rename = os.rename
    def fail_once(source, destination, *args, **kwargs):
        if Path(source) == doc["live"]:
            raise OSError("synthetic retirement rename failure")
        return original_rename(source, destination, *args, **kwargs)
    monkeypatch.setattr(index.os, "rename", fail_once)
    failed = docs.remove_document_details(doc["job"].id)
    assert failed["status"] == "partial" and failed["failures"][0]["stage"] == "index"
    assert index.read_corpus_manifest(docs.DOCUMENT_INDEX_DIR)["documents"] == []
    assert doc["live"].exists() and stack["service"].list_document_records()
    monkeypatch.setattr(index.os, "rename", original_rename)
    result = docs.remove_document_details(doc["job"].id, removal_id=failed["removal_id"])
    assert result["status"] == "complete" and not doc["live"].exists()
    assert all(Path(item["path"]).exists() for item in result["retained_copies"])


def test_retry_replaces_moved_live_source_reference_with_verified_retired_copy(stack, monkeypatch):
    doc = document(stack)
    original = stack["index"].remove_document_shard
    monkeypatch.setattr(stack["index"], "remove_document_shard", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic early interruption")))
    failed = stack["docs"].remove_document_details(doc["job"].id)
    assert failed["status"] == "partial"
    live_reference = {"kind":"document_source", "path":doc["job"].staged_path}
    assert live_reference in failed["retained_copies"]
    monkeypatch.setattr(stack["index"], "remove_document_shard", original)
    result = stack["docs"].remove_document_details(doc["job"].id, removal_id=failed["removal_id"])
    assert result["status"] == "complete"
    assert live_reference not in result["retained_copies"]
    assert all(Path(item["path"]).exists() for item in result["retained_copies"])
    assert any(Path(item["path"]).read_bytes() == doc["content"] for item in result["retained_copies"] if item["kind"] == "document_source")
    assert stack["docs"].remove_document_details(doc["job"].id, removal_id=failed["removal_id"]) == result


def test_fresh_process_resumes_post_manifest_retirement_failure(stack, monkeypatch):
    doc = document(stack)
    original_rename = os.rename
    def fail_index(source, destination, *args, **kwargs):
        if Path(source) == doc["live"]:
            raise OSError("synthetic process interruption boundary")
        return original_rename(source, destination, *args, **kwargs)
    monkeypatch.setattr(os, "rename", fail_index)
    failed = stack["docs"].remove_document_details(doc["job"].id)
    assert failed["status"] == "partial"
    script = """
import json, sys
from row_bot import documents, knowledge_graph as kg
kg._skip_reindex = True
kg.semantic_search = lambda *args, **kwargs: []
result = documents.remove_document_details(sys.argv[1], removal_id=sys.argv[2])
print(json.dumps(result))
"""
    environment = dict(os.environ, ROW_BOT_DATA_DIR=str(stack["service"].data_dir))
    completed = subprocess.run(
        [sys.executable, "-c", script, doc["job"].id, failed["removal_id"]],
        env=environment, capture_output=True, text=True, timeout=30, check=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "complete" and result["derived_entities_removed"] == 1
    assert not doc["live"].exists() and not doc["raw"].exists()


def test_projection_failure_after_entity_commit_retries_cleanup(stack, monkeypatch):
    doc = document(stack)
    kg, docs = stack["kg"], stack["docs"]
    original_rebuild = kg.rebuild_fts_index
    monkeypatch.setattr(kg, "rebuild_fts_index", lambda: (_ for _ in ()).throw(OSError("synthetic FTS failure")))
    failed = docs.remove_document_details(doc["job"].id)
    assert failed["status"] == "partial"
    assert failed["failures"][0]["stage"] == "derived_knowledge"
    assert kg.get_entity(doc["entity"]["id"]) is None
    assert doc["article"].exists()
    monkeypatch.setattr(kg, "rebuild_fts_index", original_rebuild)
    result = docs.remove_document_details(doc["job"].id, removal_id=failed["removal_id"])
    assert result["status"] == "complete" and result["derived_entities_removed"] == 1
    assert not doc["article"].exists()


def test_source_rename_then_receipt_failure_resumes_from_retained_location(stack, monkeypatch):
    doc = document(stack)
    docs, jobs = stack["docs"], stack["jobs"]
    original_save = jobs.DocumentJobService.save_removal_result
    def fail_save(self, removal_id, result):
        if result["stages"].get("source") == "complete":
            raise OSError("synthetic result commit failure")
        return original_save(self, removal_id, result)
    monkeypatch.setattr(jobs.DocumentJobService, "save_removal_result", fail_save)
    failed = docs.remove_document_details(doc["job"].id)
    assert failed["status"] == "partial"
    assert not Path(doc["job"].staged_path).exists()
    monkeypatch.setattr(jobs.DocumentJobService, "save_removal_result", original_save)
    result = docs.remove_document_details(doc["job"].id, removal_id=failed["removal_id"])
    assert result["status"] == "complete"
    assert any(Path(row["path"]).read_bytes() == doc["content"] for row in result["retained_copies"] if row["kind"] == "document_source")


@pytest.mark.parametrize("copy_kind", ["source", "raw"])
def test_interrupted_no_replace_retirement_retains_and_recovers_bytes(stack, monkeypatch, copy_kind):
    doc = document(stack)
    docs = stack["docs"]
    original_link = os.link
    def fail_link(source, destination, *args, **kwargs):
        destination = Path(destination)
        is_raw = ".row-bot-retired" in destination.parts
        if is_raw == (copy_kind == "raw"):
            raise OSError("synthetic no-replace link interruption")
        return original_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(os, "link", fail_link)
    failed = docs.remove_document_details(doc["job"].id)
    assert failed["status"] == "partial"
    owner = stack["wiki"].get_vault_path() / "raw" if copy_kind == "raw" else stack["service"].root
    assert any(path.read_bytes() == doc["content"] for path in owner.rglob("*.retiring"))
    monkeypatch.setattr(os, "link", original_link)
    result = docs.remove_document_details(doc["job"].id, removal_id=failed["removal_id"])
    assert result["status"] == "complete"


def test_private_retirement_collision_never_overwrites_existing_file(stack, monkeypatch):
    doc = document(stack)
    original_link = os.link
    created = []
    def race_link(source, destination, *args, **kwargs):
        destination = Path(destination)
        if "retired" in destination.parts and destination.name == doc["job"].stored_name:
            destination.write_bytes(b"unowned concurrent file")
            created.append(destination)
        return original_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(os, "link", race_link)
    result = stack["docs"].remove_document_details(doc["job"].id)
    assert result["status"] == "partial"
    assert created[0].read_bytes() == b"unowned concurrent file"
    assert any(path.read_bytes() == doc["content"] for path in created[0].parent.glob("*.retiring"))


def test_active_worker_must_acknowledge_cancellation_before_removal(stack):
    doc = document(stack, active=True)
    docs, service = stack["docs"], stack["service"]
    pending = docs.remove_document_details(doc["job"].id)
    assert pending["status"] == "pending"
    assert pending["failures"][0]["code"] == "cancellation_pending"
    assert doc["live"].exists() and doc["raw"].exists()
    assert service.should_cancel(doc["job"].id)
    service.transition_job(doc["job"].id, "cancelled")
    result = docs.remove_document_details(doc["job"].id, removal_id=pending["removal_id"])
    assert result["status"] == "complete"
    with pytest.raises(stack["jobs"].DocumentCancelled):
        service.transition_job(doc["job"].id, "searchable")


@pytest.mark.parametrize("copy_kind", ["source", "raw"])
def test_edited_copies_are_preserved_and_report_partial(stack, copy_kind):
    doc = document(stack)
    path = Path(doc["job"].staged_path) if copy_kind == "source" else doc["raw"]
    path.write_bytes(b"externally edited synthetic data")
    result = stack["docs"].remove_document_details(doc["job"].id)
    assert result["status"] == "partial"
    assert path.read_bytes() == b"externally edited synthetic data"
    assert stack["service"].list_document_records()


def test_edited_wiki_is_retained_while_authoritative_knowledge_is_removed(stack):
    doc = document(stack)
    doc["article"].write_text("external wiki edit", encoding="utf-8")
    result = stack["docs"].remove_document_details(doc["job"].id)
    assert result["status"] == "complete"
    assert doc["article"].read_text(encoding="utf-8") == "external wiki edit"
    assert any(row["kind"] == "wiki_external_and_recovery_copies" for row in result["retained_copies"])


def test_generation_change_after_pending_removal_preserves_new_generation(stack):
    doc = document(stack, active=True)
    docs, index, service = stack["docs"], stack["index"], stack["service"]
    pending = docs.remove_document_details(doc["job"].id)
    manifest = index.read_corpus_manifest(docs.DOCUMENT_INDEX_DIR)
    manifest["documents"][0]["generation"] = "generation-new"
    index._atomic_write_json(docs.DOCUMENT_INDEX_DIR / index.CORPUS_MANIFEST_NAME, manifest)
    service.transition_job(doc["job"].id, "cancelled")
    result = docs.remove_document_details(doc["job"].id, removal_id=pending["removal_id"])
    assert result["status"] == "partial"
    assert index.read_corpus_manifest(docs.DOCUMENT_INDEX_DIR)["documents"][0]["generation"] == "generation-new"
    assert doc["live"].exists()


def test_legacy_removal_tombstones_without_mutating_index(stack):
    docs, kg = stack["docs"], stack["kg"]
    docs.PROCESSED_FILES_PATH.write_text(json.dumps(["legacy.txt"]), encoding="utf-8")
    docs.VECTOR_STORE_DIR.mkdir()
    original = docs.VECTOR_STORE_DIR / "legacy-data"
    original.write_bytes(b"retained legacy bytes")
    entity = kg.save_entity("fact", "Legacy", "Synthetic legacy knowledge content.", source="document:legacy.txt")
    result = docs.remove_document_details("legacy.txt")
    assert result["status"] == "complete" and result["removed"]
    assert original.read_bytes() == b"retained legacy bytes"
    assert kg.get_entity(entity["id"]) is None
    assert docs._strict_names(docs.DOCUMENT_INDEX_DIR / "legacy_tombstones.json") == {"legacy.txt"}
    assert "legacy.txt" not in docs.load_processed_files()


def test_bulk_clear_retains_raw_copies_and_is_retryable(stack):
    first, second = document(stack, "first.txt"), document(stack, "second.txt")
    result = stack["docs"].clear_documents_details()
    assert result["status"] == "complete"
    assert result["derived_entities_removed"] == 2
    assert first["raw"].read_bytes() == first["content"]
    assert second["raw"].read_bytes() == second["content"]
    assert not stack["service"].list_document_records()
    assert stack["docs"].clear_documents_details(removal_id=result["removal_id"]) == result


def test_bulk_legacy_results_name_actual_retired_path(stack):
    docs = stack["docs"]
    docs.PROCESSED_FILES_PATH.write_text('["legacy.txt"]', encoding="utf-8")
    docs.VECTOR_STORE_DIR.mkdir()
    (docs.VECTOR_STORE_DIR / "retained-data").write_bytes(b"legacy")
    result = docs.clear_documents_details()
    assert result["status"] == "complete"
    assert not docs.VECTOR_STORE_DIR.exists()
    assert all(Path(item["path"]).exists() for item in result["retained_copies"])


def test_bulk_partial_failure_retries_remaining_target_and_counts(stack, monkeypatch):
    first, second = document(stack, "first.txt"), document(stack, "second.txt")
    docs, kg = stack["docs"], stack["kg"]
    original_delete = kg.delete_entities_by_source
    def fail_second(source, **kwargs):
        if source == f"document:{second['job'].id}":
            raise OSError("synthetic second target failure")
        return original_delete(source, **kwargs)
    monkeypatch.setattr(kg, "delete_entities_by_source", fail_second)
    failed = docs.clear_documents_details()
    assert failed["status"] == "partial"
    assert first["raw"].exists() and second["raw"].exists()
    monkeypatch.setattr(kg, "delete_entities_by_source", original_delete)
    result = docs.clear_documents_details(removal_id=failed["removal_id"])
    assert result["status"] == "complete" and result["derived_entities_removed"] == 2


def test_bulk_retry_does_not_cancel_or_remove_new_uploads(stack, monkeypatch):
    original = document(stack, "original.txt")
    docs, kg, service = stack["docs"], stack["kg"], stack["service"]
    original_delete = kg.delete_entities_by_source
    monkeypatch.setattr(kg, "delete_entities_by_source", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic partial clear")))
    failed = docs.clear_documents_details()
    assert failed["status"] == "partial"
    fresh = document(stack, "fresh.txt", active=True)
    monkeypatch.setattr(kg, "delete_entities_by_source", original_delete)
    result = docs.clear_documents_details(removal_id=failed["removal_id"])
    assert result["status"] == "complete"
    assert not original["live"].exists()
    assert fresh["live"].exists() and fresh["raw"].exists()
    assert not service.should_cancel(fresh["job"].id)
    assert kg.get_entity(fresh["entity"]["id"]) is not None


def test_wrong_removal_target_and_corrupt_markers_fail_closed(stack):
    doc = document(stack)
    result = stack["docs"].remove_document_details(doc["job"].id)
    with pytest.raises(ValueError):
        stack["docs"].remove_document_details("another", removal_id=result["removal_id"])
    path = stack["docs"].PROCESSED_FILES_PATH
    path.write_text("corrupt markers", encoding="utf-8")
    with pytest.raises(ValueError):
        stack["docs"].remove_document_details("unknown")
    assert path.read_text(encoding="utf-8") == "corrupt markers"


def test_failed_cleanup_does_not_remove_reassigned_entity(stack, monkeypatch):
    doc = document(stack)
    kg, docs = stack["kg"], stack["docs"]
    original_rebuild = kg.rebuild_fts_index
    monkeypatch.setattr(kg, "rebuild_fts_index", lambda: (_ for _ in ()).throw(OSError("synthetic failure")))
    failed = docs.remove_document_details(doc["job"].id)
    # A new user entity deliberately reusing an ID is outside the old source scope.
    connection = kg._get_conn()
    try:
        columns = list(doc["entity"])
        entity = dict(doc["entity"], source="live", description="Reassigned user entity survives.")
        connection.execute(f"INSERT INTO entities ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           [entity[key] for key in columns])
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(kg, "rebuild_fts_index", original_rebuild)
    result = docs.remove_document_details(doc["job"].id, removal_id=failed["removal_id"])
    assert result["status"] == "complete" and result["derived_entities_removed"] == 0
    assert kg.get_entity(entity["id"])["source"] == "live"


@pytest.mark.parametrize("publication", ["searchable", "completed"])
def test_removal_between_status_and_publication_cannot_resurrect_record(stack, monkeypatch, publication):
    doc = document(stack, active=True)
    service, docs = stack["service"], stack["docs"]
    # Put the synthetic job at the relevant publication entry boundary.
    connection = service._connect()
    try:
        connection.execute("UPDATE document_jobs SET status=? WHERE id=?",
                           ("indexing" if publication == "searchable" else "searchable", doc["job"].id))
        connection.commit()
    finally:
        connection.close()
    publish = service._publish_job_record
    removed = []
    def interleave(job, status, source):
        if status == publication:
            # The state/record transaction is now atomic. Interleave at its
            # admission boundary, where cancellation must still win safely.
            if status == "searchable":
                service.transition_job(job.id, "searchable")
            removed.append(docs.remove_document_details(job.id))
        return publish(job, status, source)
    monkeypatch.setattr(service, "_publish_job_record", interleave)
    with pytest.raises(stack["jobs"].DocumentCancelled):
        getattr(service, f"mark_{publication}")(doc["job"].id)
    assert removed[0]["status"] == "complete"
    assert not service.list_document_records()
    assert not Path(service.get_job(doc["job"].id).staged_path).exists()
    assert stack["jobs"].DocumentJobService(service.data_dir)._write_lock is service._write_lock


def test_compatibility_marker_publication_respects_removal(stack, monkeypatch, tmp_path):
    docs, jobs = stack["docs"], stack["jobs"]
    source = tmp_path / "original.txt"
    source.write_text("Synthetic original user file.", encoding="utf-8")
    monkeypatch.setattr(docs, "index_document_job", lambda *_args: None)
    mark_searchable = jobs.DocumentJobService.mark_searchable
    removed = []
    def interleave(self, job_id):
        job = mark_searchable(self, job_id)
        removed.append(docs.remove_document_details(job_id))
        return job
    monkeypatch.setattr(jobs.DocumentJobService, "mark_searchable", interleave)
    with pytest.raises(RuntimeError, match="marker publication"):
        docs.load_and_vectorize_document(str(source), display_name="original.txt")
    assert removed[0]["status"] == "complete"
    assert "original.txt" not in docs.load_processed_files()
    assert source.read_text(encoding="utf-8") == "Synthetic original user file."


def test_post_commit_reassignment_preserves_new_owned_article(stack, monkeypatch):
    doc = document(stack)
    kg, wiki = stack["kg"], stack["wiki"]
    rebuild = kg.rebuild_fts_index
    replacement = dict(doc["entity"], source="live", description="Replacement user knowledge survives.")
    article = []
    def interleave():
        connection = kg._get_conn()
        try:
            columns = list(replacement)
            connection.execute(f"INSERT INTO entities ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                               [replacement[key] for key in columns])
            connection.commit()
        finally:
            connection.close()
        article.append(wiki.export_entity(replacement))
        rebuild()
    monkeypatch.setattr(kg, "rebuild_fts_index", interleave)
    result = stack["docs"].remove_document_details(doc["job"].id)
    assert result["status"] == "complete" and result["derived_entities_removed"] == 0
    assert kg.get_entity(replacement["id"])["source"] == "live"
    assert "Replacement user knowledge survives." in article[0].read_text(encoding="utf-8")


def test_concurrent_marker_writer_waits_and_preserves_new_marker(stack, monkeypatch):
    doc = document(stack)
    docs = stack["docs"]
    original_lock, write_names = docs._processed_files_lock, docs._write_names
    removal_writing, writer_checked, release_removal = (threading.Event() for _ in range(3))
    blocked = []
    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "marker-writer":
                acquired = original_lock.acquire(blocking=False)
                blocked.append(not acquired)
                writer_checked.set()
                if not acquired:
                    original_lock.acquire()
            else:
                original_lock.acquire()
            return self
        def __exit__(self, *_args):
            original_lock.release()
    def pause_write(path, values):
        if path == docs.PROCESSED_FILES_PATH:
            removal_writing.set()
            assert release_removal.wait(10)
        return write_names(path, values)
    monkeypatch.setattr(docs, "_processed_files_lock", ObservedLock())
    monkeypatch.setattr(docs, "_write_names", pause_write)
    failures = []
    def add_marker():
        try:
            docs.save_processed_file("new.txt")
        except Exception as exc:
            failures.append(exc)
    with ThreadPoolExecutor(max_workers=1) as pool:
        removal = pool.submit(docs.remove_document_details, doc["job"].id)
        assert removal_writing.wait(10)
        writer = threading.Thread(target=add_marker, name="marker-writer")
        writer.start()
        try:
            assert writer_checked.wait(10)
            assert blocked == [True]
        finally:
            release_removal.set()
        assert removal.result(timeout=10)["status"] == "complete"
        writer.join(10)
    assert not writer.is_alive() and not failures
    assert docs._strict_names(docs.PROCESSED_FILES_PATH) == {"new.txt"}


@pytest.mark.parametrize("original_exists", [False, True])
def test_bulk_retry_preserves_new_or_changed_legacy_index(stack, monkeypatch, original_exists):
    document(stack)
    docs, kg = stack["docs"], stack["kg"]
    if original_exists:
        docs.VECTOR_STORE_DIR.mkdir()
        (docs.VECTOR_STORE_DIR / "data").write_bytes(b"original index")
    delete = kg.delete_entities_by_source
    monkeypatch.setattr(kg, "delete_entities_by_source", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("partial clear")))
    failed = docs.clear_documents_details()
    assert failed["status"] == "partial"
    docs.VECTOR_STORE_DIR.mkdir(exist_ok=True)
    (docs.VECTOR_STORE_DIR / "data").write_bytes(b"new index")
    monkeypatch.setattr(kg, "delete_entities_by_source", delete)
    result = docs.clear_documents_details(removal_id=failed["removal_id"])
    assert result["status"] == ("partial" if original_exists else "complete")
    assert (docs.VECTOR_STORE_DIR / "data").read_bytes() == b"new index"


def test_late_worker_entity_is_durably_captured_for_projection_retry(stack, monkeypatch):
    doc = document(stack, active=True)
    docs, kg, wiki, service = (stack[key] for key in ("docs", "kg", "wiki", "service"))
    pending = docs.remove_document_details(doc["job"].id)
    assert pending["status"] == "pending"
    late = kg.save_entity("fact", "Late extracted fact", "Worker committed before cancellation acknowledgement.",
                          source=f"document:{doc['job'].id}")
    article = wiki.export_entity(late)
    service.transition_job(doc["job"].id, "cancelled")
    rebuild = kg.rebuild_fts_index
    monkeypatch.setattr(kg, "rebuild_fts_index", lambda: (_ for _ in ()).throw(OSError("postcommit failure")))
    failed = docs.remove_document_details(doc["job"].id, removal_id=pending["removal_id"])
    assert failed["status"] == "partial"
    assert kg.get_entity(late["id"]) is None and article.exists()
    captured = service.get_removal(pending["removal_id"])["snapshot"]
    assert captured["entities_captured"] and {entity["id"] for entity in captured["entities"]} == {doc["entity"]["id"], late["id"]}
    monkeypatch.setattr(kg, "rebuild_fts_index", rebuild)
    result = docs.remove_document_details(doc["job"].id, removal_id=pending["removal_id"])
    assert result["status"] == "complete" and result["derived_entities_removed"] == 2
    assert not article.exists() and not doc["article"].exists()


def test_derived_write_after_capture_is_preserved_and_reported_partial(stack, monkeypatch):
    doc = document(stack)
    kg, docs = stack["kg"], stack["docs"]
    delete = kg.delete_entities_by_source
    late = []
    def interleave(source, **kwargs):
        late.append(kg.save_entity("fact", "New user fact", "Not part of the captured removal work.", source=source))
        return delete(source, **kwargs)
    monkeypatch.setattr(kg, "delete_entities_by_source", interleave)
    result = docs.remove_document_details(doc["job"].id)
    assert result["status"] == "partial" and result["failures"][0]["stage"] == "derived_knowledge"
    assert kg.get_entity(late[0]["id"]) is not None
    assert kg.get_entity(doc["entity"]["id"]) is not None


def test_recalled_entity_removes_unchanged_generated_article(stack):
    doc = document(stack)
    kg = stack["kg"]
    before = doc["article"].read_bytes()
    kg._touch_recalled([doc["entity"]["id"]])
    assert kg.get_entity(doc["entity"]["id"])["properties"] != doc["entity"]["properties"]
    assert doc["article"].read_bytes() == before
    result = stack["docs"].remove_document_details(doc["job"].id)
    assert result["status"] == "complete" and result["derived_entities_removed"] == 1
    assert not doc["article"].exists()


@pytest.mark.parametrize("late_publication", [False, True])
def test_bulk_clear_waits_for_unpublished_worker_and_retires_late_generation(stack, late_publication):
    service, docs, index = (stack[key] for key in ("service", "docs", "index"))
    batch = service.create_batch()
    job = service.create_staging_job(batch, 0, "unpublished.txt")
    source = Path(job.staged_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"Synthetic unfinished source")
    service.complete_staging(job.id, hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source)
    service.finish_batch_staging(batch)
    service.transition_job(job.id, "indexing")
    pending = docs.clear_documents_details()
    assert pending["status"] == "pending" and source.exists()
    assert service.should_cancel(job.id)
    live = docs.DOCUMENT_INDEX_DIR / "documents" / job.id / "late-generation"
    if late_publication:
        index.initialize_index(docs.DOCUMENT_INDEX_DIR)
        manifest = index.read_corpus_manifest(docs.DOCUMENT_INDEX_DIR)
        manifest["documents"].append({"document_id": job.id, "generation": "late-generation"})
        index._atomic_write_json(docs.DOCUMENT_INDEX_DIR / index.CORPUS_MANIFEST_NAME, manifest)
        live.mkdir(parents=True)
        (live / "synthetic-vectors").write_bytes(b"Prepared before cancellation check")
    service.transition_job(job.id, "cancelled")
    result = docs.clear_documents_details(removal_id=pending["removal_id"])
    assert result["status"] == "complete" and result["removed"]
    assert not source.exists() and not live.exists()
    assert index.read_corpus_manifest(docs.DOCUMENT_INDEX_DIR)["documents"] == []
    assert service.latest_removal(job.id)["result"]["status"] == "complete"


def test_bulk_default_resumes_after_reload_then_allows_fresh_clear(stack, monkeypatch):
    original = document(stack, "original.txt")
    docs, kg = stack["docs"], stack["kg"]
    delete = kg.delete_entities_by_source
    monkeypatch.setattr(kg, "delete_entities_by_source", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("partial clear")))
    failed = docs.clear_documents_details()
    assert failed["status"] == "partial"
    fresh = document(stack, "fresh.txt")
    monkeypatch.setattr(kg, "delete_entities_by_source", delete)
    # Discard all module/view state. The canonical existing-store receipt resumes.
    docs = importlib.reload(docs)
    resumed = docs.clear_documents_details()
    assert resumed["removal_id"] == failed["removal_id"]
    assert resumed["status"] == "complete" and resumed["derived_entities_removed"] == 1
    assert not original["live"].exists() and fresh["live"].exists()
    assert docs.clear_documents_details(removal_id=resumed["removal_id"]) == resumed
    assert fresh["live"].exists()
    new_clear = docs.clear_documents_details()
    assert new_clear["removal_id"] != resumed["removal_id"]
    assert new_clear["status"] == "complete" and new_clear["derived_entities_removed"] == 1
    assert not fresh["live"].exists()
    assert docs.clear_documents_details(removal_id=resumed["removal_id"]) == resumed
