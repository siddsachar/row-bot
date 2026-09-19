"""Credential command adapter with canonical stores and synthetic secrets."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import provider_settings_commands as commands
from row_bot.providers import auth_store, config, credential_controls
from row_bot.runtime import admissions
from tests.subsystem.providers import test_provider_settings_controls as fixtures

store = fixtures.store

pytestmark = pytest.mark.subsystem


def intent(operation="save", value="synthetic-replacement"):
    return {"command_id": str(uuid4()), "client_session_id": "synthetic-session", "expected_revision": "0",
            "type": "provider.credential." + operation, "payload": {"provider_id": "openai",
            "provider_revision": credential_controls.credential_snapshot("openai")["revision"],
            **({"value": value} if operation == "save" else {})}}


def review(value):
    payload = value["payload"]
    return commands.review_provider_settings_command("openai", payload["provider_revision"],
        value["type"].split(".")[-1], payload.get("value"), validate=lambda: None)


def execute(value, reviewed=None, **kwargs):
    reviewed = reviewed or review(value)
    def validate_review(actual):
        assert actual == {key: reviewed[key] for key in actual}
    return commands.execute_provider_settings_command(owner_id="owner", key=value["command_id"], command=value,
        validate=kwargs.get("validate", lambda: None), validate_review=kwargs.get("validate_review", validate_review),
        invalidate=kwargs.get("invalidate", lambda: None))


def receipt(value, owner="owner", provider="openai"):
    return commands.read_provider_settings_receipt(provider, owner_id=owner, command_id=value["command_id"], validate=lambda: None)


def test_review_exact_input_and_redacted_three_actions(store):
    original = Path(config.CONFIG_PATH).read_bytes()
    value = intent()
    first = review(value)
    value["payload"]["value"] += " "
    assert review(value)["action_digest"] != first["action_digest"]
    assert "synthetic-replacement" not in json.dumps(first)
    assert Path(config.CONFIG_PATH).read_bytes() == original and store.writes == []
    execute(value)
    for operation in ("clear", "restore"):
        command = intent(operation)
        execute(command)
        assert receipt(command)["status"] == "completed"
    assert auth_store.get_provider_secret("openai") == "synthetic-replacement"


@pytest.mark.parametrize("secret", ["é" * 8193, "x" * 16385, "\ud800"], ids=["utf8-limit", "ascii-limit", "invalid-unicode"])
def test_byte_limit_before_admission_or_secret_store(store, secret):
    value = intent(value=secret)
    with pytest.raises(credential_controls.CredentialControlError, match="invalid_command"):
        execute(value)
    assert store.writes == []


def test_modified_review_and_late_store_revocation_do_not_publish(store, monkeypatch):
    value = intent()
    reviewed = review(value)
    value["payload"]["value"] += "other"
    with pytest.raises(AssertionError):
        execute(value, reviewed)
    assert store.writes == []
    original = store.set_password
    revoked = False
    def write(*args):
        nonlocal revoked
        original(*args)
        revoked = True
    monkeypatch.setattr(store, "set_password", write)
    def validate():
        if revoked:
            raise credential_controls.CredentialControlError("action_denied")
    with pytest.raises(credential_controls.CredentialControlError, match="provider_credential_unconfirmed"):
        execute(value, validate=validate)
    assert auth_store.get_provider_secret("openai") == "old-synthetic-secret"
    assert receipt(value)["status"] == "uncertain"


def test_passive_receipt_recovers_publication_without_writes_or_names(store, monkeypatch):
    value = intent()
    reviewed = review(value)
    monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError("private failure")))
    with pytest.raises(OSError):
        execute(value, reviewed)
    writes = list(store.writes)
    before = Path(config.CONFIG_PATH).read_bytes()
    monkeypatch.setattr(admissions, "transaction", lambda: (_ for _ in ()).throw(AssertionError("GET must not initialize")))
    result = receipt(value)
    assert result["status"] == "completed" and result["published"]
    assert "display_name" not in json.dumps(result) and "synthetic-replacement" not in json.dumps(result)
    assert receipt(value, owner="different") is None and receipt(value, provider="anthropic") is None
    assert store.writes == writes and Path(config.CONFIG_PATH).read_bytes() == before


def test_replay_reconciles_only_original_publication(store, monkeypatch):
    value = intent()
    reviewed = review(value)
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        execute(value, reviewed)
    count = len(store.writes)
    monkeypatch.setattr(admissions, "complete_command", original)
    assert execute(value, reviewed)["status"] == "completed"
    assert execute(value, reviewed)["status"] == "completed"
    assert len(store.writes) == count


def test_cache_invalidation_only_calls_existing_loaded_owners(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "row_bot.agent", SimpleNamespace(clear_agent_cache=lambda: calls.append("agent")))
    monkeypatch.setitem(sys.modules, "row_bot.models", SimpleNamespace(clear_llm_cache=lambda: calls.append("models")))
    commands.invalidate_provider_runtime()
    assert calls == ["agent", "models"]
    monkeypatch.delitem(sys.modules, "row_bot.agent")
    monkeypatch.delitem(sys.modules, "row_bot.models")
    commands.invalidate_provider_runtime()
    assert "row_bot.agent" not in sys.modules and "row_bot.models" not in sys.modules


def test_passive_missing_receipt_never_creates_directory(store, tmp_path, monkeypatch):
    from row_bot import tasks
    path = tmp_path / "absent" / "tasks.db"
    monkeypatch.setattr(tasks, "_DB_PATH", str(path))
    assert commands.read_provider_settings_receipt("openai", owner_id="owner", command_id="absent", validate=lambda: None) is None
    assert not path.parent.exists()


def test_receipt_rechecks_authority_after_saved_status_read(store, monkeypatch):
    value = intent()
    execute(value)
    original = credential_controls.credential_snapshot
    revoked = False
    def snapshot(provider):
        nonlocal revoked
        result = original(provider)
        revoked = True
        return result
    monkeypatch.setattr(credential_controls, "credential_snapshot", snapshot)
    def validate():
        if revoked:
            raise credential_controls.CredentialControlError("action_denied")
    with pytest.raises(credential_controls.CredentialControlError, match="action_denied"):
        commands.read_provider_settings_receipt("openai", owner_id="owner", command_id=value["command_id"], validate=validate)
