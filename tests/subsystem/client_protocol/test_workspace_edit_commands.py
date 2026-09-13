"""Actual bound workspace text editing with private durable recovery."""
# ruff: noqa: F811 -- shared pytest fixture names.
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_empty_workspace_setup import workspace_api, _command, _payload, _grant  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def editable(workspace_api, monkeypatch):
    from row_bot import threads
    from row_bot.developer import change_ledger
    service, _, parent, client, headers, _, _ = workspace_api
    monkeypatch.setattr(change_ledger, "DEVELOPER_DIR", parent.parent / "ledger")
    monkeypatch.setattr(change_ledger, "LEDGER_PATH", parent.parent / "ledger/changes.json")
    response = _command(client, headers, _payload(_grant(client, headers)))
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["status"] == "completed"
    threads._set_thread_approval_mode(created["conversation_id"], "approve")
    root = parent / "new-project"
    (root / "hello.txt").write_bytes(b"original\r\n")
    return service, client, headers, created, root, change_ledger


def read(client, headers, created, path="hello.txt"):
    return client.get(f"/api/v1/conversations/{created['conversation_id']}/workspaces/{created['binding_id']}/editing",
                      params={"path": path}, headers=headers)


def command(service, headers, created, snapshot, content="edited\r\n"):
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"], "type": "workspace.edit",
            "expected_revision": service.get_conversation(created["conversation_id"])["revision"], "payload": {
                "target": {"kind": "workspace", "resource_id": created["resource_id"], "resource_revision": snapshot["resource_revision"],
                           "binding_id": created["binding_id"], "binding_revision": snapshot["binding_revision"]},
                "relative_path": "hello.txt", "file_digest": snapshot["digest"], "review_token": snapshot["review_token"], "content": content}}


def send(client, headers, created, body):
    return client.post(f"/api/v1/conversations/{created['conversation_id']}/commands", json=body,
                       headers={**headers, "Idempotency-Key": body["command_id"]})


def test_real_read_save_replay_and_receipt_never_expose_recovery(editable):
    service, client, headers, created, root, ledger = editable
    snapshot = read(client, headers, created)
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["content"] == "original\r\n"
    body = command(service, headers, created, snapshot.json())
    saved = send(client, headers, created, body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["workspace_edit"]["status"] == "saved"
    assert saved.json()["workspace_edit"]["ledger_saved"]
    assert (root / "hello.txt").read_bytes() == b"edited\r\n"
    assert send(client, headers, created, body).json() == saved.json()
    receipt = client.get(f"/api/v1/commands/{body['command_id']}", headers=headers)
    assert receipt.status_code == 200, receipt.text
    for secret in ("_workspace_edit", "root_identity", "candidate_identity", "metadata_digest", "original\r\n", str(root)):
        assert secret not in receipt.text and secret not in saved.text
    assert len(ledger.list_change_sets(workspace_id=created["resource_id"])) == 1


def test_lost_ledger_completion_reconciles_original_file_once(editable, monkeypatch):
    service, client, headers, created, root, ledger = editable
    body = command(service, headers, created, read(client, headers, created).json())
    record = ledger.record_change_set
    def lost(*args, **kwargs):
        raise RuntimeError("synthetic unavailable ledger")
    monkeypatch.setattr(ledger, "record_change_set", lost)
    first = send(client, headers, created, body)
    assert first.status_code == 200 and first.json()["status"] == "partial", first.text
    assert first.json()["workspace_edit"]["file_saved"]
    assert (root / "hello.txt").read_bytes() == b"edited\r\n"
    monkeypatch.setattr(ledger, "record_change_set", record)
    completed = send(client, headers, created, body)
    assert completed.status_code == 200, completed.text
    assert completed.json()["workspace_edit"]["status"] == "saved"
    assert completed.json()["workspace_edit"]["ledger_saved"]
    assert len(ledger.list_change_sets(workspace_id=created["resource_id"])) == 1
    assert next(root.glob(".row-bot-edit-recovery/*/previous")).read_bytes() == b"original\r\n"


def test_external_file_revision_and_read_only_policy_preserve_bytes(editable):
    from row_bot import threads
    service, client, headers, created, root, _ = editable
    snapshot = read(client, headers, created).json()
    body = command(service, headers, created, snapshot)
    (root / "hello.txt").write_bytes(b"external\n")
    conflicted = send(client, headers, created, body)
    assert conflicted.status_code == 200, conflicted.text
    assert conflicted.json()["workspace_edit"]["status"] == "conflict"
    assert (root / "hello.txt").read_bytes() == b"external\n"
    threads._set_thread_approval_mode(created["conversation_id"], "block")
    body = command(service, headers, created, read(client, headers, created).json())
    denied = send(client, headers, created, body)
    assert denied.status_code == 200 and denied.json()["workspace_edit"]["status"] == "denied", denied.text
    assert (root / "hello.txt").read_bytes() == b"external\n"
    escape = read(client, headers, created, "../private.txt")
    assert escape.status_code == 200 and escape.json()["status"] == "denied", escape.text
