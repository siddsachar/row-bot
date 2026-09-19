"""Saved default selection without provider activation or active-run mutation."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application import provider_default_model as controls
from row_bot.providers import config, saved_model_settings as settings
from row_bot.runtime import admissions
from tests.subsystem.providers import test_provider_settings_controls as fixtures

store = fixtures.store
pytestmark = pytest.mark.subsystem


@pytest.fixture
def saved(store, tmp_path, monkeypatch):
    import row_bot.models as models
    path = tmp_path / "model_settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    monkeypatch.setattr(models, "_SETTINGS_PATH", path)
    monkeypatch.setattr(models, "_current_model", "model:openai:previous")
    monkeypatch.setattr(models, "_llm_instance", None)
    monkeypatch.setattr(controls, "_saved_catalog", lambda: SimpleNamespace(cloud_cache={
        "model:openai:same": {"provider": "openai", "model_id": "same", "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}},
        "model:anthropic:same": {"provider": "anthropic", "model_id": "same", "capabilities_snapshot": {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}},
    }, ollama_rows=[]))
    return path


def command(provider="openai", model="same"):
    return {"command_id": str(uuid4()), "type": "provider.default_model.save", "expected_revision": "0", "payload": {
        "settings_revision": controls.read_default_model().revision, "provider_id": provider, "model_id": model}}


def execute(cmd, validate=lambda: None):
    return controls.execute_default_model(owner_id="synthetic-owner", key=cmd["command_id"], command=cmd, validate=validate, validate_review=lambda _: None)


def test_passive_cold_read_creates_nothing_and_never_imports_runtime(saved, tmp_path, monkeypatch):
    cold = tmp_path / "missing" / "model_settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", cold)
    snapshot = controls.read_default_model()
    assert snapshot.saved_state == "missing" and snapshot.selection_ref is None and snapshot.runtime_state == "unknown"
    assert not cold.parent.exists()


def test_fresh_process_passive_reads_do_not_create_data_or_import_runtime(tmp_path):
    import os
    import subprocess
    import sys
    cold = tmp_path / "cold-process-data"
    code = """
import os, sys
from pathlib import Path
from row_bot.runtime import admissions
assert admissions.read_command_metadata('owner', 'missing') is None
assert admissions.read_unfinished_target_commands('settings:mcp') == {'items': [], 'overflow': False}
from row_bot.application.provider_default_model import read_default_model
assert read_default_model().saved_state == 'missing'
from row_bot.providers.model_catalog_cache import read_model_catalog_cache
assert read_model_catalog_cache(allow_runtime_bootstrap=False, max_bytes=1024).is_empty
assert 'row_bot.models' not in sys.modules and 'row_bot.tasks' not in sys.modules
assert not Path(os.environ['ROW_BOT_DATA_DIR']).exists()
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "ROW_BOT_DATA_DIR": str(cold)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not cold.exists()


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"x" * (2 * 1024 * 1024 + 1)], ids=["malformed", "wrong-shape", "oversized"])
def test_malformed_or_oversized_settings_preserved(saved, raw):
    saved.write_bytes(raw)
    with pytest.raises(config.ProviderConfigError, match="model_settings_unavailable"):
        controls.read_default_model()
    assert saved.read_bytes() == raw


def test_legacy_default_not_inferred_or_automatically_replaced(saved):
    saved.write_text(json.dumps({"model": "same", "context_size": 48000, "legacy": {"keep": True}}))
    before = saved.read_bytes()
    assert controls.read_default_model().saved_state == "unavailable"
    assert saved.read_bytes() == before


def test_save_preserves_exact_provider_unknown_fields_and_frozen_active_objects(saved, monkeypatch):
    from row_bot import models
    saved.write_text(json.dumps({"model": "model:openai:previous", "context_size": 48000, "legacy": {"keep": True}}))
    for name in ("_ollama_client", "_chat_ollama", "_get_cloud_llm", "set_model"):
        monkeypatch.setattr(models, name, lambda *_a, **_kw: pytest.fail("No runtime activation"))
    active_llm = object()
    monkeypatch.setattr(models, "_llm_instance", active_llm)
    monkeypatch.setattr(models, "_override_llm_cache", {("frozen", 48000, ""): active_llm})
    token = models._active_model_override.set("model:openai:previous")
    try:
        result = execute(command("anthropic"))
        assert result["selection"]["selection_ref"] == "model:anthropic:same"
        assert models._current_model == "model:anthropic:same" and models._llm_instance is None
        assert models._active_model_override.get() == "model:openai:previous"
        assert models._override_llm_cache[("frozen", 48000, "")] is active_llm
        raw = json.loads(saved.read_text())
        assert raw["context_size"] == 48000 and raw["legacy"] == {"keep": True}
    finally:
        models._active_model_override.reset(token)


def test_stale_revision_and_unknown_model_never_save(saved):
    first = command()
    execute(command("anthropic"))
    before = saved.read_bytes()
    with pytest.raises(config.ProviderConfigError, match="revision_conflict"):
        execute(first)
    with pytest.raises(config.ProviderConfigError, match="model_configuration_unavailable"):
        execute(command(model="unknown"))
    assert saved.read_bytes() == before


def test_receipt_failure_recovers_same_publication_without_rewrite(saved, monkeypatch):
    cmd = command()
    complete = admissions.complete_command
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(OSError):
        execute(cmd)
    before = saved.read_bytes()
    monkeypatch.setattr(admissions, "complete_command", complete)
    assert execute(cmd)["status"] == "completed"
    assert saved.read_bytes() == before


def test_context_setter_waiting_before_save_does_not_revert_new_default(saved, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from row_bot import models
    arrived, release = Event(), Event()
    original = models._save_settings
    monkeypatch.setattr(models, "is_cloud_model", lambda _: True)
    monkeypatch.setattr(models, "_cloud_context_override", None)
    monkeypatch.setattr(models, "_cloud_num_ctx", 32000)
    def waiting(payload):
        arrived.set()
        assert release.wait(5)
        original(payload)
    monkeypatch.setattr(models, "_save_settings", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(models.set_cloud_context_size, 64000)
        try:
            assert arrived.wait(5)
            execute(command("anthropic"))
        finally:
            release.set()
        future.result(5)
    raw = json.loads(saved.read_text())
    assert raw["model"] == "model:anthropic:same" and raw["cloud_context_override"] == 64000


def test_revocation_before_publication_keeps_original_bytes(saved, monkeypatch):
    cmd = command()
    before = saved.read_bytes() if saved.exists() else None
    revoked = False
    original = settings.update_saved_model_settings
    def before_publication(update, **kwargs):
        def computed(raw):
            nonlocal revoked
            value = update(raw)
            revoked = True
            return value
        return original(computed, **kwargs)
    monkeypatch.setattr(settings, "update_saved_model_settings", before_publication)
    def validate():
        if revoked:
            raise config.ProviderConfigError("action_denied")
    with pytest.raises(config.ProviderConfigError, match="action_denied"):
        execute(cmd, validate)
    assert (saved.read_bytes() if saved.exists() else None) == before


def test_passive_receipt_observes_publication_without_replay_or_initialization(saved, monkeypatch):
    cmd = command()
    monkeypatch.setattr(admissions, "complete_command", lambda *_a: (_ for _ in ()).throw(OSError("synthetic receipt failure")))
    with pytest.raises(OSError):
        execute(cmd)
    before = saved.read_bytes()
    monkeypatch.setattr(controls, "_adopt", lambda *_: pytest.fail("No passive runtime mutation"))
    result = controls.read_default_model_receipt(owner_id="synthetic-owner", command_id=cmd["command_id"], validate=lambda: None)
    assert result["status"] == "completed" and result["published"]
    assert saved.read_bytes() == before
    assert controls.read_default_model_receipt(owner_id="other", command_id=cmd["command_id"], validate=lambda: None) is None


def test_passive_command_metadata_cold_missing_corrupt_and_view_schema(saved, tmp_path, monkeypatch):
    import sqlite3
    from row_bot import tasks
    path = tmp_path / "cold" / "commands.db"
    monkeypatch.setattr(tasks, "_DB_PATH", str(path))
    assert admissions.read_command_metadata("owner", "id") is None
    assert admissions.read_unfinished_target_commands("settings:mcp") == {"items": [], "overflow": False}
    assert not path.parent.exists()
    path.parent.mkdir()
    path.write_bytes(b"not sqlite")
    with pytest.raises(admissions.AdmissionError, match="command_metadata_unavailable"):
        admissions.read_command_metadata("owner", "id")
    other = path.with_name("view.db")
    monkeypatch.setattr(tasks, "_DB_PATH", str(other))
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE VIEW client_commands AS SELECT 'owner' owner_id,'id' command_id,'key' key,'target' target,'type' type,'admitting' status")
    with pytest.raises(admissions.AdmissionError, match="command_metadata_unavailable"):
        admissions.read_unfinished_target_commands("target")


def test_unfinished_target_metadata_is_exact_bounded_and_contains_no_result_content(saved):
    for index in range(34):
        value = {"command_id": f"id-{index:03}", "type": "mcp.configure", "payload": {}}
        admissions.claim_command(f"owner-{index % 2}", f"key-{index:03}", value, "settings:mcp")
    admissions.claim_command("other", "other", {"command_id": "other", "type": "mcp.configure"}, "different")
    admissions.complete_command("owner-0", "key-000", {"private": "synthetic-hidden-result"})
    first = admissions.read_unfinished_target_commands("settings:mcp", limit=32)
    assert len(first["items"]) == 32 and first["overflow"]
    assert all(set(item) == {"owner_id", "key", "command_id", "type", "status"} and item["status"] == "admitting" for item in first["items"])
    metadata = admissions.read_command_metadata("owner-0", "id-000")
    assert metadata == {"key": "key-000", "target": "settings:mcp", "type": "mcp.configure", "status": "completed"}
    admissions.reject_command("owner-1", "key-001", "cancelled")
    assert not admissions.read_unfinished_target_commands("settings:mcp")["overflow"]


@pytest.mark.parametrize("content", [b"{invalid", b"x" * 1025], ids=["malformed", "oversized"])
def test_bounded_catalog_read_never_bootstraps_or_logs_content(tmp_path, monkeypatch, caplog, content):
    from row_bot.providers import model_catalog_cache as cache
    path = tmp_path / "synthetic-catalog.json"
    path.write_bytes(content)
    monkeypatch.setattr(cache, "_bootstrap_snapshot_from_runtime", lambda: pytest.fail("No fallback snapshot"))
    assert cache.read_model_catalog_cache(path, max_bytes=1024).is_empty
    assert not caplog.records
    assert path.read_bytes() == content


def test_default_review_rejects_whole_oversized_cache_without_selecting_prefix(saved, tmp_path, monkeypatch):
    from row_bot.providers import model_catalog_cache as cache
    path = tmp_path / "saved-catalog.json"
    payload = {"version": 1, "cloud_cache": {"model:openai:same": {
        "provider": "openai", "model_id": "same", "capabilities_snapshot": {
            "tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"]}}},
        "padding": "x" * (16 * 1024 * 1024)}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", path)
    monkeypatch.setattr(controls, "_saved_catalog", lambda: cache.read_model_catalog_cache(allow_runtime_bootstrap=False, max_bytes=16 * 1024 * 1024))
    before = path.stat().st_size
    with pytest.raises(config.ProviderConfigError, match="model_configuration_unavailable"):
        execute(command())
    assert not saved.exists() and path.stat().st_size == before
    # The legacy default reader retains its existing full-file behavior.
    assert not cache.read_model_catalog_cache(path, allow_runtime_bootstrap=False).is_empty


@pytest.mark.parametrize("value", [" same", "same ", "same\n", "x" * 513, "\ud800"], ids=["leading-space", "trailing-space", "control", "oversized", "surrogate"])
def test_model_identity_is_not_silently_normalized(saved, value):
    with pytest.raises(config.ProviderConfigError, match="invalid_model_selection"):
        execute(command(model=value))
    assert not saved.exists()
