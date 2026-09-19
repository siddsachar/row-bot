"""Real Buddy preferences and approved Hatch bind the authenticated conversation."""
# ruff: noqa: F811 -- isolated shared fixtures.
from uuid import uuid4

import pytest

from row_bot.application import buddy_commands
from row_bot.buddy import config, client_service
from row_bot.runtime import admissions
from tests.subsystem.buddy.test_client_buddy_commands import env  # noqa: F401
from tests.subsystem.buddy.test_client_buddy_policy import env as policy_env  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem
BASE = "/api/v1/conversations/buddy-conversation/buddy"


def prepare(service, monkeypatch, mode="ask"):
    monkeypatch.setattr(service, "_metadata", lambda _: {"approval_mode": mode, "agent_profile_id": ""})


def send(client, headers, revision="missing", changes=None):
    command = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
               "expected_revision": "0", "type": "buddy.update", "payload": {
                   "config_revision": revision, "changes": {"visible": False} if changes is None else changes}}
    return command, client.post(BASE + "/commands", headers=headers | {"Idempotency-Key": command["command_id"]}, json=command)


def test_buddy_preferences_real_publication_receipt_and_conversation_owner(service, env, monkeypatch):
    prepare(service, monkeypatch)
    with _client(service) as client:
        _, headers = bootstrap(client)
        read = client.get(BASE, headers=headers)
        assert read.status_code == 200 and read.json()["placement"] == "docked", read.text
        assert not config._BUDDY_CONFIG_PATH.exists()
        command, result = send(client, headers)
        assert result.status_code == 200 and result.json()["status"] == "completed", result.text
        assert not config.read_buddy_config_revision()[0]["visible"]
        saved = config._BUDDY_CONFIG_PATH.read_bytes()
        receipt = client.get(BASE + "/commands/" + command["command_id"], headers=headers)
        assert receipt.status_code == 200 and "_buddy" not in receipt.text, receipt.text
        assert config._BUDDY_CONFIG_PATH.read_bytes() == saved
        assert client.get(BASE.replace("buddy-conversation", "another-conversation") + "/commands/" + command["command_id"], headers=headers).status_code == 404
        _, foreign = bootstrap(client)
        assert client.get(BASE + "/commands/" + command["command_id"], headers=foreign).status_code == 404


def test_blocked_and_invalid_preferences_have_no_file_effect(service, env, monkeypatch):
    prepare(service, monkeypatch, "block")
    with _client(service) as client:
        _, headers = bootstrap(client)
        _, result = send(client, headers)
        assert result.status_code == 409 and result.json()["code"] == "buddy_action_blocked", result.text
        for changes in ({"visible": None}, {}, {"placement": "floating"}):
            command, result = send(client, headers, changes=changes)
            assert result.status_code == 422, result.text
            assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        assert not config._BUDDY_CONFIG_PATH.exists()


def test_lost_buddy_save_reply_reconciles_exact_retained_publication(service, env, monkeypatch):
    prepare(service, monkeypatch)
    original = buddy_commands._progress
    def progress(*args, **kwargs):
        if kwargs.get("complete"):
            raise OSError("synthetic lost save reply")
        return original(*args, **kwargs)
    monkeypatch.setattr(buddy_commands, "_progress", progress)
    with _client(service) as client:
        _, headers = bootstrap(client)
        command, result = send(client, headers)
        assert result.status_code != 200, result.text
        saved = config._BUDDY_CONFIG_PATH.read_bytes()
        monkeypatch.setattr(client_service, "update_buddy", lambda *_a, **_kw: pytest.fail("No repeat publication"))
        receipt = client.get(BASE + "/commands/" + command["command_id"], headers=headers)
        assert receipt.status_code == 200 and receipt.json()["status"] == "completed", receipt.text
        assert config._BUDDY_CONFIG_PATH.read_bytes() == saved


def test_hatch_review_binds_real_policy_nonce_and_captured_auth_before_each_stage(service, env, policy_env, monkeypatch):
    from row_bot.buddy import client_hatch
    from row_bot.providers.media_auth import CapturedMediaAuth
    prepare(service, monkeypatch)
    started = []
    with _client(service) as client:
        _, headers = bootstrap(client)
        request = {"action": "full", "prompt": "Synthetic companion", "config_revision": "missing"}
        reviewed = client.post(BASE + "/review", headers=headers, json=request)
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["provider_calls"] == 7
        command = {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"], "expected_revision": "0",
                   "type": "buddy.hatch", "payload": {"request": request, "review_id": reviewed.json()["review_id"], "nonce": reviewed.json()["nonce"]}}
        result = client_hatch.HatchResult(1, command["command_id"], "buddy-hatch-" + command["command_id"],
            "completed", "completed", "hatch-synthetic", True, 6, 6, "", True)
        monkeypatch.setattr(client_hatch, "read_result", lambda *_a, **_kw: result)
        def start(**kwargs):
            started.append(kwargs["command_id"])
            for media, selection in [("image", "openai/gpt-image-1.5")] + [("motion", "google/veo-3.1-generate-preview")] * 6:
                auth = kwargs["validate_provider"](media, selection)
                assert isinstance(auth, CapturedMediaAuth)
                assert auth.selection == selection
            kwargs["checkpoint"](client_hatch.HatchProgress("completed", command["command_id"], result.job_id, result.pack_id, 6, 6))
        monkeypatch.setattr(client_hatch, "start_hatch", start)
        forged = {**command, "payload": {**command["payload"], "nonce": "forged"}}
        response = client.post(BASE + "/commands", headers=headers | {"Idempotency-Key": command["command_id"]}, json=forged)
        assert response.status_code == 409 and not started, response.text
        response = client.post(BASE + "/commands", headers=headers | {"Idempotency-Key": command["command_id"]}, json=command)
        assert response.status_code == 200 and response.json()["hatch"]["completed_clips"] == 6, response.text
        assert started == [command["command_id"]]
        assert all(secret not in response.text for secret in policy_env.keys.values())
