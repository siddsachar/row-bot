"""Actual Undo/import owners behind isolated authenticated HTTP requests."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _isolated_service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.developer import test_client_workspace_edits as edit_tests
from tests.subsystem.developer import test_client_workspace_imports as import_tests
from tests.subsystem.developer import test_client_workspace_undo as undo_tests

pytestmark = pytest.mark.subsystem
domain = edit_tests.domain
imports = import_tests.imports
undo = undo_tests.undo


@pytest.fixture
def api(undo, request):
    d = undo
    before, after = getattr(request, "param", ({"file.txt": "before\r\n"}, {"file.txt": "after\r\n"}))
    change = d.imported(before, after)
    binding = d.resources.list_bindings("chat").bindings[0].binding_id
    service = _isolated_service()
    now = [100.0]
    security = ClientSecurity(instance_id=service.instance_id, clock=lambda: now[0])
    app = create_client_platform_app(service, security=security, choices=lambda: {"models": [], "capabilities": []})
    base = f"/api/v1/conversations/chat/workspaces/{binding}/undo"
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        def reviewed():
            response = client.post(base + "/review", headers=headers, json={"change_set_id": change})
            assert response.status_code == 200, response.text
            review = response.json()
            nonce = review.pop("nonce")
            return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                "expected_revision": "0", "type": "workspace.undo", "payload": {"review": review, "nonce": nonce}}
        def send(command, use_headers=None):
            return client.post(base + "/commands", headers=(use_headers or headers) | {"Idempotency-Key": command["command_id"]}, json=command)
        yield SimpleNamespace(d=d, client=client, headers=headers, security=security, now=now,
            base=base, change=change, binding=binding, review=reviewed, send=send, before=before, after=after)


@pytest.mark.parametrize("api", [(
    {"crlf.txt": "before\r\n", "no-eof.txt": "before", "empty-deleted.txt": "", "deleted.txt": "delete me\n"},
    {"crlf.txt": "after\r\n", "no-eof.txt": "after", "empty-deleted.txt": None, "deleted.txt": None,
     "new/empty-created.txt": "", "new/created.txt": "created\n"},
)], indirect=True)
def test_mixed_exact_bytes_and_completed_api_replay_are_passive(api, monkeypatch):
    from row_bot.application import workspace_undo_commands
    command = api.review()
    result = api.send(command)
    assert result.status_code == 200 and result.json()["status"] == "undone", result.text
    assert result.json()["ledger_saved"] and result.json()["reverted"]
    for name in api.before.keys() | api.after.keys():
        expected = api.before.get(name)
        path = api.d.root / name
        assert path.read_bytes() == expected.encode() if expected is not None else not path.exists()
    inode = (api.d.root / "crlf.txt").stat().st_ino
    monkeypatch.setattr(workspace_undo_commands.client_undo, "undo_workspace_change", lambda *a, **k: pytest.fail("physical replay"))
    monkeypatch.setattr(workspace_undo_commands.client_undo, "review_workspace_undo", lambda *a, **k: pytest.fail("fresh domain review on original"))
    api.now[0] += 301
    assert api.send(command).json() == result.json()
    receipt = api.client.get(api.base + "/commands/" + command["command_id"], headers=api.headers)
    assert receipt.status_code == 200 and receipt.json() == result.json()
    assert (api.d.root / "crlf.txt").stat().st_ino == inode
    assert "_workspace_undo" not in receipt.text and str(api.d.root) not in receipt.text


@pytest.mark.parametrize("change", ["nonce", "binding", "bytes", "same_bytes_inode"])
def test_changed_authority_or_file_before_admission_has_no_undo(api, change):
    command = api.review()
    path = api.d.root / "file.txt"
    if change == "nonce":
        api.now[0] += 301
    elif change == "binding":
        snapshot = api.d.resources.list_bindings("chat")
        api.d.resources.unbind("chat", api.binding, expected_revision=snapshot.bindings_revision)
    elif change == "bytes":
        path.write_bytes(b"user current bytes")
    else:
        path.rename(path.with_name("retained-user-file"))
        path.write_bytes(b"after\r\n")
    expected = path.read_bytes()
    result = api.send(command)
    assert result.status_code in {403, 404, 409}, result.text
    assert admissions.read_command_metadata(api.headers["X-Client-Session"], command["command_id"]) is None
    assert path.read_bytes() == expected
    assert not api.d.ledger.read_change_set(api.change)[0].reverted


def test_expiry_after_atomic_claim_recovers_original_review_with_fresh_nonce(api, monkeypatch):
    command = api.review()
    claim = admissions.claim_command
    def expire(*args, **kwargs):
        value = claim(*args, **kwargs)
        api.now[0] += 301
        return value
    monkeypatch.setattr(admissions, "claim_command", expire)
    response = api.send(command)
    assert response.status_code == 409 and response.json()["code"] == "approval_expired", response.text
    assert (api.d.root / "file.txt").read_bytes() == b"after\r\n"
    monkeypatch.setattr(admissions, "claim_command", claim)
    renewed = api.client.post(api.base + "/commands/" + command["command_id"] + "/review", headers=api.headers, json={})
    assert renewed.status_code == 200, renewed.text
    original_review = renewed.json()
    command["payload"]["nonce"] = original_review.pop("nonce")
    assert original_review == command["payload"]["review"]
    restored = api.send(command)
    assert restored.status_code == 200 and restored.json()["status"] == "undone", restored.text


@pytest.mark.parametrize("after_commit", [False, True])
def test_final_receipt_uncertainty_and_lost_response_recover_without_republication(api, monkeypatch, after_commit):
    command = api.review()
    complete = admissions.complete_command
    def fail(*args, **kwargs):
        if after_commit:
            complete(*args, **kwargs)
        raise OSError("synthetic final receipt fault")
    monkeypatch.setattr(admissions, "complete_command", fail)
    first = api.send(command)
    assert first.status_code == 503, first.text
    path = api.d.root / "file.txt"
    assert path.read_bytes() == b"before\r\n"
    inode = path.stat().st_ino
    receipt = api.client.get(api.base + "/commands/" + command["command_id"], headers=api.headers)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == ("undone" if after_commit else "partial")
    monkeypatch.setattr(admissions, "complete_command", complete)
    monkeypatch.setattr(api.d.edits, "publish_text_revision", lambda *a, **k: pytest.fail("bytes republished during exact recovery"))
    result = api.send(command)
    assert result.status_code == 200 and result.json()["status"] == "undone", result.text
    assert path.stat().st_ino == inode


def test_auth_revocation_at_actual_publication_prevents_effect_and_new_owner_adoption(api, monkeypatch):
    command = api.review()
    publish = api.d.edits.publish_text_revision
    def revoked(*args, **kwargs):
        with api.security._lock:
            api.security._sessions.pop(api.headers["X-Client-Session"])
        return publish(*args, **kwargs)
    monkeypatch.setattr(api.d.edits, "publish_text_revision", revoked)
    response = api.send(command)
    assert response.status_code == 401, response.text
    assert (api.d.root / "file.txt").read_bytes() == b"after\r\n"
    _, fresh_headers = bootstrap(api.client)
    old_receipt = api.client.get(api.base + "/commands/" + command["command_id"], headers=fresh_headers)
    assert old_receipt.status_code == 404, old_receipt.text
    altered = deepcopy(command)
    altered["client_session_id"] = fresh_headers["X-Client-Session"]
    denied = api.send(altered, fresh_headers)
    assert denied.status_code == 409 and denied.json()["code"] == "approval_expired", denied.text
    assert admissions.read_command_metadata(fresh_headers["X-Client-Session"], command["command_id"]) is None
    assert (api.d.root / "file.txt").read_bytes() == b"after\r\n"


def test_binding_revocation_at_actual_publication_retains_original_no_effect(api, monkeypatch):
    command = api.review()
    publish = api.d.edits.publish_text_revision
    def revoked(*args, **kwargs):
        snapshot = api.d.resources.list_bindings("chat")
        api.d.resources.unbind("chat", api.binding, expected_revision=snapshot.bindings_revision)
        return publish(*args, **kwargs)
    monkeypatch.setattr(api.d.edits, "publish_text_revision", revoked)
    response = api.send(command)
    assert response.status_code in {403, 404, 409}, response.text
    assert (api.d.root / "file.txt").read_bytes() == b"after\r\n"
    assert admissions.read_command_metadata(api.headers["X-Client-Session"], command["command_id"]) is not None


def test_lost_file_publication_acknowledgement_recovers_exact_retained_identity(api, monkeypatch):
    command = api.review()
    publish = api.d.edits.publish_text_revision
    calls = []
    def lose_ack(*args, **kwargs):
        publish(*args, **kwargs)
        calls.append(kwargs["command_id"])
        raise OSError("synthetic interruption after original bytes publication")
    monkeypatch.setattr(api.d.edits, "publish_text_revision", lose_ack)
    first = api.send(command)
    assert first.status_code == 200 and first.json()["status"] == "partial", first.text
    path = api.d.root / "file.txt"
    assert path.read_bytes() == b"before\r\n"
    inode = path.stat().st_ino
    receipt = api.client.get(api.base + "/commands/" + command["command_id"], headers=api.headers)
    assert receipt.status_code == 200 and receipt.json() == first.json(), receipt.text
    assert len(calls) == 1
    def original_recovery(*args, **kwargs):
        assert kwargs["command_id"] == calls[0]
        assert kwargs["recovery"] is not None
        return publish(*args, **kwargs)
    monkeypatch.setattr(api.d.edits, "publish_text_revision", original_recovery)
    recovered = api.send(command)
    assert recovered.status_code == 200 and recovered.json()["status"] == "undone", recovered.text
    assert path.stat().st_ino == inode
    assert len(api.d.ledger.list_change_sets(include_reverted=True)) == 1
