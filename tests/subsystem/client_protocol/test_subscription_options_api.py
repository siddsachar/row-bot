"""Reviewed subscription options use canonical isolated owners through HTTP."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
from uuid import uuid4

import pytest

from row_bot.application import subscription_options
from row_bot.providers import config
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_subscription_accounts_api import send
from tests.subsystem.providers.test_subscription_options import reference, store  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/settings/providers/subscriptions/options"


def reviewed(client, headers, operation="client_id_save", provider="xai_oauth", value="synthetic-client"):
    snapshot = client.get(BASE, headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    intent = {"provider_id": provider, "provider_revision": snapshot.json()["revision"],
              "operation": operation, "value": value}
    response = client.post(BASE + "/review", headers=headers, json=intent)
    assert response.status_code == 200, response.text
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "expected_revision": "0", "type": subscription_options.COMMANDS[operation],
            "payload": {key: item for key, item in intent.items() if key != "operation"}
                       | {"nonce": response.json()["nonce"]}}


def test_save_reset_and_owner_bound_passive_receipts(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        original = config.CONFIG_PATH.read_bytes()
        command = reviewed(client, headers)
        assert config.CONFIG_PATH.read_bytes() == original
        forged = copy.deepcopy(command)
        forged["payload"]["value"] = "different-client"
        response = send(client, headers, forged)
        assert response.status_code == 409 and response.json()["code"] == "approval_expired", response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        result = send(client, headers, command)
        assert result.status_code == 200, result.text
        assert result.json()["options"]["xai_saved_client_id"] == "synthetic-client"
        receipt = client.get(BASE + "/receipts/" + command["command_id"], headers=headers)
        assert receipt.status_code == 200 and receipt.json()["published"], receipt.text
        _, foreign = bootstrap(client)
        assert client.get(BASE + "/receipts/" + command["command_id"], headers=foreign).status_code == 404
        reset = reviewed(client, headers, "client_id_reset", value=None)
        result = send(client, headers, reset)
        assert result.status_code == 200 and result.json()["options"]["xai_saved_client_id"] is None, result.text
        # Original command recovery cannot resurrect the old client ID.
        result = send(client, headers, command)
        assert result.status_code == 200 and result.json()["options"]["xai_saved_client_id"] is None, result.text


def test_reference_review_binds_exact_private_cli_capture_before_admission(service, reference, store):
    provider, primary, _ = reference
    with _client(service) as client:
        _, headers = bootstrap(client)
        original = primary.read_bytes()
        command = reviewed(client, headers, "reference", provider, None)
        primary.write_bytes(original.replace(b"private-synthetic", b"changed-synthetic"))
        response = send(client, headers, command)
        assert response.status_code == 409 and response.json()["code"] == "approval_expired", response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        primary.write_bytes(original)
        # Restoring the bytes does not restore the reviewed filesystem identity.
        assert send(client, headers, command).status_code == 409
        command = reviewed(client, headers, "reference", provider, None)
        result = send(client, headers, command)
        assert result.status_code == 200, result.text
        assert any(row["provider_id"] == provider and row["metadata_saved"] for row in result.json()["options"]["references"])
        assert primary.read_bytes() == original and store.writes == []
        assert "private-synthetic" not in result.text and str(primary) not in result.text


def test_lost_publication_response_recovers_original_receipt_without_republishing(service, store, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        complete = admissions.complete_command
        monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError("synthetic interrupted ack")))
        response = send(client, headers, command)
        assert response.status_code == 409, response.text
        saved = config.CONFIG_PATH.read_bytes()
        receipt = client.get(BASE + "/receipts/" + command["command_id"], headers=headers)
        assert receipt.status_code == 200 and receipt.json()["published"], receipt.text
        assert config.CONFIG_PATH.read_bytes() == saved
        monkeypatch.setattr(admissions, "complete_command", complete)
        result = send(client, headers, command)
        assert result.status_code == 200 and config.CONFIG_PATH.read_bytes() == saved, result.text
