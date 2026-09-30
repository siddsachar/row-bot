"""Docs and marketing capture never write a person's data or read their secrets.

The capture mode is opt-in; its seeded demo data carries no keys or real paths;
while it runs, secrets are not read (docs) or not written (authorized marketing
capture of a real profile), and the model defaults of a real profile stay as they
were. These guard the app, so they run with every pull request (the docs tooling
tests run in docs.yml).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_docs_capture_is_opt_in_and_seed_data_is_safe(tmp_path: Path, monkeypatch) -> None:
    from row_bot.docs_capture import (
        is_docs_capture,
        is_authorized_marketing_capture,
        is_docs_read_only_real_data_capture,
        is_docs_real_data_capture,
        scan_demo_data_safety,
        write_docs_capture_demo_state,
    )

    monkeypatch.delenv("ROW_BOT_DOCS_CAPTURE", raising=False)
    monkeypatch.delenv("ROW_BOT_DOCS_REAL_DATA", raising=False)
    assert not is_docs_capture()
    assert not is_docs_real_data_capture()

    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    assert is_docs_capture()
    assert not is_docs_real_data_capture()
    monkeypatch.setenv("ROW_BOT_DOCS_REAL_DATA", "1")
    assert is_docs_real_data_capture()
    assert is_docs_read_only_real_data_capture()
    assert not is_authorized_marketing_capture()
    monkeypatch.setenv("ROW_BOT_MARKETING_CAPTURE", "1")
    assert is_authorized_marketing_capture()
    assert not is_docs_read_only_real_data_capture()
    data = json.loads(write_docs_capture_demo_state(tmp_path, scenario="full").read_text(encoding="utf-8"))
    payload = json.dumps(data, sort_keys=True)

    assert "example.com" in payload
    assert "sk-" not in payload
    assert "ghp_" not in payload
    assert "C:\\Users\\" not in payload
    assert "/Users/" not in payload
    assert scan_demo_data_safety(tmp_path) == []


def test_authorized_real_capture_keeps_model_defaults_read_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import row_bot.models as models
    import row_bot.vision as vision

    model_path = tmp_path / "model_settings.json"
    vision_path = tmp_path / "vision_settings.json"
    model_original = b'{"model":"model:codex:gpt-5.6-sol"}'
    vision_original = b'{"model":"model:codex:gpt-5.6-sol"}'
    model_path.write_bytes(model_original)
    vision_path.write_bytes(vision_original)
    monkeypatch.setattr(models, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(models, "_SETTINGS_PATH", model_path)
    monkeypatch.setattr(vision, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(vision, "_SETTINGS_PATH", vision_path)
    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    monkeypatch.setenv("ROW_BOT_DOCS_REAL_DATA", "1")

    models._save_settings({"model": "model:ollama:llama3.1:8b"})
    vision._save_settings({"model": "model:ollama:llama3.1:8b"})

    assert model_path.read_bytes() == model_original
    assert vision_path.read_bytes() == vision_original


def test_docs_capture_never_reads_the_keyring(monkeypatch) -> None:
    import row_bot.secret_store as secret_store

    class FailingBackend:
        def get_password(self, *_args):
            raise AssertionError("keyring backend was read")

    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    monkeypatch.setattr(secret_store, "_backend_override", FailingBackend())

    assert secret_store.is_available() is False
    assert secret_store.get_secret("OPENAI_API_KEY") is None


def test_authorized_marketing_capture_reads_but_never_writes_keyring(monkeypatch) -> None:
    import row_bot.secret_store as secret_store

    calls: list[str] = []

    class ReadOnlyBackend:
        def get_password(self, _service, account):
            calls.append(f"read:{account}")
            return "configured-token"

        def set_password(self, *_args):
            raise AssertionError("marketing capture wrote the keyring")

        def delete_password(self, *_args):
            raise AssertionError("marketing capture deleted from the keyring")

    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    monkeypatch.setenv("ROW_BOT_DOCS_REAL_DATA", "1")
    monkeypatch.setenv("ROW_BOT_MARKETING_CAPTURE", "1")
    monkeypatch.setattr(secret_store, "_backend_override", ReadOnlyBackend())

    assert secret_store.is_available() is True
    assert secret_store.get_secret("access_token", namespace="providers:codex") == (
        "configured-token"
    )
    with pytest.raises(secret_store.SecretStoreError, match="write_disabled"):
        secret_store.set_secret("access_token", "replacement", namespace="providers:codex")
    with pytest.raises(secret_store.SecretStoreError, match="delete_disabled"):
        secret_store.delete_secret("access_token", namespace="providers:codex")
    assert calls
