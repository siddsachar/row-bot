"""Actual framed upload API with canonical staging and synthetic ASGI streams."""
# ruff: noqa: F811 -- shared isolated canonical service fixtures.
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import struct
from uuid import UUID, uuid4

import httpx
import pytest

from row_bot import document_jobs as jobs
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_document_queue_api import isolated_queue_environment, queue  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/documents/uploads"
CONTENT_TYPE = "application/vnd.row-bot.document-upload-v1"


@pytest.fixture(autouse=True)
def fake_disk_capacity(monkeypatch):
    from row_bot import document_uploads
    original = document_uploads.stage_upload
    async def stage(*args, **kwargs):
        return await original(*args, **{**kwargs, "disk_free": lambda _path: 100 * 1024**3})
    monkeypatch.setattr(document_uploads, "stage_upload", stage)


def reviewed(client, headers, files=None):
    files = files or [{"name": "one.txt", "size_bytes": 3}, {"name": "two.md", "size_bytes": 4}]
    response = client.post(BASE + "/review", headers=headers, json={"files": files})
    assert response.status_code == 200, response.text
    review = response.json()
    command = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "type": "document.upload", "expected_revision": "0", "payload": {"files": files, "review_id": review["review_id"]}}
    return review, command


def prefix(command):
    value = json.dumps(command).encode("utf-8")
    return struct.pack(">I", len(value)) + value


def send(client, headers, command, data=(b"one", b"two!"), *, source=None):
    async def body():
        yield prefix(command)
        for chunk in data:
            yield chunk
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://localhost") as connection:
            return await connection.post(BASE + "/commands", headers={**headers,
                "Content-Type": CONTENT_TYPE, "Idempotency-Key": command["command_id"]}, content=source or body())
    return asyncio.run(request())


def batch_id(command):
    return "client_" + UUID(command["command_id"]).hex


def test_two_file_upload_is_paused_with_exact_bytes_and_original_receipt(service, queue):
    with _client(service) as client:
        _, headers = bootstrap(client)
        review, command = reviewed(client, headers)
        assert review["processing"] == "paused" and review["provider_work"] is False
        result = send(client, headers, command)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "completed" and result.json()["processing"] == "paused"
        assert result.json()["batch_id"] == batch_id(command)
        rows = queue.service.list_jobs(batch_id(command))
        assert [Path(row.staged_path).read_bytes() for row in rows] == [b"one", b"two!"]
        assert queue.service.get_batch(batch_id(command)).pause_requested
        assert queue.service.claim_next("synthetic-worker") is None
        receipt = client.get(BASE + "/commands/" + command["command_id"], headers=headers)
        assert receipt.status_code == 200 and receipt.json() == result.json()
        assert "_document_upload" not in result.text and "staged_path" not in result.text


def test_review_is_passive_with_no_service_or_body_effects(service, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Review initialized service"))
    with _client(service) as client:
        _, headers = bootstrap(client)
        review, _ = reviewed(client, headers)
        assert review["file_count"] == 2 and review["total_bytes"] == 7
        assert not (tmp_path / "data" / "document_ingestion").exists()


@pytest.mark.parametrize("tamper", ["nonce", "file", "session"])
def test_forged_review_stops_before_body_service_and_admission(service, tmp_path, monkeypatch, tamper):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, foreign = bootstrap(client)
        _, command = reviewed(client, headers)
        if tamper == "nonce":
            command["payload"]["review_id"] = "forged"
        elif tamper == "file":
            command["payload"]["files"][0]["name"] = "different.txt"
        else:
            headers = foreign
            command["client_session_id"] = foreign["X-Client-Session"]
        monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Invalid approval initialized service"))
        consumed = []
        async def source():
            yield prefix(command)
            consumed.append("file")
            yield b"forbidden"
        response = send(client, headers, command, source=source())
        assert response.status_code == 409 and response.json()["code"] == "approval_expired", response.text
        assert consumed == []
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        assert not (tmp_path / "data" / "document_ingestion").exists()


@pytest.mark.parametrize("data", [(b"one", b"tw"), (b"one", b"two!", b"trailing"), (b"one", b"x" * (1024**2 + 1))])
def test_malformed_file_stream_is_partial_and_never_searchable(service, queue, data):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers)
        result = send(client, headers, command, data)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "partial" and result.json()["code"] == "document_upload_uncertain"
        rows = queue.service.list_jobs(batch_id(command))
        assert rows[0].status == "queued" and Path(rows[0].staged_path).read_bytes() == b"one"
        assert rows[1].status == "staging" and not rows[1].searchable_at
        assert queue.service.get_batch(batch_id(command)).pause_requested
        assert queue.service.claim_next("synthetic-worker") is None
        receipt = client.get(BASE + "/commands/" + command["command_id"], headers=headers)
        assert receipt.json() == result.json()


@pytest.mark.parametrize("partial", [False, True])
def test_original_replay_consumes_only_command_prefix_without_service_or_file_reads(service, queue, monkeypatch, partial):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers)
        first = send(client, headers, command, (b"one", b"x") if partial else (b"one", b"two!"))
        assert first.status_code == 200, first.text
        assert first.json()["status"] == ("partial" if partial else "completed")
        monkeypatch.setattr(jobs, "_supervisor", None)
        monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Replay initialized service"))
        consumed = []
        async def source():
            yield prefix(command)
            consumed.append("body")
            yield b"must never be read"
        replay = send(client, headers, command, source=source())
        assert replay.status_code == 200 and replay.json() == first.json()
        assert consumed == [] and len(queue.service.list_jobs(batch_id(command))) == 2


@pytest.mark.parametrize("published", [False, True])
def test_lost_publication_is_truthful_without_republication(service, queue, monkeypatch, published):
    original = admissions.complete_command
    def lost(owner, key, result):
        if "_document_upload" in result:
            if published:
                original(owner, key, result)
            raise OSError("synthetic private publication failure")
        return original(owner, key, result)
    monkeypatch.setattr(admissions, "complete_command", lost)
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers)
        first = send(client, headers, command)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == ("completed" if published else "partial")
        assert "synthetic private publication failure" not in first.text
        replay = send(client, headers, command, ())
        assert replay.json() == first.json()
        assert [Path(row.staged_path).read_bytes() for row in queue.service.list_jobs(batch_id(command))] == [b"one", b"two!"]


def test_foreign_receipt_and_changed_original_intent_are_rejected(service, queue):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, foreign = bootstrap(client)
        _, command = reviewed(client, headers)
        assert send(client, headers, command).json()["status"] == "completed"
        denied = client.get(BASE + "/commands/" + command["command_id"], headers=foreign)
        assert denied.status_code == 409 and denied.json()["code"] == "document_upload_unavailable"
        changed = copy.deepcopy(command)
        changed["payload"]["files"][0]["name"] = "changed.txt"
        replay = send(client, headers, changed, ())
        assert replay.status_code == 409 and replay.json()["code"] == "idempotency_mismatch", replay.text
        assert len(queue.service.list_jobs(batch_id(command))) == 2


def test_actual_auth_revocation_after_file_receive_prevents_publication(service, queue):
    from fastapi.testclient import TestClient
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    active = {"value": True}
    app = create_client_platform_app(service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: SessionIdentity("synthetic-device", "synthetic-auth") if active["value"] else None,
        choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers)
        async def source():
            yield prefix(command)
            active["value"] = False
            yield b"one"
            yield b"two!"
        response = send(client, headers, command, source=source())
        assert response.status_code == 401 and response.json()["code"] == "authentication_required", response.text
        assert all(row.status == "staging" for row in queue.service.list_jobs(batch_id(command)))
        assert queue.service.claim_next("synthetic-worker") is None
        assert client.get(BASE + "/commands/" + command["command_id"], headers=headers).status_code == 401


def test_oversized_command_frame_never_initializes_jobs_or_admission(service, tmp_path, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers)
        monkeypatch.setattr(jobs, "get_document_job_service", lambda: pytest.fail("Oversized header initialized service"))
        async def source():
            yield struct.pack(">I", 16385)
            raise AssertionError("Oversized header consumed body")
        response = send(client, headers, command, source=source())
        assert response.status_code == 413 and response.json()["code"] == "payload_too_large", response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        assert not (tmp_path / "data" / "document_ingestion").exists()
