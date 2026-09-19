"""Independent authenticated M3 review with fake metadata/downloads and real installers."""
# ruff: noqa: F811 -- canonical shared service fixture.
from __future__ import annotations

import copy
import hashlib
import io
import json
import platform
from types import SimpleNamespace
import threading
from uuid import uuid4
import zipfile

import pytest

from row_bot.application import mcp_runtime_installation as controls
from row_bot.mcp_client import requirements
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/settings/mcp/installations"


@pytest.fixture(autouse=True)
def fake_provider_availability(monkeypatch):
    from row_bot import models
    monkeypatch.setattr(models, "is_cloud_available", lambda: False)


@pytest.fixture
def runtimes(tmp_path, monkeypatch):
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", tmp_path / "managed-runtimes")
    monkeypatch.setattr(controls, "_OPERATIONS", {})
    windows = platform.system().lower() == "windows"
    calls, archives = [], {}
    for runtime_id in ("node", "uv"):
        output = io.BytesIO()
        path = runtime_id + ".exe" if windows else "bin/node" if runtime_id == "node" else "uv"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("synthetic/" + path, b"synthetic runtime bytes never executed")
        archives[runtime_id] = output.getvalue()
    def node_version():
        calls.append("node:metadata")
        return "v1.2.3"
    def node_checksum(version, name):
        assert version == "v1.2.3" and name == "node.zip"
        calls.append("node:checksum")
        return hashlib.sha256(archives["node"]).hexdigest()
    def remote_size(url):
        assert url == "https://nodejs.org/dist/v1.2.3/node.zip"
        calls.append("node:size")
        return len(archives["node"])
    def uv_asset():
        calls.append("uv:metadata")
        return "1.2.3", {"name": "uv.zip", "browser_download_url": "https://example.invalid/uv.zip",
            "size": len(archives["uv"]), "digest": "sha256:" + hashlib.sha256(archives["uv"]).hexdigest()}
    def download(url, destination, progress=None, *, validate=lambda: None):
        validate()
        runtime_id = "node" if url == "https://nodejs.org/dist/v1.2.3/node.zip" else "uv"
        assert runtime_id == "node" or url == "https://example.invalid/uv.zip"
        calls.append(runtime_id + ":download")
        destination.write_bytes(archives[runtime_id])
    monkeypatch.setattr(requirements, "_latest_node_lts_version", node_version)
    monkeypatch.setattr(requirements, "_node_asset_name", lambda _version: ("node.zip", "zip"))
    monkeypatch.setattr(requirements, "_node_checksum", node_checksum)
    monkeypatch.setattr(requirements, "_remote_size", remote_size)
    monkeypatch.setattr(requirements, "_uv_release_asset", uv_asset)
    monkeypatch.setattr(requirements, "_download", download)
    yield SimpleNamespace(root=tmp_path / "managed-runtimes", calls=calls, archives=archives)
    assert controls.McpRuntimeInstallationService().close(timeout=5)


def reviewed(client, headers, runtime_id="node", operation="resolve", source=None):
    snapshot = client.get(BASE + "/" + runtime_id, headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    response = client.post(BASE + "/review", headers=headers, json={"runtime_id": runtime_id,
        "operation": operation, "resource_revision": snapshot.json()["resource_revision"], "source_command_id": source})
    assert response.status_code == 200, response.text
    review = response.json()
    body = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "type": "mcp.runtime." + operation, "expected_revision": "0", "payload": {
            "runtime_id": runtime_id, "source_command_id": source, "resource_revision": review["resource_revision"],
            "action_digest": review["action_digest"], "nonce": review["nonce"]}}
    return review, body


def send(client, headers, body):
    return client.post(BASE + "/commands", headers={**headers, "Idempotency-Key": body["command_id"]}, json=body)


def settled(client, headers, body):
    runtime_id = body["payload"]["runtime_id"]
    active = controls._OPERATIONS.get(runtime_id)
    if active and active.thread:
        active.thread.join(5)
        assert not active.thread.is_alive(), "Synthetic installer failed to quiesce"
    response = client.get(BASE + "/" + runtime_id + "/commands/" + body["command_id"], headers=headers)
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize("runtime_id", ["node", "uv"])
def test_passive_snapshot_and_review_never_resolve_download_or_create_runtime(service, runtimes, runtime_id):
    with _client(service) as client:
        _, headers = bootstrap(client)
        review, command = reviewed(client, headers, runtime_id)
        assert review["plan"] is None and review["network_required"] and not review["executes_runtime"]
        assert review["disclosures"] and review["nonce"]
        assert runtimes.calls == [] and not runtimes.root.exists()
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None


@pytest.mark.parametrize("runtime_id", ["node", "uv"])
def test_real_resolve_pinned_review_install_and_receipt_are_exact_and_idempotent(service, runtimes, runtime_id):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, resolve = reviewed(client, headers, runtime_id)
        accepted = send(client, headers, resolve)
        assert accepted.status_code == 200, accepted.text
        resolved = settled(client, headers, resolve)
        assert resolved.json()["status"] == "completed" and resolved.json()["installation"]["stage"] == "resolved"
        calls = list(runtimes.calls)
        assert calls and not any("download" in item for item in calls)
        plan, install = reviewed(client, headers, runtime_id, "install", resolve["command_id"])
        assert runtimes.calls == calls
        assert plan["plan"]["sha256"] == hashlib.sha256(runtimes.archives[runtime_id]).hexdigest()
        assert plan["plan"]["size_bytes"] == len(runtimes.archives[runtime_id])
        assert send(client, headers, install).status_code == 200
        finished = settled(client, headers, install)
        assert finished.json()["installation"]["installed"] is True and finished.json()["installation"]["quiesced"] is True
        assert finished.headers["Cache-Control"] == "no-store"
        assert runtimes.calls == calls + [runtime_id + ":download"]
        raw = admissions.read_command_receipt(headers["X-Client-Session"], install["command_id"])
        proof = raw[controls._PRIVATE]
        assert requirements.read_runtime_installation_proof(runtime_id, install["command_id"], proof)
        manifest = requirements._read_manifest(runtime_id)
        from pathlib import Path
        assert Path(manifest["executable_path"]).read_bytes() == b"synthetic runtime bytes never executed"
        assert str(runtimes.root) not in finished.text and controls._PRIVATE not in finished.text
        assert send(client, headers, install).json() == finished.json()
        assert send(client, headers, resolve).json() == resolved.json()
        assert runtimes.calls == calls + [runtime_id + ":download"]


def test_forged_review_nonce_cannot_claim_or_start_a_command(service, runtimes):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, command = reviewed(client, headers)
        forged = copy.deepcopy(command)
        forged["payload"]["nonce"] = "forged-nonce"
        rejected = send(client, headers, forged)
        assert rejected.status_code == 409 and rejected.json()["code"] == "approval_expired", rejected.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        assert runtimes.calls == [] and not runtimes.root.exists()
        assert send(client, headers, command).status_code == 200
        assert settled(client, headers, command).json()["installation"]["stage"] == "resolved"


def test_other_auth_owner_cannot_read_install_or_cancel_the_original_plan(service, runtimes):
    with _client(service) as client:
        _, a = bootstrap(client)
        _, b = bootstrap(client)
        _, resolve = reviewed(client, a)
        assert send(client, a, resolve).status_code == 200
        settled(client, a, resolve)
        path = BASE + "/node/commands/" + resolve["command_id"]
        assert client.get(path, headers=b).status_code == 404
        snapshot = client.get(BASE + "/node", headers=b).json()
        foreign = client.post(BASE + "/review", headers=b, json={"runtime_id": "node", "operation": "install",
            "resource_revision": snapshot["resource_revision"], "source_command_id": resolve["command_id"]})
        assert foreign.status_code == 404, foreign.text
        cancel = {"command_id": str(uuid4()), "client_session_id": b["X-Client-Session"],
            "type": "mcp.runtime.install.cancel", "expected_revision": "0", "payload": {"runtime_id": "node", "source_command_id": resolve["command_id"]}}
        assert send(client, b, cancel).status_code == 404
        assert admissions.read_command_metadata(b["X-Client-Session"], cancel["command_id"]) is None
        assert not runtimes.root.exists() and "node:download" not in runtimes.calls


def test_cancellation_preserves_receive_ownership_and_never_blindly_restarts(service, runtimes, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = requirements._latest_node_lts_version
    def blocked():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(requirements, "_latest_node_lts_version", blocked)
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, resolve = reviewed(client, headers)
        assert send(client, headers, resolve).status_code == 200
        assert entered.wait(5)
        cancel = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": "mcp.runtime.install.cancel", "expected_revision": "0", "payload": {"runtime_id": "node", "source_command_id": resolve["command_id"]}}
        try:
            ack = send(client, headers, cancel)
            assert ack.status_code == 200 and ack.json()["installation"]["quiesced"] is False, ack.text
            assert ack.json()["installation"]["cancel_requested"]
            _, duplicate = reviewed(client, headers)
            rejected = send(client, headers, duplicate)
            assert rejected.status_code == 409 and rejected.json()["code"] == "runtime_installation_pending", rejected.text
            state = client.get(BASE + "/node", headers=headers).json()
            assert state["availability"] == "recovery_required" and state["active_command_id"] == resolve["command_id"]
            assert not state["quiesced"] and not runtimes.calls
        finally:
            release.set()
        finished = settled(client, headers, resolve).json()
        assert finished["installation"]["stage"] == "cancelled" and finished["installation"]["quiesced"]
        calls = list(runtimes.calls)
        assert send(client, headers, resolve).json() == finished
        assert send(client, headers, cancel).json() == ack.json()
        assert runtimes.calls == calls and not runtimes.root.exists()


def test_reviewed_checksum_corruption_never_publishes_runtime_or_leaks_private_error(service, runtimes, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, resolve = reviewed(client, headers)
        assert send(client, headers, resolve).status_code == 200
        settled(client, headers, resolve)
        _, install = reviewed(client, headers, operation="install", source=resolve["command_id"])
        def corrupt(_url, destination, progress=None, *, validate=lambda: None):
            validate()
            destination.write_bytes(b"wrong bytes")
        monkeypatch.setattr(requirements, "_download", corrupt)
        response = send(client, headers, install)
        assert response.status_code == 200, response.text
        final = settled(client, headers, install)
        assert final.json()["status"] == "partial" and final.json()["installation"]["installed"] is not True
        assert final.json()["installation"]["quiesced"] is True
        assert not (runtimes.root / "node/manifest.json").exists()
        assert str(runtimes.root) not in json.dumps(final.json())


def test_actual_remote_auth_revocation_after_metadata_wait_prevents_next_stage(service, runtimes, monkeypatch):
    from fastapi.testclient import TestClient
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    active = {"value": True}
    entered, release = threading.Event(), threading.Event()
    original = requirements._latest_node_lts_version
    def blocked():
        entered.set()
        assert release.wait(5)
        return original()
    monkeypatch.setattr(requirements, "_latest_node_lts_version", blocked)
    app = create_client_platform_app(service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: SessionIdentity("synthetic-device", "synthetic-auth") if active["value"] else None,
        choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        _, resolve = reviewed(client, headers)
        assert send(client, headers, resolve).status_code == 200
        assert entered.wait(5)
        operation = controls._OPERATIONS["node"]
        active["value"] = False
        release.set()
        operation.thread.join(5)
        assert not operation.thread.is_alive()
        response = client.get(BASE + "/node/commands/" + resolve["command_id"], headers=headers)
        assert response.status_code == 401 and response.json()["code"] == "authentication_required"
        assert runtimes.calls == ["node:metadata"] and not runtimes.root.exists()


def test_other_session_cannot_reuse_a_resolve_review_nonce_before_claim(service, runtimes):
    with _client(service) as client:
        _, first = bootstrap(client)
        _, second = bootstrap(client)
        _, command = reviewed(client, first)
        command["client_session_id"] = second["X-Client-Session"]
        rejected = send(client, second, command)
        assert rejected.status_code == 409 and rejected.json()["code"] == "approval_expired", rejected.text
        assert admissions.read_command_metadata(second["X-Client-Session"], command["command_id"]) is None
        assert not runtimes.calls and not runtimes.root.exists()
