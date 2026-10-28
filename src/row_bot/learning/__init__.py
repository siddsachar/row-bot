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
     "starting_model": "qwen3.5-9b", "model_download": "not_asked",
     "hugging_face": {"model": "", "monthly_cap_usd": 0, "runs_confirmed": false}}

**Training on Hugging Face** (C1, O130-O134): ``hugging_face.model`` is the model it trains there (empty: the
strongest certified one this computer can run, ``training/catalog.py``); ``monthly_cap_usd`` the spend cap the
person chose with the first run's quote (0: not chosen yet, so nothing runs on its own); ``runs_confirmed``
whether the person has started a first run (until then every run asks, O132).

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
# Starting models a personal model can be trained from on this computer (``training/hardware.py``
# describes them, ``training/weights.py`` pins them). O124: Qwen3.5 9B is the smallest personal model
# (the 2B tier went in T1); a card trains only the certified ones it fits (``HardwareCheck.starting_models``).
STARTING_MODELS = ("qwen3.5-9b",)  # a saved model no build trains any more (the 2B) reads as the 9B
# The answer to the download ask after turning Learning on: not asked yet, Download
# ("waiting": the person agreed; L2b downloads when they tap it, and an old answer reads
# as "Ready to download", O36) or Not now.
MODEL_DOWNLOADS = ("not_asked", "waiting", "not_now")
# Where the personal model runs ("Runs in", D10): Row-Bot's own llama.cpp server by default (O75;
# Ollama where it has no build, ``serving.effective_runtime``), Ollama (automated), or a runtime the
# person starts (LM Studio, llama.cpp server, vLLM on Linux) with one command Row-Bot shows.
SERVE_RUNTIMES = ("row_bot", "ollama", "lmstudio", "llama_cpp", "vllm")
DEFAULT_MONTHLY_TOKEN_LIMIT = 2_000_000
# The most a monthly Hugging Face cap may be (dollars); the choices the quote offers are in the client.
MAX_HUGGING_FACE_CAP_USD = 1_000
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
    serve_runtime: str = SERVE_RUNTIMES[0]
    # C1: Hugging Face training (see the module's docstring).
    hf_model: str = ""
    # The Hugging Face card the person chose in the quote (empty: the recipe's own).
    hf_flavor: str = ""
    hf_monthly_cap_usd: int = 0
    hf_runs_confirmed: bool = False
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
    serve_runtime = document.get("serve_runtime")
    limit = document.get("monthly_token_limit")
    if type(limit) is not int or not 0 <= limit <= MAX_MONTHLY_TOKEN_LIMIT:
        limit = DEFAULT_MONTHLY_TOKEN_LIMIT
    paused = document.get("paused", False)
    hugging = document.get("hugging_face")
    hugging = hugging if isinstance(hugging, dict) else {}
    hf_model = hugging.get("model")
    hf_model = hf_model if isinstance(hf_model, str) and len(hf_model) <= 128 else ""
    hf_flavor = hugging.get("flavor")
    hf_flavor = hf_flavor if isinstance(hf_flavor, str) and len(hf_flavor) <= 64 else ""
    cap = hugging.get("monthly_cap_usd")
    cap = cap if type(cap) is int and 0 <= cap <= MAX_HUGGING_FACE_CAP_USD else 0
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
        serve_runtime=serve_runtime if serve_runtime in SERVE_RUNTIMES else DEFAULTS.serve_runtime,
        hf_model=hf_model,
        hf_flavor=hf_flavor,
        hf_monthly_cap_usd=cap,
        hf_runs_confirmed=hugging.get("runs_confirmed") is True,
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


def changed() -> None:
    """Learning's state changed: every open client reads its Learning screens again at once (O127).

    Only the ``learning`` live topic's name and revision go out (B407), never what changed. Called when a
    setting is saved, a Learning command runs, a job or other Learning record changes state (a version
    planned, training, finished or failed; the past-chat import starting or done), a version's phase moves
    (ready, couldn't start), a comparison starts, moves on or finishes, the download changes state, the
    vault refreshes and a personal model's use is recorded. Capture's own records never call it.
    """

    from row_bot.application.live_updates import publish

    publish("learning")


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
    changed()
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
    # The personal model's download (L2b): off or paused stops it (it shows as paused).
    downloads = sys.modules.get("row_bot.learning.training.downloads")
    if downloads is not None and ((field == "enabled" and value is not True) or (field == "paused" and value is True)):
        try:
            downloads.learning_stopped()
        except Exception:
            logger.warning("Learning could not stop its download", exc_info=True)
    # Training (L2a): off stops it at once; paused pauses it at its next step.
    runner = sys.modules.get("row_bot.learning.runner")
    if runner is not None:
        try:
            if field == "enabled" and value is not True:
                runner.learning_turned_off()
            elif field == "paused" and value is True:
                runner.learning_paused()
        except Exception:
            logger.warning("Learning could not stop training", exc_info=True)
    if (
        (field == "enabled" and value is True)
        or (field == "sources.past_chats" and value is True)
        or (field == "paused" and value is False)
    ):
        start_past_chat_import()
    if (field == "enabled" and value is True) or (field == "paused" and value is False):
        start_runner()
    if field == "train_where" and runner is not None:
        try:
            runner.place_changed(str(value))
        except Exception:
            logger.warning("Learning could not set aside a run planned for the other place", exc_info=True)


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


def start_blob_sweep() -> Any:
    """The one-off sweep on this build's first start (P2): in the background, remove the blobs no
    Learning record references any more (a deleted or switched-off chat's messages and pictures that
    earlier builds left behind), logging counts only. Learning on or off: it is maintenance on kept
    data. One path check without a store; once it has run, one value read. Returns the thread, if any."""

    if not store_exists():
        return None
    import threading

    def sweep() -> None:
        try:
            from row_bot.learning.store import LearningStore

            LearningStore().sweep_once()
        except Exception as exc:
            logger.warning("Learning could not sweep its unreferenced files (%s)", type(exc).__name__)

    thread = threading.Thread(target=sweep, name="learning-blob-sweep", daemon=True)
    thread.start()
    return thread


def start_runner() -> None:
    """Start the quiet-hours runner while Learning is on (L2a): it pauses training that
    stopped when Row-Bot closed, then looks once a minute for what is due."""

    try:
        if not is_enabled():
            return
        from row_bot.learning import runner

        runner.start_background()
    except Exception as exc:
        logger.warning("Learning could not start its quiet-hours runner (%s)", type(exc).__name__)


MODELS_FOLDER = "learning_models"
USE_FILE = "use.json"


def route_turn(**request: Any) -> Any | None:
    """Where a new chat turn runs (L2c2): a route to the personal model when the person gave its
    kind of work to a version, else None (the main model). While nothing is in use this is one path
    check and imports nothing. Models in use stay in use while Learning is off or paused (UX §8);
    without the preview flag nothing is routed (O34)."""

    if not _PREVIEW:
        return None
    try:
        if not (data_dir() / MODELS_FOLDER / USE_FILE).is_file():
            return None
        from row_bot.learning import routing

        return routing.route(**request)
    except Exception as exc:
        logger.warning("Learning couldn't route a turn; the main model answers (%s)", type(exc).__name__)
        return None


WAYS_OF_WORKING_FILE = "learning_ways_of_working.json"


def ways_of_working_text() -> str:
    """The ways of working in use (E7, D18), as the agent's stable system section, or "". The agent
    reads it once per turn. While none was ever used this is one path check and imports nothing; ways
    of working in use stay in use while Learning is off or paused (UX §8); without the preview flag
    nothing is added (O34)."""

    if not _PREVIEW:
        return ""
    try:
        if not (data_dir() / WAYS_OF_WORKING_FILE).is_file():
            return ""
        from row_bot.learning import behaviour

        return behaviour.section_text()
    except Exception as exc:
        logger.warning("Learning couldn't read its ways of working; Row-Bot's own apply (%s)", type(exc).__name__)
        return ""


LESSONS_FILE = "learning_lessons.json"


def lesson_memory_ids() -> set[str]:
    """The memories that are lessons in use (E5), for the chat's quiet "Used a lesson" line. While none was
    ever used this is one path check; without the preview flag there are none (O34)."""

    if not _PREVIEW:
        return set()
    try:
        path = data_dir() / LESSONS_FILE
        if not path.is_file():
            return set()
        found = json.loads(path.read_text(encoding="utf-8")).get("lessons")
        return {str(key) for key in found} if isinstance(found, dict) else set()
    except Exception as exc:
        logger.debug("Learning couldn't read its lessons in use (%s)", type(exc).__name__)
        return set()


def note_lessons_recalled(memory_ids: Iterable[str]) -> None:
    """A turn's recall showed these memories: counts the lessons in use among them ("used 3 times this
    month"). Nothing while no lesson is in use."""

    ids = [str(item) for item in memory_ids if item]
    if not ids or not lesson_memory_ids().intersection(ids):
        return
    try:
        from row_bot.learning import changes

        changes.note_recalled(ids)
    except Exception as exc:
        logger.debug("Learning couldn't count a recalled lesson (%s)", type(exc).__name__)


def lesson_memory_deleted_soon(old_id: str, new_id: str) -> None:
    """A Dream merge is about to delete a ``self_knowledge`` memory that a lesson in use may be (O7):
    the lesson moves to the surviving memory instead of being marked deleted. Nothing without a store."""

    try:
        if not store_exists():
            return
        from row_bot.learning import changes

        changes.relink_lesson(str(old_id or ""), str(new_id or ""))
    except Exception as exc:
        logger.warning("Learning couldn't move a lesson to its merged memory (%s)", type(exc).__name__)


def offered_personal_models() -> dict[str, str]:
    """``{model reference: "Personal model v1"}`` of the personal-model versions model lists offer
    (D11: every ready version), or ``{}``. While there is none this is one path check."""

    if not _PREVIEW:
        return {}
    try:
        if not (data_dir() / MODELS_FOLDER / "versions").is_dir():
            return {}
        from row_bot.learning import versions

        return versions.offered()
    except Exception as exc:
        logger.debug("Learning couldn't list its personal models (%s)", type(exc).__name__)
        return {}


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
    "MODELS_FOLDER",
    "MODEL_DOWNLOADS",
    "PREVIEW_ENV",
    "SERVE_RUNTIMES",
    "SETTINGS_FILE",
    "STARTING_MODELS",
    "STORE_FILE",
    "TRAIN_PLACES",
    "WAYS_OF_WORKING_FILE",
    "changed",
    "data_dir",
    "folder_path",
    "invalidate",
    "is_enabled",
    "note_channel_sender",
    "note_retry",
    "offered_personal_models",
    "preview_enabled",
    "read_settings",
    "route_turn",
    "settings_path",
    "settings_saved",
    "start_past_chat_import",
    "start_runner",
    "store_exists",
    "store_path",
    "ways_of_working_text",
    "lesson_memory_deleted_soon",
    "lesson_memory_ids",
    "note_lessons_recalled",
    "LESSONS_FILE",
]
