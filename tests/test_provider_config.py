import json
import pathlib

import pytest

from row_bot.providers import config
from row_bot.providers.config import DEFAULT_ROUTE_PROFILES, load_provider_config, mask_provider_config, save_provider_config


def test_provider_config_normalizes_defaults(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"quick_choices": "bad", "routes": []}))

    cfg = load_provider_config(path)

    assert cfg["version"] == 1
    assert cfg["quick_choices"] == []
    assert [route["id"] for route in cfg["routes"]][:5] == [route["id"] for route in DEFAULT_ROUTE_PROFILES]


def test_provider_config_atomic_save_and_masking(tmp_path):
    path = tmp_path / "providers.json"
    saved = save_provider_config({"providers": {"openai": {"api_key": "sk-test-secret"}}}, path)

    assert path.exists()
    assert json.loads(path.read_text())["version"] == 1
    assert saved["providers"]["openai"]["api_key"] == "sk-test-secret"

    masked = mask_provider_config(saved)
    assert masked["providers"]["openai"]["api_key"] == "****cret"


def _sharing_violation() -> OSError:
    error = PermissionError(13, "Access is denied")
    error.winerror = 5
    return error


def test_provider_metadata_retries_a_transient_windows_sharing_violation(tmp_path, monkeypatch):
    path = tmp_path / "providers.json"
    real_replace = pathlib.Path.replace
    attempts = []

    def flaky_replace(self, target):
        attempts.append(target)
        if len(attempts) == 1:
            raise _sharing_violation()
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", flaky_replace)
    monkeypatch.setattr(config.time, "sleep", lambda _seconds: None)

    config.write_provider_metadata(path, {"version": 1})

    assert len(attempts) == 2
    assert json.loads(path.read_text()) == {"version": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_provider_metadata_raises_other_errors_and_removes_the_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "providers.json"
    attempts = []

    def failing_replace(self, target):
        attempts.append(target)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "replace", failing_replace)
    monkeypatch.setattr(config.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        config.write_provider_metadata(path, {"version": 1})

    assert len(attempts) == 1
    assert list(tmp_path.iterdir()) == []
