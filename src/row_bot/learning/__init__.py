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

The capture seams (``agent``, the approval gate, Retry, the Slack and Discord
handlers) call :func:`is_enabled` first and import ``capture`` only when it is on;
:func:`note_retry` and :func:`note_channel_sender` are those guards for the
seams outside the Agent.

Settings live in ``learning_config.json`` in the data folder::

    {"enabled": false, "paused": false,
     "sources": {"my_chats": true, "workflows_and_goals": true, "past_chats": true},
     "apply_mode": "ask_first", "monthly_token_limit": 2000000,
     "train_in_quiet_hours": true, "train_where": "this_computer",
     "starting_model": "qwen3.5-2b", "model_download": "not_asked"}

**The preview flag (O34).** Until the 5.4 cut, Learning exists only in a build
started with ``ROW_BOT_LEARNING_PREVIEW=1``. The flag is read once, when this
package is imported at start-up; it is a build gate, not a setting. Without it
Learning is off whatever the settings say: :func:`read_settings` reports it off,
:func:`is_enabled` is false, nothing is recorded and the Learning API answers
"unavailable".

**Pause.** A paused Learning stays on (its settings, changes and models in use are
kept) but records, tests and trains nothing: :func:`is_enabled` is false while it is
paused, at the same one cached read.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterable
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
# Starting models a personal model can be trained from (``training/hardware.py``
# describes them); only the certified 8 GB recipe's model so far (O23).
STARTING_MODELS = ("qwen3.5-2b",)
# The answer to the download ask after turning Learning on: not asked yet,
# Download (waiting until the engine can be installed, L2b) or Not now.
MODEL_DOWNLOADS = ("not_asked", "waiting", "not_now")
DEFAULT_MONTHLY_TOKEN_LIMIT = 2_000_000
MAX_MONTHLY_TOKEN_LIMIT = 1_000_000_000
_MAX_SETTINGS_BYTES = 64 * 1024

PREVIEW_ENV = "ROW_BOT_LEARNING_PREVIEW"
# Read once at start-up (O34); tests change it through ``tests/fixtures/learning.py``.
_PREVIEW = os.environ.get(PREVIEW_ENV, "").strip() == "1"


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
    paused: bool = False
    starting_model: str = STARTING_MODELS[0]
    model_download: str = "not_asked"
    # False when a saved file exists but could not be read; Learning is then off.
    readable: bool = True


def preview_enabled() -> bool:
    """Whether this build was started with Learning's preview flag (O34)."""

    return _PREVIEW


DEFAULTS = LearningSettings()

# (settings path, mtime_ns, size, inode) -> settings; one entry, swapped whole.
# The key carries the preview flag too, so the gate costs nothing per call.
_cache: tuple[tuple[str, int, int, int, bool], LearningSettings] | None = None


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
    starting_model = document.get("starting_model")
    model_download = document.get("model_download")
    limit = document.get("monthly_token_limit")
    if type(limit) is not int or not 0 <= limit <= MAX_MONTHLY_TOKEN_LIMIT:
        limit = DEFAULT_MONTHLY_TOKEN_LIMIT
    paused = document.get("paused", False)
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
        # A pause that can't be read keeps Learning paused (nothing is recorded).
        paused=paused if type(paused) is bool else True,
        starting_model=starting_model if starting_model in STARTING_MODELS else DEFAULTS.starting_model,
        model_download=model_download if model_download in MODEL_DOWNLOADS else DEFAULTS.model_download,
    )


def read_settings(root: Path | None = None) -> LearningSettings:
    """The saved settings, cached until the file changes. Never raises.

    A missing file reads as the defaults (Learning off). A file that exists but
    can't be read or parsed reads as off and ``readable=False``. Without the
    preview flag Learning reads as off whatever was saved (O34).
    """

    global _cache
    try:
        path = settings_path(root)
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return DEFAULTS
        key = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino, _PREVIEW)
        cached = _cache
        if cached is not None and cached[0] == key:
            return cached[1]
        if stat.st_size > _MAX_SETTINGS_BYTES:
            settings = replace(DEFAULTS, readable=False)
        else:
            with open(path, encoding="utf-8") as handle:
                settings = _normalise(json.load(handle))
        if not _PREVIEW and settings.enabled:
            settings = replace(settings, enabled=False)
        _cache = (key, settings)
        return settings
    except Exception:
        logger.debug("Learning settings could not be read; Learning stays off", exc_info=True)
        return replace(DEFAULTS, readable=False)


def is_enabled(root: Path | None = None) -> bool:
    """Whether Learning may record, test or train now: on and not paused.

    Cheap, cached, and False when settings can't be read or the build has no
    preview flag.
    """

    try:
        settings = read_settings(root)
        return settings.enabled and not settings.paused
    except Exception:
        return False


def invalidate() -> None:
    """Forget the cached settings (a settings save calls this)."""

    global _cache
    _cache = None


def settings_saved(field: str, value: Any) -> None:
    """React to one saved Learning setting.

    Turning Learning on creates its store and, when "Also learn from the last 30 days
    of chats" is ticked, starts reading past chats in the background (D26). Turning it
    off or pausing it keeps the data and stops capture and the import; resuming
    carries the import on. A store that can't be created now is created by the first
    write while Learning is on. Without the preview flag nothing happens.
    """

    invalidate()
    if not _PREVIEW:
        return
    if field == "enabled" and value is True:
        try:
            from row_bot.learning.store import LearningStore

            LearningStore().create()
        except Exception:
            logger.warning("Learning is on but its store could not be created yet", exc_info=True)
    if (field == "enabled" and value is not True) or (field == "paused" and value is True):
        capture = sys.modules.get("row_bot.learning.capture")
        if capture is not None:
            capture.learning_turned_off()
    if (
        (field == "enabled" and value is True)
        or (field == "sources.past_chats" and value is True)
        or (field == "paused" and value is False)
    ):
        start_past_chat_import()


def start_past_chat_import() -> None:
    """Read past chats in the background when Learning and "past chats" are on (D26).

    Resumes an unfinished import (after a restart, say); never starts a second one.
    """

    try:
        if not is_enabled() or not read_settings().past_chats:
            return
        from row_bot.learning import past_chats

        past_chats.start_background()
    except Exception as exc:
        logger.warning("Learning could not start reading past chats (%s)", type(exc).__name__)


def note_retry(thread_id: str, replaced: Iterable[Any]) -> None:
    """Retry ran the person's last message again in place: an implicit signal. Off: nothing."""

    if not is_enabled():
        return
    try:
        from row_bot.learning import capture

        capture.retry(thread_id, [str(getattr(message, "id", "") or "") for message in replaced
                                  if str(getattr(message, "type", "") or "") == "ai"])
    except Exception as exc:
        logger.debug("Learning did not see a retry (%s)", type(exc).__name__)


def note_channel_sender(thread_id: str, sender_id: object) -> None:
    """Who sent a Slack or Discord message, so scope can tell the owner's DM (O3). Off: nothing."""

    if not is_enabled():
        return
    try:
        from row_bot.learning import capture

        capture.note_channel_sender(thread_id, str(sender_id or ""))
    except Exception as exc:
        logger.debug("Learning did not see a channel sender (%s)", type(exc).__name__)


__all__ = [
    "APPLY_MODES",
    "DEFAULTS",
    "DEFAULT_MONTHLY_TOKEN_LIMIT",
    "FOLDER",
    "LearningSettings",
    "MAX_MONTHLY_TOKEN_LIMIT",
    "MODEL_DOWNLOADS",
    "PREVIEW_ENV",
    "SETTINGS_FILE",
    "STARTING_MODELS",
    "STORE_FILE",
    "TRAIN_PLACES",
    "data_dir",
    "folder_path",
    "invalidate",
    "is_enabled",
    "note_channel_sender",
    "note_retry",
    "preview_enabled",
    "read_settings",
    "settings_path",
    "settings_saved",
    "start_past_chat_import",
    "store_exists",
    "store_path",
]
