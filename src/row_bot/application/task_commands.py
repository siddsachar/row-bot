"""Task edit admission; the canonical row and saved receipt proof commit together."""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application.task_controls import (
    TaskControlError, TaskEditableFields, create_saved_task, get_task_editor, update_saved_task,
)
from row_bot.runtime import admissions
from row_bot.application.task_graph_controls import (
    TaskGraphError, TaskGraphFields, TaskGraphStepEdit, get_task_graph, update_saved_task_graph,
)
from row_bot.application.task_settings_controls import (
    TaskSettingsError, TaskSettingsFields, get_task_settings, update_saved_task_settings, rotate_task_webhook,
)


def execute_task_command(*, owner_id: str, key: str, command: dict,
                         validate: Callable[[], None]) -> dict:
    """Called under the shared command lock; retry reconciles only proven saves."""
    validate()
    previous = None
    try:
        previous = admissions.claim_command(owner_id, key, command, "tasks")
    except admissions.AdmissionError as exc:
        if str(exc) != "operation_uncertain":
            raise ClientPlatformError(str(exc), exc.current_revision) from exc
        previous = admissions.receipt(owner_id, command["command_id"])
        if not previous or not previous.get("task_saved"):
            raise ClientPlatformError("operation_uncertain") from exc
    if previous and previous.get("status") == "completed":
        return previous
    payload = command["payload"]
    creating = command["type"] == "task.create"
    deleting = command["type"] == "task.delete"
    delivery = command["type"] == "task.delivery.update"
    graph = command["type"] == "task.graph.update"
    settings = command["type"] in {"task.settings.update", "task.webhook.rotate"}
    task_id = (
        str(uuid.uuid5(uuid.UUID(command["command_id"]), "task"))
        if creating
        else "delivery-defaults"
        if delivery
        else payload["task_id"]
    )
    progress = {"command_id": command["command_id"], "task_id": task_id,
                "task_saved": True, "task_created": creating,
                "task_deleted": deleting, "status": "admitting"}

    def record_commit(conn: sqlite3.Connection, saved_id: str) -> None:
        validate()
        if saved_id != task_id:
            raise ClientPlatformError("action_denied")
        changed = conn.execute(
            "UPDATE client_commands SET result_json=? WHERE owner_id=? AND key=? AND status='admitting'",
            (json.dumps(progress, separators=(",", ":")), owner_id, key),
        ).rowcount
        if changed != 1:
            raise ClientPlatformError("operation_uncertain")

    try:
        validate()
        if delivery:
            from row_bot.application.task_delivery_controls import (
                read_task_delivery_defaults,
                update_task_delivery_defaults,
            )

            current = read_task_delivery_defaults(validate=validate)
            requested = set(payload["channels"])
            selected = {
                item["id"] for item in current["channels"] if item["selected"]
            }
            if previous and selected == requested:
                result = {
                    **progress,
                    "status": "completed",
                    "task_revision": current["revision"],
                }
                admissions.complete_command(owner_id, key, result)
                return result
            updated = update_task_delivery_defaults(
                payload["channels"],
                expected_revision=payload["delivery_revision"],
                validate=validate,
            )
            result = {
                **progress,
                "status": "completed",
                "task_revision": updated["revision"],
            }
            admissions.complete_command(owner_id, key, result)
            return result
        if deleting:
            from row_bot.tasks import TaskMutationError, delete_task

            try:
                current = get_task_editor(task_id)
            except TaskControlError as exc:
                if previous and exc.code == "task_not_found":
                    result = {**progress, "status": "completed", "task_revision": None}
                    admissions.complete_command(owner_id, key, result)
                    return result
                raise
            if current.revision != payload["task_revision"]:
                raise TaskControlError("task_delete_revision_conflict")
            try:
                delete_task(
                    task_id,
                    expected_revision=payload["task_revision"],
                    validate=validate,
                    preserve_conversations=True,
                )
            except TaskMutationError as exc:
                raise TaskControlError(exc.code) from exc
            result = {**progress, "status": "completed", "task_revision": None}
            admissions.complete_command(owner_id, key, result)
            return result
        if previous and not graph and not settings:
            from row_bot.tasks import sync_task_schedule
            sync_task_schedule(task_id, validate=validate)
        elif not previous and settings:
            if command["type"] == "task.webhook.rotate":
                rotate_task_webhook(task_id, expected_revision=payload["task_revision"], validate=validate,
                                    record_commit=record_commit)
            else:
                update_saved_task_settings(task_id, TaskSettingsFields(**payload["fields"]),
                    expected_revision=payload["task_revision"], expected_profile_revision=payload["profile_revision"],
                    validate=validate, record_commit=record_commit)
        elif not previous and graph:
            steps = tuple(TaskGraphStepEdit(step["id"], step["type"], TaskGraphFields(**{
                **step["fields"], "run_ids": None if step["fields"].get("run_ids") is None
                else tuple(step["fields"]["run_ids"]),
            })) for step in payload["steps"])
            update_saved_task_graph(task_id, steps, expected_revision=payload["task_revision"],
                                    validate=validate, record_commit=record_commit)
        elif not previous:
            values = payload["fields"]
            fields = TaskEditableFields(**{**values, "prompts": tuple(values["prompts"]),
                "channels": None if values["channels"] is None else tuple(values["channels"])})
            if creating:
                create_saved_task(fields, stable_task_id=task_id, validate=validate, record_commit=record_commit)
            else:
                update_saved_task(task_id, fields, expected_revision=payload["task_revision"],
                                  validate=validate, record_commit=record_commit)
        current = get_task_settings(task_id) if settings else get_task_graph(task_id) if graph else get_task_editor(task_id)
        validate()
        result = {**progress, "status": "completed", "task_revision": current.revision}
        admissions.complete_command(owner_id, key, result)
        return result
    except Exception as exc:
        # Inspect the transaction's proof, including failures after commit but
        # before the domain caller could report success. Never repeat the edit.
        saved = admissions.receipt(owner_id, command["command_id"])
        if saved and saved.get("task_saved"):
            code = exc.code if isinstance(exc, (TaskControlError, TaskGraphError, TaskSettingsError)) else "task_saved_read_unconfirmed"
            result = {**progress, "status": "partial", "code": code}
            admissions.command_progress(owner_id, key, result)
            return result
        if isinstance(exc, (TaskControlError, TaskGraphError, TaskSettingsError)):
            admissions.reject_command(owner_id, key, exc.code)
            raise ClientPlatformError(exc.code) from exc
        if isinstance(exc, ClientPlatformError):
            admissions.reject_command(owner_id, key, exc.code, exc.current_revision)
        raise
