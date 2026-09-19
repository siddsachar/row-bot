"""Reviewed basic workflow edits through the canonical task owner.

The caller binds the creation identity to its durable admission before entering
this module. This service neither runs workflows nor starts a scheduler.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
import re
import sqlite3
from typing import Callable

from row_bot import tasks


class TaskControlError(ValueError):
    def __init__(self, code: str, *, task_id: str | None = None, committed: bool = False):
        self.code = code
        self.task_id = task_id
        self.committed = committed
        super().__init__(code)


@dataclass(frozen=True)
class TaskEditableFields:
    name: str
    description: str
    icon: str
    prompts: tuple[str, ...]
    enabled: bool
    schedule: str | None
    at: str | None
    notify_only: bool
    notify_label: str
    channels: tuple[str, ...] | None


@dataclass(frozen=True)
class TaskEditorSnapshot:
    id: str
    revision: str
    fields: TaskEditableFields
    advanced: bool
    agent_profile_id: str
    approval_mode: str
    conversation_id: str | None
    legacy_delivery: bool


@dataclass(frozen=True)
class TaskSaveResult:
    task: TaskEditorSnapshot
    created: bool
    replayed: bool


def _identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise TaskControlError("invalid_task_identity")


def _text(value: str, maximum: int, *, required: bool = False) -> None:
    if (
        not isinstance(value, str) or len(value) > maximum or "\x00" in value
        or (required and not value.strip())
    ):
        raise TaskControlError("invalid_task_fields")


def _validate_fields(fields: TaskEditableFields, *, schedule: bool = True) -> None:
    if not isinstance(fields, TaskEditableFields):
        raise TaskControlError("invalid_task_fields")
    _text(fields.name, 256, required=True)
    _text(fields.description, 4096)
    _text(fields.icon, 32)
    _text(fields.notify_label, 4096)
    if type(fields.enabled) is not bool or type(fields.notify_only) is not bool:
        raise TaskControlError("invalid_task_fields")
    if not isinstance(fields.prompts, tuple) or len(fields.prompts) > 100:
        raise TaskControlError("invalid_task_fields")
    for prompt in fields.prompts:
        _text(prompt, 16384, required=True)
    if sum(map(len, fields.prompts)) > 65536:
        raise TaskControlError("invalid_task_fields")
    if fields.channels is not None:
        if not isinstance(fields.channels, tuple) or len(fields.channels) > 32:
            raise TaskControlError("invalid_task_fields")
        if any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name)
               for name in fields.channels):
            raise TaskControlError("invalid_task_fields")
        if len(set(fields.channels)) != len(fields.channels):
            raise TaskControlError("invalid_task_fields")
    if fields.schedule is not None:
        _text(fields.schedule, 256, required=True)
    if fields.at is not None:
        _text(fields.at, 80, required=True)
    if not schedule:
        return
    if fields.schedule is not None and fields.at is not None:
        raise TaskControlError("invalid_task_schedule")
    if fields.at is not None:
        try:
            at = datetime.fromisoformat(fields.at)
            if at.tzinfo is not None:
                raise ValueError("local scheduler dates must be naive")
        except (ValueError, TypeError) as exc:
            raise TaskControlError("invalid_task_schedule") from exc
    if fields.schedule is not None:
        parsed = tasks._parse_schedule(fields.schedule)
        if parsed is None:
            raise TaskControlError("invalid_task_schedule")
        kind = parsed["kind"]
        # The legacy parser accepts surplus colon fields and rounds minutes.
        # Reviewed input must represent exactly the schedule being saved.
        if kind in {"daily", "weekly"} and len(fields.schedule.split(":")) != (
            3 if kind == "daily" else 4
        ):
            raise TaskControlError("invalid_task_schedule")
        if kind in {"interval", "interval_minutes"}:
            amount = parsed["hours" if kind == "interval" else "minutes"]
            if not math.isfinite(amount) or amount <= 0 or (
                kind == "interval_minutes" and not amount.is_integer()
            ):
                raise TaskControlError("invalid_task_schedule")
        try:
            if tasks._build_trigger({"schedule": fields.schedule}) is None:
                raise ValueError("unsupported schedule")
        except (ValueError, OverflowError, TypeError) as exc:
            raise TaskControlError("invalid_task_schedule") from exc


def _snapshot(row: dict, revision: str) -> TaskEditorSnapshot:
    fields = TaskEditableFields(
        name=row["name"], description=row.get("description") or "",
        icon=row.get("icon") or "", prompts=tuple(row.get("prompts") or ()),
        enabled=bool(row.get("enabled")), schedule=row.get("schedule"), at=row.get("at"),
        notify_only=bool(row.get("notify_only")), notify_label=row.get("notify_label") or "",
        channels=None if row.get("channels") is None else tuple(row["channels"]),
    )
    # Do not silently truncate an existing workflow into an editable subset.
    _validate_fields(fields, schedule=False)
    return TaskEditorSnapshot(
        id=row["id"], revision=revision, fields=fields,
        advanced=bool(row.get("advanced_mode") or row.get("_stored_steps")),
        agent_profile_id=str(row.get("agent_profile_id") or ""),
        approval_mode=str(row.get("safety_mode") or ""),
        conversation_id=row.get("persistent_thread_id"),
        legacy_delivery=bool(row.get("delivery_channel") or row.get("delivery_target")),
    )


def get_task_editor(task_id: str) -> TaskEditorSnapshot:
    _identity(task_id)
    try:
        captured = tasks.read_task_for_edit(task_id)
    except tasks.TaskMutationError as exc:
        raise _owner_error(exc) from exc
    if captured is None:
        raise TaskControlError("task_not_found", task_id=task_id)
    return _snapshot(*captured)


def _kwargs(fields: TaskEditableFields) -> dict:
    values = asdict(fields)
    values["prompts"] = list(fields.prompts)
    values["channels"] = None if fields.channels is None else list(fields.channels)
    return values


def _owner_error(exc: tasks.TaskMutationError) -> TaskControlError:
    return TaskControlError(exc.code, task_id=exc.task_id, committed=exc.committed)


def _read_committed(task_id: str) -> TaskEditorSnapshot:
    try:
        return get_task_editor(task_id)
    except Exception as exc:
        raise TaskControlError("task_saved_read_unconfirmed", task_id=task_id, committed=True) from exc


def create_saved_task(
    fields: TaskEditableFields, *, stable_task_id: str, validate: Callable[[], None],
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
) -> TaskSaveResult:
    """Create once using the server admission's stable identity, or verify replay."""
    _identity(stable_task_id)
    _validate_fields(fields)
    if not fields.notify_only and not fields.prompts:
        raise TaskControlError("invalid_task_fields")
    validate()
    try:
        tasks.create_task(**_kwargs(fields), task_id=stable_task_id, validate=validate,
                          **({"record_commit": record_commit} if record_commit is not None else {}))
    except sqlite3.IntegrityError:
        validate()
        saved = get_task_editor(stable_task_id)
        if saved.fields != fields or saved.advanced or saved.legacy_delivery:
            raise TaskControlError("task_creation_conflict", task_id=stable_task_id) from None
        try:
            tasks.sync_task_schedule(stable_task_id, validate=validate)
        except tasks.TaskMutationError as exc:
            raise _owner_error(exc) from exc
        return TaskSaveResult(_read_committed(stable_task_id), created=False, replayed=True)
    except tasks.TaskMutationError as exc:
        raise _owner_error(exc) from exc
    return TaskSaveResult(_read_committed(stable_task_id), created=True, replayed=False)


def update_saved_task(
    task_id: str, fields: TaskEditableFields, *, expected_revision: str,
    validate: Callable[[], None],
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
) -> TaskSaveResult:
    _validate_fields(fields)
    if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
        raise TaskControlError("invalid_task_revision")
    validate()
    current = get_task_editor(task_id)
    if current.revision != expected_revision:
        raise TaskControlError("task_revision_conflict", task_id=task_id)
    if current.advanced and (
        fields.prompts != current.fields.prompts or fields.notify_only != current.fields.notify_only
    ):
        raise TaskControlError("task_advanced_edit_required", task_id=task_id)
    if current.legacy_delivery and fields.channels != current.fields.channels:
        raise TaskControlError("task_delivery_review_required", task_id=task_id)
    if not current.advanced and not fields.notify_only and not fields.prompts:
        raise TaskControlError("invalid_task_fields")
    try:
        tasks.update_task(task_id, expected_revision=expected_revision, validate=validate, **_kwargs(fields),
                          **({"record_commit": record_commit} if record_commit is not None else {}))
    except tasks.TaskMutationError as exc:
        raise _owner_error(exc) from exc
    return TaskSaveResult(_read_committed(task_id), created=False, replayed=False)
