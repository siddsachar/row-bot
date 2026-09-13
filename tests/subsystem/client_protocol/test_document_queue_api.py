"""Independent queue API checks with canonical SQLite jobs and an inert supervisor."""
# ruff: noqa: F811 -- canonical shared application fixture.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot import document_jobs as jobs
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/documents/queue"


@pytest.fixture(autouse=True)
def isolated_queue_environment(tmp_path, monkeypatch):
    from row_bot import models
    monkeypatch.setattr(models, "is_cloud_available", lambda: False)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(jobs, "_supervisor", None)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    owner = jobs.DocumentJobService(tmp_path / "data")
    wakes = []
    monkeypatch.setattr(jobs, "_supervisor", SimpleNamespace(service=owner, wake=lambda: wakes.append("wake")))
    return SimpleNamespace(service=owner, wakes=wakes)


def queued(queue, name="example.txt", *, finish=True):
    owner = queue.service
    batch = owner.create_batch()
    job = owner.create_staging_job(batch, 0, name)
    source = Path(job.staged_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic document")
    owner.complete_staging(job.id, hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, source)
    if finish:
        owner.finish_batch_staging(batch)
    return batch, owner.get_job(job.id)


def page(client, headers, **params):
    response = client.get(BASE, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def reviewed(client, headers, action, target):
    kind = "jobs" if action.startswith("document.job.") else "batches"
    items = page(client, headers, kind=kind)["items"]
    row = next(value for value in items if value["id"] == target)
    payload = ({"targets": [{"id": target, "revision": row["revision"]}]}
        if action == "document.jobs.clear_finished" else {"target_id": target, "revision": row["revision"]})
    response = client.post(BASE + "/review", headers=headers, json={"action": action, "payload": payload})
    assert response.status_code == 200, response.text
    review = response.json()
    command = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "type": action, "expected_revision": "0", "payload": {**payload, "review_id": review["review_id"]}}
    return review, command


def send(client, headers, command):
    return client.post(BASE + "/commands", headers={**headers, "Idempotency-Key": command["command_id"]}, json=command)


def completed(client, headers, command):
    response = send(client, headers, command)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed" and result["command_id"] == command["command_id"]
    read = client.get(BASE + "/commands/" + command["command_id"], headers=headers)
    assert read.status_code == 200 and read.json() == result
    assert "_document_queue" not in json.dumps(result)
    return result


def test_cold_snapshot_is_passive_and_missing(service, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Passive GET initialized jobs"))
    with _client(service) as client:
        _, headers = bootstrap(client)
        result = page(client, headers)
        assert result["availability"] == "missing" and result["items"] == []
        assert not (tmp_path / "data" / "document_ingestion").exists()


def test_pause_resume_receipts_and_replay_never_recreate_service(service, queue, monkeypatch):
    batch, job = queued(queue)
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, pause = reviewed(client, headers, "document.batch.pause", batch)
        result = completed(client, headers, pause)
        assert result["outcome"] == "paused" and queue.service.get_batch(batch).pause_requested
        monkeypatch.setattr(jobs, "_supervisor", None)
        monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Replay initialized jobs"))
        assert send(client, headers, pause).json() == result
        monkeypatch.setattr(jobs, "_supervisor", SimpleNamespace(service=queue.service, wake=lambda: None))
        review, resume = reviewed(client, headers, "document.batch.resume", batch)
        assert review["provider_work"]
        assert completed(client, headers, resume)["outcome"] == "resumed"
        assert not queue.service.get_batch(batch).pause_requested
        assert queue.service.get_job(job.id).status == "queued"


@pytest.mark.parametrize("action", ["document.batch.cancel", "document.job.cancel"])
def test_cancel_active_job_remains_requested_until_actual_worker_ack(service, queue, action):
    batch, job = queued(queue)
    assert queue.service.claim_next("synthetic-worker").id == job.id
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers, action, job.id if action == "document.job.cancel" else batch)
        result = completed(client, headers, command)
        assert result["outcome"] == "cancellation_requested"
        current = queue.service.get_job(job.id)
        assert current.cancel_requested and current.status == "indexing"
        assert Path(job.staged_path).read_bytes() == b"synthetic document"


def test_retry_preserves_previous_work_and_never_retries_twice(service, queue):
    batch, job = queued(queue)
    queue.service.claim_next("synthetic-worker")
    queue.service.mark_failed(job.id, "parse_failed", "synthetic private diagnostic", stage="parse")
    work = queue.service.work_root / job.id
    work.mkdir()
    (work / "intermediate.txt").write_text("retained intermediate")
    with _client(service) as client:
        _, headers = bootstrap(client)
        visible = page(client, headers, kind="jobs", batch_id=batch)
        assert visible["items"][0]["error_code"] == "parse_failed"
        assert "synthetic private diagnostic" not in json.dumps(visible) and "staged_path" not in json.dumps(visible)
        _, command = reviewed(client, headers, "document.job.retry", job.id)
        result = completed(client, headers, command)
        assert result["retained_work"] and result["saved_status"] == "queued"
        assert send(client, headers, command).json() == result
        retained = list(queue.service.work_root.glob(".retry-*/intermediate.txt"))
        assert len(retained) == 1 and retained[0].read_text() == "retained intermediate"
        assert Path(job.staged_path).read_bytes() == b"synthetic document"


def test_clear_only_reviewed_finished_batch_retains_files_and_other_rows(service, queue):
    batch, job = queued(queue)
    other, other_job = queued(queue, "other.txt")
    queue.service.cancel_batch(batch)
    queue.service.cancel_batch(other)
    work = queue.service.work_root / job.id
    work.mkdir()
    (work / "retained.txt").write_text("keep this")
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers, "document.jobs.clear_finished", batch)
        result = completed(client, headers, command)
        assert result["count"] == 1 and result["retained_work"]
        assert [row["id"] for row in page(client, headers)["items"]] == [other]
        assert queue.service.get_job(other_job.id).status == "cancelled"
        assert Path(job.staged_path).read_bytes() == b"synthetic document"
        assert (work / "retained.txt").read_text() == "keep this"


@pytest.mark.parametrize("tamper", ["nonce", "action", "target", "session"])
def test_invalid_review_never_initializes_service_or_claims_command(service, queue, monkeypatch, tamper):
    batch, _ = queued(queue)
    other, _ = queued(queue, "other.txt")
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, foreign = bootstrap(client)
        _, command = reviewed(client, headers, "document.batch.pause", batch)
        if tamper == "nonce":
            command["payload"]["review_id"] = "forged"
        elif tamper == "action":
            command["type"] = "document.batch.cancel"
        elif tamper == "target":
            row = next(row for row in page(client, headers)["items"] if row["id"] == other)
            command["payload"].update(target_id=other, revision=row["revision"])
        else:
            headers = foreign
            command["client_session_id"] = foreign["X-Client-Session"]
        monkeypatch.setattr(jobs, "_supervisor", None)
        monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Invalid review initialized service"))
        response = send(client, headers, command)
        assert response.status_code == 409 and response.json()["code"] == "approval_expired", response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        assert not queue.service.get_batch(batch).pause_requested


def test_stale_scope_and_foreign_receipt_are_rejected(service, queue):
    batch, _ = queued(queue, finish=False)
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, foreign = bootstrap(client)
        _, stale = reviewed(client, headers, "document.batch.pause", batch)
        queue.service.create_staging_job(batch, 1, "late.txt")
        response = send(client, headers, stale)
        assert response.status_code == 409 and response.json()["code"] == "approval_expired", response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], stale["command_id"]) is None
        _, valid = reviewed(client, headers, "document.batch.pause", batch)
        completed(client, headers, valid)
        response = client.get(BASE + "/commands/" + valid["command_id"], headers=foreign)
        assert response.status_code == 409 and response.json()["code"] == "document_operation_unavailable", response.text


@pytest.mark.parametrize("published", [False, True])
def test_lost_receipt_stays_truthful_and_never_reapplies(service, queue, monkeypatch, published):
    batch, _ = queued(queue)
    original = admissions.complete_command
    def lost(owner, key, result):
        if "_document_queue" in result:
            if published:
                original(owner, key, result)
            raise OSError("synthetic lost receipt")
        return original(owner, key, result)
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers, "document.batch.pause", batch)
        monkeypatch.setattr(admissions, "complete_command", lost)
        failed = send(client, headers, command)
        assert failed.status_code == 200, failed.text
        assert failed.json()["status"] == ("completed" if published else "partial")
        assert "synthetic lost receipt" not in failed.text
        assert queue.service.get_batch(batch).pause_requested
        monkeypatch.setattr(queue.service, "pause_batch", lambda *a, **kw: pytest.fail("Uncertain effect repeated"))
        response = send(client, headers, command)
        assert response.status_code == 200, response.text
        assert response.json() == failed.json()
        if not published:
            assert response.json()["code"] == "document_outcome_uncertain"
        assert client.get(BASE + "/commands/" + command["command_id"], headers=headers).json() == response.json()


@pytest.mark.parametrize("revoke_at", [1, 3])
def test_actual_remote_revocation_at_writer_boundary_rolls_back(service, queue, monkeypatch, revoke_at):
    from fastapi.testclient import TestClient
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    active = {"value": True}
    batch, job = queued(queue)
    original = queue.service.cancel_batch
    def revoked(*args, **kwargs):
        validation = kwargs["validate"]
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == revoke_at:
                active["value"] = False
            validation()
        kwargs["validate"] = check
        return original(*args, **kwargs)
    monkeypatch.setattr(queue.service, "cancel_batch", revoked)
    app = create_client_platform_app(service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: SessionIdentity("synthetic-device", "synthetic-auth") if active["value"] else None,
        choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers, "document.batch.cancel", batch)
        response = send(client, headers, command)
        assert response.status_code == 401 and response.json()["code"] == "authentication_required", response.text
        assert not queue.service.get_batch(batch).cancel_requested
        assert queue.service.get_job(job.id).status == "queued"
        assert client.get(BASE + "/commands/" + command["command_id"], headers=headers).status_code == 401


def test_publication_failure_rechecks_revoked_authority_before_receipt(service, queue, monkeypatch):
    from fastapi.testclient import TestClient
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    active = {"value": True}
    batch, _ = queued(queue)
    original = admissions.complete_command
    def lost(owner, key, result):
        if "_document_queue" in result:
            active["value"] = False
            raise OSError("synthetic private failure")
        return original(owner, key, result)
    monkeypatch.setattr(admissions, "complete_command", lost)
    app = create_client_platform_app(service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: SessionIdentity("synthetic-device", "synthetic-auth") if active["value"] else None,
        choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers, "document.batch.pause", batch)
        response = send(client, headers, command)
        assert response.status_code == 401 and response.json()["code"] == "authentication_required", response.text
        assert queue.service.get_batch(batch).pause_requested
        assert "synthetic private failure" not in response.text


def test_queue_pagination_is_bound_to_exact_snapshot_and_filter(service, queue):
    for index in range(3):
        queued(queue, f"document-{index}.txt")
    with _client(service) as client:
        _, headers = bootstrap(client)
        first = page(client, headers, limit=2)
        assert first["total"] == 3 and len(first["items"]) == 2 and first["next_cursor"]
        second = page(client, headers, limit=2, cursor=first["next_cursor"])
        assert len(second["items"]) == 1
        invalid = client.get(BASE, headers=headers, params={"kind": "jobs", "limit": 2, "cursor": first["next_cursor"]})
        assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_document_queue"
        queue.service.pause_batch(first["items"][0]["id"])
        stale = client.get(BASE, headers=headers, params={"limit": 2, "cursor": first["next_cursor"]})
        assert stale.status_code == 410 and stale.json()["code"] == "cursor_expired"
