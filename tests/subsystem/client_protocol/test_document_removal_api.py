"""Document removal HTTP uses exact reviews and the original K07 operation."""
# ruff: noqa: F811 -- shared isolated owner fixtures.
import copy
import sqlite3
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.knowledge_graph.test_document_removal import stack, document  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/knowledge/documents"


def review(client, headers, document_id):
    result = client.post(BASE + "/removal-review", headers=headers, json={"document_id": document_id})
    assert result.status_code == 200, result.text
    snapshot = result.json()
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "expected_revision": "0", "type": "document.remove", "payload": {name:snapshot[name] for name in ("document_id", "source_revision", "review_id")}}


def send(client, headers, command):
    return client.post(BASE + "/commands", headers={**headers,"Idempotency-Key":command["command_id"]}, json=command)


def test_remove_binds_exact_target_and_retains_canonical_recovery_copies(service, stack):
    saved = document(stack)
    with _client(service) as client:
        _, headers = bootstrap(client)
        original = review(client, headers, saved["job"].id)
        forged = copy.deepcopy(original)
        forged["payload"]["document_id"] = None
        response = send(client, headers, forged)
        assert response.status_code == 409, response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], original["command_id"]) is None
        assert saved["live"].exists() and saved["raw"].exists()
        result = send(client, headers, original)
        assert result.status_code == 200, result.text
        outcome = result.json()["removal"]
        assert outcome["status"] == "complete" and outcome["retained_copy_count"] >= 3
        assert outcome["derived_entities_removed"] == 1
        catalog = client.get(BASE, headers=headers)
        assert catalog.status_code == 200, catalog.text
        assert next(row for row in catalog.json()["items"] if row["id"] == saved["job"].id)["record_state"] == "removed"
        assert not saved["live"].exists() and stack["kg"].get_entity(saved["entity"]["id"]) is None
        assert "_document_removal" not in result.text and str(saved["raw"]) not in result.text
        observed = client.get(BASE + "/removals/" + original["command_id"], headers=headers)
        assert observed.status_code == 200 and observed.json()["removal"] == outcome, observed.text
        _, foreign = bootstrap(client)
        assert client.get(BASE + "/removals/" + original["command_id"], headers=foreign).status_code == 409
        # A same-ID replacement must not inherit an older cleanup's state.
        with sqlite3.connect(stack["service"].db_path) as connection:
            connection.execute("UPDATE document_jobs SET content_sha256=? WHERE id=?", ("f" * 64, saved["job"].id))
        current = client.get(BASE, headers=headers)
        assert next(row for row in current.json()["items"] if row["id"] == saved["job"].id)["record_state"] == "partial"


def test_partial_receipt_and_duplicate_do_not_retry_until_explicit_same_operation_review(service, stack, monkeypatch):
    saved = document(stack)
    rebuild = stack["kg"].rebuild_fts_index
    calls = []
    def failed(*, validate=None):
        if validate is not None:
            validate()
        calls.append("fts")
        raise OSError("synthetic partial cleanup")
    monkeypatch.setattr(stack["kg"], "rebuild_fts_index", failed)
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers, saved["job"].id)
        result = send(client, headers, command)
        assert result.status_code == 200 and result.json()["removal"]["status"] == "partial", result.text
        removal_id = result.json()["removal"]["removal_id"]
        assert len(calls) == 1, result.text
        assert send(client, headers, command).json()["removal"]["removal_id"] == removal_id
        assert client.get(BASE + "/removals/" + command["command_id"], headers=headers).status_code == 200
        assert len(calls) == 1
        response = client.post(BASE + "/removals/" + command["command_id"] + "/retry-review", headers=headers, json={})
        assert response.status_code == 200, response.text
        reviewed = response.json()
        assert reviewed["removal_id"] == removal_id
        monkeypatch.setattr(stack["kg"], "rebuild_fts_index", rebuild)
        retry = {**command, "command_id":str(uuid4()), "type":"document.removal.retry", "payload":{
            "source_command_id":command["command_id"], "review_id":reviewed["review_id"]}}
        result = send(client, headers, retry)
        assert result.status_code == 200 and result.json()["removal"]["status"] == "complete", result.text
        assert result.json()["removal"]["removal_id"] == removal_id
