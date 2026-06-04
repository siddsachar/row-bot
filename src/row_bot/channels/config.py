"""
Thoth – Channel configuration store
=====================================
Lightweight key-value config persisted to ``~/.thoth/channels_config.json``.
Keeps channel settings separate from API keys and tool configs.
"""

from __future__ import annotations

import json
from row_bot.data_paths import get_row_bot_data_dir

_DATA_DIR = get_row_bot_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_PATH = _DATA_DIR / "channels_config.json"


def _load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: dict):
    with open(_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get(channel: str, key: str, default=None):
    """Get a config value for a channel.  e.g. get("email", "poll_interval", 60)"""
    return _load().get(channel, {}).get(key, default)


def set(channel: str, key: str, value):
    """Set a config value for a channel."""
    data = _load()
    data.setdefault(channel, {})[key] = value
    _save(data)


def get_all(channel: str) -> dict:
    """Get all config for a channel."""
    return _load().get(channel, {})
