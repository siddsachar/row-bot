"""Actual credential owners with an isolated deterministic secret backend."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot import api_keys, secret_store, tasks
from row_bot.application import provider_settings_controls as settings
from row_bot.providers import auth_store, config, credential_controls as controls
from row_bot.runtime import admissions

pytestmark = pytest.mark.subsystem


class MemorySecrets:
    def __init__(self):
        self.values = {}
        self.writes = []
        self.failure = False
        self.corrupt = False

    def get_password(self, service, account):
        value = self.values.get((service, account))
        return "wrong-local-value" if self.corrupt and ".g." in account else value

    def set_password(self, service, account, value):
        self.writes.append(account)
        if self.failure:
            raise RuntimeError("synthetic private backend message")
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    backend = MemorySecrets()
    monkeypatch.setattr(secret_store, "_backend_override", backend)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "KEYS_PATH", tmp_path / "api_keys.json")
    monkeypatch.setattr(api_keys, "_session_keys", {})
    monkeypatch.setattr(auth_store, "_session_provider_secrets", {})
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    for name in auth_store.PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    for name in ("ROW_BOT_SECRETS_DIR", "ROW_BOT_SECRET_STORE_KEY", "ROW_BOT_DEPLOYMENT_MODE"):
        monkeypatch.delenv(name, raising=False)
    auth_store.set_provider_secret("openai", "api_key", "old-synthetic-secret")
    backend.writes.clear()
    return backend


def command(operation="save", value="new-synthetic-secret"):
    return {"command_id": str(uuid4()), "type": f"provider.credential.{operation}",
            "expected_revision": controls.credential_snapshot("openai")["revision"],
            "payload": {"value": value} if operation == "save" else {}}


def apply(command, **kwargs):
    return controls.apply_credential_command("openai", owner_id="isolated-owner", key=command["command_id"],
        command=command, validate=kwargs.get("validate", lambda: None),
        invalidate=kwargs.get("invalidate", lambda: None))


def test_passive_review_redacts_secrets_and_never_writes(store):
    before = Path(config.CONFIG_PATH).read_bytes()
    snapshot = settings.read_provider_settings("openai")
    reviewed = settings.review_provider_credential("openai", snapshot.revision, "save", "replacement-synthetic-secret")
    assert reviewed == snapshot and reviewed.runtime_state == "unknown"
    assert store.writes == [] and Path(config.CONFIG_PATH).read_bytes() == before
    public = json.dumps(asdict(snapshot))
    assert "secret" not in public and "cret" not in public


@pytest.mark.parametrize("failure", ["write", "readback", "publication"])
def test_failed_replacement_preserves_original_bytes_and_effective_value(store, monkeypatch, failure):
    before = Path(config.CONFIG_PATH).read_bytes()
    stored_before = dict(store.values)
    if failure == "write":
        store.failure = True
    elif failure == "readback":
        store.corrupt = True
    else:
        monkeypatch.setattr(auth_store, "save_provider_config", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private failure")))
    value = command()
    with pytest.raises(controls.CredentialControlError, match="provider_credential_unconfirmed"):
        apply(value)
    assert auth_store.get_provider_secret("openai") == "old-synthetic-secret"
    assert Path(config.CONFIG_PATH).read_bytes() == before
    assert store.values == stored_before
    assert auth_store._session_provider_secrets == {}
    with pytest.raises(controls.CredentialControlError, match="operation_uncertain"):
        apply(value)


def test_verified_replacement_clear_and_explicit_previous_recovery(store):
    first = apply(command())
    assert first["credential"]["configured"]
    assert auth_store.get_provider_secret("openai") == "new-synthetic-secret"
    assert any(value == "old-synthetic-secret" for value in store.values.values())
    apply(command("clear"))
    assert auth_store.get_provider_secret("openai") == ""
    assert controls.credential_snapshot("openai")["recovery_available"]
    apply(command("restore"))
    assert auth_store.get_provider_secret("openai") == "new-synthetic-secret"
    public = Path(config.CONFIG_PATH).read_text()
    assert "new-synthetic-secret" not in public and "old-synthetic-secret" not in public


def test_restore_original_legacy_after_first_replacement(store):
    apply(command())
    apply(command("restore"))
    assert auth_store.get_provider_secret("openai") == "old-synthetic-secret"


def test_repeated_replacements_retain_only_current_and_previous_generations(store):
    apply(command(value="first-synthetic-secret"))
    apply(command(value="second-synthetic-secret"))
    apply(command(value="third-synthetic-secret"))

    generation_accounts = {account for _service, account in store.values
                           if account.startswith("providers:openai:api_key.g.")}
    assert len(generation_accounts) == 2
    assert "first-synthetic-secret" not in store.values.values()
    assert auth_store.get_provider_secret("openai") == "third-synthetic-secret"


def test_crash_after_config_publication_recovers_original_receipt_without_second_write(store, monkeypatch):
    value = command()
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_args: (_ for _ in ()).throw(OSError("lost receipt")))
    with pytest.raises(OSError):
        apply(value)
    count = len(store.writes)
    monkeypatch.setattr(admissions, "complete_command", original)
    assert apply(value)["status"] == "completed"
    assert len(store.writes) == count
    with admissions.transaction() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM client_commands")]
    assert "new-synthetic-secret" not in json.dumps(rows)


def test_stale_review_and_revocation_before_pointer_publication_keep_old_key(store):
    value = command()
    calls = 0

    def validate():
        nonlocal calls
        calls += 1
        if calls == 5:
            raise controls.CredentialControlError("action_denied")

    before = Path(config.CONFIG_PATH).read_bytes()
    with pytest.raises(controls.CredentialControlError):
        apply(value, validate=validate)
    assert Path(config.CONFIG_PATH).read_bytes() == before
    assert auth_store.get_provider_secret("openai") == "old-synthetic-secret"
    config.update_provider_config(lambda cfg: cfg["providers"]["openai"].update({"enabled": False}))
    with pytest.raises(controls.CredentialControlError, match="revision_conflict"):
        settings.review_provider_credential("openai", value["expected_revision"], "clear")


def test_external_environment_is_read_only_and_precedes_active_generation(store, monkeypatch):
    apply(command())
    monkeypatch.setenv("OPENAI_API_KEY", "external-synthetic-secret")
    snapshot = settings.read_provider_settings("openai")
    assert snapshot.externally_managed and snapshot.source == "environment"
    assert auth_store.get_provider_secret("openai") == "external-synthetic-secret"
    count = len(store.writes)
    with pytest.raises(controls.CredentialControlError, match="action_denied"):
        apply(command())
    assert len(store.writes) == count


def test_config_cas_preserves_unrelated_owner_and_corrupt_file(store):
    captured = config.load_provider_config()
    revision = config.provider_config_revision(captured)
    config.update_provider_config(lambda cfg: cfg.update({"other_owner": {"retained": True}}))
    with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
        config.save_provider_config(captured, expected_revision=revision)
    assert config.load_provider_config()["other_owner"] == {"retained": True}
    Path(config.CONFIG_PATH).write_text("{broken saved config", encoding="utf-8")
    with pytest.raises(config.ProviderConfigError, match="provider_settings_unavailable"):
        config.update_provider_config(lambda cfg: cfg.clear())
    assert Path(config.CONFIG_PATH).read_text() == "{broken saved config"


def test_maximum_sized_secret_is_verified_in_bounded_chunks(store):
    apply(command(value="a" * 16384))
    assert len(store.writes) == 32
    assert auth_store.get_provider_secret("openai") == "a" * 16384


def test_nested_config_owner_cannot_silently_overwrite_a_newer_save(store):
    def outer(cfg):
        cfg["outer"] = "not committed"
        config.update_provider_config(lambda latest: latest.update({"inner": "preserved"}))

    with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
        config.update_provider_config(outer)
    current = config.load_provider_config()
    assert current["inner"] == "preserved" and "outer" not in current


def test_authority_is_rechecked_after_writer_admission_before_any_secret_write(store, monkeypatch):
    from contextlib import contextmanager

    admitted = False
    original = controls.provider_config_transaction

    @contextmanager
    def delayed_writer():
        nonlocal admitted
        with original():
            admitted = True
            yield

    monkeypatch.setattr(controls, "provider_config_transaction", delayed_writer)

    def validate():
        if admitted:
            raise controls.CredentialControlError("action_denied")

    with pytest.raises(controls.CredentialControlError, match="action_denied"):
        apply(command(), validate=validate)
    assert not store.writes


def test_legacy_env_projection_retires_only_after_successful_publication(store, monkeypatch):
    api_keys.set_key("OPENAI_API_KEY", "legacy-synthetic-key")
    original = auth_store.save_provider_config
    monkeypatch.setattr(auth_store, "save_provider_config", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("failure")))
    with pytest.raises(controls.CredentialControlError):
        apply(command())
    assert auth_store.get_provider_secret("openai") == "legacy-synthetic-key"
    monkeypatch.setattr(auth_store, "save_provider_config", original)
    apply(command())
    assert auth_store.get_provider_secret("openai") == "new-synthetic-secret"
    apply(command("restore"))
    assert auth_store.get_provider_secret("openai") == "legacy-synthetic-key"


def test_a_later_owner_change_never_reconciles_an_older_uncertain_command_as_current(store, monkeypatch):
    value = command()
    original = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_args: (_ for _ in ()).throw(OSError("lost receipt")))
    with pytest.raises(OSError):
        apply(value)
    monkeypatch.setattr(admissions, "complete_command", original)
    apply(command(value="later-explicit-key"))
    count = len(store.writes)
    with pytest.raises(controls.CredentialControlError, match="operation_uncertain"):
        apply(value)
    assert auth_store.get_provider_secret("openai") == "later-explicit-key"
    assert len(store.writes) == count


def test_actual_second_process_cannot_enter_owned_config_writer(store):
    script = """
import pathlib, sys
from row_bot.providers import config
config.CONFIG_PATH = pathlib.Path(sys.argv[1])
try:
    config.update_provider_config(lambda cfg: cfg.update({'child_owner': 'committed'}))
except config.ProviderConfigError as exc:
    print(exc.code)
else:
    print('committed')
"""
    with config.provider_config_transaction():
        result = subprocess.run([sys.executable, "-c", script, str(config.CONFIG_PATH)],
                                text=True, capture_output=True, timeout=10, check=True)
        assert result.stdout.strip() == "provider_settings_busy"
        assert "child_owner" not in config.load_provider_config()
    result = subprocess.run([sys.executable, "-c", script, str(config.CONFIG_PATH)],
                            text=True, capture_output=True, timeout=10, check=True)
    assert result.stdout.strip() == "committed"
    assert config.load_provider_config()["child_owner"] == "committed"


def test_environment_override_appearing_after_staging_prevents_publication(store, monkeypatch):
    calls = 0

    def validate():
        nonlocal calls
        calls += 1
        if calls == 5:
            monkeypatch.setenv("OPENAI_API_KEY", "late-external-synthetic")

    before = Path(config.CONFIG_PATH).read_bytes()
    with pytest.raises(controls.CredentialControlError, match="provider_credential_unconfirmed"):
        apply(command(), validate=validate)
    assert Path(config.CONFIG_PATH).read_bytes() == before
    assert auth_store.get_provider_secret("openai") == "late-external-synthetic"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert auth_store.get_provider_secret("openai") == "old-synthetic-secret"


def test_corrupt_current_generation_can_explicitly_restore_verified_previous_value(store):
    apply(command())
    ref = config.load_provider_config()["providers"]["openai"]["credential_ref"]
    key = next(key for key in store.values if ref["generation"] in key[1])
    del store.values[key]
    snapshot = settings.read_provider_settings("openai")
    assert snapshot.storage_unavailable and snapshot.recovery_available
    settings.review_provider_credential("openai", snapshot.revision, "restore")
    apply(command("restore"))
    assert auth_store.get_provider_secret("openai") == "old-synthetic-secret"
