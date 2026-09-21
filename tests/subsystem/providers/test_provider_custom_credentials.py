"""Custom credential publication and recovery with isolated canonical owners."""
from dataclasses import asdict
from uuid import uuid4

import pytest

from row_bot.application import provider_custom_credentials as controls
from row_bot.providers import auth_store, config, custom
from row_bot.runtime import admissions
from tests.subsystem.providers import test_provider_settings_controls as fixtures

store = fixtures.store
pytestmark = pytest.mark.subsystem
PROVIDER = "custom_openai_synthetic"


def endpoint():
    return {"id": "synthetic", "name": "Synthetic endpoint", "base_url": "http://127.0.0.1:8123/v1", "auth_required": True}


def start():
    custom.save_custom_endpoint({**endpoint(), "api_key": "old-synthetic-value"})


def command(operation="save", value="replacement-synthetic-value"):
    snapshot = controls.read_custom_provider_credentials(PROVIDER)
    return {"command_id": str(uuid4()), "type": f"provider.custom_credential.{operation}", "expected_revision": "0",
            "payload": {"provider_id": PROVIDER, "provider_revision": snapshot.revision, **({"value": value} if operation == "save" else {})}}


def execute(cmd, validate=lambda: None):
    return controls.execute_custom_provider_credentials(owner_id="synthetic-owner", key=cmd["command_id"], command=cmd,
        validate=validate, validate_review=lambda _: None)


def test_saved_review_redacted_and_no_network_or_write(store, monkeypatch):
    import httpx
    start()
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: pytest.fail("No probe"))
    before = config.CONFIG_PATH.read_bytes(), dict(store.values), len(store.writes)
    snapshot = controls.read_custom_provider_credentials(PROVIDER)
    reviewed = controls.review_custom_provider_credentials(PROVIDER, snapshot.revision, "save", "new-synthetic-value", validate=lambda: None)
    assert reviewed["snapshot"] == asdict(snapshot) and snapshot.runtime_state == "unknown"
    assert "synthetic-value" not in str(reviewed)
    assert before == (config.CONFIG_PATH.read_bytes(), store.values, len(store.writes))


@pytest.mark.parametrize("failure", ["write", "readback", "publication"])
def test_failure_preserves_endpoint_config_and_active_secret(store, monkeypatch, failure):
    start()
    before = config.CONFIG_PATH.read_bytes()
    if failure == "write":
        store.failure = True
    elif failure == "readback":
        store.corrupt = True
    else:
        monkeypatch.setattr(auth_store, "save_provider_config", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic publication failure")))
    with pytest.raises(Exception):
        custom.save_custom_endpoint({**endpoint(), "base_url": "http://127.0.0.1:8124/v1", "api_key": "new-synthetic-value"})
    assert config.CONFIG_PATH.read_bytes() == before
    store.failure = store.corrupt = False
    assert auth_store.get_provider_secret(PROVIDER) == "old-synthetic-value"
    assert not auth_store._session_provider_secrets


def test_new_endpoint_with_failed_key_never_published(store):
    before = config.CONFIG_PATH.read_bytes()
    store.failure = True
    with pytest.raises(Exception):
        start()
    assert config.CONFIG_PATH.read_bytes() == before
    assert custom.get_custom_endpoint("synthetic") is None


def test_no_auth_save_publication_failure_retains_original_configuration_and_key(store, monkeypatch):
    start()
    before = config.CONFIG_PATH.read_bytes()
    monkeypatch.setattr(auth_store, "save_provider_config", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic publication failure")))
    with pytest.raises(OSError):
        custom.save_custom_endpoint({**endpoint(), "auth_required": False})
    assert config.CONFIG_PATH.read_bytes() == before
    assert auth_store.get_provider_secret(PROVIDER) == "old-synthetic-value"


def test_probe_started_before_same_url_recreation_cannot_publish(store, monkeypatch):
    from types import SimpleNamespace
    start()
    def receive(*_a, **_kw):
        custom.delete_custom_endpoint_configuration("synthetic", expected_revision=config.provider_config_revision(config.load_provider_config()), validate=lambda: None, record_commit=lambda _: None)
        custom.save_custom_endpoint(endpoint(), manage_secret=False)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "stale-model"}]})
    monkeypatch.setattr(custom, "_probe_request", receive)
    with pytest.raises(custom.EndpointAuthorityError, match="revision_conflict"):
        custom.refresh_custom_endpoint_models("synthetic", strict=True)
    assert "models" not in custom.get_custom_endpoint("synthetic")


def test_save_clear_restore_retires_unreferenced_bytes_and_keeps_scope(store):
    start()
    before = dict(store.values)
    superseded = {key for key in before if key[1].startswith(f"providers:{PROVIDER}:api_key.g.")}
    assert superseded
    first_scope = config.load_provider_config()["custom_endpoints"][0]["credential_scope"]
    execute(command())
    assert auth_store.get_provider_secret(PROVIDER) == "replacement-synthetic-value"
    execute(command("clear"))
    assert auth_store.get_provider_secret(PROVIDER) == ""
    execute(command("restore"))
    assert auth_store.get_provider_secret(PROVIDER) == "replacement-synthetic-value"
    assert superseded.isdisjoint(store.values)
    assert config.load_provider_config()["custom_endpoints"][0]["credential_scope"] == first_scope


def test_delete_recreate_blocks_old_key_and_command_until_explicit_restore(store, monkeypatch):
    start()
    old_command = command()
    execute(old_command)
    old_scope = config.load_provider_config()["custom_endpoints"][0]["credential_scope"]
    custom.delete_custom_endpoint_configuration("synthetic", expected_revision=config.provider_config_revision(config.load_provider_config()), validate=lambda: None, record_commit=lambda cfg: None)
    custom.save_custom_endpoint(endpoint(), manage_secret=False)
    assert config.load_provider_config()["custom_endpoints"][0]["credential_scope"] != old_scope
    assert auth_store.get_provider_secret(PROVIDER) == ""
    snapshot = controls.read_custom_provider_credentials(PROVIDER)
    assert not snapshot.configured and snapshot.recovery_available
    with monkeypatch.context() as guarded:
        guarded.setattr(auth_store.secret_store, "get_secret", lambda *_a, **_kw: pytest.fail("Old command must not read secret"))
        guarded.setattr(auth_store.secret_store, "set_secret", lambda *_a, **_kw: pytest.fail("Old command must not write secret"))
        with pytest.raises(controls.CredentialControlError, match="revision_conflict"):
            execute(old_command)
    execute(command("restore"))
    assert auth_store.get_provider_secret(PROVIDER) == "replacement-synthetic-value"


def test_old_unsubmitted_review_after_recreation_rejects_before_secret_access(store, monkeypatch):
    start()
    old = command()
    custom.delete_custom_endpoint_configuration("synthetic", expected_revision=config.provider_config_revision(config.load_provider_config()), validate=lambda: None, record_commit=lambda _: None)
    start()
    monkeypatch.setattr(auth_store.secret_store, "get_secret", lambda *_a, **_kw: pytest.fail("Old review cannot read replacement key"))
    monkeypatch.setattr(auth_store.secret_store, "set_secret", lambda *_a, **_kw: pytest.fail("Old review cannot write replacement key"))
    with pytest.raises(controls.CredentialControlError, match="revision_conflict"):
        execute(old)


def test_endpoint_input_cannot_overwrite_scope_and_stale_review_rejects(store):
    start()
    original = command()
    scope = config.load_provider_config()["custom_endpoints"][0]["credential_scope"]
    custom.save_custom_endpoint({**endpoint(), "name": "Renamed", "credential_scope": "f" * 32}, manage_secret=False)
    assert config.load_provider_config()["custom_endpoints"][0]["credential_scope"] == scope
    before = dict(store.values)
    with pytest.raises(controls.CredentialControlError, match="revision_conflict"):
        execute(original)
    assert store.values == before


def test_receipt_failure_recovers_readback_proof_without_second_secret_write(store, monkeypatch):
    start()
    cmd = command()
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic receipt failure")))
    with pytest.raises(OSError):
        execute(cmd)
    writes = list(store.writes)
    monkeypatch.setattr(admissions, "complete_command", complete)
    assert execute(cmd)["status"] == "completed"
    assert store.writes == writes


@pytest.mark.parametrize("bad", ["custom_openai_bad:alias", "custom_openai_../bad", "openai", "synthetic", "custom_openai_absent"])
def test_exact_identity_required(store, bad):
    with pytest.raises(controls.CredentialControlError, match="not_found"):
        controls.read_custom_provider_credentials(bad)


def test_utf8_bound_and_revocation_during_staging_preserve_old_value(store):
    start()
    with pytest.raises(controls.CredentialControlError, match="invalid_command"):
        execute(command(value="é" * 8193))
    cmd = command(value="x" * 16384)
    writes = len(store.writes)
    def validate():
        if len(store.writes) > writes:
            raise controls.CredentialControlError("action_denied")
    with pytest.raises(controls.CredentialControlError, match="provider_credential_unconfirmed"):
        execute(cmd, validate)
    assert auth_store.get_provider_secret(PROVIDER) == "old-synthetic-value"


def test_storage_backend_error_cannot_echo_attempted_secret_into_logs_or_public_error(store, caplog):
    start()
    marker = "synthetic-credential-echo-marker"
    def failure(_service, _account, value):
        raise RuntimeError(f"Synthetic backend rejected {value}")
    store.set_password = failure
    with pytest.raises(controls.CredentialControlError) as error:
        execute(command(value=marker))
    assert marker not in str(error.value)
    assert marker not in caplog.text
    assert auth_store.get_provider_secret(PROVIDER) == "old-synthetic-value"


def test_legacy_endpoint_gets_scope_only_on_successful_staged_publication(store):
    cfg = config.load_provider_config()
    cfg["custom_endpoints"] = [custom.normalize_custom_endpoint(endpoint())]
    config.save_provider_config(cfg)
    assert "credential_scope" not in config.load_provider_config()["custom_endpoints"][0]
    controls.read_custom_provider_credentials(PROVIDER)
    assert "credential_scope" not in config.load_provider_config()["custom_endpoints"][0]
    execute(command())
    cfg = config.load_provider_config()
    assert cfg["custom_endpoints"][0]["credential_scope"] == cfg["providers"][PROVIDER]["credential_scope"]


def test_custom_receipt_is_passive_and_owner_scoped(store, monkeypatch):
    start()
    cmd = command()
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic receipt failure")))
    with pytest.raises(OSError):
        execute(cmd)
    before = config.CONFIG_PATH.read_bytes(), list(store.writes)
    monkeypatch.setattr(controls, "invalidate_provider_runtime", lambda: pytest.fail("No read invalidation"))
    result = controls.read_custom_provider_credentials_receipt(PROVIDER, owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None)
    assert result["published"] and result["status"] == "completed"
    assert result["credential"]["provider_id"] == PROVIDER
    assert before == (config.CONFIG_PATH.read_bytes(), store.writes)
    assert controls.read_custom_provider_credentials_receipt(PROVIDER, owner_id="other", command_id=cmd["command_id"], validate=lambda: None) is None


@pytest.mark.parametrize("editing", [False, True])
def test_actual_nicegui_save_callback_reports_failure_without_refresh_or_success(store, editing):
    import ast
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    # Execute the actual nested callback with deterministic presentation controls.
    # No NiceGUI server, real provider, browser, or default environment is started.
    source = Path("src/row_bot/ui/provider_settings.py").read_text()
    tree = ast.parse(source)
    section = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_custom_endpoints_section")
    callback = next(node for node in ast.walk(section) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == ("_save_edit" if editing else "_save"))
    module = ast.Module(body=[callback], type_ignores=[])
    notifications = []
    def fail_save(_payload):
        raise OSError("synthetic-private-error")
    def effect(*_args, **_kwargs):
        pytest.fail("No effect after failed save")
    values = {"name_input": "Retained draft", "base_url_input": "http://127.0.0.1/v1", "no_auth_input": False,
              "api_key_input": "synthetic-private-key", "vision_mode": "auto", "tool_mode": "auto", "context_input": "",
              "reasoning_mode": "auto", "thinking_budget": "", "extra_body_json": "{}", "supports_reasoning_content": False,
              "supports_reasoning_replay": False, "no_auth": False, "location": "local", "profile": "generic_openai",
              "vision_override": "auto", "tool_override": "auto", "context_override": ""}
    context = {key: SimpleNamespace(value=value) for key, value in values.items()}
    context.update(endpoint=endpoint(), save_custom_endpoint=fail_save, on_change=effect,
        refresh_custom_endpoint_models=effect, run=SimpleNamespace(io_bound=effect),
        ui=SimpleNamespace(notify=lambda *args, **kwargs: notifications.append((args, kwargs)), notification=effect),
        _custom_endpoint_edit_payload=lambda *_a, **_kw: (endpoint(), True),
        _manual_capabilities_from_ui=lambda *_a: {}, _parse_custom_extra_body=lambda *_a: {})
    exec(compile(module, "actual-provider-callback", "exec"), context)
    result = context[callback.name]()
    if editing:
        asyncio.run(result)
    assert len(notifications) == 1 and notifications[0][1]["type"] == "negative"
    assert "synthetic-private" not in str(notifications)
    assert "could not be saved" in notifications[0][0][0]
    assert context["name_input"].value == "Retained draft" and context["api_key_input"].value == "synthetic-private-key"
