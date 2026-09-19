"""Saved endpoint and picker controls against isolated canonical owners."""
from dataclasses import asdict
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import provider_configuration_controls as controls
from row_bot.providers import auth_store, config, custom, selection
from row_bot.runtime import admissions
from tests.subsystem.providers import test_provider_settings_controls as fixtures

store = fixtures.store
pytestmark = pytest.mark.subsystem


def fields(endpoint_id="synthetic"):
    return asdict(controls.ProviderEndpointFields(endpoint_id, "Synthetic endpoint", "http://127.0.0.1:8123/v1"))


def command(operation=None, value=None):
    if operation is None:
        operation = "provider.endpoint.save" if custom.get_custom_endpoint((value or fields())["endpoint_id"]) else "provider.endpoint.create"
    return {"command_id": str(uuid4()), "type": operation, "expected_revision": "0", "payload": {
        "configuration_revision": config.provider_config_revision(config.load_provider_config(strict=True)),
        "fields": fields() if value is None else value}}


def execute(value, **kwargs):
    return controls.execute_provider_configuration(owner_id="owner", key=value["command_id"], command=value,
        validate=kwargs.get("validate", lambda: None), validate_review=kwargs.get("validate_review", lambda _: None))


def test_saved_editor_no_network_or_secret_reads_and_preserves_unowned_metadata(store, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: pytest.fail("No implicit network"))
    monkeypatch.setattr(auth_store, "replace_provider_api_key", lambda *_a, **_k: pytest.fail("No credential writes"))
    monkeypatch.setattr(custom, "delete_provider_secret", lambda *_a, **_k: pytest.fail("No credential deletion"))
    first = command()
    execute(first)
    cfg = config.load_provider_config(strict=True)
    cfg["custom_endpoints"][0]["legacy_metadata"] = {"preserve": True}
    cfg["custom_endpoints"][0]["last_probe"] = {"classification": "agent_ready"}
    cfg["custom_endpoints"][0]["models"] = [{"id": "saved"}]
    cfg["unrelated"] = {"preserve": True}
    config.save_provider_config(cfg)
    changed = fields()
    changed["reasoning_mode"] = "on"
    execute(command(value=changed))
    result = config.load_provider_config(strict=True)
    assert result["unrelated"] == {"preserve": True}
    endpoint = result["custom_endpoints"][0]
    assert endpoint["legacy_metadata"] == {"preserve": True}
    assert "last_probe" not in endpoint and "models" not in endpoint
    snapshot = controls.read_provider_configuration()
    assert snapshot.items[0].probe_state == "unknown" and snapshot.items[0].model_count is None
    assert snapshot.items[0].runtime_state == "unknown"
    assert store.writes == []


@pytest.mark.parametrize("patch", [{"base_url": "https://name:private@example.invalid/v1"}, {"base_url": "https://example.invalid/v1?token=private"}, {"extra_body_json": '{"nested":{"api_key":"private"}}'}, {"context_window": True}, {"thinking_budget": -1}, {"enabled": "false"}, {"endpoint_id": "../escape"}, {"profile": "unknown"}], ids=["userinfo", "query", "nested-secret", "bool-context", "negative-budget", "bool-type", "bad-id", "profile"])
def test_invalid_configuration_never_saves(store, patch):
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError):
        execute(command(value={**fields(), **patch}))
    assert config.CONFIG_PATH.read_bytes() == before and store.writes == []


def test_collision_identity_stale_revision_and_nonce_binding(store):
    first = command()
    execute(first)
    with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
        execute({**first, "command_id": str(uuid4())})
    with pytest.raises(config.ProviderConfigError, match="provider_endpoint_identity_conflict"):
        execute(command(value={**fields(), "execution_location": "remote"}))
    calls = []
    execute(command(value={**fields(), "enabled": False}), validate_review=lambda review: calls.append(review))
    assert len(calls) >= 3 and all(item == calls[0] for item in calls)
    assert custom.get_custom_endpoint("synthetic")["enabled"] is False


def test_receipt_failure_proves_publication_without_second_save(store, monkeypatch):
    value = command()
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError("receipt failed")))
    with pytest.raises(OSError):
        execute(value)
    before = config.CONFIG_PATH.read_bytes()
    monkeypatch.setattr(admissions, "complete_command", complete)
    assert execute(value)["status"] == "completed"
    assert execute(value)["status"] == "completed"
    assert config.CONFIG_PATH.read_bytes() == before


def test_delete_removes_exact_provider_pins_and_retains_credentials_and_other_references(store, monkeypatch):
    execute(command())
    cfg = config.load_provider_config(strict=True)
    cfg["quick_choices"] = [{"id": "model:custom_openai_synthetic:same", "provider_id": "custom_openai_synthetic", "model_id": "same"}, {"id": "model:openai:same", "provider_id": "openai", "model_id": "same"}]
    config.save_provider_config(cfg)
    monkeypatch.setattr(custom, "delete_provider_secret", lambda *_: pytest.fail("Retain secret bytes"))
    execute(command("provider.endpoint.delete", {"endpoint_id": "synthetic"}))
    assert config.load_provider_config(strict=True)["quick_choices"] == [cfg["quick_choices"][1]]
    assert custom.get_custom_endpoint("synthetic") is None


def test_page_search_covers_library_and_revision_rejects_stale_cursor(store):
    cfg = config.load_provider_config(strict=True)
    cfg["custom_endpoints"] = [custom.normalize_custom_endpoint({"id": f"saved-{i}", "name": f"Saved {i}", "base_url": "http://127.0.0.1/v1", "execution_location": "local"}) for i in range(41)]
    config.save_provider_config(cfg)
    page = controls.read_provider_configuration()
    assert len(page.items) == 20 and page.total == 41 and page.next_cursor
    assert controls.read_provider_configuration(query="saved-40").items[0].fields.endpoint_id == "saved-40"
    assert len(controls.read_provider_configuration(cursor=page.next_cursor).items) == 20
    config.update_provider_config(lambda value: value.update(unrelated=True))
    with pytest.raises(config.ProviderConfigError, match="cursor_expired"):
        controls.read_provider_configuration(cursor=page.next_cursor)


def test_probe_target_edit_prevents_old_url_models_from_publication(store, monkeypatch):
    import httpx
    execute(command())
    def get(*_args, **_kwargs):
        config.update_provider_config(lambda cfg: cfg["custom_endpoints"][0].update(base_url="http://127.0.0.1:8124/v1"))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "stale-model"}]})
    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(custom.EndpointAuthorityError, match="revision_conflict"):
        custom.refresh_custom_endpoint_models("synthetic")
    assert "models" not in custom.get_custom_endpoint("synthetic")


def test_explicit_probe_cancellation_stops_before_next_network_effect(store, monkeypatch):
    import httpx
    execute(command())
    calls = []
    revoked = False
    def get(*_args, **_kwargs):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "synthetic-model"}]})
    def post(*_args, **_kwargs):
        nonlocal revoked
        calls.append("post")
        revoked = True
        return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(custom, "_probe_request", lambda method, *args, **kwargs: get() if method == "GET" else post())
    def validate():
        if revoked:
            raise config.ProviderConfigError("action_denied")
    with pytest.raises(custom.EndpointAuthorityError, match="action_denied"):
        execute(command("provider.endpoint.probe", {"endpoint_id": "synthetic"}), validate=validate)
    assert calls == ["post"]
    assert "last_probe" not in custom.get_custom_endpoint("synthetic")


@pytest.mark.parametrize("outcome", ["oversized", "blocked", "chunks", "revoked", "compressed"])
def test_strict_response_stream_is_bounded_cancelled_and_closed(store, monkeypatch, outcome):
    import asyncio
    import httpx
    from contextlib import asynccontextmanager
    closed = []
    checks = []
    class Response:
        status_code = 200
        headers = {"content-encoding": "gzip"} if outcome == "compressed" else {}
        request = httpx.Request("GET", "http://synthetic.invalid/models")
        def raise_for_status(self):
            pass
        async def aiter_raw(self):
            if outcome == "blocked":
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.append("cancelled-reader")
            elif outcome == "oversized":
                yield b"x" * 33
            else:
                yield b""
                yield b'{"data":'
                yield b"[]}"
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"follow_redirects": False}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            closed.append("client")
        @asynccontextmanager
        async def stream(self, *_args, **kwargs):
            assert 0 < kwargs["timeout"] <= .02
            assert kwargs["headers"]["Accept-Encoding"] == "identity"
            try:
                yield Response()
            finally:
                closed.append("response")
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    def validate():
        checks.append(True)
        if outcome == "revoked" and len(checks) >= 3:
            raise custom.EndpointAuthorityError("action_denied")
    if outcome == "chunks":
        result = custom._probe_request("GET", "http://synthetic.invalid/models", headers={}, timeout=.02,
            validate=validate, strict=True, maximum_bytes=32)
        assert result.json() == {"data": []} and len(checks) >= 4
    else:
        with pytest.raises(custom.EndpointAuthorityError, match={"oversized": "provider_response_too_large", "blocked": "provider_probe_limit", "revoked": "action_denied", "compressed": "provider_response_encoding_unsupported"}[outcome]):
            custom._probe_request("GET", "http://synthetic.invalid/models", headers={}, timeout=.02,
                validate=validate, strict=True, maximum_bytes=32)
    assert closed[-2:] == ["response", "client"]
    if outcome == "blocked":
        assert closed[0] == "cancelled-reader"


def test_model_pin_preserves_exact_provider_and_other_surface_groups(store, monkeypatch):
    from row_bot.providers import model_catalog_cache
    monkeypatch.setattr(model_catalog_cache, "read_model_catalog_cache", lambda **kw: SimpleNamespace(cloud_cache={"model:openai:shared": {"provider": "openai", "model_id": "shared", "label": "Shared", "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text", "image"], "output_modalities": ["text"], "tool_calling": True}}}, ollama_rows=[]))
    for surface in ("chat", "vision"):
        execute(command("provider.model.pin", {"provider_id": "openai", "model_id": "shared", "surface": surface}))
    execute(command("provider.model.unpin", {"provider_id": "openai", "model_id": "shared", "surface": "vision"}))
    quick = config.load_provider_config(strict=True)["quick_choices"]
    assert len(quick) == 1 and quick[0]["id"] == "model:openai:shared"
    assert set(quick[0]["visibility"]) == set(selection.CHAT_VISIBILITY)
    with pytest.raises(config.ProviderConfigError, match="model_configuration_unavailable"):
        execute(command("provider.model.pin", {"provider_id": "anthropic", "model_id": "shared", "surface": "chat"}))


def test_create_collision_and_save_missing_never_overwrite(store):
    execute(command())
    before = config.CONFIG_PATH.read_bytes()
    with pytest.raises(config.ProviderConfigError, match="provider_endpoint_identity_conflict"):
        execute(command("provider.endpoint.create", {**fields(), "display_name": "Overwrite"}))
    with pytest.raises(config.ProviderConfigError, match="not_found"):
        execute(command("provider.endpoint.save", fields("missing")))
    assert config.CONFIG_PATH.read_bytes() == before


def test_receipt_read_is_passive_exact_owner_and_publication_bound(store, monkeypatch):
    from row_bot import tasks
    import sqlite3
    value = command()
    monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError("receipt unavailable")))
    with pytest.raises(OSError):
        execute(value)
    before = config.CONFIG_PATH.read_bytes()
    monkeypatch.setattr(config, "save_provider_config", lambda *_a, **_k: pytest.fail("Passive read"))
    monkeypatch.setattr(controls, "invalidate_provider_runtime", lambda: pytest.fail("Passive read"))
    def read(owner="owner"):
        return controls.read_provider_configuration_receipt(owner_id=owner, command_id=value["command_id"], validate=lambda: None)
    result = read()
    assert result == {"command_id": value["command_id"], "status": "completed", "configuration_revision": config.provider_config_revision(config.load_provider_config(strict=True))}
    assert read("other") is None and config.CONFIG_PATH.read_bytes() == before
    with sqlite3.connect(tasks._DB_PATH) as conn:
        assert conn.execute("SELECT status FROM client_commands WHERE command_id=?", (value["command_id"],)).fetchone()[0] != "completed"
        conn.execute("UPDATE client_commands SET type='task.create' WHERE command_id=?", (value["command_id"],))
    assert read() is None


def test_unpublished_receipt_remains_uncertain_and_late_authority_denies(store):
    value = command()
    admissions.claim_command("owner", value["command_id"], value, "provider_configuration")
    assert controls.read_provider_configuration_receipt(owner_id="owner", command_id=value["command_id"], validate=lambda: None)["status"] == "uncertain"
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise config.ProviderConfigError("action_denied")
    with pytest.raises(config.ProviderConfigError, match="action_denied"):
        controls.read_provider_configuration_receipt(owner_id="owner", command_id=value["command_id"], validate=validate)


def test_strict_refresh_retains_pins_and_default_and_probe_redacts_remote_diagnostics(store, monkeypatch, caplog):
    execute(command())
    monkeypatch.setattr(selection, "remove_quick_choices_for_missing_models", lambda *_: pytest.fail("No incidental pin changes"))
    monkeypatch.setattr(custom, "_probe_request", lambda *_a, **_k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [{"id": "saved"}]}))
    refreshed = custom.refresh_custom_endpoint_models("synthetic", strict=True)
    assert [item.model_id for item in refreshed] == ["saved"] and not refreshed.default_reset
    monkeypatch.setattr(custom, "_probe_request", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("synthetic-private-marker")))
    result = custom.probe_custom_endpoint("synthetic", strict=True)
    assert result["classification"] == "unavailable"
    assert "synthetic-private-marker" not in str(result) + config.CONFIG_PATH.read_text() + caplog.text


def test_nested_probe_stage_cannot_restart_overall_deadline(store, monkeypatch):
    execute(command())
    now = [10.0]
    monkeypatch.setattr(custom.time, "monotonic", lambda: now[0])
    endpoint = custom.get_custom_endpoint("synthetic")
    outer = custom._endpoint_probe_authority(endpoint, {}, lambda: None)
    now[0] = 129.0
    inner = custom._endpoint_probe_authority(endpoint, {}, outer)
    assert custom._endpoint_timeout(inner, 15) == 1
    now[0] = 130.0
    with pytest.raises(custom.EndpointAuthorityError, match="provider_probe_limit"):
        custom._endpoint_timeout(inner, 15)
