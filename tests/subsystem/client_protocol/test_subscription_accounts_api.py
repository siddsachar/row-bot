"""Subscription HTTP boundaries over real isolated account owners and fake I/O."""

# ruff: noqa: F811 -- shared isolated fixtures.
import copy
import json
from uuid import uuid4

import pytest

from row_bot.providers import config
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.providers.test_subscription_controls import account, store  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/settings/providers/subscriptions"


def review(client, headers, provider, operation="start", flow=None):
    view = client.get(BASE, headers=headers)
    assert view.status_code == 200, view.text
    intent = {
        "provider_id": provider,
        "provider_revision": view.json()["revision"],
        "operation": operation,
    }
    if flow:
        intent.update({name: flow[name] for name in ("flow_id", "server_epoch")})
    if operation == "submit":
        intent["value"] = "synthetic-authorization-code"
    response = client.post(BASE + "/review", headers=headers, json=intent)
    assert response.status_code == 200, response.text
    assert "synthetic-authorization-code" not in response.text
    return {
        "command_id": str(uuid4()),
        "client_session_id": headers["X-Client-Session"],
        "expected_revision": "0",
        "type": "provider.subscription." + operation,
        "payload": {key: value for key, value in intent.items() if key != "operation"}
        | {"nonce": response.json()["nonce"]},
    }


def send(client, headers, body):
    return client.post(
        "/api/v1/settings/providers/commands",
        headers={**headers, "Idempotency-Key": body["command_id"]},
        json=body,
    )


def test_explicit_signin_uses_same_session_flow_and_private_receipt(service, account):
    provider, owner, module, calls, _, listener_done = account
    service.subscription_flows = owner
    with _client(service) as client:
        _, headers = bootstrap(client)
        before = config.CONFIG_PATH.read_bytes()
        start = review(client, headers, provider)
        assert calls == [] and config.CONFIG_PATH.read_bytes() == before
        response = send(client, headers, start)
        assert response.status_code == 200, response.text
        flow = response.json()["flow"]
        assert flow["state"] == "waiting"
        if provider == "xai_oauth":
            assert listener_done.wait(5)
        observed = client.get(
            BASE + "/flows/" + flow["flow_id"],
            params={"server_epoch": flow["server_epoch"]},
            headers=headers,
        )
        assert observed.status_code == 200, observed.text
        assert "private-verifier" not in observed.text
        command = review(
            client,
            headers,
            provider,
            "submit" if provider == "claude_subscription" else "check",
            flow,
        )
        result = send(client, headers, command)
        assert result.status_code == 200, result.text
        assert (
            result.json()["flow"]["state"] == "connected"
            and calls.count("exchange") == 1
        )
        receipt = client.get(
            BASE + "/" + provider + "/receipts/" + command["command_id"],
            headers=headers,
        )
        assert receipt.status_code == 200 and receipt.json()["published"], receipt.text
        assert all(
            value not in result.text + receipt.text
            for value in (
                "synthetic-access",
                "synthetic-refresh",
                "private-verifier",
                "synthetic-authorization-code",
            )
        )
        _, foreign = bootstrap(client)
        assert (
            client.get(
                BASE + "/" + provider + "/receipts/" + command["command_id"],
                headers=foreign,
            ).status_code
            == 404
        )
        assert (
            client.get(
                BASE + "/flows/" + flow["flow_id"],
                params={"server_epoch": flow["server_epoch"]},
                headers=foreign,
            ).status_code
            == 409
        )


def test_tampered_review_cannot_claim_original_and_cancel_survives_config_change(
    service, account
):
    provider, owner, _, calls, *_ = account
    service.subscription_flows = owner
    with _client(service) as client:
        _, headers = bootstrap(client)
        original = review(client, headers, provider)
        forged = copy.deepcopy(original)
        forged["payload"]["provider_id"] = (
            "claude_subscription" if provider != "claude_subscription" else "codex"
        )
        response = send(client, headers, forged)
        assert (
            response.status_code == 409
            and response.json()["code"] == "approval_expired"
        ), response.text
        assert (
            admissions.read_command_metadata(
                headers["X-Client-Session"], original["command_id"]
            )
            is None
        )
        response = send(client, headers, original)
        assert response.status_code == 200, response.text
        flow = response.json()["flow"]
        saved = json.loads(config.CONFIG_PATH.read_text())
        config.CONFIG_PATH.write_text(
            json.dumps({**saved, "synthetic_unrelated_setting": True})
        )
        cancel = review(client, headers, provider, "cancel", flow)
        response = send(client, headers, cancel)
        assert response.status_code == 200, response.text
        assert response.json()["flow"]["state"] in {"cancelled", "draining"}
        assert calls == ["start"]
        again = client.post(
            BASE + "/starts/" + original["command_id"] + "/cancel",
            headers=headers,
            json={},
        )
        assert again.status_code == 200, again.text
        assert again.json()["state"] in {"cancelled", "draining"}


def test_host_close_reports_account_work_still_draining(service):
    from row_bot.application.lifecycle import ApplicationLifecycle
    import asyncio

    states = iter([False, True])

    async def nothing():
        pass

    lifecycle = ApplicationLifecycle(
        registry=service.registry,
        recover=lambda _: None,
        close_voice=lambda: True,
        close_settings=lambda: next(states),
        shutdown_inspector=nothing,
        close_live_content=lambda: None,
    )
    first = asyncio.run(lifecycle.shutdown())
    assert first["status"] == "stopping" and first["pending_settings"]
    second = asyncio.run(lifecycle.shutdown())
    assert second["status"] == "quiesced" and "pending_settings" not in second
