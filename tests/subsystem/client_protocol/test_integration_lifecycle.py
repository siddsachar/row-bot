"""First use and lifecycle isolation through the existing public owners."""
# ruff: noqa: F401, F811
from types import SimpleNamespace
import json
from uuid import uuid4

import pytest
from row_bot.application import integration_use, client_integrations, client_mcp_auth
from row_bot.application import capability_configuration_controls as configuration
from row_bot.mcp_client import auth, config
from tests.subsystem.client_protocol.test_integrations_api import isolated
from tests.subsystem.client_protocol.test_protocol_application import service, _command
from tests.subsystem.client_protocol.test_mcp_configuration_api import client_for
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.platform


@pytest.fixture
def use_context(monkeypatch):
    from row_bot import agent_profiles, agent_tool_catalog
    from row_bot.application import workspace_setup
    item = {"id": "skill:writing", "kind": "skill", "owner_ref": "writing", "account_label": "Work", "status": "ready", "children": []}
    workspace = {"conversation_id": "chat", "revision": "3", "controls": {"runtime_mode": "agent", "profile_id": "writer"}, "model_status": {"state": "ready"}}
    profile = {"id": "writer", "enabled": True, "skill_policy_json": {"skills_override": ["writing"]}, "tool_policy_json": {"capability": "write_capable"}}
    monkeypatch.setattr(client_integrations, "read_integration", lambda *_a, **_k: item)
    monkeypatch.setattr(workspace_setup, "conversation_workspace", lambda *_: workspace)
    monkeypatch.setattr(agent_profiles, "get_agent_profile", lambda *_a, **_k: profile)
    tools = []
    monkeypatch.setattr(agent_tool_catalog, "list_cached_tools", lambda **_: SimpleNamespace(items=tools, next_cursor=None))
    return item, workspace, profile, tools


def advice():
    return integration_use.read_integration_use(None, "chat", "skill:writing", validate=lambda: None)


def test_use_is_passive_and_preserves_current_account_profile_model(use_context):
    item, workspace, profile, _ = use_context
    before = json.dumps([item, workspace, profile], sort_keys=True)
    result = advice()
    assert result["eligible"] and result["account_label"] == "Work"
    assert result["conversation_revision"] == "3"
    assert json.dumps([item, workspace, profile], sort_keys=True) == before


@pytest.mark.parametrize("restriction", ["off", "chat_only", "model", "profile_off", "skill_scope"])
def test_exact_current_chat_restrictions(use_context, restriction):
    item, workspace, profile, _ = use_context
    if restriction == "off": item["status"] = "off"
    if restriction == "chat_only": workspace["controls"]["runtime_mode"] = "chat_only"
    if restriction == "model": workspace["model_status"] = {"state": "unavailable", "reason": "Tools unsupported"}
    if restriction == "profile_off": profile["enabled"] = False
    if restriction == "skill_scope": profile["skill_policy_json"]["skills_override"] = []
    assert not advice()["eligible"]
    assert advice()["reason"]


@pytest.mark.parametrize("policy,allowed", [({"capability": "write_capable", "allow_tools": ["mcp"]}, True), ({"capability": "write_capable", "allow_tools": ["filesystem"]}, False), ({"capability": "write_capable", "deny_tools": ["mcp"]}, False), ({"capability": "read_only"}, False)])
def test_tool_profile_scope_uses_canonical_dispatch(use_context, policy, allowed):
    item, _, profile, tools = use_context
    item.update(kind="mcp", owner_ref=configuration._server_id("Work"))
    profile["tool_policy_json"] = policy
    tools.append(SimpleNamespace(id="mcp_work_get_record", plugin_id=None, server_name="Work", enabled=True, source="mcp", parent_id="mcp"))
    assert advice()["eligible"] is allowed


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


def test_use_http_read_requires_session_and_preserves_chat_without_generation(service, use_context):
    _, workspace, _, _ = use_context
    with client_for(service) as client:
        _, headers = bootstrap(client)
        created = _command(client, headers, "conversation.create", {"title": "Lifecycle fixture"})
        assert created.status_code == 200, created.text
        conversation = created.json()["conversation_id"]
        workspace["conversation_id"] = conversation
        endpoint = f"/api/v1/conversations/{conversation}/integrations/skill:writing/use"
        assert client.get(endpoint).status_code in {401, 403}
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["eligible"] is True
        assert response.json()["conversation_id"] == conversation
        assert not service.registry.active()
        assert client.get("/api/v1/conversations/missing/integrations/skill:writing/use", headers=headers).status_code == 404


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
