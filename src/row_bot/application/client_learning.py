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
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from row_bot import learning
from row_bot.application import learning_hugging_face as _hugging_face
from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

logger = logging.getLogger(__name__)

_ACTIONS = frozenset({
    "turn_on", "turn_off", "pause", "resume", "delete_data", "opt_out", "opt_in", "teach",
    "undo_teach", "answer_download", "train_now", "stop_training", "continue_training",
    "discard_training", "use_recommended", "start_download", "stop_download", "cancel_download",
    "remove_engine", "delete_starting_model", "make_ready", "choose_runtime",
    "compare_version", "stop_compare", "use_version", "keep_version", "keep_not_now", "undo_use",
    "export_version", "delete_version", "save_advanced", "reset_advanced", "retrain_without", "dismiss",
    "review_version", "use_smaller_model", "change_starting_model",
    "test_change", "use_change", "undo_change", "not_now_change", "dont_suggest_change", "edit_change",
    "test_lesson", "delete_lesson", "undo_method",
})
# Changes Learning drafted (L3b): test, use, undo, not now, don't suggest again, edit; a lesson Row-Bot saved
# for itself tested, or deleted. Every one is the person's own choice (D24).
_CHANGE_ACTIONS = frozenset({"test_change", "use_change", "undo_change", "not_now_change", "dont_suggest_change",
                             "edit_change", "test_lesson", "delete_lesson"})
_CHANGE_ERRORS = {
    "not_found": "learning_change_changed", "not_ready": "learning_change_changed",
    "edited": "learning_change_changed", "not_in_use": "learning_change_changed",
    "e9": "learning_change_refused", "never_automatic": "learning_change_refused",
    "empty": "invalid_learning_command", "too_long": "invalid_learning_command",
    "unknown_slot": "invalid_learning_command", "slot_full": "learning_change_refused",
    "apply_failed": "learning_change_changed", "running": "learning_change_busy",
}
# Using the personal model (L2c2): its comparison, trial, keep and undo, Export…, Delete version,
# Advanced and Retrain without it. Every one of them is the person's own choice (D24).
_USE_ACTIONS = frozenset({"compare_version", "stop_compare", "use_version", "keep_version", "keep_not_now",
                          "undo_use", "export_version", "delete_version", "save_advanced", "reset_advanced",
                          "retrain_without", "dismiss", "review_version", "use_smaller_model",
                          "change_starting_model"})
_KIND_ACTIONS = frozenset({"use_version", "keep_version", "keep_not_now", "undo_use"})
_VERSION_NUMBER_ACTIONS = frozenset({"compare_version", "use_version", "export_version", "delete_version",
                                     "retrain_without", "review_version"})
# The personal model's version (L2c): make it ready again (Try again, Check again), or choose where it runs.
_VERSION_ACTIONS = frozenset({"make_ready", "choose_runtime"})
# The personal model's download (L2b): the training engine and the starting model.
_ACTIONS = _ACTIONS | _hugging_face.ACTIONS
# The Hugging Face commands for one run (the run the person saw).
_HF_JOB_ACTIONS = frozenset({"hf_start", "hf_cancel", "hf_check_now"})
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
    "planned", "planning", "up_to_date", "gpu_busy", "memory_busy", "comparing",
    # C1: training on Hugging Face.
    "hf_signed_out", "hf_no_model", "hf_unknown", "hf_first_run", "hf_over_cap", "hf_no_credit", "hf_network",
    "hf_busy", "hf_not_confirmed",
})
_VERSION_PHASES = frozenset({"exporting", "export_failed", "making_ready", "ready", "runtime_failed",
                             "waiting_for_you"})
_VERSION_FAILURES = frozenset({"not_enough_disk", "adapter_missing", "engine_missing", "tools_missing",
                               "export_failed", "not_installed", "not_running", "not_local", "create_failed",
                               "not_loaded", "probe_failed", "tool_turn_failed", "server_missing",
                               "server_unavailable", "server_failed", "load_failed"})
_FAILURES = frozenset({"out_of_gpu_memory", "out_of_disk", "cannot_resume", "data_unreadable", "engine_error"})
_SHOWN_STATES = frozenset({"planned", "waiting", "preparing", "running", "pausing", "paused", "evaluating", "failed",
                           "unknown_remote"})
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
    "incognito_chat": "Learning doesn’t use chats that are or were incognito.",
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


def _quiet_hours(root: Path, settings: learning.LearningSettings, night: dict[str, Any] | None,
                 now: datetime | None = None) -> dict[str, Any]:
    start, end = 1, 5
    try:
        document = json.loads((root / "dream_config.json").read_text(encoding="utf-8"))
        if isinstance(document, dict):
            start = max(0, min(23, int(document.get("window_start", 1))))
            end = max(0, min(23, int(document.get("window_end", 5))))
    except (OSError, ValueError, TypeError):
        pass
    return {"window_start": start, "window_end": end, "next_look_at": _next_look(start, end, settings, night, now)}


def _next_look(start: int, end: int, settings: learning.LearningSettings, night: dict[str, Any] | None,
               now: datetime | None = None) -> str | None:
    """When Learning next looks for things to improve (L3a): the next quiet hours' start, or tonight's when
    tonight's look hasn't run yet. None while Learning is off or paused."""

    if not settings.enabled or settings.paused:
        return None
    moment = now or datetime.now()
    began = moment.replace(hour=start, minute=0, second=0, microsecond=0)
    inside = start <= moment.hour < end if start <= end else (moment.hour >= start or moment.hour < end)
    if inside and began > moment:
        began -= timedelta(days=1)
    if inside:
        done = bool(night and night.get("night") == began.date().isoformat() and night.get("state") != "stopped")
        upcoming = began + timedelta(days=1) if done else moment
    else:
        upcoming = began if began > moment else began + timedelta(days=1)
    return upcoming.isoformat(timespec="minutes")


_OVERNIGHT_STATES = frozenset({"done", "nothing", "stopped", "claude_plan", "no_model", "limit", "unavailable",
                               "failed"})


def _night() -> dict[str, Any] | None:
    if not learning.store_exists():
        return None
    try:
        from row_bot.learning import diagnosis

        return diagnosis.last_night(_store())
    except Exception:
        logger.debug("Learning could not read last night's look", exc_info=True)
        return None


def _tests() -> dict[str, Any] | None:
    if not learning.store_exists():
        return None
    try:
        from row_bot.learning import changes

        return changes.last_tests(_store())
    except Exception:
        logger.debug("Learning could not read last night's tests", exc_info=True)
        return None


def _trial_night() -> dict[str, Any] | None:
    if not learning.store_exists():
        return None
    try:
        from row_bot.learning.improver import meta

        return meta.last_night(_store())
    except Exception:
        logger.debug("Learning could not read last night's trial", exc_info=True)
        return None


def _method_line(trial: dict[str, Any] | None, night: str) -> dict[str, Any]:
    """The night's strategy trial for the Overnight lines: a switch (with what it changes) or a kept method."""

    if not trial or trial.get("night") != night or trial.get("state") != "done":
        return {}
    switched = trial.get("switched")
    if isinstance(switched, dict) and switched.get("to"):
        return {"method": "switched", "method_version": max(1, int(switched["to"])),
                "method_words": _text(switched.get("words"), 200)}
    from row_bot.learning.improver import archive

    current = archive.current(_store(), create=False)
    return {"method": "kept", "method_version": current.version if current else 1}


def _overnight(night: dict[str, Any] | None, today: date | None = None,
               tests: dict[str, Any] | None = None, trial: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Last night's look, its tests and its strategy trial (UX §3 Overnight): only a recent one, in counts."""

    if not night or night.get("state") not in _OVERNIGHT_STATES:
        return None
    try:
        when = date.fromisoformat(str(night.get("night")))
    except ValueError:
        return None
    if ((today or date.today()) - when).days > 1:
        return None
    same = tests if tests and tests.get("night") == night.get("night") else {}

    def count(name: str) -> int:
        return max(0, int(same.get(name) or 0))

    return {"night": when.isoformat(), "state": night["state"], "found": max(0, int(night.get("found") or 0)),
            "drafted": max(0, int(night.get("drafted") or 0)), "left_out": max(0, int(night.get("refused") or 0)),
            "needs_examples": max(0, int(night.get("needs_examples") or 0)),
            "looks_like_bug": max(0, int(night.get("bugs") or 0)),
            "tested": count("tested"), "ready": count("ready"), "not_better": count("not_better"),
            "used": count("used"),
            "tests_state": same.get("state") if same.get("state") in ("stopped", "limit", "claude_plan", "failed")
            else None, **_method_line(trial, when.isoformat())}


def _testing_now() -> dict[str, Any] | None:
    """The change being tested now ("Testing 'Pick documents by ID' on 24 tasks · 9 done"), else None."""

    if not learning.store_exists():
        return None
    try:
        from row_bot.learning import changes

        found = changes.progress()
    except Exception:
        logger.debug("Learning could not read its test's progress", exc_info=True)
        return None
    if not found:
        return None
    return {"change_id": _text(found.get("change_id"), 128), "title": _text(found.get("title"), 200),
            "done": max(0, int(found.get("done") or 0)), "total": max(0, int(found.get("total") or 0))}


_CHANGE_OF = {"lesson": "lesson", "behaviour": "way_of_working", "create_skill": "skill", "patch_skill": "skill"}
_CHANGE_VERDICTS = frozenset({"better", "no_difference", "worse", "worse_elsewhere"})
_KINDS = ("document_lookups", "structured_transforms", "small_coding", "tool_workflows", "other")
_WHY = ("chats", "corrections", "stops", "retries", "denials", "failures", "imported")


def _test_view(meta: dict[str, Any]) -> dict[str, Any] | None:
    test = meta.get("test")
    if not isinstance(test, dict) or test.get("verdict") not in _CHANGE_VERDICTS:
        return None
    rows = [{"label": _text(row.get("label"), 120), "total": max(0, int(row.get("total") or 0)),
             "without": max(0, int(row.get("without") or 0)), "with_change": max(0, int(row.get("with") or 0))}
            for row in (test.get("rows") or ())[:4] if isinstance(row, dict)]
    own = test.get("own") if isinstance(test.get("own"), dict) else {}

    def points(value: Any) -> float | None:
        return None if value is None else round(float(value) * 100, 1)

    return {"verdict": test["verdict"], "rows": rows, "note": _text(test.get("note"), 300),
            "small": bool(test.get("small")), "tested_at": _iso(test.get("tested_at")),
            "difference": points(own.get("difference")), "low": points(own.get("low")),
            "high": points(own.get("high"))}


def _diff_of(proposal: dict[str, Any]) -> str:
    """A skill edit's before/after (``patch_skill``), read when the change is shown."""

    if proposal.get("proposal_type") != "patch_skill":
        return ""
    payload = proposal.get("payload") or {}
    try:
        from row_bot import evolution

        found = evolution.build_skill_diff_preview(str(payload.get("target_skill") or ""),
                                                   str(payload.get("updated_instructions") or ""))
    except Exception:
        logger.debug("Learning couldn't show a skill edit's diff", exc_info=True)
        return ""
    return _text(found.get("diff"), 6000)


def _changes() -> list[dict[str, Any]]:
    """Learning's changes (UX §7): waiting to be tested, testing, ready, edited, in use and not used,
    newest first. Not-used changes older than 30 days drop off the list."""

    try:
        from row_bot.learning import behaviour, changes, diagnosis

        store = _store() if learning.store_exists() else None
        gone = diagnosis._gone_attempts(store) if store is not None else set()  # noqa: SLF001 - its own module's rule
        proposals = [item for item in changes.learning_changes()
                     if changes.block(item).get("attempt_id") not in gone or changes.view_state(item) == "in_use"]
    except Exception:
        logger.debug("Learning could not read its changes", exc_info=True)
        return []
    found = []
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()
    for proposal in proposals:
        change = _CHANGE_OF.get(str(proposal.get("proposal_type")))
        if change is None:
            continue
        state = changes.view_state(proposal)
        if state == "not_used" and str(proposal.get("updated_at") or "") < month_ago:
            continue
        payload = proposal.get("payload") or {}
        meta = changes.block(proposal)
        why = meta.get("why") if isinstance(meta.get("why"), dict) else {}
        text = payload.get("text") or payload.get("description") or payload.get("updated_instructions") or ""
        kind = str(meta.get("kind_of_work") or "other")
        reason = str(meta.get("state") or "")
        found.append({
            "id": _text(proposal.get("id"), 128),
            "change": change,
            "title": _text(proposal.get("title"), 200),
            "kind": kind if kind in _KINDS else "other",
            "state": state,
            "created_at": _text(proposal.get("created_at"), 64),
            "text": _text(text, 1200),
            "why": {key: max(0, int(why.get(key) or 0)) for key in _WHY},
            "never_automatic": bool(meta.get("never_automatic")),
            "setting": behaviour.describe(str(payload.get("slot") or "")) if change == "way_of_working" else "",
            "diff": _diff_of(proposal),
            "test": _test_view(meta),
            "used_by": meta.get("used_by") if state == "in_use" and meta.get("used_by") in ("person", "automatic")
            else None,
            "used_at": _iso(meta.get("used_at")) if state == "in_use" else None,
            "not_used": ({"turned_down": "turned_down", "undone": "undone"}.get(reason, "not_better")
                         if state == "not_used" else None),
            "snoozed": bool(meta.get("snoozed_at")) and state == "ready",
            "agent_saved": meta.get("origin") == "agent_saved",
        })
    return found[:50]


def _method() -> dict[str, Any] | None:
    """How Row-Bot learns (L4a; designs/learning-ux.md §6): the method in use, its trials, its history."""

    if not learning.store_exists():
        return None
    try:
        return _method_view(_store())
    except Exception:
        logger.debug("Learning could not read how it learns", exc_info=True)
        return None


def _method_view(store: Any, now: float | None = None) -> dict[str, Any]:
    from row_bot.learning import evaluation, spending
    from row_bot.learning.contracts import Attempt
    from row_bot.learning.improver import archive, meta
    from row_bot.learning.improver import strategy as strategy_module

    current = archive.current(store, create=False)
    method = current.strategy if current is not None else strategy_module.FIRST
    chain = archive.chain(store)
    trials = archive.trials(store)
    versions = {entry.id: entry for entry in archive.entries(store)}
    since = next((step for step in reversed(chain) if current is not None and step.get("release") == current.id), None)
    start = spending.month_start(now)
    attempts = [item for item in store.find("attempt", limit=100_000)
                if isinstance(item, Attempt) and item.change_kind != "strategy" and item.created_at >= start]
    tried = sum(1 for item in attempts if item.outcome not in ("pending", "failed", "stopped"))
    kept = sum(1 for item in attempts if item.outcome == "improved" and item.kept)

    def version_of(release: Any) -> int | None:
        found = versions.get(str(release or ""))
        return found.version if found else None

    trial_rows = []
    for item in reversed(trials[-20:]):
        arms = []
        for arm in item.get("arms") or ():
            role = str(arm.get("role") or "")
            role = "generation" if role.startswith("generation") else ("challenger" if role.startswith("challenger") else role)
            if role not in ("incumbent", "challenger", "control", "generation") or not arm.get("version"):
                continue
            arms.append({"role": role, "version": int(arm["version"]), "accepted": max(0, int(arm.get("accepted") or 0)),
                         "better": max(0, int(arm.get("better") or 0)), "tokens": max(0, int(arm.get("tokens") or 0)),
                         "per_million": max(0.0, float(arm.get("yield") or 0.0))})
        outcome = "final" if item.get("final") else ("switched" if item.get("switched") else "kept")
        trial_rows.append({"at": _iso(item.get("finished_at") or item.get("at")) or "", "problems": max(0, int(item.get("problems") or 0)),
                           "arm_tokens": max(0, int(item.get("arm_tokens") or 0)), "arms": arms[:8], "outcome": outcome,
                           "to_version": version_of(item.get("winner")) if outcome == "switched" else None})
    history: list[dict[str, Any]] = []
    can_undo = bool(chain) and chain[-1].get("by") == "trial"
    for index, step in enumerate(chain):
        entry = versions.get(str(step.get("release")))
        if entry is None:
            continue
        before = versions.get(str(step.get("from") or ""))
        event = {"first": "first", "trial": "switched", "undo": "undone"}.get(str(step.get("by")), "switched")
        words = strategy_module.change_words(before.strategy, entry.strategy) if before and event == "switched" else ""
        history.append({"at": _iso(step.get("at")) or "", "version": entry.version, "event": event, "words": words,
                        "against": None, "can_undo": can_undo and index == len(chain) - 1})
    for item in trials:
        if item.get("final"):
            continue
        for arm in item.get("arms") or ():
            if str(arm.get("role") or "").startswith("challenger") and arm.get("release") != item.get("winner"):
                entry = versions.get(str(arm.get("release")))
                parent = versions.get(str(item.get("incumbent")))
                if entry is None:
                    continue
                history.append({"at": _iso(item.get("finished_at") or item.get("at")) or "", "version": entry.version,
                                "event": "tried",
                                "words": strategy_module.change_words(parent.strategy, entry.strategy) if parent else "",
                                "against": parent.version if parent else None, "can_undo": False})
    # Newest first; the first method is always the oldest line.
    history.sort(key=lambda row: (row["event"] != "first", row["at"], row["version"]), reverse=True)
    plan = meta.plan(store, quick=True)
    fresh = evaluation.fresh_count(store=store)
    state = {"resume": "running", "ready": "ready", "limit": "limit"}.get(plan.state, "waiting")
    return {"version": current.version if current is not None else 1, "since": _iso(since.get("at")) if since else None,
            "description": _text(method.describe(), 600), "tried": tried, "kept": kept,
            "tokens": max(0, int(spending.used_this_month(now=now))), "trials": trial_rows, "history": history[:30],
            "can_undo": can_undo, "next_trial": {"state": state, "fresh": max(0, int(fresh)),
                                                 "needed": evaluation.MIN_FRESH}}


def _kinds_learning(changes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Kinds of work with a change waiting to be tested, or that need more examples (UX §3)."""

    states: dict[str, str] = {}
    rank = ("change_in_use", "change_ready", "testing_change")
    for item in changes:
        state = {"in_use": "change_in_use", "ready": "change_ready", "waiting_to_test": "testing_change",
                 "testing": "testing_change", "edited": "testing_change"}.get(item["state"])
        if state and (item["kind"] not in states or rank.index(state) < rank.index(states[item["kind"]])):
            states[item["kind"]] = state
    if learning.store_exists():
        try:
            from row_bot.learning.contracts import Attempt

            latest: dict[str, Attempt] = {}
            for item in _store().find("attempt", limit=100_000):
                if isinstance(item, Attempt) and item.issue and item.kind_of_work in _KINDS:
                    if item.kind_of_work not in latest or item.created_at > latest[item.kind_of_work].created_at:
                        latest[item.kind_of_work] = item
            for kind, item in latest.items():
                if kind not in states and item.reason.startswith("needs_examples"):
                    states[kind] = "needs_examples"
        except Exception:
            logger.debug("Learning could not read what it is learning", exc_info=True)
    return [{"kind": kind, "state": states[kind]} for kind in _KINDS if kind in states]


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

    model = hardware.STARTING_MODELS.get(settings.starting_model, hardware.QWEN35_9B)
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
        "runtime": _runtime_now(settings),
        "runtimes": _runtimes(),
        "examples": _examples(settings, engine == "installed"),
        "version": _version(),
        # Read only once the person chose Hugging Face: its quote reads Hugging Face's public price list (cached).
        "hugging_face": _hugging_face.view(settings) if settings.enabled and settings.train_where == "hugging_face"
        else None,
        "room": _room_view(settings),
        "stronger_model": _stronger_view(settings),
        **_use_parts(),
    }


def _room_view(settings: learning.LearningSettings) -> dict[str, Any] | None:
    """O112: after two quiet-hours nights without room on the card for this model, what it needs, what the
    card had, and the next smaller certified model: the largest whose need fits what was free, else the
    smallest below this one."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning.training import hardware, recipes

    try:
        found = runner_module.card_room()
        if not found or len(found["nights"]) < runner_module.ROOM_NIGHTS or found["model"] != settings.starting_model:
            return None
        check = hardware.peek()
        smaller: list[tuple[int, Any]] = []
        for model in check.starting_models if check is not None else ():
            recipe = recipes.for_model(model.id, check) if model.id != settings.starting_model else None
            if recipe is not None and (not found["need_mib"] or recipe.memory_mib < found["need_mib"]):
                smaller.append((int(recipe.memory_mib), model))
        roomy = [item for item in smaller if item[0] <= found["free_mib"]]
        pick = max(roomy, key=lambda item: item[0]) if roomy else min(smaller, key=lambda item: item[0], default=None)
        return {"nights": len(found["nights"]), "need_gb": round(found["need_mib"] / 1024, 1),
                "free_gb": round(found["free_mib"] / 1024, 1), "smaller": _model_view(pick[1]) if pick else None}
    except Exception:
        logger.debug("Learning could not read the nights it waited for the card", exc_info=True)
        return None


def _use_smaller_model(model_id: str) -> str:
    """O112: train the smaller model the Personal model view offered (the person tapped it and agreed to its
    download): it becomes the starting model, a run planned for the larger one that never started is set
    aside, and its download starts."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning.training import hardware

    offered = (_room_view(learning.read_settings()) or {}).get("smaller") or {}
    if not model_id or offered.get("id") != model_id:
        raise ClientPlatformError("learning_training_changed")
    supervisor = runner_module.get().supervisor
    if learning.store_exists():
        job = supervisor.jobs.current()
        if job is not None and job.state in ("planned", "waiting") and not job.steps_done:
            supervisor.jobs.move(job.id, "stopped", reason="model_changed")
    _save({"starting_model": model_id})
    runner_module.forget_card_room()
    name = hardware.STARTING_MODELS[model_id].name
    words = _answer_download("download")
    return f"Switched to {name}. {words}"


#: The order of the starting models, smallest first (a later one is stronger).
_STRENGTH = {model_id: index for index, model_id in enumerate(learning.STARTING_MODELS)}
STRONGER_ITEM = "stronger_model:"


def _stronger_view(settings: learning.LearningSettings) -> dict[str, Any] | None:
    """O124: a build that certifies a stronger starting model this card trains (what setup would recommend
    now) offers it once in the Personal model view, until the person switches or dismisses it."""

    from row_bot.learning import versions
    from row_bot.learning.training import hardware

    try:
        check = hardware.peek()
        offered = check.starting_models if check is not None and check.can_train else ()
        if not offered or settings.train_where != "this_computer":
            return None
        best = offered[0]
        if _STRENGTH.get(best.id, -1) <= _STRENGTH.get(settings.starting_model, -1):
            return None
        if STRONGER_ITEM + best.id in versions.dismissed():
            return None
        return _model_view(best)
    except Exception:
        logger.debug("Learning could not tell whether a stronger starting model fits", exc_info=True)
        return None


def _change_busy() -> bool:
    """Something is in flight for the starting model in use: training (or a run with steps that may go on),
    a version being made from it, or its download."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning.training import releases

    if _training_runs():
        return True
    view = _downloads_view()
    if view is not None and view.state == "downloading":
        return True
    if not learning.store_exists():
        return False
    job = runner_module.get().supervisor.jobs.current()
    if job is None or job.state == "failed":
        return False
    if job.state in ("planned", "waiting"):
        return bool(job.steps_done)
    if job.state != "evaluating" or not job.version:
        return True
    state = releases.read_state(releases.version_dir(int(job.version)))
    return state is None or state.phase in (releases.EXPORTING, releases.MAKING_READY)


def _change_starting_model(model_id: str) -> str:
    """O124: the person chose another certified starting model in Settings (after the client's ask, with
    its download size and the free space): it becomes the starting model, a run planned for the old one that
    never trained a step is set aside, and its download starts. Versions already made stay as they are (each
    is compared with its own starting model); the old model's files stay until the person deletes them."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning import versions
    from row_bot.learning.training import hardware

    settings = learning.read_settings()
    check = hardware.peek()
    offered = {model.id for model in check.starting_models} if check is not None and check.can_train else set()
    if model_id not in offered:
        raise ClientPlatformError("learning_training_changed")
    name = hardware.STARTING_MODELS[model_id].name
    if model_id == settings.starting_model:
        return f"{name} is already the starting model."
    if _change_busy():
        raise ClientPlatformError("learning_download_busy")
    if learning.store_exists():
        jobs = runner_module.get().supervisor.jobs
        job = jobs.current()
        if job is not None and job.state in ("planned", "waiting") and not job.steps_done:
            jobs.move(job.id, "stopped", reason="model_changed")
    _save({"starting_model": model_id})
    runner_module.forget_card_room()
    versions.dismiss(STRONGER_ITEM + model_id)  # the offer is answered
    return f"Switched to {name}. {_answer_download('download')}"


def _use_parts() -> dict[str, Any]:
    from row_bot.learning.versions import suggested_folder

    rows, comparison = _versions_view()
    return {"versions": rows, "comparison": comparison, "uses": _uses_view(), "advanced": _advanced_view(),
            "export_folder": _text(suggested_folder(), 1024)}


def _runtime_now(settings: learning.LearningSettings) -> str:
    """Where new versions run: the person's choice, Row-Bot's own server by default where it can run."""

    from row_bot.learning.training import serving

    try:
        return serving.effective_runtime(settings.serve_runtime)
    except Exception:
        logger.debug("Learning could not tell where personal models run", exc_info=True)
        return "ollama"


def _runtimes() -> list[dict[str, Any]]:
    """Where a personal model can run here, and whether Row-Bot can register it there itself. Row-Bot's
    own server is listed everywhere, marked not available where it has no build (no NVIDIA card that
    can train, macOS, Arm); ``installed`` says whether its files are here yet."""

    from row_bot.learning.training import serving, server_files

    try:
        ollama = serving.OllamaRuntime().executable() is not None
    except Exception:
        ollama = False
    here = set(serving.available_runtimes())
    shown = []
    for runtime in serving.RUNTIMES:
        if runtime == "vllm" and runtime not in here:
            continue
        installed: bool | None = None
        if runtime == "ollama":
            installed = ollama
        elif runtime == serving.ROW_BOT and runtime in here:
            try:
                installed = server_files.status(learning.data_dir()).installed
            except Exception:
                installed = False
        shown.append({
            "id": runtime, "name": serving.RUNTIME_NAMES[runtime], "automated": runtime in serving.AUTOMATED,
            "installed": installed, "available": runtime in here,
        })
    return shown


def _examples(settings: learning.LearningSettings, engine_installed: bool) -> dict[str, Any] | None:
    """How many examples of the person's work there are and how many training needs (no store is made)."""

    if not settings.enabled or not engine_installed or not learning.store_exists():
        return None
    runner_module = sys.modules.get("row_bot.learning.runner")
    runner = runner_module.loaded() if runner_module is not None else None
    planner = getattr(runner, "planner", None)
    if planner is None:
        return None
    try:
        summary = planner.summary(compute=True)
    except Exception:
        logger.debug("Learning could not count its examples", exc_info=True)
        return None
    if summary is None:
        return None
    return {"examples": max(0, summary.examples), "needed": max(1, summary.needed),
            "new_examples": max(0, summary.new_examples), "left_out": max(0, summary.left_out)}


def _iso(at: float | None) -> str | None:
    if not at:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(at), tz=timezone.utc).isoformat()


def _version() -> dict[str, Any] | None:
    """The newest personal-model version: being made, ready (waiting for its comparison) or stuck."""

    try:
        from row_bot.learning.training import releases

        state = releases.latest_state()
    except Exception:
        logger.debug("Learning could not read its newest version", exc_info=True)
        return None
    if state is None or state.phase not in _VERSION_PHASES:
        return None
    folder = releases.version_dir(state.version)
    return {
        "version": state.version,
        "phase": state.phase,
        "step": state.step if state.step in ("merge", "convert", "quantize") else None,
        "failure": state.failure if state.failure in _VERSION_FAILURES else None,
        "runtime": state.runtime if state.runtime in learning.SERVE_RUNTIMES else "ollama",
        "quantization": state.quantization if state.quantization in ("Q8_0", "Q4_K_M") else "Q8_0",
        "file_name": _text(state.gguf, 128) or None,
        "file_bytes": max(0, int(state.gguf_bytes or 0)),
        "folder": _text(str(folder.absolute()), 1024),
        "command": _text(state.command, 2048) or None,
        "needs_bytes": state.needs_bytes if isinstance(state.needs_bytes, int) and state.needs_bytes >= 0 else None,
        "free_bytes": state.free_bytes if isinstance(state.free_bytes, int) and state.free_bytes >= 0 else None,
        "trained_at": _iso(state.trained_at),
        "examples": max(0, int(state.examples or 0)),
    }


def _model_view(model: Any) -> dict[str, Any]:
    return {"id": model.id, "name": model.name, "licence": model.licence, "download_bytes": model.download_bytes}


# ── Using the personal model (L2c2) ─────────────────────────────────────────

_VERDICTS = frozenset({"better", "same", "worse", "too_few"})
_OVERALL = frozenset({"better", "mixed", "worse", "no_difference", "unclear"})
_ROW_KINDS = frozenset({"document_lookups", "structured_transforms", "small_coding", "tool_workflows", "own_work"})


def _comparing_now(version: int) -> bool:
    runner_module = sys.modules.get("row_bot.learning.runner")
    runner = runner_module.loaded() if runner_module is not None else None
    comparisons = getattr(runner, "comparisons", None)
    try:
        return bool(comparisons is not None and comparisons.running() and comparisons.version == version)
    except Exception:
        return False


def _comparison_view(item: Any) -> dict[str, Any] | None:
    """A ready version's comparison: waiting, running (with its real denominator), paused, or its result."""

    if not item.ready:
        return None
    result = item.comparison
    progress = item.progress or {}
    state = "waiting"
    if result is not None:
        state = "done"
    elif _comparing_now(item.version):
        state = "running"
    elif progress.get("state") in ("paused", "stopped", "failed"):
        state = str(progress["state"])
    view: dict[str, Any] = {
        "version": item.version, "state": state,
        "done": max(0, int(progress.get("done") or 0)) if state != "done" else 0,
        "total": max(0, int(progress.get("total") or 0)) if state != "done" else 0,
        "compared_at": None, "arms": [], "rows": [], "verdict": None, "line": "", "recommend": None, "note": "",
        "coding_left_out": False, "baseline_unavailable": False, "main_unavailable": None, "reviewed": False,
    }
    if result is None:
        return view
    arms = []
    for arm in result.get("arms") or ():
        if arm.get("role") not in ("new", "baseline", "main"):
            continue
        arms.append({
            "key": _text(arm.get("key"), 32), "label": _text(arm.get("label"), 128), "role": arm["role"],
            "ran": max(0, int(arm.get("ran") or 0)), "cached": max(0, int(arm.get("cached") or 0)),
            "seconds_per_task": float(arm["seconds_per_task"]) if arm.get("seconds_per_task") is not None else None,
            "cloud_tokens_per_task": int(arm["cloud_tokens_per_task"]) if arm.get("cloud_tokens_per_task") is not None
            else None,
        })
    keys = {arm["key"] for arm in arms}
    rows = []
    for row in result.get("rows") or ():
        if row.get("kind") not in _ROW_KINDS:
            continue
        counted = row.get("counted") or {}
        rows.append({
            "kind": row["kind"], "label": _text(row.get("label"), 64), "total": max(0, int(row.get("total") or 0)),
            "cells": [{"key": key, "passed": max(0, int(value)), "counted": max(0, int(counted.get(key, row.get("total") or 0)))}
                      for key, value in (row.get("passed") or {}).items() if key in keys],
            "verdict": row.get("verdict") if row.get("verdict") in _VERDICTS else "too_few",
        })
    overall = result.get("overall") or {}
    view.update({
        "compared_at": _iso(result.get("compared_at")), "arms": arms, "rows": rows,
        "verdict": overall.get("verdict") if overall.get("verdict") in _OVERALL else "unclear",
        "line": _text(overall.get("line"), 400),
        "recommend": overall.get("recommend") if overall.get("recommend") in _ROW_KINDS - {"own_work"} else None,
        "note": _text(result.get("note"), 400), "coding_left_out": bool(result.get("left_out")),
        "baseline_unavailable": bool(result.get("baseline_unavailable")),
        "main_unavailable": result.get("main_unavailable") if result.get("main_unavailable") in (
            "no_main_model", "sign_in_unsupported", "main_failed") else None,
        "reviewed": bool(result.get("reviewed_at")),
    })
    return view


def _uses_view() -> list[dict[str, Any]]:
    """The kinds of work given to personal-model versions: on trial or in use, with the trial's numbers."""

    if not (learning.data_dir() / learning.MODELS_FOLDER / learning.USE_FILE).is_file():
        return []
    from row_bot.learning import kinds, use

    shown = []
    for item in sorted(use.read().assignments.values(), key=lambda entry: kinds.KINDS.index(entry.kind)):
        shown.append({
            "kind": item.kind, "label": kinds.label(item.kind), "version": item.version, "state": item.state,
            "since": _iso(item.since), "previous_version": item.previous,
            "trial": {"requests": item.trial.requests, "answered": item.trial.answered,
                      "fallbacks": item.trial.fallbacks, "seconds_per_request": item.trial.mean_seconds},
            "total": {"requests": item.total.requests, "answered": item.total.answered,
                      "fallbacks": item.total.fallbacks, "seconds_per_request": item.total.mean_seconds},
            "keep_due": use.keep_due(item),
        })
    return shown


def _versions_view() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Every version (newest first) and the newest ready one's comparison."""

    if not (learning.data_dir() / learning.MODELS_FOLDER / "versions").is_dir():
        return [], None
    try:
        from row_bot.learning import versions

        found = versions.all_versions()
    except Exception:
        logger.debug("Learning could not read its versions", exc_info=True)
        return [], None
    rows = []
    for item in found[:20]:
        rows.append({
            "version": item.version, "ready": item.ready, "file_bytes": max(0, int(item.gguf_bytes or 0)),
            "trained_at": _iso(item.trained_at), "compared": item.comparison is not None,
            "uses": [{"kind": entry.kind, "state": entry.state} for entry in item.uses],
        })
    newest_ready = next((item for item in found if item.ready), None)
    return rows, _comparison_view(newest_ready) if newest_ready is not None else None


def _advanced_view() -> dict[str, Any] | None:
    """Advanced ›: the next run's settings, the recommended ones and the choices (D9)."""

    try:
        from row_bot.learning.training import advanced, recipes

        recipe = recipes.current()
        found = advanced.view(recipe)
    except Exception:
        logger.debug("Learning could not read its advanced settings", exc_info=True)
        return None
    return found


def _needs_you(settings: learning.LearningSettings) -> list[dict[str, Any]]:
    if not settings.enabled or not (learning.data_dir() / learning.MODELS_FOLDER).is_dir():
        return []
    try:
        from row_bot.learning import versions

        items = versions.needs_you()
    except Exception:
        logger.debug("Learning could not read what needs the person", exc_info=True)
        return []
    return [{"id": _text(item["id"], 128), "type": item["type"], "version": int(item["version"]),
             "kind": item.get("kind"), "title": _text(item["title"], 200)}
            for item in items[:20] if item.get("type") in ("compare", "keep", "retrain")]


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
        remote = job.backend == "hugging_face"
        if state in LIVE and supervisor.live_job_id() != job.id and not remote:
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
            and state in ("planned", "paused") and settings.train_where == "this_computer" and not remote
        )
        failure = None
        if state == "failed":
            failure = _hugging_face.failure(job) if remote else (
                job.error_code if job.error_code in _FAILURES else "engine_error")
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
            "failure": failure,
            "recovered": job.recovered,
            "trigger": job.trigger,
            "can_train_now": bool(can_train_now),
            "runner": runner_view,
            "where": "hugging_face" if remote else "this_computer",
            "remote": _hugging_face.run_view(job) if remote else None,
        }
    except Exception:
        logger.debug("Learning could not read its training", exc_info=True)
        return _no_training({"state": "nothing_to_do", "reason": None})


def _data() -> dict[str, Any]:
    from row_bot.learning.store import PICTURE_CAP_BYTES

    if not learning.store_exists():
        return {"exists": False, "bytes": 0, "picture_bytes": 0, "picture_cap_bytes": PICTURE_CAP_BYTES}
    pictures = 0
    try:
        store = _store()
        size = store.storage_bytes()
        # Pictures from browser and computer use (P2): part of the size, kept within their own cap.
        pictures = store.picture_stats().bytes
        vault = learning.folder_path() / "audit.db"
        if vault.is_file():
            size += vault.stat().st_size
        from row_bot.learning.training import jobs as training_jobs

        runs = training_jobs.runs_dir()
        if runs.is_dir() and not runs.is_symlink():
            size += sum(path.stat().st_size for path in runs.rglob("*") if path.is_file() and not path.is_symlink())
    except OSError:
        size = 0
    return {"exists": True, "bytes": max(0, int(size)), "picture_bytes": max(0, int(pictures)),
            "picture_cap_bytes": PICTURE_CAP_BYTES}


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
        "train": [settings.train_in_quiet_hours, settings.train_where, settings.starting_model,
                  settings.serve_runtime],
        "download": settings.model_download,
        "opted_out": sorted(opted_out),
        # Which version answers which kind of work (L2c2): a Keep or Undo from a stale view conflicts.
        "use": [[item["kind"], item["version"], item["state"]] for item in _uses_view()],
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
    night = _night() if settings.enabled else None
    changes = _changes() if settings.enabled else []
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
        "quiet_hours": _quiet_hours(root, settings, night),
        "past_chats": _past_chats(settings),
        "personal_model": _personal_model(settings),
        "training": training,
        "data": _data(),
        "opted_out_conversations": [_text(item, 256) for item in opted_out[:2000]],
        "owner_unset_channels": _owner_unset_channels(),
        "needs_you": _needs_you(settings),
        "overnight": _overnight(night, tests=_tests() if settings.enabled else None,
                                trial=_trial_night() if settings.enabled else None),
        "changes": changes,
        "testing_now": _testing_now() if settings.enabled else None,
        "kinds_learning": _kinds_learning(changes) if settings.enabled else [],
        "method": _method() if settings.enabled else None,
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
        need = getattr(value, "system_memory_mib", None)
        card = getattr(value, "need_mib", None)
        return {"id": value.id, "label": value.label, "certified": value.certified, "summary": value.summary,
                "system_memory_gb": max(1, -(-int(need) // 1024)) if need else None,
                "card_need_gb": round(int(card) / 1024, 1) if card else None}

    from row_bot.learning.training import recipes

    memory = found.memory
    system = recipes.this_system()
    result = {
        "state": found.state,
        "gpu": {"name": _text(found.gpu.name, 128), "memory_gb": found.gpu.memory_gb, "label": _text(found.gpu.label, 160)}
        if found.gpu else None,
        "tier": tier(found.tier) if found.tier else None,
        "larger_tiers": [tier(item) for item in found.larger_tiers],
        "memory_short": [tier(item) for item in found.memory_short],
        "memory": {"limit_gb": memory.limit_mib // 1024, "free_gb": memory.free_mib // 1024,
                   "system": system if system in ("windows", "linux") else "other"} if memory else None,
        "card_free_gb": round(found.gpu.free_mib / 1024, 1) if found.gpu and found.gpu.free_mib is not None else None,
        "recommended": tier(found.recommended) if found.recommended else None,
        "starting_models": [_model_view(item) for item in found.starting_models],
        "engine_download_bytes": hardware.engine_download_bytes(found.variant),
        "free_bytes": free,
        # C1 (O131): whether a model trained on Hugging Face could run on this computer afterwards.
        "hugging_face": bool(_hugging_face_runs_here(found)),
    }
    validate()
    return result


def _hugging_face_runs_here(found: Any) -> bool:
    from row_bot.learning.training import catalog

    gpu = getattr(found, "gpu", None)
    return found.state != "container" and bool(catalog.for_hugging_face(int(gpu.memory_mib) if gpu else None))


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
    """Lessons (UX §7): those Learning put into use (In use, or Off after Undo), with their kind of work and
    how often recall used them this month, and those Row-Bot saved for itself during chats (its
    troubleshooting memories), marked Not tested with Test it."""

    _require_preview()
    validate()
    items: list[dict[str, Any]] = []
    linked: set[str] = set()
    try:
        from row_bot.learning import changes

        for proposal in changes.learning_changes():
            if proposal.get("proposal_type") != "lesson":
                continue
            meta = changes.block(proposal)
            state = changes.view_state(proposal)
            memory_id = str(meta.get("memory_id") or "")
            if memory_id:
                linked.add(memory_id)
            if meta.get("origin") == "agent_saved":
                continue  # shown below, as the memory it tests
            if state == "in_use" or (state == "not_used" and meta.get("state") == "undone"):
                kind = str(meta.get("kind_of_work") or "other")
                items.append({
                    "id": _text(proposal.get("id"), 128), "title": _text(proposal.get("title"), 200),
                    "text": _text((proposal.get("payload") or {}).get("text"), 600), "origin": "learning",
                    "status": "in_use" if state == "in_use" else "off", "kind": kind if kind in _KINDS else "other",
                    "used": changes.used_this_month(memory_id) if state == "in_use" else 0,
                    "change_id": _text(proposal.get("id"), 128),
                    "updated_at": _text(proposal.get("updated_at"), 64),
                })
        tested = {str(changes.block(item).get("memory_id") or ""): item for item in changes.learning_changes()
                  if changes.block(item).get("origin") == "agent_saved"}
    except Exception:
        logger.debug("Learning could not read its lessons", exc_info=True)
        tested = {}
    try:
        from row_bot import knowledge_graph

        rows = knowledge_graph.list_entity_summaries("self_knowledge", limit=_LESSON_LIMIT, description_chars=600)
    except Exception:
        logger.debug("Learning could not read the saved lessons", exc_info=True)
        rows = []
    for row in rows:
        subject = _text(row.get("subject"), 200).strip()
        memory_id = _text(row.get("id"), 128)
        if not subject or (memory_id in linked and memory_id not in tested):
            continue
        status = "not_tested"
        change_id = ""
        if memory_id in tested:
            from row_bot.learning import changes

            proposal = tested[memory_id]
            change_id = _text(proposal.get("id"), 128)
            status = {"testing": "testing", "waiting_to_test": "testing", "ready": "tested_better",
                      "in_use": "tested_better", "not_used": "tested_not_better"}.get(changes.view_state(proposal),
                                                                                       "not_tested")
        items.append({
            "id": memory_id,
            "title": subject,
            "text": _text(row.get("description"), 600),
            "origin": "agent_saved",
            "status": status,
            "kind": None,
            "used": 0,
            "change_id": change_id,
            "updated_at": _text(row.get("updated_at"), 64),
        })
    validate()
    return {"items": items[:_LESSON_LIMIT], "total": len(items)}


def read_learning_version(version: int, *, validate: Callable[[], None]) -> dict[str, Any]:
    """Experiment details of one version (UX §4, §7; D7, D14): the data it was trained on (counts, the
    providers' terms note), the method, the versions that made it, every test run (aggregates only, no
    hidden task) and its files (names belong here and in Export…, O54)."""

    _require_preview()
    validate()
    from row_bot.learning import versions
    from row_bot.learning.training import releases

    if type(version) is not int or version < 1:
        raise ClientPlatformError("invalid_learning_command")
    found = versions.get(version)
    if found is None:
        raise ClientPlatformError("learning_version_changed")
    folder = releases.version_dir(version)

    def load(name: str) -> dict[str, Any]:
        try:
            value = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    dataset = load(releases.DATASET_FILE)
    manifest = load(releases.MANIFEST_FILE)
    identity = manifest.get("identity") if isinstance(manifest.get("identity"), dict) else {}
    from row_bot.learning.training import dataset as dataset_module

    reasons = getattr(releases, "_REASONS", {})
    providers = sorted({releases._provider_name(str(item), dataset_module.TERMS_PROVIDERS)  # noqa: SLF001
                        for item in dataset.get("providers") or ()})
    left_out = [{"reason": _text(reasons.get(key, key.replace("_", " ")), 80), "count": int(count)}
                for key, count in sorted((dataset.get("left_out") or {}).items()) if isinstance(count, int) and count]
    recipe = identity.get("recipe") if isinstance(identity.get("recipe"), dict) else {}
    base = identity.get("base") if isinstance(identity.get("base"), dict) else {}
    runtime = identity.get("runtime") if isinstance(identity.get("runtime"), dict) else {}
    from row_bot.learning.training import recipes

    try:
        recipe_now = recipes.load(str(recipe.get("name") or "16gb-v1").split("-v")[0])
    except Exception:
        recipe_now = recipes.default()
    settings = dict(recipe_now.settings)
    epochs = recipe_now.epochs
    if learning.store_exists():
        # What this version really trained with (Advanced may have changed the recipe's values, D9).
        try:
            from row_bot.learning.training.jobs import TrainingJobs

            jobs = TrainingJobs()
            job = next((item for item in jobs.all() if item.version == version and item.id == found_job_id(found)),
                       None) or next((item for item in jobs.all() if item.version == version), None)
            if job is not None:
                settings.update({key: value for key, value in (jobs.plan_of(job)["recipe"].get("settings") or {}).items()
                                 if key in ("rank", "learning_rate", "max_seq_length")})
        except Exception:
            logger.debug("Learning couldn't read the version's training plan", exc_info=True)
    named_versions = [{"name": "Starting model", "value": _text(
        f"{base.get('name') or '?'} at {str(base.get('version') or '')[:12]}", 200)}]
    if recipe:
        named_versions.append({"name": "Recipe", "value": _text(f"{recipe_now.title} ({recipe.get('name')})", 200)})
    if runtime:
        named_versions.append({"name": "Runs in", "value": _text(f"{runtime.get('name')} {runtime.get('version') or ''}".strip(), 200)})
    from row_bot.learning.training import lockfile
    from row_bot.version import __version__

    named_versions.append({"name": "Exported with", "value": f"llama.cpp {lockfile.LLAMA_CPP_RELEASE}"})
    named_versions.append({"name": "Made by", "value": f"Row-Bot {__version__}"})
    runs: list[dict[str, Any]] = []
    comparison = found.comparison or {}
    labels = {item.get("key"): item.get("label") for item in comparison.get("arms") or ()}
    for suite, title in (("visible", "Visible tasks"), ("hidden", "Hidden tasks and your held-back work")):
        for key, table in ((comparison.get("details") or {}).get(suite) or {}).items():
            passed = sum(int(value[0]) for value in table.values())
            total = sum(int(value[1]) for value in table.values())
            runs.append({"suite": title, "arm": _text(labels.get(key) or key, 128), "passed": passed, "total": total})
    files = []
    try:
        files = [{"name": name, "bytes": size} for name, _, size in versions.export_files(version)]
    except versions.VersionError:
        pass
    result = {
        "version": version,
        "data": {
            "examples": int(dataset.get("examples") or 0), "validation": int(dataset.get("validation") or 0),
            "conversations": len(dataset.get("conversations") or ()),
            "first_at": _iso(dataset.get("first_at")), "last_at": _iso(dataset.get("last_at")),
            "providers": [_text(item, 80) for item in providers][:16], "left_out": left_out[:32],
            "terms_note": bool(dataset.get("terms_note")),
        },
        "method": {
            "name": "Standard (LoRA)", "title": _text(recipe_now.title, 80),
            "rank": int(settings.get("rank", 16)), "learning_rate": float(settings.get("learning_rate", 2e-4)),
            "epochs": int(epochs), "context": int(settings.get("max_seq_length", recipe_now.max_seq_length)),
            "quantization": _text(found.quantization, 16),
        },
        "versions": named_versions,
        "runs": runs[:12],
        "files": files[:16],
        "budget_tokens_per_task": int((comparison.get("budget") or {}).get("task_tokens") or 0),
        "comparison_note": _text(comparison.get("note"), 400),
    }
    validate()
    return result


def found_job_id(found: Any) -> str:
    """The training job a version came from (its state file names it)."""

    from row_bot.learning.training import releases

    state = releases.read_state(releases.version_dir(found.version))
    return state.job_id if state is not None else ""


# ── Commands ─────────────────────────────────────────────────────────────────


def _trainable_models() -> set[str]:
    """The starting models setup may choose: the certified ones this computer's card trains (the smallest
    tier's alone before the card was read, or on a computer that can't train)."""

    from row_bot.learning.training import hardware

    found = hardware.peek()
    models = {item.id for item in found.starting_models} if found is not None and found.can_train else set()
    return models or {learning.STARTING_MODELS[0]}


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
            or setup.get("starting_model", learning.STARTING_MODELS[0]) not in _trainable_models()
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
    if action in _VERSION_ACTIONS:
        return "learning:version"
    if action in _USE_ACTIONS:
        return "learning:use"
    if action in _CHANGE_ACTIONS:
        return f"learning:change:{command.get('change_id', '')}"
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


def _version_action(action: str, runtime: str) -> str:
    """Try again / Check again on the newest version, or choose where personal models run."""

    from row_bot.learning import runner as runner_module
    from row_bot.learning.training import releases, serving

    runner = runner_module.get()
    maker = runner.releases
    state = releases.latest_state()
    job = runner.supervisor.jobs.get(state.job_id) if state is not None and learning.store_exists() else None
    if action == "choose_runtime":
        if runtime not in learning.SERVE_RUNTIMES or runtime not in serving.available_runtimes():
            raise ClientPlatformError("invalid_learning_command")
        _save({"serve_runtime": runtime})
        name = serving.RUNTIME_NAMES[runtime]
        if (state is not None and state.gguf and job is not None and job.state in ("evaluating", "ready")
                and maker is not None and not maker.busy()):
            maker.start(job, runtime=runtime)
            runner_module.start_background()
            return f"Personal models run in {name}. Making v{state.version} ready there."
        return f"Personal models run in {name}."
    if state is None or job is None or job.state not in ("evaluating", "ready") or maker is None:
        raise ClientPlatformError("learning_version_changed")
    if maker.busy():
        raise ClientPlatformError("learning_version_busy")
    maker.start(job)
    runner_module.start_background()
    if state.phase == "export_failed":
        return f"Preparing v{state.version} again."
    if state.phase == "waiting_for_you":
        return f"Checking whether v{state.version} is loaded in {serving.RUNTIME_NAMES.get(state.runtime, 'it')}."
    return f"Making v{state.version} ready in {serving.RUNTIME_NAMES.get(_runtime_now(learning.read_settings()), 'it')}."


_VERSION_ERRORS = {
    "version_missing": "learning_version_changed",
    "files_missing": "learning_version_changed",
    "invalid_folder": "learning_export_unavailable",
    "inside_data_folder": "learning_export_unavailable",
    "folder_unavailable": "learning_export_unavailable",
    "copy_failed": "learning_export_unavailable",
    "not_enough_disk": "learning_not_enough_disk",
}


def _use_action(action: str, wire: dict[str, Any]) -> str:
    """The person's choices about their personal model (L2c2), in words."""

    from row_bot.learning import kinds, model_compare, use, versions

    if action == "use_smaller_model":
        return _use_smaller_model(str(wire.get("model") or ""))
    if action == "change_starting_model":
        return _change_starting_model(str(wire.get("model") or ""))
    settings = learning.read_settings()
    version = wire.get("version")
    kind = wire.get("kind") or ""
    needs_on = {"compare_version", "use_version", "retrain_without", "save_advanced", "reset_advanced"}
    if action in needs_on and not settings.enabled:
        raise ClientPlatformError("learning_off")
    if action in {"compare_version", "retrain_without"} and settings.paused:
        raise ClientPlatformError("learning_paused")
    try:
        if action == "compare_version":
            from row_bot.learning import runner as runner_module

            found = versions.get(int(version))
            if found is None or not found.ready:
                raise ClientPlatformError("learning_version_changed")
            comparisons = runner_module.get().comparisons
            if comparisons is None or comparisons.running():
                raise ClientPlatformError("learning_version_busy")
            if runner_module.training_now():
                raise ClientPlatformError("learning_compare_unavailable")
            for path in (model_compare.comparison_path(int(version)), model_compare.progress_path(int(version))):
                path.unlink(missing_ok=True)
            comparisons.start(int(version))
            runner_module.start_background()
            return f"Comparing v{int(version)} with your main model."
        if action == "stop_compare":
            from row_bot.learning import runner as runner_module

            runner = runner_module.loaded()
            if runner is not None and runner.comparisons is not None:
                runner.comparisons.stop()
            return "Comparison stopped. It runs again later."
        if action == "use_version":
            ref = versions.ready_ref(int(version))
            if ref is None:
                raise ClientPlatformError("learning_version_changed")
            use.start_trial(int(version), kind, ref)
            _move_version(int(version), "trial")
            model_compare.mark_reviewed(int(version))
            return (f"v{int(version)} now answers {kinds.phrase(kind)}, on trial. Your main model finishes "
                    "anything it gets stuck on.")
        if action == "keep_version":
            kept = use.keep(kind)
            _move_version(kept.version, "in_use")
            return f"v{kept.version} is in use for {kinds.phrase(kind)}."
        if action == "keep_not_now":
            use.not_now(kind)
            return "The trial goes on. Row-Bot asks again after more requests."
        if action == "undo_use":
            restored = use.undo(kind, still_ready=lambda other: versions.ready_ref(other))
            if restored is None:
                return f"{kinds.label(kind)} go back to your main model."
            return f"{kinds.label(kind)} go back to v{restored.version}."
        if action == "export_version":
            report = versions.export_version(int(version), wire["folder"])
            return f"Exported v{int(version)} ({_size_words(report['bytes'])}) to {report['folder']}."
        if action == "delete_version":
            if _training_runs():
                raise ClientPlatformError("learning_version_busy")
            report = versions.delete_version(int(version))
            freed = f" {_size_words(report['freed'])} freed." if report["freed"] else ""
            undone = report["undone"]
            back = (f" {', '.join(kinds.label(item) for item in undone)} go back to your main model."
                    if undone else "")
            return f"v{int(version)} deleted.{freed}{back}"
        if action == "save_advanced":
            from row_bot.learning.training import advanced, recipes

            recipe = recipes.current()
            advanced.save(wire.get("advanced") or {}, recipe)
            return "Saved. The next training run uses these settings."
        if action == "reset_advanced":
            from row_bot.learning.training import advanced

            advanced.reset()
            return "Back to the recommended settings."
        if action == "retrain_without":
            from row_bot.learning import runner as runner_module

            runner = runner_module.get()
            if runner.planner is None:
                raise ClientPlatformError("learning_nothing_to_train")
            outcome = versions.retrain_without(int(version), planner=runner.planner)
            runner_module.start_background()
            if outcome.job is None:
                if outcome.reason == "current":
                    return "A new version is already planned. It trains without that chat."
                return "Row-Bot retrains once there are enough examples without that chat."
            return f"v{outcome.job.version} is planned without that chat. It trains during quiet hours."
        if action == "dismiss":
            versions.dismiss(wire["item_id"])
            return "Dismissed."
        model_compare.mark_reviewed(int(version))
        return "Not now. The comparison stays under Personal model."
    except use.UseError as error:
        raise ClientPlatformError("learning_version_changed") from error
    except versions.VersionError as error:
        raise ClientPlatformError(_VERSION_ERRORS.get(error.code, "learning_version_changed")) from None


def _move_version(version: int, to: str) -> None:
    """The version's job and release follow the person's choice (trial, in use)."""

    from dataclasses import replace

    from row_bot.learning import contracts, versions
    from row_bot.learning.training.jobs import TrainingJobs, can_move

    if not learning.store_exists():
        return
    found = versions.get(version)
    try:
        jobs = TrainingJobs()
        for job in jobs.all():
            if job.version == version and can_move(job.state, to):
                jobs.move(job.id, to, reason="person")
        store = _store()
        release = store.get("release", found.release_id) if found is not None and found.release_id else None
        if isinstance(release, contracts.Release) and release.status in ("candidate", "trial"):
            store.put(replace(release, status=to))  # type: ignore[arg-type]
    except Exception:
        logger.debug("Learning couldn't record the version's use", exc_info=True)


def _download_words(view: Any, model_name: str) -> str:
    """"Downloading the training engine and Qwen3.5 9B." (what is still missing)."""

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
    model = weights.MODELS.get(settings.starting_model, weights.QWEN35_9B)
    name = hardware.STARTING_MODELS.get(settings.starting_model, hardware.QWEN35_9B).name
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
            from row_bot.learning.training import llama_server

            freed = llama_server.remove() + engine.remove()
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
    name = hardware.STARTING_MODELS.get(settings.starting_model, hardware.QWEN35_9B).name
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
    if action in _HF_JOB_ACTIONS and not job_id:
        raise ClientPlatformError("invalid_learning_command")
    cap = command.get("cap_usd")
    if action == "hf_set_cap" and type(cap) is not int:
        raise ClientPlatformError("invalid_learning_command")
    if action == "hf_choose_model" and not _text(command.get("model"), 128):
        raise ClientPlatformError("invalid_learning_command")
    if action == "hf_choose_flavor" and not _text(command.get("flavor"), 64):
        raise ClientPlatformError("invalid_learning_command")
    runtime = _text(command.get("runtime"), 32)
    if action == "choose_runtime" and runtime not in learning.SERVE_RUNTIMES:
        raise ClientPlatformError("invalid_learning_command")
    version = command.get("version")
    if action in _VERSION_NUMBER_ACTIONS and (type(version) is not int or not 1 <= version <= 100_000):
        raise ClientPlatformError("invalid_learning_command")
    kind = command.get("kind")
    if action in _KIND_ACTIONS and kind not in ("document_lookups", "structured_transforms", "small_coding",
                                                "tool_workflows"):
        raise ClientPlatformError("invalid_learning_command")
    folder = _text(command.get("folder"), 1024)
    if action == "export_version" and not folder.strip():
        raise ClientPlatformError("invalid_learning_command")
    advanced_values = command.get("advanced")
    if action == "save_advanced" and not isinstance(advanced_values, dict):
        raise ClientPlatformError("invalid_learning_command")
    item_id = _text(command.get("item_id"), 128)
    if action == "dismiss" and not item_id:
        raise ClientPlatformError("invalid_learning_command")
    model_id = _text(command.get("model"), 128)
    if action in ("use_smaller_model", "change_starting_model") and model_id not in learning.STARTING_MODELS:
        raise ClientPlatformError("invalid_learning_command")
    change_id = _text(command.get("change_id"), 128)
    if action in _CHANGE_ACTIONS and not change_id:
        raise ClientPlatformError("invalid_learning_command")
    text = str(command.get("text") or "")
    if action == "edit_change" and not (text.strip() and len(text) <= 4000):
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
        "runtime": runtime,
        "version": version if type(version) is int else None,
        "kind": kind if isinstance(kind, str) else None,
        "folder": folder,
        "advanced": dict(advanced_values) if isinstance(advanced_values, dict) else None,
        "item_id": item_id,
        "model": model_id,
        "change_id": change_id,
        "text": text[:4000],
        "answer": _text(command.get("answer"), 2000),
        "cap_usd": cap if type(cap) is int else None,
        "quote_cost_cents": command.get("quote_cost_cents") if type(command.get("quote_cost_cents")) is int else None,
        "flavor": _text(command.get("flavor"), 64),
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
        # Every other window reads Learning again (O127); this one gets the snapshot in its receipt.
        learning.changed()
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
                answer=wire["answer"],
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
    elif action in _VERSION_ACTIONS:
        summary = _version_action(action, wire["runtime"])
    elif action in _USE_ACTIONS:
        summary = _use_action(action, wire)
    elif action in _CHANGE_ACTIONS:
        summary = _change_action(action, wire)
    elif action == "undo_method":
        from row_bot.learning.improver import meta

        back = meta.undo(_store()) if learning.store_exists() else None
        if back is None:
            raise ClientPlatformError("learning_method_changed")
        summary = f"Back to learning method v{back.version}."
    elif action in _hugging_face.ACTIONS:
        summary = _hugging_face.perform(action, wire)
    elif action == "delete_data":
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
        from row_bot import evolution

        evolution.remove_learning_drafts()  # the changes drafted from it go too (never one in use)
        summary = f"Learning data deleted ({_size_words(report.bytes_freed + runs_bytes)})."
    else:
        # An action no branch handles is refused: it never falls through to deleting learning data.
        raise ClientPlatformError("invalid_learning_command")
    return summary, signal


def _change_action(action: str, wire: dict[str, Any]) -> str:
    """One of the person's choices about a change Learning drafted (L3b; designs/learning-ux.md §4, §7)."""

    from row_bot.learning import changes

    change_id = wire["change_id"]
    try:
        if action == "use_change":
            changes.use(change_id)
            return "Now in use."
        if action == "undo_change":
            changes.undo(change_id)
            return "Undone. Row-Bot works as it did before."
        if action == "not_now_change":
            changes.not_now(change_id)
            return "It stays in Changes › Ready."
        if action == "dont_suggest_change":
            changes.dont_suggest(change_id)
            return "Row-Bot won’t suggest this again unless something new comes up."
        if action == "edit_change":
            changes.edit(change_id, wire["text"])
            return "Edited: test again before using."
        if action == "test_change":
            if changes.testing().running():
                raise changes.ChangeError("running")
            changes.test_again(change_id)
            changes.testing().start(change_id)
            return "Testing it now. It stops while you use Row-Bot and carries on later."
        if action == "test_lesson":
            if changes.testing().running():
                raise changes.ChangeError("running")
            found = changes.draft_saved_lesson(change_id)
            changes.testing().start(str(found["id"]))
            return "Testing it now. It stops while you use Row-Bot and carries on later."
        changes.delete_lesson(change_id)
        return "Lesson deleted."
    except changes.ChangeError as error:
        raise ClientPlatformError(_CHANGE_ERRORS.get(error.code, "learning_change_changed")) from None


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
    "read_learning_version",
]
