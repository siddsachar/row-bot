"""Revision-bound React onboarding over the existing app config."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any
from uuid import UUID, uuid4

from row_bot.application.client_platform import ClientPlatformError
from row_bot.data_paths import get_row_bot_data_dir, get_tasks_db_path
from row_bot.onboarding_catalog import HOSTED_STEPS, INTENT_OPTIONS, ONBOARDING_VERSION, SETUP_STEPS

_LOCK = threading.RLock()
_FIELDS = (
    "setup_complete", "onboarding_profile", "onboarding_completed_steps",
    "onboarding_skipped_steps", "onboarding_dismissed_home_card", "onboarding_version",
    "onboarding_dismissed_phone_card",
)
_RECEIPTS = "react_onboarding_receipts"
_STARTER_WORKFLOW_NAMES = (
    "Daily Operating Brief",
    "Document Decision Brief",
    "Launch Content Pack",
    "Research Pipeline With Review",
    "Opportunity Monitor",
)


def _path() -> Path:
    return get_row_bot_data_dir(create=False) / "app_config.json"


def _read() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    raise ClientPlatformError("onboarding_config_unavailable")


def _revision(config: dict[str, Any]) -> str:
    value = {key: config.get(key) for key in _FIELDS}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# Setup steps that became Apps (accounts and channels are apps now) keep what was done.
_RENAMED_STEPS = {"accounts": "apps", "channels": "apps", "extensions": "apps"}


def _clean(values: object, allowed: dict[str, Any], *, steps: bool = False) -> list[str]:
    if not isinstance(values, list):
        return []
    renamed = (_RENAMED_STEPS.get(value, value) if steps else value for value in values)
    return list(dict.fromkeys(value for value in renamed if isinstance(value, str) and value in allowed))


def _host() -> Any:
    from row_bot.hosting import hosted_by

    return hosted_by()


def _steps() -> dict[str, dict[str, str]]:
    """The Setup Center's areas. A hosted Row-Bot adds "Add your phone" after Models and
    "Back up" before Final Check (U4); elsewhere they don't exist."""
    if _host() is None:
        return SETUP_STEPS
    steps: dict[str, dict[str, str]] = {}
    for key, meta in SETUP_STEPS.items():
        if key == "final":
            steps["backup"] = HOSTED_STEPS["backup"]
        steps[key] = meta
        if key == "models":
            steps["phone"] = HOSTED_STEPS["phone"]
    return steps


def _clean_steps(values: object, steps: dict[str, dict[str, str]]) -> list[str]:
    return _clean(values, steps, steps=True)


def _missing_starter_workflows() -> int:
    """Count missing templates without creating or repairing the task database."""
    path = get_tasks_db_path(create_parent=False)
    if not path.exists():
        return len(_STARTER_WORKFLOW_NAMES)
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            existing = {row[0] for row in connection.execute("SELECT name FROM tasks")}
    except sqlite3.Error:
        return 0
    return sum(name not in existing for name in _STARTER_WORKFLOW_NAMES)


def _own_workflow() -> bool:
    """A workflow the person made, turned on or ran; the starters as shipped don't count."""
    path = get_tasks_db_path(create_parent=False)
    if not path.exists():
        return False
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT name, enabled, last_run FROM tasks").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return any(
        name not in _STARTER_WORKFLOW_NAMES or enabled or last_run
        for name, enabled, last_run in rows
    )


def _default_model() -> str | None:
    """The saved chat default; nothing is preset (decision 9)."""
    from row_bot.application.client_first_run import saved_default_model

    return saved_default_model()


def _code_folder_connected() -> bool:
    """A code folder the person connected; Developer tools being on by default doesn't count."""
    from row_bot.developer import storage

    return bool(storage.list_workspaces())


def _complete(config: dict[str, Any], default_model: str | None) -> bool:
    """Setup is complete once finished, or once a default model is chosen."""
    return bool(config.get("setup_complete")) or bool(default_model)


def _snapshot(config: dict[str, Any]) -> dict[str, Any]:
    from row_bot.application.client_first_run import import_sources

    host = _host()
    steps = _steps()
    default_model = _default_model()
    complete_setup = _complete(config, default_model)
    starters_missing = _missing_starter_workflows() if complete_setup else 0
    # An area ticks itself only for something the person did or set up:
    # what Row-Bot ships with (starter workflows, tools on by default) doesn't count.
    live_done = [
        step for step, done in (
            ("models", bool(default_model)),
            ("workflows", complete_setup and _own_workflow()),
            ("developer", _code_folder_connected()),
        ) if done
    ]
    # Setup shows the real state: "models" is done only while a default model exists.
    complete = [
        step for step in dict.fromkeys([*_clean_steps(config.get("onboarding_completed_steps"), steps), *live_done])
        if step != "models" or default_model
    ]
    skipped = [step for step in _clean_steps(config.get("onboarding_skipped_steps"), steps) if step not in complete]
    return {
        "schema_version": 1,
        "revision": _revision(config),
        "setup_complete": complete_setup,
        "needs_model": not default_model,
        "default_model": default_model,
        "live_done": live_done,
        "import_sources": import_sources() if not config.get("setup_complete") else [],
        "starter_workflows_missing": starters_missing,
        "profile": _clean(config.get("onboarding_profile"), INTENT_OPTIONS),
        "completed_steps": complete,
        "skipped_steps": skipped,
        "dismissed_home_card": bool(config.get("onboarding_dismissed_home_card")),
        # The phone card after the first answer (U10) never comes back once dismissed.
        "dismissed_phone_card": bool(config.get("onboarding_dismissed_phone_card")),
        # The company hosting this Row-Bot (H13): its first run and Setup Center differ (U3, U4).
        "host_name": host.name if host is not None else None,
        "steps": [
            {"id": key, "title": meta["title"], "description": meta["description"]}
            for key, meta in steps.items()
        ],
        "intents": [{"id": key, "label": label} for key, label in INTENT_OPTIONS.items()],
    }


def read_onboarding() -> dict[str, Any]:
    """Read setup state without starting provider checks.

    A profile that finished setup while presets applied keeps what it was
    running on first (idempotent), so it never lands in the first run.
    """
    from row_bot.application.model_choice_migration import migrate_legacy_presets

    migrate_legacy_presets()
    with _LOCK:
        return _snapshot(_read())


def _save(config: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_onboarding(
    *, command_id: str, expected_revision: str, action: str,
    profile: list[str], step: str, model_ref: str = "", provider_id: str = "",
) -> dict[str, Any]:
    """Apply one explicit, idempotent setup choice while preserving other config."""
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_onboarding_command") from None
    payload = {"action": action, "profile": profile, "step": step}
    if model_ref:
        payload["model_ref"] = model_ref
    if provider_id:
        payload["provider_id"] = provider_id
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with _LOCK:
        config = _read()
        receipts = config.get(_RECEIPTS)
        if not isinstance(receipts, list):
            receipts = []
        for receipt in receipts:
            if receipt.get("command_id") == command_id:
                if receipt.get("digest") != digest:
                    raise ClientPlatformError("onboarding_command_conflict")
                return {key: value for key, value in receipt.items() if key != "digest"}
        if _revision(config) != expected_revision:
            raise ClientPlatformError("onboarding_changed")
        steps = _steps()
        if action == "save_profile":
            if len(profile) > len(INTENT_OPTIONS) or any(value not in INTENT_OPTIONS for value in profile):
                raise ClientPlatformError("invalid_onboarding_command")
            config["onboarding_profile"] = list(dict.fromkeys(profile))
        elif action == "choose_model":
            if profile or step or not model_ref:
                raise ClientPlatformError("invalid_onboarding_command")
            from row_bot.application.client_first_run import choose_model

            # The person's pick becomes the default (decision 9).
            choose_model(model_ref)
        elif action == "choose_suggested":
            if profile or step or model_ref or not provider_id:
                raise ClientPlatformError("invalid_onboarding_command")
            from row_bot.application.client_first_run import choose_model, suggested_model

            # A sign-in the person just finished starts on its provider's own first pick (U3).
            reference = suggested_model(provider_id)
            if reference is None:
                raise ClientPlatformError("model_configuration_unavailable")
            choose_model(reference)
        elif action == "finish_models":
            # Only a model the person chose finishes the first run (no preset).
            if not _default_model():
                raise ClientPlatformError("onboarding_model_required")
            config["setup_complete"] = True
            completed = _clean_steps(config.get("onboarding_completed_steps"), steps)
            config["onboarding_completed_steps"] = list(dict.fromkeys([*completed, "models"]))
            config["onboarding_skipped_steps"] = [
                value for value in _clean_steps(config.get("onboarding_skipped_steps"), steps)
                if value != "models"
            ]
        elif action in {"mark_done", "skip_step"}:
            if step not in steps or not _complete(config, _default_model()):
                raise ClientPlatformError("invalid_onboarding_command")
            selected = "onboarding_completed_steps" if action == "mark_done" else "onboarding_skipped_steps"
            other = "onboarding_skipped_steps" if action == "mark_done" else "onboarding_completed_steps"
            config[selected] = list(dict.fromkeys([*_clean_steps(config.get(selected), steps), step]))
            config[other] = [value for value in _clean_steps(config.get(other), steps) if value != step]
        elif action == "add_starters":
            if not _complete(config, _default_model()) or profile or step:
                raise ClientPlatformError("invalid_onboarding_command")
            from row_bot.tasks import add_default_workflow_templates

            # This owner adds only missing disabled templates. If saving the
            # setup receipt fails, replay can safely inspect and add the rest.
            add_default_workflow_templates()
            completed = _clean_steps(config.get("onboarding_completed_steps"), steps)
            config["onboarding_completed_steps"] = list(dict.fromkeys([*completed, "workflows"]))
            config["onboarding_skipped_steps"] = [
                value for value in _clean_steps(config.get("onboarding_skipped_steps"), steps)
                if value != "workflows"
            ]
        elif action == "dismiss_home":
            config["onboarding_dismissed_home_card"] = True
        elif action == "dismiss_phone_card":
            if profile or step or model_ref or provider_id:
                raise ClientPlatformError("invalid_onboarding_command")
            config["onboarding_dismissed_phone_card"] = True
        else:
            raise ClientPlatformError("invalid_onboarding_command")
        config["onboarding_version"] = ONBOARDING_VERSION
        result = {
            "schema_version": 1,
            "command_id": command_id,
            "status": "completed",
            "snapshot": _snapshot(config),
        }
        config[_RECEIPTS] = [*receipts[-31:], {**result, "digest": digest}]
        _save(config)
        return result
