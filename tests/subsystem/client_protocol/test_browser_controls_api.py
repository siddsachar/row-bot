"""Authenticated managed-browser review, command, and recovery routes."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.access.config import AccessConfig, DeploymentMode
from row_bot.access.request_context import SessionIdentity
from row_bot.api.v1.routes import create_client_platform_app

from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def test_browser_picture_is_available_to_authenticated_remote_owner_only(service, monkeypatch):
    from row_bot.application import client_browser_controls as controls

    active = {"value": True}
    app = create_client_platform_app(
        service,
        access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: (
            SessionIdentity("fixture-device", "fixture-session") if active["value"] else None
        ),
        choices=lambda: {"models": [], "capabilities": []},
    )
    called = []

    def picture(conversation_id, revision, *, validate):
        validate()
        called.append(conversation_id)
        return {
            "schema_version": 1, "conversation_id": conversation_id,
            "revision": revision, "state": "available", "image_base64": "c3ludGhldGlj",
        }

    monkeypatch.setattr(controls, "read_browser_preview", picture)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        created = _command(client, headers, "conversation.create", {"title": "Synthetic browser"})
        assert created.status_code == 200, created.text
        conversation = created.json()["conversation_id"]
        url = f"/api/v1/conversations/{conversation}/browser/preview?revision={'a' * 64}"
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["image_base64"] == "c3ludGhldGlj"
        active["value"] = False
        denied = client.get(url, headers=headers)
        assert denied.status_code == 401
    assert called == [conversation]


def _snapshot(conversation: str, *, active: bool = False) -> dict:
    return {
        "schema_version": 1,
        "conversation_id": conversation,
        "revision": "a" * 64,
        "active": active,
        "paused": False,
        "state": "observing" if active else "idle",
        "site": "example.test" if active else "",
        "url": "https://example.test/path" if active else "",
        "last_action": "Opened website" if active else "",
        "availability": {
            "browser.navigate": {"state": "available", "code": None},
            "browser.take_over": {
                "state": "available" if active else "unavailable",
                "code": None if active else "browser_session_inactive",
            },
        },
    }


def test_browser_routes_bind_review_nonce_and_receipt_to_conversation_and_session(
    service, monkeypatch
):
    from row_bot.application import client_browser_controls as controls
    from row_bot.tools import profile_policy, registry

    monkeypatch.setattr(registry, "is_enabled", lambda name: name == "browser_navigate")
    monkeypatch.setattr(profile_policy, "dispatch_refusal", lambda *_a, **_k: None)
    effects: list[str] = []
    receipts: dict[str, dict] = {}

    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _command(
            client, headers, "conversation.create", {"title": "Browser owner"}
        )
        assert created.status_code == 200, created.text
        conversation = created.json()["conversation_id"]
        root = f"/api/v1/conversations/{conversation}/browser"
        snapshot = _snapshot(conversation)

        def read(conversation_id, *, validate):
            validate()
            assert conversation_id == conversation
            return deepcopy(snapshot)

        def review(action, payload, conversation_id, *, validate):
            validate()
            assert action == "browser.navigate"
            assert payload == {
                "revision": "a" * 64,
                "url": "https://example.test/path?private=query",
            }
            assert conversation_id == conversation
            return {
                "schema_version": 1,
                "action": action,
                "conversation_id": conversation,
                "revision": "a" * 64,
                "policy_action": "browser_navigate",
                "policy_decision": "ask",
                "policy_reason": "Cross-origin navigation requires confirmation.",
                "approval_required": True,
                "origin_and_path": "https://example.test/path",
                "query_present": True,
                "disclosures": ["The managed browser will open this address."],
                "action_digest": "b" * 64,
            }

        def execute(
            command,
            conversation_id,
            *,
            validate,
            validate_action,
            validate_review,
            **_kwargs,
        ):
            validate()
            validate_action("browser_navigate")
            assert command["payload"]["nonce"]
            reviewed = review(
                command["type"],
                {
                    "revision": command["payload"]["revision"],
                    "url": command["payload"]["url"],
                },
                conversation_id,
                validate=validate,
            )
            validate_review(command, reviewed)
            effects.append(command["command_id"])
            receipt = {
                "schema_version": 1,
                "command_id": command["command_id"],
                "action": command["type"],
                "conversation_id": conversation,
                "status": "completed",
                "code": None,
                "revision": "c" * 64,
                "browser_control": {**snapshot, "revision": "c" * 64},
            }
            receipts[command["command_id"]] = receipt
            return receipt

        def receipt(*, command_id, validate, **_kwargs):
            validate()
            if command_id not in receipts:
                raise controls.ClientBrowserControlError("browser_receipt_unavailable")
            return deepcopy(receipts[command_id])

        monkeypatch.setattr(controls, "read_browser_controls", read)
        monkeypatch.setattr(controls, "review_browser_command", review)
        monkeypatch.setattr(controls, "execute_browser_command", execute)
        monkeypatch.setattr(controls, "read_browser_receipt", receipt)

        observed = client.get(root, headers=headers)
        assert observed.status_code == 200, observed.text
        assert observed.json() == snapshot
        assert effects == []

        reviewed = client.post(
            root + "/review",
            headers=headers,
            json={
                "action": "browser.navigate",
                "type": "browser.navigate",
                "payload": {
                    "revision": "a" * 64,
                    "url": "https://example.test/path?private=query",
                },
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["nonce"]
        assert "private=query" not in reviewed.text

        command_id = str(uuid4())
        command = {
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": "browser.navigate",
            "expected_revision": "0",
            "payload": {
                "revision": "a" * 64,
                "url": "https://example.test/path?private=query",
                "nonce": reviewed.json()["nonce"],
            },
        }
        command_headers = {**headers, "Idempotency-Key": command_id}
        completed = client.post(
            root + "/commands", headers=command_headers, json=command
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert "private=query" not in completed.text
        recovered = client.get(root + f"/commands/{command_id}", headers=headers)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == completed.json()
        assert effects == [command_id]


@pytest.mark.parametrize(
    "review_request",
    [
        {
            "action": "browser.navigate",
            "payload": {
                "revision": "a" * 64,
                "url": "https://example.test/",
            },
        },
        {
            "action": "browser.navigate",
            "type": "browser.check",
            "payload": {
                "revision": "a" * 64,
                "url": "https://example.test/",
            },
        },
    ],
    ids=["missing-type", "mismatched-type"],
)
def test_browser_review_rejects_invalid_discriminant_before_review(
    service, monkeypatch, review_request
):
    from row_bot.application import client_browser_controls as controls

    review_effects: list[str] = []

    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _command(
            client, headers, "conversation.create", {"title": "Browser review denial"}
        )
        conversation = created.json()["conversation_id"]

        def review(*_args, **_kwargs):
            review_effects.append("reviewed")
            raise AssertionError("invalid reviews must fail before review")

        monkeypatch.setattr(controls, "review_browser_command", review)
        response = client.post(
            f"/api/v1/conversations/{conversation}/browser/review",
            headers=headers,
            json=review_request,
        )

        assert response.status_code == 422
        assert response.json()["code"] == "invalid_command"
        assert review_effects == []


def test_browser_route_rejects_wrong_session_and_non_browser_commands(
    service, monkeypatch
):
    from row_bot.application import client_browser_controls as controls
    from row_bot.tools import profile_policy, registry

    monkeypatch.setattr(registry, "is_enabled", lambda _name: True)
    monkeypatch.setattr(profile_policy, "dispatch_refusal", lambda *_a, **_k: None)
    effects: list[str] = []

    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _command(
            client, headers, "conversation.create", {"title": "Browser denial"}
        )
        conversation = created.json()["conversation_id"]
        root = f"/api/v1/conversations/{conversation}/browser"

        def execute(*_args, **_kwargs):
            effects.append("executed")
            raise AssertionError("invalid commands must fail before execution")

        monkeypatch.setattr(controls, "execute_browser_command", execute)
        command_id = str(uuid4())
        response = client.post(
            root + "/commands",
            headers={**headers, "Idempotency-Key": command_id},
            json={
                "command_id": command_id,
                "client_session_id": str(uuid4()),
                "type": "browser.navigate",
                "expected_revision": "0",
                "payload": {
                    "revision": "a" * 64,
                    "url": "https://example.test/",
                    "nonce": "foreign-review",
                },
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_browser_command"
        assert effects == []

        wrong_type = client.post(
            root + "/commands",
            headers={**headers, "Idempotency-Key": command_id},
            json={
                "command_id": command_id,
                "client_session_id": headers["X-Client-Session"],
                "type": "conversation.stop",
                "expected_revision": "0",
                "payload": {},
            },
        )
        assert wrong_type.status_code == 422
        assert wrong_type.json()["code"] == "invalid_browser_command"
        assert effects == []
