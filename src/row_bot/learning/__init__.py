"""Learning (preview): the switch.

Importing this package loads nothing else from ``row_bot.learning``: no store,
no database connection, no thread. Later seams call :func:`is_enabled` on every
model call, so it is a cached read of the saved setting that is re-read only
when the settings file changes (or :func:`invalidate` is called), and it says
``False`` whenever the setting can't be read.

Learning is off by default. While it is off nothing under ``row_bot.learning``
is imported, and the data folder holds no Learning file until Learning is first
turned on: ``learning.db`` and ``learning/blobs/`` are created by the first
enable or by the first write while it is on (``store.py``). Turning it off keeps
the data until the person deletes it.

Settings live in ``learning_config.json`` in the data folder::

    {"enabled": false,
     "sources": {"my_chats": true, "workflows_and_goals": true, "past_chats": true},
     "apply_mode": "ask_first", "monthly_token_limit": 2000000,
     "train_in_quiet_hours": true, "train_where": "this_computer"}
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

SETTINGS_FILE = "learning_config.json"
STORE_FILE = "learning.db"
FOLDER = "learning"

APPLY_MODES = ("ask_first", "automatic")
TRAIN_PLACES = ("this_computer", "hugging_face")
DEFAULT_MONTHLY_TOKEN_LIMIT = 2_000_000
MAX_MONTHLY_TOKEN_LIMIT = 1_000_000_000
_MAX_SETTINGS_BYTES = 64 * 1024


@dataclass(frozen=True)
class LearningSettings:
    """The saved Learning settings, normalised. Defaults match the setup sheet."""

    enabled: bool = False
    my_chats: bool = True
    workflows_and_goals: bool = True
    past_chats: bool = True
    apply_mode: str = "ask_first"
    monthly_token_limit: int = DEFAULT_MONTHLY_TOKEN_LIMIT
    train_in_quiet_hours: bool = True
    train_where: str = "this_computer"
    # False when a saved file exists but could not be read; Learning is then off.
    readable: bool = True


DEFAULTS = LearningSettings()

# (settings path, mtime_ns, size, inode) -> settings; one entry, swapped whole.
_cache: tuple[tuple[str, int, int, int], LearningSettings] | None = None


def data_dir() -> Path:
    """The data folder Learning uses now (never created here)."""

    return get_row_bot_data_dir(create=False)


def settings_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / SETTINGS_FILE


def store_path(root: Path | None = None) -> Path:
    """Where ``learning.db`` lives (it may not exist)."""

    return (root if root is not None else data_dir()) / STORE_FILE


def folder_path(root: Path | None = None) -> Path:
    """Where ``learning/`` (blobs) lives (it may not exist)."""

    return (root if root is not None else data_dir()) / FOLDER


def store_exists(root: Path | None = None) -> bool:
    """A cheap path check: whether Learning has a store in the data folder."""

    try:
        return store_path(root).is_file()
    except OSError:
        return False


def _bool(document: dict[str, Any], key: str, default: bool) -> bool:
    """A saved switch: its default when it was never saved, off when it can't be read."""

    if key not in document:
        return default
    value = document[key]
    return value if type(value) is bool else False


def _normalise(document: Any) -> LearningSettings:
    if not isinstance(document, dict):
        return replace(DEFAULTS, readable=False)
    sources = document.get("sources")
    sources = sources if isinstance(sources, dict) else {}
    apply_mode = document.get("apply_mode")
    train_where = document.get("train_where")
    limit = document.get("monthly_token_limit")
    if type(limit) is not int or not 0 <= limit <= MAX_MONTHLY_TOKEN_LIMIT:
        limit = DEFAULT_MONTHLY_TOKEN_LIMIT
    return LearningSettings(
        # Only an explicit true turns Learning on.
        enabled=document.get("enabled") is True,
        my_chats=_bool(sources, "my_chats", DEFAULTS.my_chats),
        workflows_and_goals=_bool(sources, "workflows_and_goals", DEFAULTS.workflows_and_goals),
        past_chats=_bool(sources, "past_chats", DEFAULTS.past_chats),
        apply_mode=apply_mode if apply_mode in APPLY_MODES else DEFAULTS.apply_mode,
        monthly_token_limit=limit,
        train_in_quiet_hours=_bool(document, "train_in_quiet_hours", DEFAULTS.train_in_quiet_hours),
        train_where=train_where if train_where in TRAIN_PLACES else DEFAULTS.train_where,
    )


def read_settings(root: Path | None = None) -> LearningSettings:
    """The saved settings, cached until the file changes. Never raises.

    A missing file reads as the defaults (Learning off). A file that exists but
    can't be read or parsed reads as off and ``readable=False``.
    """

    global _cache
    try:
        path = settings_path(root)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return DEFAULTS
        key = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = _cache
        if cached is not None and cached[0] == key:
            return cached[1]
        if stat.st_size > _MAX_SETTINGS_BYTES:
            settings = replace(DEFAULTS, readable=False)
        else:
            with open(path, encoding="utf-8") as handle:
                settings = _normalise(json.load(handle))
        _cache = (key, settings)
        return settings
    except Exception:
        logger.debug("Learning settings could not be read; Learning stays off", exc_info=True)
        return replace(DEFAULTS, readable=False)


def is_enabled(root: Path | None = None) -> bool:
    """Whether Learning is on. Cheap, cached, and False when settings can't be read."""

    try:
        return read_settings(root).enabled
    except Exception:
        return False


def invalidate() -> None:
    """Forget the cached settings (a settings save calls this)."""

    global _cache
    _cache = None


def settings_saved(field: str, value: Any) -> None:
    """React to one saved Learning setting.

    Turning Learning on creates its store. Turning it off keeps the data. A store
    that can't be created now is created by the first write while Learning is on.
    """

    invalidate()
    if field == "enabled" and value is True:
        try:
            from row_bot.learning.store import LearningStore

            LearningStore().create()
        except Exception:
            logger.warning("Learning is on but its store could not be created yet", exc_info=True)


__all__ = [
    "APPLY_MODES",
    "DEFAULTS",
    "DEFAULT_MONTHLY_TOKEN_LIMIT",
    "FOLDER",
    "LearningSettings",
    "MAX_MONTHLY_TOKEN_LIMIT",
    "SETTINGS_FILE",
    "STORE_FILE",
    "TRAIN_PLACES",
    "data_dir",
    "folder_path",
    "invalidate",
    "is_enabled",
    "read_settings",
    "settings_path",
    "settings_saved",
    "store_exists",
    "store_path",
]
