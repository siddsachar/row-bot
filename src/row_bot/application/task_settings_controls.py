"""Reviewed workflow settings with private server-owned webhook credentials."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Callable
from urllib.parse import quote

from row_bot import tasks


class TaskSettingsError(ValueError):
    def __init__(self, code: str, *, task_id: str = "", committed: bool = False):
        self.code, self.task_id, self.committed = code, task_id, committed
        super().__init__(code)


@dataclass(frozen=True)
class TaskSettingsFields:
    trigger_type: str
    trigger_task_id: str | None
    model_override: str | None
    agent_profile_id: str
    approval_mode: str
    # The workflow's own code folder (N9): every step runs in it. None: the conversation's.
    workspace_id: str | None = None
    # A scheduled time while Row-Bot was closed: "skip" it or "run_once" when Row-Bot starts.
    missed_runs: str = "skip"
    # Apps and tools whose actions always wait for the person ("Always ask me").
    always_ask: tuple[str, ...] = ()
    # When its results go to its channels (G8): "every_run", "needs_me" or "digest".
    send_results: str = "every_run"

    def __post_init__(self) -> None:
        if isinstance(self.always_ask, list):  # As a wire payload carries it.
            object.__setattr__(self, "always_ask", tuple(self.always_ask))


@dataclass(frozen=True)
class TaskSettingsSnapshot:
    task_id: str
    revision: str
    fields: TaskSettingsFields
    profile_revision: str | None
    effective_approval_mode: str | None
    profile_available: bool
    webhook_configured: bool
    conversation_id: str | None
    # The person turned Hands-off on once on this install: choosing it needs no dialog again.
    full_autonomy_confirmed: bool = False
    # The saved folder's name, kept when the folder is removed, and whether it is still a code folder.
    workspace_name: str | None = None
    workspace_available: bool = True


_MAX_ALWAYS_ASK = 16


def _identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise TaskSettingsError("invalid_task_settings")


def _revision(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise TaskSettingsError("invalid_task_settings")


def _validate(fields: TaskSettingsFields) -> None:
    if not isinstance(fields, TaskSettingsFields):
        raise TaskSettingsError("invalid_task_settings")
    if fields.trigger_type not in {"none", "task_complete", "webhook"}:
        raise TaskSettingsError("invalid_task_settings")
    if fields.trigger_type == "task_complete":
        _identity(fields.trigger_task_id)
    elif fields.trigger_task_id is not None:
        raise TaskSettingsError("invalid_task_settings")
    _identity(fields.agent_profile_id)
    if fields.approval_mode not in {"block", "approve", "auto", "full"}:
        raise TaskSettingsError("invalid_task_settings")
    if fields.model_override is not None and (
        not isinstance(fields.model_override, str) or not 1 <= len(fields.model_override) <= 1024
        or "\x00" in fields.model_override
    ):
        raise TaskSettingsError("invalid_task_settings")
    if fields.workspace_id is not None:
        _identity(fields.workspace_id)
    if fields.missed_runs not in tasks.MISSED_RUN_RULES or fields.send_results not in tasks.SEND_RESULTS:
        raise TaskSettingsError("invalid_task_settings")
    if (not isinstance(fields.always_ask, tuple) or len(fields.always_ask) > _MAX_ALWAYS_ASK
            or len(set(fields.always_ask)) != len(fields.always_ask)
            or any(not isinstance(item, str) or not 1 <= len(item) <= 512
                   or any(ord(character) < 32 for character in item) for item in fields.always_ask)):
        raise TaskSettingsError("invalid_task_settings")


def _fields(task: dict) -> TaskSettingsFields:
    trigger = task.get("trigger")
    if trigger is not None and not isinstance(trigger, dict):
        raise TaskSettingsError("task_settings_unavailable")
    kind = trigger.get("type", "none") if trigger else "none"
    fields = TaskSettingsFields(
        kind,
        trigger.get("target_task") if kind == "task_complete" else None,
        task.get("model_override") or None,
        task.get("agent_profile_id") or tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
        tasks.get_task_approval_mode(task),
        task.get("workspace_id") or None, task.get("missed_runs") or "skip",
        tuple(task.get("always_ask") or ()), task.get("send_results") or "every_run",
    )
    _validate(fields)
    return fields


def _snapshot(task_id: str, task: dict, revision: str, profile: dict | None,
              proposed: TaskSettingsFields | None = None) -> TaskSettingsSnapshot:
    trigger = task.get("trigger")
    conversation = task.get("persistent_thread_id") or None
    if conversation is not None:
        _identity(conversation)
    fields = proposed or _fields(task)
    folder_name, folder_available = None, True
    if fields.workspace_id:
        name, folder_available = tasks.code_folder_state(fields.workspace_id)
        saved = task.get("workspace_name") if task.get("workspace_id") == fields.workspace_id else ""
        folder_name = (name or saved or fields.workspace_id)[:256]
    return TaskSettingsSnapshot(
        task_id, revision, fields, tasks._task_settings_profile_revision(profile),
        profile["effective_approval_mode"] if profile else None,
        bool(profile and profile["profile"].get("enabled", True)),
        bool(isinstance(trigger, dict) and trigger.get("type") == "webhook" and trigger.get("secret")),
        conversation,
        _full_autonomy_confirmed(),
        folder_name,
        folder_available,
    )


def _full_autonomy_confirmed() -> bool:
    from row_bot.full_autonomy import is_confirmed

    return is_confirmed()


def _read(task_id: str, values: dict | None = None) -> tuple[dict, str, dict | None]:
    _identity(task_id)
    try:
        return tasks.read_task_settings_review(task_id, values)
    except tasks.TaskMutationError as exc:
        raise TaskSettingsError(exc.code, task_id=task_id) from exc


def get_task_settings(task_id: str) -> TaskSettingsSnapshot:
    return _snapshot(task_id, *_read(task_id))


def _proposed(task: dict, fields: TaskSettingsFields) -> dict:
    _validate(fields)
    current = _fields(task)
    values = {}
    for public, stored in (("agent_profile_id", "agent_profile_id"),
                           ("approval_mode", "safety_mode"), ("model_override", "model_override")):
        value = getattr(fields, public)
        if value != getattr(current, public):
            values[stored] = value
    if "model_override" in values:
        try:
            values["model_override"] = tasks._canonicalize_workflow_model_override(values["model_override"])
        except ValueError as exc:
            raise TaskSettingsError("task_settings_model_unavailable") from exc
    if fields.trigger_type != current.trigger_type or fields.trigger_task_id != current.trigger_task_id:
        values["trigger"] = None if fields.trigger_type == "none" else {
            "type": fields.trigger_type, **({"target_task": fields.trigger_task_id} if fields.trigger_type == "task_complete" else {})}
    if fields.workspace_id != current.workspace_id:
        name = ""
        if fields.workspace_id:
            name, available = tasks.code_folder_state(fields.workspace_id)
            if not available:
                raise TaskSettingsError("task_settings_folder_unavailable")
        values["workspace_id"] = fields.workspace_id or ""
        values["workspace_name"] = name
    if fields.missed_runs != current.missed_runs:
        values["missed_runs"] = fields.missed_runs
    if fields.always_ask != current.always_ask:
        values["always_ask"] = list(fields.always_ask)
    if fields.send_results != current.send_results:
        values["send_results"] = fields.send_results
    return values


def review_task_settings(task_id: str, fields: TaskSettingsFields) -> TaskSettingsSnapshot:
    task, revision, _ = _read(task_id)
    values = _proposed(task, fields)
    current, current_revision, profile = _read(task_id, values)
    if current_revision != revision:
        raise TaskSettingsError("task_revision_conflict", task_id=task_id)
    proposed = _fields({**current, **values})
    if "agent_profile_id" in values and profile is not None:
        from dataclasses import replace
        proposed = replace(proposed, agent_profile_id=profile["profile_id"])
    return _snapshot(task_id, current, revision, profile, proposed)


def _after_save(task_id: str) -> TaskSettingsSnapshot:
    try:
        return get_task_settings(task_id)
    except Exception as exc:
        raise TaskSettingsError("task_settings_read_unconfirmed", task_id=task_id, committed=True) from exc


def update_saved_task_settings(
    task_id: str, fields: TaskSettingsFields, *, expected_revision: str,
    expected_profile_revision: str, validate: Callable[[], None],
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
    confirm_full_autonomy: bool = False,
) -> TaskSettingsSnapshot:
    """Save a workflow's reviewed settings. Turning Hands-off on needs its one-time confirmation
    (``confirm_full_autonomy``: the person turned it on in the dialog just now)."""
    _revision(expected_revision)
    _revision(expected_profile_revision)
    task, revision, _ = _read(task_id)
    if revision != expected_revision:
        raise TaskSettingsError("task_revision_conflict", task_id=task_id)
    values = _proposed(task, fields)
    if values.get("safety_mode") == "full":
        from row_bot.full_autonomy import FullAutonomyError, require_choice

        try:
            require_choice("full", confirmed=confirm_full_autonomy)
        except FullAutonomyError as exc:
            raise TaskSettingsError(exc.code, task_id=task_id) from exc
    try:
        tasks.update_task_settings(task_id, expected_revision=revision, values=values,
            expected_profile_revision=expected_profile_revision, validate=validate, record_commit=record_commit)
    except tasks.TaskMutationError as exc:
        raise TaskSettingsError(exc.code, task_id=task_id, committed=exc.committed) from exc
    return _after_save(task_id)


def rotate_task_webhook(
    task_id: str, *, expected_revision: str, validate: Callable[[], None],
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
) -> TaskSettingsSnapshot:
    _identity(task_id)
    _revision(expected_revision)
    try:
        tasks.update_task_settings(task_id, expected_revision=expected_revision, values={},
            expected_profile_revision=None, validate=validate, record_commit=record_commit, rotate_webhook=True)
    except tasks.TaskMutationError as exc:
        raise TaskSettingsError(exc.code, task_id=task_id, committed=exc.committed) from exc
    return _after_save(task_id)


def export_webhook_configuration(task_id: str, *, expected_revision: str,
                                 validate: Callable[[], None]) -> bytes:
    """Private explicit download body, never a public DTO or durable receipt."""
    _revision(expected_revision)
    validate()
    task, revision, _ = _read(task_id)
    if revision != expected_revision:
        raise TaskSettingsError("task_revision_conflict", task_id=task_id)
    trigger = task.get("trigger")
    secret = trigger.get("secret") if isinstance(trigger, dict) and trigger.get("type") == "webhook" else None
    if not isinstance(secret, str) or not secret or len(secret) > 4096 or "\x00" in secret:
        raise TaskSettingsError("task_webhook_unavailable", task_id=task_id)
    from row_bot.tasks import WEBHOOK_SECRET_HEADER
    path = f"/api/webhook/{quote(task_id, safe='')}"
    # The secret goes in a header, out of the address (B132); the older
    # address form stays for services that can only take an address.
    payload = json.dumps({"method": "POST", "relative_url": path,
                          "headers": {WEBHOOK_SECRET_HEADER: secret},
                          "relative_url_with_secret": f"{path}?secret={quote(secret, safe='')}",
                          "note": "Keep this file private. Use the address of your Row-Bot app; no request has been sent."},
                         ensure_ascii=True, indent=2).encode()
    validate()
    return payload
