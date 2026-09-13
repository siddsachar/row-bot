"""Reviewed document removal retains exact authority, sources and operation IDs."""
# ruff: noqa: F811 -- reuse canonical isolated K07 owner fixture.
from concurrent.futures import ThreadPoolExecutor
import threading
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from tests.subsystem.knowledge_graph.test_document_removal import stack, document  # noqa: F401


@pytest.fixture
def client(stack, monkeypatch):
    from row_bot import tasks
    from row_bot.application import document_commands as adapter
    monkeypatch.setattr(tasks, "_DB_PATH", str(stack["service"].data_dir / "tasks.db"))
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    return adapter, {"owner_id": "synthetic-installation", "authority_id": "synthetic-auth", "validate": lambda: None}


def reviewed(client, document_id):
    adapter, context = client
    review = adapter.read_document_removal_review(document_id, validate=context["validate"])
    value = {"command_id": str(uuid4()), "type": "document.remove", "expected_revision": "0",
             "payload": {"document_id": document_id, "source_revision": review["source_revision"], "review_id": "synthetic-reviewed"}}
    return value


def execute(client, command, **callbacks):
    adapter, context = client
    return adapter.execute_document_command(command, key=command["command_id"], **context,
        **{"validate_action": lambda action: None, "validate_review": lambda command, review: None, **callbacks})


def test_review_snapshot_rejection_happens_before_durable_intent_or_effect(stack):
    saved = document(stack)
    sentinel = ValueError("synthetic changed review")
    captured = []
    def review(target, snapshot):
        captured.append((target, snapshot))
        raise sentinel
    with pytest.raises(ValueError) as caught:
        stack["docs"].remove_document_details(saved["job"].id, validate=lambda: None, validate_snapshot=review)
    assert caught.value is sentinel and captured[0][0] == saved["job"].id
    assert captured[0][1]["record"]["content_sha256"] == saved["job"].content_sha256
    assert stack["service"].latest_removal(saved["job"].id) is None
    assert saved["live"].exists() and saved["raw"].exists() and saved["article"].exists()


def test_bulk_review_captures_exact_source_set_before_admission(stack):
    first, second = document(stack, "first.txt"), document(stack, "second.txt")
    def review(target, snapshot):
        assert target == "*"
        assert set(snapshot["documents"]) == {first["job"].id, second["job"].id}
        raise ValueError("changed bulk review")
    with pytest.raises(ValueError, match="changed bulk review"):
        stack["docs"].clear_documents_details(validate=lambda: None, validate_snapshot=review)
    assert stack["service"].latest_removal("*") is None
    assert first["live"].exists() and second["live"].exists()


def test_revocation_during_index_lock_wait_preserves_index_and_later_sources(stack, monkeypatch):
    saved = document(stack)
    index, docs = stack["index"], stack["docs"]
    ready, revoked = threading.Event(), threading.Event()
    original = index.remove_document_shard
    sentinel = PermissionError("synthetic revoked while waiting")
    def blocked(*args, **kwargs):
        ready.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(index, "remove_document_shard", blocked)
    def validate():
        if revoked.is_set():
            raise sentinel
    with ThreadPoolExecutor(max_workers=1) as pool:
        with index._manifest_lock:
            pending = pool.submit(docs.remove_document_details, saved["job"].id, validate=validate)
            assert ready.wait(5)
            revoked.set()
        with pytest.raises(PermissionError) as caught:
            pending.result(5)
    assert caught.value is sentinel
    assert saved["live"].exists() and saved["raw"].exists() and saved["article"].exists()
    intent = stack["service"].latest_removal(saved["job"].id)
    assert intent["result"]["status"] == "partial"


def test_revocation_after_source_retirement_stops_raw_and_knowledge_effects(stack, monkeypatch):
    saved = document(stack)
    service, docs = stack["service"], stack["docs"]
    original = type(service).retire_document_source
    revoked = []
    sentinel = ValueError("synthetic one-time authority rejection")
    def retire(*args, **kwargs):
        result = original(*args, **kwargs)
        revoked.append(True)
        return result
    monkeypatch.setattr(type(service), "retire_document_source", retire)
    def validate():
        if revoked:
            revoked.clear()
            raise sentinel
    with pytest.raises(ValueError) as caught:
        docs.remove_document_details(saved["job"].id, validate=validate)
    assert caught.value is sentinel
    assert not saved["live"].exists() and saved["raw"].exists() and saved["article"].exists()
    intent = service.latest_removal(saved["job"].id)
    assert intent["result"]["stages"]["source"] == "complete"
    assert intent["result"]["status"] == "partial"


def test_passive_review_import_and_read_never_initialize_user_stores(tmp_path):
    data = tmp_path / "absent-data"
    code = """
import sys
from pathlib import Path
from row_bot.application.document_commands import read_document_removal_review
value = read_document_removal_review(None, validate=lambda: None)
assert value['source_count'] == 0
assert not {'row_bot.documents', 'row_bot.knowledge_graph', 'row_bot.document_index'} & set(sys.modules)
assert not Path(sys.argv[1]).exists()
"""
    result = subprocess.run([sys.executable, "-c", code, str(data)], env={**os.environ, "ROW_BOT_DATA_DIR": str(data)},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_command_removes_once_and_projects_no_private_paths(client, stack, monkeypatch):
    saved = document(stack)
    adapter, context = client
    value = reviewed(client, saved["job"].id)
    result = execute(client, value)
    assert result["status"] == "completed" and result["removal"]["status"] == "complete"
    assert result["removal"]["derived_entities_removed"] == 1
    assert result["removal"]["retained_copy_count"] > 0
    assert "path" not in json.dumps(result) and str(stack["service"].data_dir) not in json.dumps(result)
    monkeypatch.setattr(stack["docs"], "remove_document_details", lambda *a, **k: pytest.fail("duplicate effect"))
    assert execute(client, value) == result
    assert adapter.read_document_command(**context, command_id=value["command_id"]) == result


def test_changed_review_and_rejected_authority_do_not_create_removal(client, stack):
    saved = document(stack)
    value = reviewed(client, saved["job"].id)
    value["payload"]["source_revision"] = "stale"
    with pytest.raises(ValueError, match="document_review_changed"):
        execute(client, value)
    value = reviewed(client, saved["job"].id)
    sentinel = PermissionError("synthetic policy denied")
    def reject(action):
        raise sentinel
    with pytest.raises(PermissionError) as caught:
        execute(client, value, validate_action=reject)
    assert caught.value is sentinel
    assert stack["service"].latest_removal(saved["job"].id) is None
    assert saved["live"].exists()


def test_lost_ack_only_reads_and_explicit_retry_resumes_same_operation(client, stack, monkeypatch):
    saved = document(stack)
    adapter, context = client
    value = reviewed(client, saved["job"].id)
    original = stack["index"].remove_document_shard
    monkeypatch.setattr(stack["index"], "remove_document_shard", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic fault")))
    partial = execute(client, value)
    assert partial["status"] == "partial"
    monkeypatch.setattr(stack["index"], "remove_document_shard", original)
    # A duplicate response cannot silently resume a failed destructive stage.
    assert execute(client, value) == partial and saved["live"].exists()
    retry = {"command_id": str(uuid4()), "type": "document.removal.retry", "payload": {
        "source_command_id": value["command_id"], "review_id": "synthetic-retry-review"}}
    result = execute(client, retry)
    assert result["status"] == "completed" and not saved["live"].exists()
    assert result["removal"]["removal_id"] == partial["removal"]["removal_id"]
    with pytest.raises(ValueError, match="document_operation_unavailable"):
        adapter.read_document_command(**{**context, "authority_id": "foreign-auth"}, command_id=value["command_id"])


def test_bulk_retry_preserves_reviewed_unstarted_child_source_identity(client, stack, monkeypatch):
    first, second = document(stack, "first.txt"), document(stack, "second.txt")
    identifiers = sorted([first["job"].id, second["job"].id])
    value = reviewed(client, None)
    original = stack["docs"]._remove_document_details
    def interrupt(identifier, **kwargs):
        if identifier == identifiers[1]:
            raise OSError("synthetic stop before later child admission")
        return original(identifier, **kwargs)
    monkeypatch.setattr(stack["docs"], "_remove_document_details", interrupt)
    partial = execute(client, value)
    intent = stack["service"].get_removal(partial["removal"]["removal_id"])
    assert set(intent["snapshot"]["client_review"]["sources"]) == set(identifiers)
    assert partial["status"] == "partial"
    later = identifiers[1]
    with stack["service"]._connect() as connection:
        connection.execute("UPDATE document_records SET content_sha256=? WHERE document_id=?", ("f" * 64, later))
    monkeypatch.setattr(stack["docs"], "_remove_document_details", original)
    retry = {"command_id": str(uuid4()), "type": "document.removal.retry", "payload": {
        "source_command_id": value["command_id"], "review_id": "synthetic-retry-review"}}
    result = execute(client, retry)
    assert result["status"] == "partial"
    assert stack["service"].latest_removal(later) is None
    assert stack["kg"].get_entity((first if first["job"].id == later else second)["entity"]["id"]) is not None


def test_manifest_only_same_id_replacement_invalidates_review(client, stack):
    saved = document(stack)
    with stack["service"]._connect() as connection:
        connection.execute("DELETE FROM document_records WHERE document_id=?", (saved["job"].id,))
        connection.execute("DELETE FROM document_jobs WHERE id=?", (saved["job"].id,))
    value = reviewed(client, saved["job"].id)
    path = stack["docs"].DOCUMENT_INDEX_DIR / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["documents"][0]["generation"] = "synthetic-replacement"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="document_review_changed"):
        execute(client, value)
    assert stack["service"].latest_removal(saved["job"].id) is None
    assert saved["live"].exists()


def test_oversized_identity_is_rejected_instead_of_silently_truncated(client, stack):
    saved = document(stack)
    with stack["service"]._connect() as connection:
        connection.execute("UPDATE document_jobs SET content_sha256=? WHERE id=?", ("f" * 130, saved["job"].id))
    with pytest.raises(ValueError, match="document_review_unavailable"):
        reviewed(client, saved["job"].id)


def test_edited_removal_result_cannot_publish_foreign_target_or_removal_id(client, stack):
    saved = document(stack)
    adapter, context = client
    value = reviewed(client, saved["job"].id)
    result = execute(client, value)
    intent = stack["service"].get_removal(result["removal"]["removal_id"])
    corrupted = intent["result"]
    corrupted["document_id"] = "synthetic-private-path/foreign"
    stack["service"].save_removal_result(corrupted["removal_id"], corrupted)
    with pytest.raises(ValueError, match="document_operation_unavailable"):
        adapter.read_document_command(**context, command_id=value["command_id"])


def test_opened_metadata_leaf_replacement_is_not_adopted(client, stack, monkeypatch):
    adapter, _ = client
    path = stack["docs"].DATA_DIR / "processed_files.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[]')
    original = os.open
    retained = path.with_name("retained.json")
    def swapped(name, flags, *args, **kwargs):
        if str(name) == str(path):
            path.rename(retained)
            path.write_text('["foreign"]')
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(ValueError, match="document_review_unavailable"):
        adapter._read_file(path)
    assert retained.read_text() == '[]' and path.read_text() == '["foreign"]'


@pytest.mark.parametrize("owner", ["index", "wiki"])
def test_revocation_after_staging_fsync_preserves_published_manifest(stack, monkeypatch, owner):
    saved = document(stack)
    path = (stack["docs"].DOCUMENT_INDEX_DIR / "manifest.json" if owner == "index" else stack["wiki"]._manifest_path())
    before = path.read_bytes()
    revoked = []
    original = os.fsync
    sentinel = PermissionError("synthetic staging revocation")
    def fsync(fd):
        original(fd)
        revoked.append(True)
    def validate():
        if revoked:
            raise sentinel
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(PermissionError) as caught:
        if owner == "index":
            stack["index"].remove_document_shard(saved["job"].id, index_root=stack["docs"].DOCUMENT_INDEX_DIR, validate=validate)
        else:
            stack["wiki"]._write_manifest({"version": 1, "files": {}}, validate=validate)
    assert caught.value is sentinel
    assert path.read_bytes() == before and saved["live"].exists() and saved["article"].exists()


def test_wiki_revocation_after_recovery_note_preserves_original_article(stack, monkeypatch):
    saved = document(stack)
    wiki = stack["wiki"]
    original = type(saved["article"]).write_text
    sentinel = PermissionError("synthetic recovery-note revocation")
    revoked = []
    def write(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.parent.name == ".row-bot-recovery" and path.suffix == ".json":
            revoked.append(True)
        return result
    def validate():
        if revoked:
            raise sentinel
    monkeypatch.setattr(type(saved["article"]), "write_text", write)
    before = saved["article"].read_bytes()
    with pytest.raises(PermissionError) as caught:
        wiki.delete_entity_md(saved["entity"], validate=validate)
    assert caught.value is sentinel and saved["article"].read_bytes() == before


def test_strict_bulk_retry_rejects_missing_saved_client_review(client, stack, monkeypatch):
    document(stack)
    value = reviewed(client, None)
    monkeypatch.setattr(stack["docs"], "_remove_document_details", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic stop")))
    partial = execute(client, value)
    intent = stack["service"].get_removal(partial["removal"]["removal_id"])
    intent["snapshot"].pop("client_review")
    with stack["service"]._connect() as connection:
        connection.execute("UPDATE document_removals SET snapshot=? WHERE id=?", (json.dumps(intent["snapshot"]), partial["removal"]["removal_id"]))
    retry = {"command_id": str(uuid4()), "type": "document.removal.retry", "payload": {
        "source_command_id": value["command_id"], "review_id": "synthetic-retry-review"}}
    with pytest.raises(ValueError, match="document_review_unavailable"):
        execute(client, retry)
