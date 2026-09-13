"""Subscription checks preserve reviewed ownership and cleanup through HTTP."""
# ruff: noqa: F811 -- shared isolated fixtures.
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from row_bot.application import subscription_probes
from row_bot.providers import config
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_protocol.test_subscription_accounts_api import send
from tests.subsystem.providers.test_subscription_probes import probes, store  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/settings/providers/subscriptions/probes"


def reviewed(client, headers):
    before = config.CONFIG_PATH.read_bytes()
    snapshot = client.get(BASE, headers=headers)
    assert snapshot.status_code == 200 and len(snapshot.json()["items"]) == 6, snapshot.text
    intent = {"provider_id": "codex", "provider_revision": snapshot.json()["revision"],
              "kind": "tokens", "model_ref": None}
    response = client.post(BASE + "/review", headers=headers, json=intent)
    assert response.status_code == 200, response.text
    assert config.CONFIG_PATH.read_bytes() == before
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "expected_revision": "0", "type": "provider.subscription.probe",
            "payload": intent | {"nonce": response.json()["nonce"]}}


def test_reviewed_token_check_receipt_status_and_foreign_owner(service, store):
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        forged = copy.deepcopy(command)
        forged["payload"]["provider_id"] = "xai_oauth"
        response = send(client, headers, forged)
        assert response.status_code == 409, response.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        response = send(client, headers, command)
        assert response.status_code == 200, response.text
        assert response.json()["result"]["status"] == "missing"
        saved = config.CONFIG_PATH.read_bytes()
        identity = BASE + "/" + command["command_id"]
        state = client.get(identity + "/status", headers=headers)
        assert state.status_code == 200 and state.json()["operation"]["quiescent"], state.text
        receipt = client.get(identity + "/receipt", headers=headers)
        assert receipt.status_code == 200 and receipt.json()["published"], receipt.text
        assert config.CONFIG_PATH.read_bytes() == saved
        _, foreign = bootstrap(client)
        assert client.get(identity + "/status", headers=foreign).json() == {"operation": None}
        assert client.get(identity + "/receipt", headers=foreign).status_code == 404
        assert client.post(identity + "/cancel", headers=foreign, json={}).status_code != 200
        assert send(client, headers, command).status_code == 200
        assert config.CONFIG_PATH.read_bytes() == saved


def test_cancel_while_admission_waits_uses_exact_original_operation(service, store, monkeypatch):
    entered, release = Event(), Event()
    claim = admissions.claim_command
    def blocked_claim(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return claim(*args, **kwargs)
    monkeypatch.setattr(admissions, "claim_command", blocked_claim)
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        before = config.CONFIG_PATH.read_bytes()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(send, client, headers, command)
            try:
                assert entered.wait(10)
                cancelled = client.post(BASE + "/" + command["command_id"] + "/cancel", headers=headers, json={})
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["state"] == "draining" and not cancelled.json()["quiescent"]
            finally:
                release.set()
            result = pending.result(10)
            assert result.status_code == 409, result.text
        state = client.get(BASE + "/" + command["command_id"] + "/status", headers=headers).json()["operation"]
        assert state["quiescent"] and state["state"] == "cancelled"
        assert config.CONFIG_PATH.read_bytes() == before


def test_lost_saved_check_response_reads_proof_without_repeating_check(service, store, monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        complete = admissions.complete_command
        monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError("synthetic lost acknowledgement")))
        response = send(client, headers, command)
        assert response.status_code == 409, response.text
        saved = config.CONFIG_PATH.read_bytes()
        receipt = client.get(BASE + "/" + command["command_id"] + "/receipt", headers=headers)
        assert receipt.status_code == 200 and receipt.json()["published"], receipt.text
        monkeypatch.setattr(admissions, "complete_command", complete)
        monkeypatch.setattr(subscription_probes, "review_probe", lambda *_a, **_kw: pytest.fail("Do not re-review/replay saved check"))
        response = send(client, headers, command)
        assert response.status_code == 200, response.text
        assert config.CONFIG_PATH.read_bytes() == saved
