"""Independent Buddy integration ownership regressions; all providers are fake."""
# ruff: noqa: F811 -- canonical isolated fixtures are imported deliberately.
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity
from row_bot.buddy import client_hatch, client_service
from row_bot.providers.media_auth import CapturedMediaAuth
from tests.subsystem.buddy.test_client_buddy_commands import env  # noqa: F401
from tests.subsystem.buddy.test_client_buddy_policy import env as policy_env  # noqa: F401
from tests.subsystem.client_protocol.test_buddy_api import BASE, prepare
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def reviewed(client, headers):
    request = {"action": "full", "prompt": "Synthetic review companion", "config_revision": "missing"}
    review = client.post(BASE + "/review", headers=headers, json=request)
    assert review.status_code == 200, review.text
    return {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
            "expected_revision": "0", "type": "buddy.hatch", "payload": {"request": request,
                "review_id": review.json()["review_id"], "nonce": review.json()["nonce"]}}


def test_captured_provider_auth_return_reaches_worker_and_later_account_change_blocks_next_call(service, env, policy_env, monkeypatch):
    prepare(service, monkeypatch)
    supplied = []
    def start(**kwargs):
        auth = kwargs["validate_provider"]("image", "openai/gpt-image-1.5")
        assert isinstance(auth, CapturedMediaAuth)
        supplied.append(auth)
        policy_env.keys["openai"] = "synthetic-replaced-secret"
        kwargs["validate_provider"]("motion", "google/veo-3.1-generate-preview")
        pytest.fail("No next provider effect after captured account changes")
    monkeypatch.setattr(client_hatch, "start_hatch", start)
    with _client(service) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        response = client.post(BASE + "/commands", headers=headers | {"Idempotency-Key": command["command_id"]}, json=command)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "buddy_policy_changed"
        assert len(supplied) == 1
        assert "synthetic-openai" not in response.text and "synthetic-replaced-secret" not in response.text
        monkeypatch.setattr(client_hatch, "start_hatch", lambda **_kwargs: pytest.fail("Original receipt must never re-execute provider"))
        again = client.post(BASE + "/commands", headers=headers | {"Idempotency-Key": command["command_id"]}, json=command)
        assert again.status_code == 200 and again.json()["status"] == "partial", again.text


def test_worker_provider_callback_rechecks_real_session_after_start_was_admitted(service, env, policy_env, monkeypatch):
    prepare(service, monkeypatch)
    security = ClientSecurity(instance_id=service.instance_id)
    callbacks = []
    def start(**kwargs):
        callbacks.append(kwargs["validate_provider"]("image", "openai/gpt-image-1.5"))
        security._sessions.clear()
        kwargs["validate_provider"]("motion", "google/veo-3.1-generate-preview")
        pytest.fail("Revoked session must not reach next provider effect")
    monkeypatch.setattr(client_hatch, "start_hatch", start)
    app = create_client_platform_app(service, security=security, choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        command = reviewed(client, headers)
        response = client.post(BASE + "/commands", headers=headers | {"Idempotency-Key": command["command_id"]}, json=command)
        assert response.status_code == 401, response.text
        assert len(callbacks) == 1 and isinstance(callbacks[0], CapturedMediaAuth)


def test_media_revoked_after_owned_read_is_not_delivered(service, env, monkeypatch):
    prepare(service, monkeypatch)
    security = ClientSecurity(instance_id=service.instance_id)
    secret = b"synthetic-private-media-bytes"
    def media(*_args, validate, **_kwargs):
        validate()
        security._sessions.clear()
        return secret, "video/mp4"
    monkeypatch.setattr(client_service, "read_buddy_media", media)
    app = create_client_platform_app(service, security=security, choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345), raise_server_exceptions=False) as client:
        _, headers = bootstrap(client)
        response = client.get(BASE + "/packs/glyph/media/idle?revision=revision", headers=headers)
        assert secret not in response.content


def test_delayed_buddy_current_run_stop_does_not_cancel_replacement_generation(service, monkeypatch):
    # Carry the same exact generation BuddySurface captured. The original
    # empty payload cancelled a replacement because metadata revision alone
    # does not identify a generation. Generic empty Stop retains its semantics.
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _command(client, headers, "conversation.create", {"title": "Synthetic Buddy stop"})
        assert created.status_code == 200, created.text
        conversation = created.json()["conversation_id"]
        first = service.registry.register(conversation, generation_id=str(uuid4()))
        revision = service.get_conversation(conversation)["revision"]
        service.registry.finish(first)
        replacement = service.registry.register(conversation, generation_id=str(uuid4()))
        try:
            assert service.get_conversation(conversation)["revision"] == revision
            response = _command(client, headers, "conversation.stop", {"generation_id": first.generation_id}, target=conversation, revision=revision)
            assert response.status_code in {200, 409}, response.text
            assert not replacement.cancel_scope.is_cancelled(), "Buddy stop for completed run A cancelled replacement run B"
        finally:
            service.registry.finish(replacement)
