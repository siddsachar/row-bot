"""Credential commands use isolated receipts and a fake secret backend."""
import json
from uuid import uuid4

import pytest

from row_bot.providers import credential_controls as controls
from row_bot.runtime import admissions

pytestmark = pytest.mark.subsystem


@pytest.fixture
def credentials(tmp_path, monkeypatch):
    from row_bot import tasks
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    state = {"value": "", "source": "keyring", "external": False, "revision": 0}
    writes = []

    def status(*_args):
        return {"configured": bool(state["value"]), "source": state["source"],
                "fingerprint": state["value"][-4:], "externally_managed": state["external"]}

    def save(_provider, value, **_kwargs):
        state["value"] = value or ""
        state["revision"] += 1
        writes.append(("save", value))

    def clear(*_args):
        state["value"] = ""
        state["revision"] += 1
        writes.append(("clear", ""))

    monkeypatch.setattr(controls.auth_store, "provider_secret_status", status)
    monkeypatch.setattr(controls.auth_store, "replace_provider_api_key", save)
    monkeypatch.setattr(controls.auth_store, "delete_provider_secret", clear)
    monkeypatch.setattr(controls, "load_provider_config", lambda **_kwargs: {"providers": {"openai": {"revision": state["revision"]}}})
    return state, writes


def command(kind="save", value="synthetic-private-key"):
    return {"command_id": str(uuid4()), "type": f"provider.credential.{kind}",
            "expected_revision": controls.credential_snapshot("openai")["revision"],
            "payload": {"value": value} if kind == "save" else {}}


def apply(value, **kwargs):
    return controls.apply_credential_command("openai", owner_id="fixture", key=value["command_id"],
        command=value, validate=kwargs.get("validate", lambda: None), invalidate=kwargs.get("invalidate", lambda: None))


def test_save_replay_and_clear_use_existing_content_free_receipts(credentials):
    state, writes = credentials
    value = command()
    result = apply(value)
    assert result["credential"]["configured"]
    count = len(writes)
    assert apply(value) == result and len(writes) == count
    with admissions.transaction() as conn:
        saved = [dict(row) for row in conn.execute("SELECT * FROM client_commands")]
    assert value["payload"]["value"] not in json.dumps(saved)
    assert "-key" not in json.dumps(result)
    assert apply(command("clear"))["credential"]["configured"] is False
    assert state["value"] == ""


def test_same_identity_cannot_replace_payload(credentials):
    value = command()
    apply(value)
    value["payload"]["value"] = "different-secret"
    with pytest.raises(controls.CredentialControlError, match="idempotency_mismatch"):
        apply(value)


def test_stale_or_externally_managed_configuration_cannot_write(credentials):
    state, writes = credentials
    value = command()
    state["revision"] += 1
    with pytest.raises(controls.CredentialControlError, match="revision_conflict"):
        apply(value)
    state["external"] = True
    with pytest.raises(controls.CredentialControlError, match="action_denied"):
        apply(command())
    assert writes == []


def test_revocation_at_dispatch_preserves_secret_and_does_not_replay(credentials):
    state, writes = credentials
    calls = []

    def validate():
        calls.append(True)
        if len(calls) == 2:
            raise controls.CredentialControlError("authentication_required")

    with pytest.raises(controls.CredentialControlError, match="authentication_required"):
        apply(command(), validate=validate)
    assert writes == [] and state["value"] == ""


def test_partial_store_failure_stays_uncertain(credentials, monkeypatch):
    _state, writes = credentials
    value = command()
    monkeypatch.setattr(controls.auth_store, "replace_provider_api_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private-backend-detail")))
    with pytest.raises(controls.CredentialControlError, match="provider_credential_unconfirmed"):
        apply(value)
    count = len(writes)
    with pytest.raises(controls.CredentialControlError, match="operation_uncertain"):
        apply(value)
    assert len(writes) == count
    assert "private-backend-detail" not in json.dumps(admissions.receipt("fixture", value["command_id"]))


@pytest.mark.parametrize("value", ["", "\n", "a\nb", "a" * 16385, None])
def test_invalid_secret_is_rejected_before_admission(credentials, value):
    with pytest.raises(controls.CredentialControlError, match="invalid_command"):
        apply(command(value=value))
    assert credentials[1] == []


def test_subscription_provider_cannot_receive_an_api_key(credentials):
    with pytest.raises(controls.CredentialControlError, match="not_found"):
        controls.credential_snapshot("codex")
