"""Computer-use card routes: local-owner only, no-store picture, idempotent commands."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.access.config import AccessConfig, DeploymentMode
from row_bot.access.request_context import SessionIdentity
from row_bot.api.v1.routes import create_client_platform_app
from row_bot.computer_use.service import LeaseOwner

from tests.subsystem.client_protocol.test_computer_controls_domain import _working, computer
from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def _conversation(client, headers) -> str:
    created = _command(client, headers, "conversation.create", {"title": "Computer card"})
    assert created.status_code == 200, created.text
    return created.json()["conversation_id"]


def _card(headers, action: str, command_id: str | None = None) -> dict:
    return {"command_id": command_id or str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "type": action}


def test_the_local_owner_reads_the_card_and_its_picture_without_caching(service, computer):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        root = f"/api/v1/conversations/{conversation}/computer"

        idle = client.get(root, headers=headers)
        assert idle.status_code == 200, idle.text
        assert (idle.json()["state"], idle.json()["active"]) == ("stopped", False)

        observation = _working(computer, LeaseOwner(conversation, "generation-api", "task-api"))
        snapshot = client.get(root, headers=headers).json()
        assert (snapshot["state"], snapshot["app"], snapshot["has_picture"]) == ("working", "Calculator", True)

        picture = client.get(root + "/preview", params={"revision": snapshot["revision"]}, headers=headers)
        assert picture.status_code == 200, picture.text
        assert picture.headers["cache-control"] == "no-store"
        assert base64.b64decode(picture.json()["image_base64"]) == observation.screenshot
        assert picture.json()["mime_type"] == "image/png"

        stale = client.get(root + "/preview", params={"revision": "0" * 64}, headers=headers)
        assert stale.status_code == 409
        assert (stale.json()["code"], stale.json()["current_revision"]) == (
            "computer_use_revision_conflict", snapshot["revision"])


def test_card_commands_are_bound_to_their_session_and_idempotency_key(service, computer):
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        root = f"/api/v1/conversations/{conversation}/computer/commands"
        _working(computer, LeaseOwner(conversation, "generation-api", "task-api"))

        pause = _card(headers, "computer_use.pause")
        mismatched = client.post(root, headers={**headers, "Idempotency-Key": str(uuid4())}, json=pause)
        assert (mismatched.status_code, mismatched.json()["code"]) == (409, "idempotency_mismatch")
        foreign = {**pause, "client_session_id": str(uuid4())}
        refused = client.post(root, headers={**headers, "Idempotency-Key": pause["command_id"]}, json=foreign)
        assert (refused.status_code, refused.json()["code"]) == (422, "invalid_computer_use_command")
        unknown = client.post(root, headers={**headers, "Idempotency-Key": pause["command_id"]},
                              json={**pause, "type": "computer_use.click"})
        assert unknown.status_code == 422
        assert computer.status_snapshot()["state"] == "observing"

        paused = client.post(root, headers={**headers, "Idempotency-Key": pause["command_id"]}, json=pause)
        assert paused.status_code == 200, paused.text
        assert (paused.json()["status"], paused.json()["computer_use"]["state"]) == ("completed", "paused")
        replay = client.post(root, headers={**headers, "Idempotency-Key": pause["command_id"]}, json=pause)
        assert replay.json() == paused.json()

        stop = _card(headers, "computer_use.stop")
        stopped = client.post(root, headers={**headers, "Idempotency-Key": stop["command_id"]}, json=stop)
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["computer_use"]["state"] == "stopped"
        assert computer.status_snapshot()["active"] is False


def test_other_devices_can_neither_see_nor_control_this_computer(service, computer):
    app = create_client_platform_app(
        service,
        access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: SessionIdentity("fixture-device", "fixture-session"),
        choices=lambda: {"models": [], "capabilities": []},
    )
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        root = f"/api/v1/conversations/{conversation}/computer"
        _working(computer, LeaseOwner(conversation, "generation-remote", "task-remote"))

        stop = _card(headers, "computer_use.stop")
        responses = [
            client.get(root, headers=headers),
            client.get(root + "/preview", params={"revision": "0" * 64}, headers=headers),
            client.post(root + "/commands", headers={**headers, "Idempotency-Key": stop["command_id"]}, json=stop),
        ]
        assert [(response.status_code, response.json()["code"]) for response in responses] == [
            (403, "computer_use_local_only")] * 3
        assert "image_base64" not in "".join(response.text for response in responses)
        assert computer.status_snapshot()["state"] == "observing"
