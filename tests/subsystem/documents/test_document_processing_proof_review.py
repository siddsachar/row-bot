"""Independent processing proof/physical-scope review with isolated owners."""
from contextlib import closing, contextmanager
import os
from types import SimpleNamespace

import pytest

from tests.subsystem.knowledge_graph.test_document_processing_policy import (
    admit,
    replacement_processing,
)
from tests.subsystem.knowledge_graph import test_document_processing_policy as owner_tests

pytestmark = pytest.mark.subsystem
processing_fixture = owner_tests.processing


@pytest.fixture
def processing(processing_fixture):
    return processing_fixture


@pytest.mark.parametrize("field", ["owner_id", "processing_owner_id", "conversation_id", "processing_command_id", "source_digest", "policy_digest", "authority_digest"])
def test_each_saved_proof_field_remains_bound_to_exact_original(processing, field):
    _, service, _, batch, *_ = processing
    admit(processing)
    before = service.processing_admission(batch)
    with service._connect() as connection:
        connection.execute(f"UPDATE document_batch_admissions SET {field}=? WHERE batch_id=?", ("f" * 64, batch))
    with pytest.raises(Exception):
        service.processing_admission(batch)
    assert before[field] != "f" * 64


def test_provider_cleanup_retains_scope_until_actual_return_and_original_receipt_is_passive(processing, monkeypatch):
    api, service, policy, batch, *_ = processing
    from row_bot import embedding_providers, document_jobs
    command, original_receipt = admit(processing)
    before = service.processing_admission(batch)
    calls = []
    @contextmanager
    def captured(*_args, **_kwargs):
        try:
            yield SimpleNamespace()
        finally:
            # Simulates slow client teardown after job/finalizer work finished.
            with pytest.raises(document_jobs.DocumentJobError, match="processing is active"):
                with service._processing_scope_lock(batch):
                    pytest.fail("Cleanup was treated as quiescent")
            assert api.read_document_processing_command(command_id=command["command_id"], policy=policy) == original_receipt
            calls.append("cleanup")
    monkeypatch.setattr(embedding_providers, "captured_embedding_provider", captured)
    with service.processing_scope(batch):
        pass
    assert calls == ["cleanup"]
    assert service.processing_admission(batch) == before
    with service._processing_scope_lock(batch):
        pass


def test_revocation_rejects_current_effect_but_keeps_scope_until_exit(processing, monkeypatch):
    _, service, _, batch, _, _, state = processing
    from row_bot import embedding_providers, document_jobs
    admit(processing)
    @contextmanager
    def captured(*_args, **_kwargs):
        yield SimpleNamespace()
    monkeypatch.setattr(embedding_providers, "captured_embedding_provider", captured)
    with service.processing_scope(batch) as worker:
        state["revoked"] = True
        with pytest.raises(RuntimeError, match="session revoked"):
            worker.validate()
        with pytest.raises(document_jobs.DocumentJobError, match="processing is active"):
            with service._processing_scope_lock(batch):
                pytest.fail("Revocation released still-active scope")
    with service._processing_scope_lock(batch):
        pass


def test_original_pending_claim_replay_never_replaces_new_authenticated_owner(processing, monkeypatch):
    api, service, policy, batch, *_ = processing
    from row_bot.runtime import admissions
    original_complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ack commit failed")))
    captured = []
    original_authorize = service.authorize_processing
    def authorize(*args, **kwargs):
        captured.append(kwargs["proof"]["processing_command_id"])
        return original_authorize(*args, **kwargs)
    monkeypatch.setattr(service, "authorize_processing", authorize)
    with pytest.raises(RuntimeError, match="ack commit failed"):
        admit(processing)
    command_id, = captured
    assert api.read_document_processing_command(command_id=command_id, policy=policy)["status"] == "partial"
    monkeypatch.setattr(admissions, "complete_command", original_complete)
    service.pause_batch(batch)
    replacement = replacement_processing(processing)
    assert admit(replacement)[1]["processing"] == "admitted"
    new_proof = service.processing_admission(batch)
    assert api.read_document_processing_command(command_id=command_id, policy=policy)["status"] == "partial"
    assert service.processing_admission(batch) == new_proof
    assert new_proof["owner_id"] == policy.owner_id
    assert new_proof["processing_owner_id"] != policy.owner_id


def test_new_processing_lock_never_writes_an_unowned_hardlinked_file(processing, tmp_path):
    _, service, _, batch, *_ = processing
    from row_bot import document_jobs
    outside = tmp_path / "unowned-empty-file"
    outside.write_bytes(b"")
    lock_path = service.root / f"processing-{batch}.lock"
    os.link(outside, lock_path)
    with pytest.raises(document_jobs.DocumentJobError):
        with service._processing_scope_lock(batch):
            pytest.fail("Unowned hardlink accepted as processing lock")
    assert outside.read_bytes() == b""


def test_same_byte_lock_replacement_cannot_be_adopted_after_original_scope(processing):
    _, service, _, batch, *_ = processing
    from row_bot import document_jobs
    path = service.root / f"processing-{batch}.lock"
    with service._processing_scope_lock(batch):
        assert path.read_bytes() == b"" if os.name != "nt" else path.stat().st_size == 0
    original = path.stat()
    path.rename(path.with_suffix(".retained"))
    path.write_bytes(b"")
    assert path.stat().st_ino != original.st_ino
    with pytest.raises(document_jobs.DocumentJobError, match="identity changed"):
        with service._processing_scope_lock(batch):
            pytest.fail("Replacement lock was adopted")
    assert path.read_bytes() == b""


def test_active_lock_name_replacement_cannot_grant_concurrent_scope(processing):
    _, service, _, batch, *_ = processing
    from row_bot import document_jobs
    path = service.root / f"processing-{batch}.lock"
    with service._processing_scope_lock(batch):
        if os.name == "nt":
            with pytest.raises(OSError):
                path.rename(path.with_suffix(".retained"))
        else:
            path.rename(path.with_suffix(".retained"))
            path.write_bytes(b"")
        with pytest.raises(document_jobs.DocumentJobError):
            with service._processing_scope_lock(batch):
                pytest.fail("Replaced name admitted a concurrent scope")


def test_body_oserror_is_preserved_and_releases_only_owned_lock(processing):
    _, service, _, batch, *_ = processing
    failure = OSError("synthetic provider failure")
    with pytest.raises(OSError) as caught:
        with service._processing_scope_lock(batch):
            raise failure
    assert caught.value is failure
    with service._processing_scope_lock(batch):
        pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow symlink path")
def test_processing_lock_rejects_symbolic_link_without_touching_target(processing, tmp_path):
    _, service, _, batch, *_ = processing
    from row_bot import document_jobs
    target = tmp_path / "unowned-target"
    target.write_bytes(b"")
    (service.root / f"processing-{batch}.lock").symlink_to(target)
    with pytest.raises(document_jobs.DocumentJobError):
        with service._processing_scope_lock(batch):
            pytest.fail("Symbolic lock admitted")
    assert target.read_bytes() == b""


@pytest.mark.skipif(os.name == "nt", reason="POSIX SQLite descriptor path and WAL runtime")
def test_posix_identity_transaction_follows_held_directory_and_live_wal(processing, monkeypatch):
    _, service, _, batch, *_ = processing
    from row_bot import document_jobs
    parent = service.root
    retained = parent.with_name("retained-document-owner")
    connect = document_jobs.sqlite3.connect
    calls = []
    with closing(service._connect()) as keeper, keeper:
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("UPDATE document_batch_admissions SET policy_digest='live-wal' WHERE batch_id=?", (batch,))
        keeper.commit()
        assert (parent / "jobs.db-wal").exists()
        def move_before_connect(database, *args, **kwargs):
            calls.append(str(database))
            assert str(database).startswith(("file:///proc/self/fd/", "file:///dev/fd/"))
            parent.rename(retained)
            parent.mkdir()
            return connect(database, *args, **kwargs)
        monkeypatch.setattr(document_jobs.sqlite3, "connect", move_before_connect)
        with service._processing_scope_lock(batch):
            row = keeper.execute("SELECT policy_digest,processing_lock_identity FROM document_batch_admissions WHERE batch_id=?", (batch,)).fetchone()
            assert row[0] == "live-wal" and row[1]
            assert not list(parent.iterdir())  # No replacement DB or sidecars.
            assert (retained / "jobs.db-wal").exists()
            assert (retained / "jobs.db-shm").exists()
        assert len(calls) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-unavailable refusal")
def test_posix_missing_sqlite_descriptor_path_never_uses_pathname_fallback(processing, monkeypatch):
    _, service, _, batch, *_ = processing
    from pathlib import Path
    from row_bot import document_jobs
    original = Path.stat
    def unavailable(path, *args, **kwargs):
        if str(path).startswith(("/proc/self/fd/", "/dev/fd/")):
            raise FileNotFoundError("synthetic unavailable descriptor bridge")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", unavailable)
    monkeypatch.setattr(service, "_connect", lambda: pytest.fail("Pathname fallback"))
    with pytest.raises(document_jobs.DocumentJobError, match="descriptor SQLite path unavailable"):
        with service._processing_scope_lock(batch):
            pytest.fail("Unavailable bridge admitted")
