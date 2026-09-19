"""Passive reads and shared publication of the existing model_settings.json."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import stat

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.providers.config import ProviderConfigError, provider_config_revision, provider_config_transaction, write_provider_metadata

SETTINGS_PATH = get_row_bot_data_dir(create=False) / "model_settings.json"
MAX_SETTINGS_BYTES = 2 * 1024 * 1024


def read_saved_model_settings(path: Path | None = None) -> tuple[dict, str, bool]:
    target = path or SETTINGS_PATH
    try:
        for component in (*reversed(target.absolute().parents), target.absolute()):
            try:
                info = component.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError
        with target.open("rb") as stream:
            raw = stream.read(MAX_SETTINGS_BYTES + 1)
        if len(raw) > MAX_SETTINGS_BYTES:
            raise ValueError
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError
        return value, provider_config_revision(value), True
    except FileNotFoundError:
        return {}, provider_config_revision({}), False
    except (OSError, ValueError, RecursionError):
        raise ProviderConfigError("model_settings_unavailable") from None


def update_saved_model_settings(update: Callable[[dict], dict], *, path: Path | None = None,
                                expected_revision: str | None = None,
                                validate: Callable[[], None] = lambda: None) -> dict:
    target = path or SETTINGS_PATH
    with provider_config_transaction(target):
        validate()
        current, revision, _ = read_saved_model_settings(target)
        if expected_revision is not None and expected_revision != revision:
            raise ProviderConfigError("revision_conflict")
        value = update(current)
        if type(value) is not dict or len(json.dumps(value, ensure_ascii=True).encode()) > MAX_SETTINGS_BYTES:
            raise ProviderConfigError("model_settings_unavailable")
        validate()
        write_provider_metadata(target, value)
        return value
