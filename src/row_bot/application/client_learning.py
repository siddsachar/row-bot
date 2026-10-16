"""Learning (preview) for signed-in clients: reads and revision-checked commands.

The same shape as Insights (``client_insights.py``): a snapshot with a revision, and commands
that carry it, are admitted once (``admissions``) and answer with a receipt that holds the next
snapshot. Everything here is plumbing for the surfaces (L1a): turning Learning on with the setup
sheet's choices, off, pause and resume, Teach and its Undo, "Don't learn from this chat", the
answer to the download ask, and deleting learning data; the personal model's training (L2a) and
its download, Remove engine and Delete starting model (L2b). Nothing here diagnoses or tests.

Without the preview flag (O34) every read and command answers ``learning_unavailable``.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from collections.abc import Callable
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

from row_bot import learning
from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

logger = logging.getLogger(__name__)

_ACTIONS = frozenset({
    "turn_on", "turn_off", "pause", "resume", "delete_data", "opt_out", "opt_in", "teach",
    "undo_teach", "answer_download", "train_now", "stop_training", "continue_training",
    "discard_training", "use_recommended", "start_download", "stop_download", "cancel_download",
    "remove_engine", "delete_starting_model",
})
# The personal model's download (L2b): the training engine and the starting model.
_DOWNLOAD_ACTIONS = frozenset({"start_download", "stop_download", "cancel_download", "remove_engine",
                               "delete_starting_model"})
_DOWNLOAD_ERRORS = {
    "learning_off": "learning_off",
    "learning_paused": "learning_paused",
    "unavailable": "learning_download_unavailable",
    "not_enough_disk": "learning_not_enough_disk",
    "busy": "learning_download_busy",
    "changed": "learning_download_changed",
}
# The personal model's training (L2a): the person's actions on the current run.
_TRAINING_ACTIONS = frozenset({"train_now", "stop_training", "continue_training", "discard_training",
                               "use_recommended"})
_TRAINING_ERRORS = {
    "learning_off": "learning_off",
    "learning_paused": "learning_paused",
    "nothing_to_train": "learning_nothing_to_train",
    "training_changed": "learning_training_changed",
}
_RUNNER_REASONS = frozenset({
    "train_off", "hugging_face", "no_engine", "no_data", "quiet_hours", "after_dream", "now", "in_use", "chat",
    "workflow", "indexing", "dream", "you", "app_closed", "worker_stopped", "out_of_gpu_memory", "out_of_disk",
    "cannot_resume", "data_unreadable", "engine_error", "learning_off", "learning_paused", "extraction",
})
_FAILURES = frozenset({"out_of_gpu_memory", "out_of_disk", "cannot_resume", "data_unreadable", "engine_error"})
_SHOWN_STATES = frozenset({"planned", "waiting", "preparing", "running", "pausing", "paused", "evaluating", "failed"})
# The owner id each channel needs before its direct chats count as the owner's (O2), in words.
_OWNER_WORDS = {
    "telegram": "Telegram user ID",
    "discord": "Discord user ID",
    "slack": "Slack user ID",
    "sms": "phone number",
    "whatsapp": "phone number",
}
# Why a conversation isn't used, in words (scope reason codes never reach the client).
_NOT_USED = {
    "shared_conversation": "Chats with other people are never used.",
    "not_owner": "Chats with other people are never used.",
    "unknown_source": "Learning can’t tell who this chat is with, so it isn’t used.",
    "my_chats_off": "Learning from your chats is off in Settings › Learning.",
    "workflows_and_goals_off": "Learning from workflows and goals is off in Settings › Learning.",
}
_LESSON_LIMIT = 200


def _require_preview() -> None:
    if not learning.preview_enabled():
        raise ClientPlatformError("learning_unavailable")


def _text(value: object, maximum: int) -> str:
    return str(value or "")[:maximum]


def _root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False).absolute()


def _store():
    from row_bot.learning.store import LearningStore

    return LearningStore()


# ── Pieces of the snapshot ──────────────────────────────────────────────────


def _next_month(today: date) -> date:
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


def _default_model_name() -> str:
    """The default chat model's name as the catalog knows it (Learning's calls use it), or ""."""

    try:
        from row_bot.application.client_first_run import saved_default_model
        from row_bot.providers.selection import parse_model_ref

        ref = saved_default_model()
        parsed = parse_model_ref(ref) if ref else None
        if not parsed:
            return ""
        model_id = parsed[1]
        try:
            from row_bot.providers.model_catalog_cache import read_model_catalog_cache

            cache = read_model_catalog_cache(allow_runtime_bootstrap=False, max_bytes=16 * 1024 * 1024)
            info = cache.cloud_cache.get(model_id) or {}
            for key in ("display_name", "label", "name"):
                value = info.get(key) if isinstance(info, dict) else None
                if isinstance(value, str) and value.strip():
                    return _text(value.strip(), 256)
        except Exception:
            logger.debug("Learning could not read the model catalog", exc_info=True)
        return _text(model_id, 256)
    except Exception:
        logger.debug("Learning could not read the default model", exc_info=True)
        return ""


def _spending(settings: learning.LearningSettings) -> dict[str, Any]:
    used = 0
    try:
        from row_bot.learning import spending

        used = spending.used_this_month()
    except Exception:
        logger.debug("Learning could not read this month's tokens", exc_info=True)
    limit = settings.monthly_token_limit
    return {
        "used_tokens": max(0, int(used)),
        "limit_tokens": limit,
        "resets_on": _next_month(date.today()).isoformat(),
        "model_name": _default_model_name(),
        "limit_reached": settings.enabled and (limit == 0 or used >= limit),
    }


def _quiet_hours(root: Path) -> dict[str, Any]:
    start, end = 1, 5
    try:
        document = json.loads((root / "dream_config.json").read_text(encoding="utf-8"))
        if isinstance(document, dict):
            start = max(0, min(23, int(document.get("window_start", 1))))
            end = max(0, min(23, int(document.get("window_end", 5))))
    except (OSError, ValueError, TypeError):
        pass
    # Nothing runs in quiet hours yet (L3a adds the runner), so there is no next look.
    return {"window_start": start, "window_end": end, "next_look_at": None}


def _past_chats(settings: learning.LearningSettings) -> dict[str, Any]:
    if not learning.store_exists():
        return {"state": "none", "total": 0, "done": 0}
    try:
        from row_bot.learning import past_chats

        found = past_chats.progress()
    except Exception:
        logger.debug("Learning could not read the past-chat import", exc_info=True)
        found = None
    if found is None:
        return {"state": "none", "total": 0, "done": 0}
    state = {
        "planned": "reading", "running": "reading", "paused": "waiting", "stopped": "stopped",
        "ready": "done", "failed": "failed",
    }.get(found.state, "stopped")
    if state in {"reading", "waiting"} and (not settings.enabled or settings.paused):
        state = "stopped"
    return {"state": state, "total": max(0, found.total), "done": max(0, min(found.done, found.total))}


_ENGINE_STATES = {"not_installed": "not_downloaded", "installed": "installed", "damaged": "damaged",
                  "outdated": "outdated"}


def _download_view(view: Any) -> dict[str, Any]:
    """The download as the client shows it (L2b)."""

    if view is None:
        return {"state": "none", "phase": None, "done_bytes": 0, "total_bytes": 0, "needs_bytes": None,
                "free_bytes": None, "failure": None}
    return {
        "state": view.state,
        "phase": view.phase,
        "done_bytes": max(0, int(view.done_bytes)),
        "total_bytes": max(0, int(view.total_bytes)),
        "needs_bytes": None if view.needs_bytes is None else max(0, int(view.needs_bytes)),
        "free_bytes": None if view.free_bytes is None else max(0, int(view.free_bytes)),
        "failure": view.failure,
    }


def _downloads_view() -> Any:
    try:
        from row_bot.learning.training import downloads

        return downloads.get().view()
    except Exception:
        logger.debug("Learning could not read its download", exc_info=True)
        return None


def _personal_model(settings: learning.LearningSettings) -> dict[str, Any]:
    from row_bot.learning.training import hardware

    model = hardware.STARTING_MODELS.get(settings.starting_model, hardware.QWEN35_2B)
    view = _downloads_view()
    engine = _ENGINE_STATES.get(view.engine_state, "not_downloaded") if view is not None else "not_downloaded"
    starting = view.model_state if view is not None else "not_downloaded"
    # A training engine plugged in for tests and the browser fixture (the fake trainer) counts as
    # installed with its starting model.
    runner_module = sys.modules.get("row_bot.learning.runner")
    runner = runner_module.loaded() if runner_module is not None else None
    try:
        configured = runner is not None and type(runner.supervisor.engine).__name__ != "LocalEngine" \
            and bool(runner.supervisor.engine.ready())
    except Exception:
        configured = False
    if configured:
        engine, starting = "installed", "downloaded"
    return {
        "train_in_quiet_hours": settings.train_in_quiet_hours,
        "train_where": settings.train_where,
        "starting_model": _model_view(model),
        "download": settings.model_download,
        "engine": engine,
        "engine_bytes": view.engine_bytes if view is not None and engine == "installed" else 0,
        "engine_download_bytes": (view.engine_download_bytes if view is not None and view.engine_download_bytes
                                  else hardware.ENGINE_DOWNLOAD_BYTES),
        "starting_model_state": starting if starting in ("downloaded", "damaged") else "not_downloaded",
        "starting_model_bytes": view.model_bytes if view is not None and starting == "downloaded" else 0,
        "download_state": _download_view(None if configured else view),
    }


def _model_view(model: Any) -> dict[str, Any]:
    return {"id": model.id, "name": model.name, "licence": model.licence, "download_bytes": model.download_bytes}


def _no_training(runner: dict[str, Any]) -> dict[str, Any]:
    return {"state": "none", "job_id": None, "version": None, "step": 0, "steps_total": None,
            "checkpoint_step": None, "seconds_left": None, "pause_reason": None, "resumes": None,
            "failure": None, "recovered": None, "trigger": None, "can_train_now": False, "runner": runner}


def _training(settings: learning.LearningSettings) -> dict[str, Any]:
    """The personal model's current training run, and what the quiet-hours runner would do.

    Reads only: nothing starts, and no store is created.
    """

    if not settings.enabled:
        return _no_training({"state": "off", "reason": None})
    try:
        from row_bot.learning import runner as runner_module
        from row_bot.learning.training.jobs import HELD, LIVE

        runner = runner_module.get()
        status = runner.status()
        runner_view = {"state": status.state, "reason": status.reason if status.reason in _RUNNER_REASONS else None}
        supervisor = runner.supervisor
        job = supervisor.jobs.current() if learning.store_exists() else None
        if job is None or job.state not in _SHOWN_STATES:
            return _no_training(runner_view)
        state, reason = job.state, job.pause_reason
        if state in LIVE and supervisor.live_job_id() != job.id:
            # Left running when Row-Bot closed; the runner pauses it when it next starts.
            state, reason = "paused", "app_closed"
        seconds_left = None
        if state in ("running", "pausing") and job.seconds_per_step and job.steps_total:
            seconds_left = int(max(0, job.steps_total - job.steps_done) * job.seconds_per_step)
        resumes = None
        if state == "paused":
            resumes = "ask" if reason in HELD else "when_free" if job.trigger == "train_now" else "quiet_hours"
        can_train_now = (
            not settings.paused and supervisor.engine.ready()
            and state in ("planned", "paused") and settings.train_where == "this_computer"
        )
        return {
            "state": state,
            "job_id": job.id,
            "version": job.version,
            "step": job.steps_done,
            "steps_total": job.steps_total,
            "checkpoint_step": job.checkpoint_step,
            "seconds_left": seconds_left,
            "pause_reason": reason,
            "resumes": resumes,
            "failure": (job.error_code if job.error_code in _FAILURES else "engine_error") if state == "failed" else None,
            "recovered": job.recovered,
            "trigger": job.trigger,
            "can_train_now": bool(can_train_now),
            "runner": runner_view,
        }
    except Exception:
        logger.debug("Learning could not read its training", exc_info=True)
        return _no_training({"state": "nothing_to_do", "reason": None})


def _data() -> dict[str, Any]:
    if not learning.store_exists():
        return {"exists": False, "bytes": 0}
    try:
        size = _store().storage_bytes()
        vault = learning.folder_path() / "audit.db"
        if vault.is_file():
            size += vault.stat().st_size
        from row_bot.learning.training import jobs as training_jobs

        runs = training_jobs.runs_dir()
        if runs.is_dir() and not runs.is_symlink():
            size += sum(path.stat().st_size for path in runs.rglob("*") if path.is_file() and not path.is_symlink())
    except OSError:
        size = 0
    return {"exists": True, "bytes": max(0, int(size))}


def _owner_unset_channels() -> list[dict[str, str]]:
    """Channels set up for pairing only: their chats are never the owner's (O2), so say so."""

    gaps: list[dict[str, str]] = []
    try:
        from row_bot.channels.auth_store import get_channel_secret
        from row_bot.channels.passive_catalog import passive_core_channels
        from row_bot.learning.scope import OWNER_SECRETS

        for channel in passive_core_channels():
            owner_key = OWNER_SECRETS.get(channel.name)
            if owner_key is None:
                continue
            fields = {field.key: field for field in channel.config_fields}
            required = [fields[key].env_key for key in channel.required_fields if key in fields]
            if not required or not all(
                str(get_channel_secret(channel.name, key) or "").strip() for key in required if key
            ):
                continue  # not set up
            if str(get_channel_secret(channel.name, owner_key) or "").strip():
                continue
            name = channel.display_name
            gaps.append({
                "channel": channel.name,
                "name": name,
                "note": f"{name} chats are used once your {_OWNER_WORDS.get(channel.name, 'user ID')} "
                        f"is set in Settings › Apps › {name}.",
            })
    except Exception:
        logger.debug("Learning could not read the channels' owners", exc_info=True)
    return gaps[:8]


def _control_state(settings: learning.LearningSettings, opted_out: list[str]) -> dict[str, Any]:
    """What a command's revision covers: the saved choices and the switched-off chats, never
    progress or spending (they change by themselves)."""

    return {
        "enabled": settings.enabled,
        "paused": settings.paused,
        "readable": settings.readable,
        "sources": [settings.my_chats, settings.workflows_and_goals, settings.past_chats],
        "apply_mode": settings.apply_mode,
        "limit": settings.monthly_token_limit,
        "train": [settings.train_in_quiet_hours, settings.train_where, settings.starting_model],
        "download": settings.model_download,
        "opted_out": sorted(opted_out),
    }


def read_learning(*, validate: Callable[[], None]) -> dict[str, Any]:
    """Learning's state for Settings › Learning, the setup sheet and the Learning tab."""

    _require_preview()
    validate()
    root = _root()
    settings = learning.read_settings()
    opted_out = _store().opted_out_chats() if learning.store_exists() else []
    state = "off" if not settings.enabled else "paused" if settings.paused else "on"
    training = _training(settings)  # first: it loads the runner the engine's state comes from
    data = {
        "schema_version": 1,
        "state": state,
        "readable": settings.readable,
        "sources": {
            "my_chats": settings.my_chats,
            "workflows_and_goals": settings.workflows_and_goals,
            "past_chats": settings.past_chats,
        },
        "apply_mode": settings.apply_mode,
        "spending": _spending(settings),
        "quiet_hours": _quiet_hours(root),
        "past_chats": _past_chats(settings),
        "personal_model": _personal_model(settings),
        "training": training,
        "data": _data(),
        "opted_out_conversations": [_text(item, 256) for item in opted_out[:2000]],
        "owner_unset_channels": _owner_unset_channels(),
    }
    data["revision"] = sha256(
        json.dumps(_control_state(settings, opted_out), sort_keys=True).encode("utf-8")
    ).hexdigest()
    validate()
    return data


def read_learning_training(*, validate: Callable[[], None]) -> dict[str, Any]:
    """Only the training run (Monitor's row and the Personal model view poll this while it runs)."""

    _require_preview()
    validate()
    result = _training(learning.read_settings())
    validate()
    return result


def read_learning_download(*, validate: Callable[[], None]) -> dict[str, Any]:
    """Only the download (Monitor's row and the Personal model view poll this while it runs)."""

    _require_preview()
    validate()
    result = _download_view(_downloads_view())
    validate()
    return result


def read_learning_hardware(*, validate: Callable[[], None], refresh: bool = False) -> dict[str, Any]:
    """What this computer can train (an ``nvidia-smi`` query, kept for the process) and its free space."""

    _require_preview()
    validate()
    from row_bot.learning.training import hardware

    found = hardware.check(refresh=refresh)
    try:
        free = int(shutil.disk_usage(_root()).free)
    except OSError:
        free = None

    def tier(value: Any) -> dict[str, Any]:
        return {"id": value.id, "label": value.label, "certified": value.certified, "summary": value.summary}

    result = {
        "state": found.state,
        "gpu": {"name": _text(found.gpu.name, 128), "memory_gb": found.gpu.memory_gb, "label": _text(found.gpu.label, 160)}
        if found.gpu else None,
        "tier": tier(found.tier) if found.tier else None,
        "larger_tiers": [tier(item) for item in found.larger_tiers],
        "starting_models": [_model_view(item) for item in (found.tier.models if found.tier else ())],
        "engine_download_bytes": hardware.engine_download_bytes(found.variant),
        "free_bytes": free,
    }
    validate()
    return result


def read_learning_conversation(conversation_id: str, *, validate: Callable[[], None]) -> dict[str, Any]:
    """Whether Learning uses this conversation, so a chat knows to offer Teach and its menu item."""

    _require_preview()
    validate()
    from dataclasses import replace

    from row_bot.learning import scope
    from row_bot.learning.contracts import valid_thread_id

    clean = _text(conversation_id, 256)
    if not valid_thread_id(clean):
        raise ClientPlatformError("invalid_learning_command")
    settings = learning.read_settings()
    state, reason = "off", ""
    if settings.enabled:
        facts = scope.describe_thread(clean)
        store = _store()
        opted = any(store.is_chat_opted_out(item)
                    for item in dict.fromkeys(filter(None, (clean, facts.root_thread_id))))
        if opted:
            state = "opted_out"
        else:
            decision = scope.decide(facts, replace(settings, paused=False))
            if decision.allowed:
                state = "paused" if settings.paused else "learning"
            else:
                state = "not_used"
                if decision.reason == scope.OWNER_UNKNOWN and facts.channel:
                    gap = next((item for item in _owner_unset_channels() if item["channel"] == facts.channel), None)
                    reason = gap["note"] if gap else _NOT_USED["unknown_source"]
                else:
                    reason = _NOT_USED.get(decision.reason, "")
    validate()
    return {"conversation_id": clean, "state": state, "reason": reason}


def read_learning_lessons(*, validate: Callable[[], None]) -> dict[str, Any]:
    """Lessons: so far only the ones Row-Bot saved for itself during chats (its troubleshooting
    memories), which nothing has tested yet. Read-only."""

    _require_preview()
    validate()
    items: list[dict[str, Any]] = []
    try:
        from row_bot import knowledge_graph

        rows = knowledge_graph.list_entity_summaries("self_knowledge", limit=_LESSON_LIMIT, description_chars=600)
    except Exception:
        logger.debug("Learning could not read the saved lessons", exc_info=True)
        rows = []
    for row in rows:
        subject = _text(row.get("subject"), 200).strip()
        if not subject:
            continue
        items.append({
            "id": _text(row.get("id"), 128),
            "title": subject,
            "text": _text(row.get("description"), 600),
            "origin": "agent_saved",
            "status": "not_tested",
            "updated_at": _text(row.get("updated_at"), 64),
        })
    validate()
    return {"items": items, "total": len(items)}


# ── Commands ─────────────────────────────────────────────────────────────────


def _setup_values(setup: Any) -> dict[str, Any]:
    if not isinstance(setup, dict):
        raise ClientPlatformError("invalid_learning_command")
    sources = setup.get("sources") if isinstance(setup.get("sources"), dict) else {}
    values: dict[str, Any] = {}
    for key in ("my_chats", "workflows_and_goals", "past_chats"):
        value = sources.get(key)
        if type(value) is not bool:
            raise ClientPlatformError("invalid_learning_command")
        values[f"sources.{key}"] = value
    limit = setup.get("monthly_token_limit")
    if (setup.get("apply_mode") not in learning.APPLY_MODES
            or setup.get("train_where") not in learning.TRAIN_PLACES
            or setup.get("starting_model", learning.STARTING_MODELS[0]) not in learning.STARTING_MODELS
            or type(setup.get("train_in_quiet_hours")) is not bool
            or type(limit) is not int or not 0 <= limit <= learning.MAX_MONTHLY_TOKEN_LIMIT):
        raise ClientPlatformError("invalid_learning_command")
    values.update({
        "apply_mode": setup["apply_mode"],
        "monthly_token_limit": limit,
        "train_in_quiet_hours": setup["train_in_quiet_hours"],
        "train_where": setup["train_where"],
        "starting_model": setup.get("starting_model", learning.STARTING_MODELS[0]),
        "paused": False,
        # Turning on last: the store and the past-chat import see every choice.
        "enabled": True,
    })
    return values


def _save(values: dict[str, Any]) -> None:
    from row_bot.application.settings_commands import SettingsCommandError, write_learning_settings

    try:
        write_learning_settings(values)
    except SettingsCommandError as error:
        raise ClientPlatformError(
            "learning_settings_unavailable" if error.code == "settings_unavailable" else "learning_save_unconfirmed"
        ) from None


def _size_words(size: int) -> str:
    """"4.6 GB", the number kept with its unit (a non-breaking space)."""
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f}\u00a0GB"
    if size >= 1_000_000:
        return f"{max(1, round(size / 1_000_000))}\u00a0MB"
    if size >= 1_000:
        return f"{max(1, round(size / 1_000))}\u00a0KB"
    return f"{size}\u00a0bytes"


def _progress_words(done: int, total: int) -> str:
    """"3.1 of 4.6 GB" (UX §9): both numbers in the total's unit."""

    giga = total >= 1_000_000_000
    unit, name = (1_000_000_000, "GB") if giga else (1_000_000, "MB")

    def show(value: int) -> str:
        return f"{value / unit:.1f}".removesuffix(".0") if giga else str(round(value / unit))

    return f"{show(min(max(0, done), total))} of {show(total)}\u00a0{name}"


def _target(action: str, command: dict[str, Any]) -> str:
    if action in {"teach", "opt_out", "opt_in"}:
        return f"learning:chat:{command.get('conversation_id', '')}"
    if action == "undo_teach":
        return f"learning:signal:{command.get('signal_id', '')}"
    if action in _TRAINING_ACTIONS:
        return "learning:training"
    if action in _DOWNLOAD_ACTIONS:
        return "learning:download"
    return "learning:settings"


def _train(action: str, job_id: str) -> str:
    """The person's action on the current training run: what happened, in words."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning.training.supervisor import TrainingError

    supervisor = runner_module.get().supervisor
    try:
        if action in ("train_now", "continue_training"):
            job = supervisor.train_now(job_id or None)
            if job.state == "waiting":
                summary = "Training starts when Row-Bot is free."
            elif job.recovered == "start_over":
                summary = "Training starts again from the beginning."
            elif job.steps_done:
                summary = f"Training continues from step {job.steps_done:,}."
            else:
                summary = "Training started."
        elif action == "stop_training":
            before = supervisor.jobs.current() if learning.store_exists() else None
            job = supervisor.stop(job_id or None)
            step = job.checkpoint_step or 0
            if before is not None and before.state == "waiting":
                summary = "Training won’t start now."
            else:
                summary = (f"Training stopped. It can continue from step {step:,}." if step
                           else "Training stopped.")
        elif action == "discard_training":
            supervisor.discard(job_id or None)
            summary = "Training discarded."
        else:
            supervisor.use_recommended(job_id or None)
            summary = "Training will use the recommended settings next time."
    except TrainingError as error:
        raise ClientPlatformError(_TRAINING_ERRORS.get(error.code, "learning_training_changed")) from None
    # The runner keeps it going (pauses on use, continues when free).
    runner_module.start_background()
    return summary


def _download_words(view: Any, model_name: str) -> str:
    """"Downloading the training engine and Qwen3.5 2B." (what is still missing)."""

    engine = view.engine_state != "installed"
    model = view.model_state != "downloaded"
    if engine and model:
        return f"Downloading the training engine and {model_name}."
    if engine:
        return "Downloading the training engine."
    if model:
        return f"Downloading {model_name}."
    return "The training engine and starting model are downloaded."


def _training_runs() -> bool:
    runner_module = sys.modules.get("row_bot.learning.runner")
    try:
        return runner_module is not None and bool(runner_module.training_now())
    except Exception:
        return False


def _download(action: str) -> str:
    """The person's action on the personal model's download: what happened, in words."""

    from row_bot.learning.training import downloads, engine, hardware, weights

    task = downloads.get()
    settings = learning.read_settings()
    model = weights.MODELS.get(settings.starting_model, weights.QWEN35_2B)
    name = hardware.STARTING_MODELS.get(settings.starting_model, hardware.QWEN35_2B).name
    try:
        if action == "start_download":
            if _training_runs():
                raise downloads.DownloadError("busy")
            missing = task.view()
            task.start()
            return _download_words(missing, name)
        if action == "stop_download":
            view = task.stop("you")
            if view.total_bytes:
                return f"Download paused at {_progress_words(view.done_bytes, view.total_bytes)}."
            return "Download paused."
        if action == "cancel_download":
            task.cancel()
            _save({"model_download": "not_now"})
            return "Download cancelled. What it had downloaded is deleted."
        if task.running() or _training_runs():
            raise downloads.DownloadError("busy")
        if action == "remove_engine":
            freed = engine.remove()
            task.forget()
            if settings.model_download == "waiting":
                _save({"model_download": "not_now"})
            return f"Training engine removed. {_size_words(freed)} freed."
        freed = weights.delete(model)
        task.forget()
        if settings.model_download == "waiting":
            _save({"model_download": "not_now"})
        return f"{name} deleted. {_size_words(freed)} freed."
    except downloads.DownloadError as error:
        raise ClientPlatformError(_DOWNLOAD_ERRORS.get(error.code, "learning_download_changed")) from None


def _answer_download(answer: str) -> str:
    """Download / Not now from the ask: Download starts the download now (L2b)."""

    _save({"model_download": "waiting" if answer == "download" else "not_now"})
    if answer != "download":
        return "Not downloading for now."
    from row_bot.learning.training import downloads, hardware

    settings = learning.read_settings()
    name = hardware.STARTING_MODELS.get(settings.starting_model, hardware.QWEN35_2B).name
    try:
        if _training_runs():
            raise downloads.DownloadError("busy")
        task = downloads.get()
        missing = task.view()
        task.start()
        return _download_words(missing, name)
    except downloads.DownloadError as error:
        if error.code == "not_enough_disk":
            return "There isn’t enough free disk space for the download yet."
        if error.code == "learning_paused":
            return "Ready to download once you resume Learning."
        return "Ready to download."


def execute_learning(command: dict, *, owner_id: str, validate: Callable[[], None]) -> dict:
    _require_preview()
    action = command.get("action")
    if action not in _ACTIONS:
        raise ClientPlatformError("invalid_learning_command")
    conversation = _text(command.get("conversation_id"), 256)
    signal_id = _text(command.get("signal_id"), 128)
    if action in {"teach", "opt_out", "opt_in"} and not conversation:
        raise ClientPlatformError("invalid_learning_command")
    if action == "undo_teach" and not signal_id:
        raise ClientPlatformError("invalid_learning_command")
    if action == "turn_on" and command.get("setup") is None:
        raise ClientPlatformError("invalid_learning_command")
    if action == "answer_download" and command.get("download") not in {"download", "not_now"}:
        raise ClientPlatformError("invalid_learning_command")
    job_id = _text(command.get("job_id"), 128)
    if action in _TRAINING_ACTIONS - {"train_now"} and not job_id:
        raise ClientPlatformError("invalid_learning_command")
    target = _target(action, command)
    wire = {
        "command_id": command["command_id"],
        "type": f"learning.{action}",
        "action": action,
        "revision": command["revision"],
        "setup": command.get("setup"),
        "conversation_id": conversation,
        "message_id": _text(command.get("message_id"), 256),
        "tool_call_id": _text(command.get("tool_call_id"), 256),
        "category": command.get("category"),
        "note": _text(command.get("note"), 2000),
        "signal_id": signal_id,
        "download": command.get("download"),
        "job_id": job_id,
    }
    existing = admissions.read_command_metadata(owner_id, command["command_id"])
    if existing is not None:
        if existing["target"] != target or existing["type"] != wire["type"]:
            raise ClientPlatformError("idempotency_mismatch")
        return admissions.claim_command(owner_id, command["command_id"], wire, target)
    snapshot = read_learning(validate=validate)
    if snapshot["revision"] != command["revision"]:
        raise ClientPlatformError("learning_revision_conflict", snapshot["revision"])
    state = snapshot["state"]
    setup_values = _setup_values(command.get("setup")) if action == "turn_on" else {}
    if action in {"turn_off", "pause", "resume", "answer_download"} and state == "off":
        raise ClientPlatformError("learning_off")
    if action == "pause" and state == "paused" or action == "resume" and state != "paused":
        raise ClientPlatformError("learning_revision_conflict", snapshot["revision"])
    if action == "turn_on" and state != "off":
        raise ClientPlatformError("learning_revision_conflict", snapshot["revision"])
    if action == "delete_data" and not snapshot["data"]["exists"]:
        raise ClientPlatformError("learning_data_missing")
    if action == "teach":
        from row_bot.learning import teach

        if wire["category"] is not None and wire["category"] not in teach.CATEGORIES:
            raise ClientPlatformError("invalid_learning_command")
    admissions.claim_command(owner_id, command["command_id"], wire, target, exclusive_target=True)
    try:
        validate()
        summary, signal = _perform(action, command, wire, conversation, signal_id, setup_values)
        validate()
    except ClientPlatformError as error:
        # A refusal is final: the same command answers it again, and the next can run.
        admissions.reject_command(owner_id, command["command_id"], error.code, error.current_revision)
        raise
    outcome = {
        "command_id": command["command_id"],
        "status": "completed",
        "summary": summary,
        "snapshot": read_learning(validate=validate),
        "signal_id": signal,
    }
    return admissions.complete_command(owner_id, command["command_id"], outcome)


def _perform(
    action: str,
    command: dict,
    wire: dict[str, Any],
    conversation: str,
    signal_id: str,
    setup_values: dict[str, Any],
) -> tuple[str, str | None]:
    """Carry out one admitted command: (what happened, in words; the saved Teach's id)."""

    signal = None
    if action == "turn_on":
        _save(setup_values)
        summary = "Learning is on."
    elif action == "turn_off":
        _save({"enabled": False, "paused": False})
        summary = "Learning is off. Your learning data is kept until you delete it."
    elif action == "pause":
        _save({"paused": True})
        summary = "Learning is paused."
    elif action == "resume":
        _save({"paused": False})
        summary = "Learning resumed."
    elif action == "answer_download":
        summary = _answer_download(command["download"])
    elif action in _DOWNLOAD_ACTIONS:
        summary = _download(action)
    elif action in {"opt_out", "opt_in"}:
        from row_bot.learning import scope
        from row_bot.learning.store import LearningStoreError

        try:
            scope.set_chat_opt_out(conversation, action == "opt_out")
        except LearningStoreError as error:
            raise ClientPlatformError("learning_off" if error.code == "learning_off" else "learning_data_busy") from None
        except ValueError:
            raise ClientPlatformError("invalid_learning_command") from None
        summary = (
            "Row-Bot won’t learn from this chat. What it recorded from it is deleted."
            if action == "opt_out" else "Row-Bot can learn from this chat again."
        )
    elif action == "teach":
        from row_bot.learning import teach

        try:
            taught = teach.teach(
                conversation,
                message_id=wire["message_id"],
                tool_call_id=wire["tool_call_id"],
                category=wire["category"],
                note=wire["note"],
            )
        except teach.TeachError as error:
            raise ClientPlatformError(error.code) from None
        signal = taught.signal.id
        summary = "Thanks. Row-Bot will learn from this."
    elif action == "undo_teach":
        from row_bot.learning import teach

        removed = teach.undo(signal_id)
        summary = "Removed." if removed else "Already removed."
    elif action in _TRAINING_ACTIONS:
        summary = _train(action, wire["job_id"])
    else:
        from row_bot.learning.store import LearningStoreError
        from row_bot.learning.training import jobs as training_jobs

        # Training stops first: its leased data and checkpoints are learning data too.
        runner_module = sys.modules.get("row_bot.learning.runner")
        if runner_module is not None:
            runner_module.forget_training()
        runs = training_jobs.runs_dir()
        runs_bytes = 0
        if runs.is_dir() and not runs.is_symlink():
            runs_bytes = sum(path.stat().st_size for path in runs.rglob("*") if path.is_file() and not path.is_symlink())
        training_jobs.remove_all_runs()
        try:
            report = _store().delete_all()
        except LearningStoreError:
            raise ClientPlatformError("learning_data_busy") from None
        summary = f"Learning data deleted ({_size_words(report.bytes_freed + runs_bytes)})."
    return summary, signal


def read_learning_receipt(command_id: str, *, owner_id: str, validate: Callable[[], None]) -> dict:
    _require_preview()
    validate()
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None or not metadata["type"].startswith("learning."):
        raise ClientPlatformError("learning_receipt_unavailable")
    result = admissions.read_command_receipt(owner_id, command_id)
    if result is None:
        raise ClientPlatformError("learning_receipt_unavailable")
    if result["status"] == "rejected":
        raise ClientPlatformError(str(result.get("code") or "learning_receipt_unavailable"))
    if result["status"] == "admitting":
        return {
            "command_id": command_id,
            "status": "uncertain",
            "summary": "Row-Bot couldn’t confirm that. Check Learning before trying again.",
            "snapshot": read_learning(validate=validate),
            "signal_id": None,
        }
    validate()
    return result


__all__ = [
    "execute_learning",
    "read_learning",
    "read_learning_conversation",
    "read_learning_download",
    "read_learning_hardware",
    "read_learning_lessons",
    "read_learning_receipt",
    "read_learning_training",
]
