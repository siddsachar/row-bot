"""Real MCP command and approval composition with fake physical transports."""

# ruff: noqa: F811 -- shared isolated fixtures.
import copy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity, current_policy_snapshot
from row_bot.application import capability_runtime_controls as controls
from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.mcp.test_capability_runtime_controls import owner, request  # noqa: F401

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/settings/mcp"


def client_for(service):
    return TestClient(
        create_client_platform_app(
            service,
            security=ClientSecurity(
                service.instance_id, policy=current_policy_snapshot
            ),
            choices=lambda: {"models": [], "capabilities": []},
        ),
        base_url="http://localhost",
        client=("127.0.0.1", 12345),
    )


def review(client, headers, operation="test", runtime_id=None):
    command = request(operation, runtime_id)
    response = client.post(
        BASE + "/runtime/review", headers=headers, json=command["payload"]
    )
    assert response.status_code == 200, response.text
    return {
        **command,
        "client_session_id": headers["X-Client-Session"],
        "payload": {**command["payload"], "nonce": response.json()["nonce"]},
    }


def send(client, headers, command):
    return client.post(
        BASE + "/commands",
        headers={**headers, "Idempotency-Key": command["command_id"]},
        json=command,
    )


def test_connect_discover_disconnect_with_real_scoped_policy_nonce(service, owner):
    runtime, calls = owner
    with client_for(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers, "connect")
        assert not calls
        response = send(client, headers, command)
        assert response.status_code == 200, response.text
        assert response.json()["mcp_runtime"]["state"] == "connected", response.text
        identity = response.json()["mcp_runtime"]["runtime_id"]
        state = client.get(
            BASE + "/runtime/" + command["payload"]["server_id"], headers=headers
        )
        assert state.status_code == 200 and state.json()["runtime_id"] == identity, (
            state.text
        )
        cleanup = review(client, headers, "disconnect", identity)
        stopped = send(client, headers, cleanup)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["mcp_runtime"]["session_quiesced"], stopped.text
        assert not runtime._servers and calls == ["connect", "list_tools"]
        public = client.get(
            "/api/v1/commands/" + command["command_id"], headers=headers
        )
        assert public.status_code == 200, public.text
        assert (
            "_mcp_runtime" not in public.text
            and "private-fixture-secret" not in response.text + public.text
        )


def test_tampered_review_cannot_claim_and_original_test_has_readonly_recovery(
    service, owner, monkeypatch
):
    runtime, calls = owner
    with client_for(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers)
        forged = copy.deepcopy(command)
        forged["payload"]["operation"] = "connect"
        response = send(client, headers, forged)
        assert (
            response.status_code == 409
            and response.json()["code"] == "approval_expired"
        ), response.text
        assert (
            admissions.read_command_metadata(service.instance_id, command["command_id"])
            is None
        )
        original = controls._persist_progress

        def lost(owner_id, key, result):
            if result.get("status") == "completed":
                raise OSError("Synthetic lost completion")
            return original(owner_id, key, result)

        monkeypatch.setattr(controls, "_persist_progress", lost)
        assert send(client, headers, command).status_code == 503
        assert calls == ["connect", "list_tools"] and not runtime._servers
        monkeypatch.setattr(controls, "_persist_progress", original)
        monkeypatch.setattr(
            runtime,
            "launch_server_owned",
            lambda *_a, **_kw: pytest.fail("Original recovery cannot repeat transport"),
        )
        result = send(client, headers, command)
        assert (
            result.status_code == 200
            and result.json()["mcp_runtime"]["state"] == "tested"
        ), result.text
        assert result.json()["mcp_runtime"]["session_quiesced"]


def test_missing_owned_runtime_is_not_cleanup_proof(service, owner):
    with client_for(service) as client:
        _, headers = bootstrap(client)
        command = request()
        command["payload"].update(
            operation="disconnect", expected_runtime_id=str(uuid4())
        )
        result = client.post(
            BASE + "/runtime/review", headers=headers, json=command["payload"]
        )
        assert (
            result.status_code == 409 and result.json()["code"] == "mcp_runtime_missing"
        ), result.text


def test_disconnect_remains_available_when_saved_configuration_is_corrupt(
    service, owner
):
    from row_bot.mcp_client import config

    runtime, _ = owner
    with client_for(service) as client:
        _, headers = bootstrap(client)
        command = review(client, headers, "connect")
        connected = send(client, headers, command)
        assert (
            connected.status_code == 200
            and connected.json()["mcp_runtime"]["state"] == "connected"
        )
        server_id = command["payload"]["server_id"]
        config.CONFIG_PATH.write_text("{synthetic-corrupt", encoding="utf-8")
        state = client.get(BASE + "/runtime/" + server_id, headers=headers)
        assert (
            state.status_code == 200 and state.json()["availability"] == "unavailable"
        ), state.text
        payload = {
            "server_id": server_id,
            "resource_revision": state.json()["cleanup_revision"],
            "operation": "disconnect",
            "expected_runtime_id": state.json()["runtime_id"],
        }
        reviewed = client.post(BASE + "/runtime/review", headers=headers, json=payload)
        assert reviewed.status_code == 200, reviewed.text
        cleanup = {
            "command_id": str(uuid4()),
            "client_session_id": headers["X-Client-Session"],
            "expected_revision": "0",
            "type": "mcp.runtime.control",
            "payload": {**payload, "nonce": reviewed.json()["nonce"]},
        }
        result = send(client, headers, cleanup)
        assert (
            result.status_code == 200
            and result.json()["mcp_runtime"]["session_quiesced"]
        ), result.text
        assert (
            not runtime._servers
            and config.CONFIG_PATH.read_text() == "{synthetic-corrupt"
        )
