"""Lifecycle isolation through the existing public owners."""
# ruff: noqa: F401, F811
import json
from uuid import uuid4

import pytest
from row_bot.application import client_mcp_auth
from row_bot.application import capability_configuration_controls as configuration
from row_bot.mcp_client import auth, config
from tests.subsystem.client_protocol.test_integrations_api import isolated

pytestmark = pytest.mark.platform


def test_disconnect_never_clears_another_connections_protected_reference(isolated):
    ref = uuid4().hex
    cfg = config.read_saved_configuration().document["servers"]["Work"]
    auth.write_credentials(ref, {"binding": auth.binding("Personal", cfg), "values": {"token": "synthetic-personal"}})
    document = config.read_saved_configuration().document
    document["servers"]["Work"]["auth"] = {"mode": "api_key", "credential_ref": ref, "label": "Work"}
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    page = configuration.read_mcp_configuration()
    result = client_mcp_auth.execute_auth(owner_id="owner", command_id=str(uuid4()), server_id=page.items[0].server_id,
        configuration_revision=page.revision, action="disconnect", mode="api_key", label="Work", bindings=[],
        validate=lambda: None, validate_review=lambda _: None)
    assert result["state"] == "disconnected"
    assert "Remote revocation was not verified" in result["message"]
    assert auth.read_credentials(ref)["values"]["token"] == "synthetic-personal"
    assert config.read_saved_configuration().document["servers"]["Work"]["url"] == cfg["url"]


@pytest.mark.parametrize("cleanup", [False, True])
def test_removal_retains_credentials_by_default_and_explicit_cleanup_is_bound(isolated, cleanup):
    ref = uuid4().hex
    document = config.read_saved_configuration().document
    cfg = document["servers"]["Work"]
    auth.write_credentials(ref, {"binding": auth.binding("Work", cfg), "values": {"token": "synthetic-work"}})
    cfg["auth"] = {"mode": "api_key", "credential_ref": ref, "label": "Work"}
    document["servers"]["Personal"] = {"transport": "streamable_http", "url": "https://personal.test/mcp", "enabled": False}
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    page = configuration.read_mcp_configuration()
    intent = {"operation": "delete", "server_id": configuration._server_id("Work"), "delete_credentials": cleanup}
    command_id = str(uuid4())
    command = {"command_id": command_id, "type": "mcp.configuration.save", "payload": {"configuration_revision": page.revision, "intent": intent}}
    result = configuration.execute_mcp_configuration_command(owner_id="owner", key=command_id, command=command, validate=lambda: None, validate_review=lambda _: None)
    assert result["status"] == "completed"
    assert "Personal" in config.read_saved_configuration().document["servers"]
    if cleanup:
        with pytest.raises(auth.McpAuthError, match="mcp_sign_in_required"): auth.read_credentials(ref)
    else:
        assert auth.read_credentials(ref)["values"]["token"] == "synthetic-work"


def test_disconnect_cleanup_failure_stays_uncertain_and_recovery_never_reauthorizes(isolated, monkeypatch):
    from row_bot.mcp_client import runtime
    monkeypatch.setattr(runtime, "get_server_lifecycle", lambda _: {"runtime_id": "original-runtime"})
    stops = []
    monkeypatch.setattr(runtime, "stop_server_owned", lambda name, identity: stops.append((name, identity)) or {"state": "cleanup_incomplete"})
    page = configuration.read_mcp_configuration()
    identity = str(uuid4())
    result = client_mcp_auth.execute_auth(owner_id="owner", command_id=identity, server_id=page.items[0].server_id,
        configuration_revision=page.revision, action="disconnect", mode="api_key", label="Work", bindings=[], validate=lambda: None, validate_review=lambda _: None)
    assert result["state"] == "uncertain"
    assert client_mcp_auth.auth_status(owner_id="owner", command_id=identity)["state"] == "uncertain"
    monkeypatch.setattr(runtime, "stop_server_owned", lambda name, original: stops.append((name, original)) or {"state": "stopped"})
    result = client_mcp_auth.cancel_auth(owner_id="owner", command_id=identity)
    assert result["state"] == "disconnected" and "Remote revocation was not verified" in result["message"]
    assert stops == [("Work", "original-runtime"), ("Work", "original-runtime")]


def test_interrupted_bound_credential_cleanup_remains_unusable_and_resumes(isolated, monkeypatch):
    from row_bot import secret_store
    ref = uuid4().hex
    bound = "a" * 64
    auth.write_credentials(ref, {"binding": bound, "values": {"token": "synthetic-work"}})
    original = secret_store.delete_secret
    def fail_chunk(key, **kwargs):
        if key.startswith(ref + ":"):
            raise secret_store.SecretStoreError("synthetic unavailable")
        return original(key, **kwargs)
    monkeypatch.setattr(secret_store, "delete_secret", fail_chunk)
    with pytest.raises(secret_store.SecretStoreError):
        auth.delete_bound_credentials(ref, bound)
    with pytest.raises(auth.McpAuthError, match="mcp_sign_in_required"):
        auth.read_credentials(ref)
    assert auth.delete_bound_credentials(ref, "b" * 64) is False
    monkeypatch.setattr(secret_store, "delete_secret", original)
    assert auth.delete_bound_credentials(ref, bound) is True
    assert auth.delete_bound_credentials(ref, bound) is True
