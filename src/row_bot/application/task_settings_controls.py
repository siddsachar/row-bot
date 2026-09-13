"""Reviewed workflow settings with private server-owned webhook credentials."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Callable
from urllib.parse import quote
import uuid

from row_bot import tasks


class TaskSettingsError(ValueError):
    def __init__(self, code: str, *, task_id: str = "", committed: bool = False):
        self.code, self.task_id, self.committed = code, task_id, committed
        super().__init__(code)


@dataclass(frozen=True)
class TaskSettingsFields:
    concurrency_group: str | None
    trigger_type: str
    trigger_task_id: str | None
    model_override: str | None
    agent_profile_id: str
    approval_mode: str
    persistent_enabled: bool


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


def _identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise TaskSettingsError("invalid_task_settings")


def _revision(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise TaskSettingsError("invalid_task_settings")


def _validate(fields: TaskSettingsFields) -> None:
    if not isinstance(fields, TaskSettingsFields):
        raise TaskSettingsError("invalid_task_settings")
    if fields.concurrency_group is not None and (
        not isinstance(fields.concurrency_group, str) or not 1 <= len(fields.concurrency_group) <= 128
        or "\x00" in fields.concurrency_group or fields.concurrency_group != fields.concurrency_group.strip()
    ):
        raise TaskSettingsError("invalid_task_settings")
    if fields.trigger_type not in {"none", "task_complete", "webhook"}:
        raise TaskSettingsError("invalid_task_settings")
    if fields.trigger_type == "task_complete":
        _identity(fields.trigger_task_id)
    elif fields.trigger_task_id is not None:
        raise TaskSettingsError("invalid_task_settings")
    _identity(fields.agent_profile_id)
    if fields.approval_mode not in {"block", "approve", "allow_all"} or type(fields.persistent_enabled) is not bool:
        raise TaskSettingsError("invalid_task_settings")
    if fields.model_override is not None and (
        not isinstance(fields.model_override, str) or not 1 <= len(fields.model_override) <= 1024
        or "\x00" in fields.model_override
    ):
        raise TaskSettingsError("invalid_task_settings")


def _fields(task: dict) -> TaskSettingsFields:
    trigger = task.get("trigger")
    if trigger is not None and not isinstance(trigger, dict):
        raise TaskSettingsError("task_settings_unavailable")
    kind = trigger.get("type", "none") if trigger else "none"
    fields = TaskSettingsFields(
        task.get("concurrency_group") or None, kind,
        trigger.get("target_task") if kind == "task_complete" else None,
        task.get("model_override") or None,
        task.get("agent_profile_id") or tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID,
        tasks.get_task_approval_mode(task), bool(task.get("persistent_thread_id")),
    )
    _validate(fields)
    return fields


def _snapshot(task_id: str, task: dict, revision: str, profile: dict | None,
              proposed: TaskSettingsFields | None = None) -> TaskSettingsSnapshot:
    trigger = task.get("trigger")
    conversation = task.get("persistent_thread_id") or None
    if conversation is not None:
        _identity(conversation)
    return TaskSettingsSnapshot(
        task_id, revision, proposed or _fields(task), tasks._task_settings_profile_revision(profile),
        profile["effective_approval_mode"] if profile else None,
        bool(profile and profile["profile"].get("enabled", True)),
        bool(isinstance(trigger, dict) and trigger.get("type") == "webhook" and trigger.get("secret")),
        conversation,
    )


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
    for public, stored in (("concurrency_group", "concurrency_group"), ("agent_profile_id", "agent_profile_id"),
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
    # Persistent enable is just a logical future conversation identity. It does
    # not initialize, clear or delete a conversation or its user content.
    if fields.persistent_enabled != current.persistent_enabled:
        values["persistent_thread_id"] = f"pt_{uuid.uuid4().hex[:20]}" if fields.persistent_enabled else None
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
) -> TaskSettingsSnapshot:
    _revision(expected_revision)
    _revision(expected_profile_revision)
    task, revision, _ = _read(task_id)
    if revision != expected_revision:
        raise TaskSettingsError("task_revision_conflict", task_id=task_id)
    values = _proposed(task, fields)
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
    payload = json.dumps({"method": "POST", "relative_url": f"/api/webhook/{quote(task_id, safe='')}?secret={quote(secret, safe='')}",
                          "note": "Keep this file private. Use the address of your Row-Bot app; no request has been sent."},
                         ensure_ascii=True, indent=2).encode()
    validate()
    return payload
