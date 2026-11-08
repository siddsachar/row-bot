"""Task execution admission with durable reservation proof and no replayed effects."""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application import task_execution
from row_bot.runtime import admissions


def enabled_tools() -> list[str]:
    """Read the current registered policy without discovery or provider calls."""
    from row_bot.tools.registry import get_enabled_tools
    return [tool.name for tool in get_enabled_tools()]


def execute_task_run_command(*, owner_id: str, key: str, command: dict,
                             validate: Callable[[], None]) -> dict:
    from row_bot import threads
    validate()
    payload = command["payload"]
    task_id = payload["task_id"]
    stopping = command["type"] == "task.stop"
    run_id = payload["run_id"] if stopping else str(uuid.uuid5(uuid.UUID(command["command_id"]), "task-run"))
    retrying = False
    try:
        previous = admissions.claim_command(owner_id, key, command, "tasks")
    except admissions.AdmissionError as exc:
        if str(exc) != "operation_uncertain":
            raise ClientPlatformError(str(exc), exc.current_revision) from exc
        previous = admissions.receipt(owner_id, command["command_id"])
        retrying = True
    if previous and previous.get("status") == "completed":
        return previous
    progress = {"command_id": command["command_id"], "task_id": task_id,
                "task_run_id": run_id, "status": "admitting"}

    def authority() -> None:
        validate()
        conversation = progress.get("conversation_id")
        if conversation:
            from row_bot.thread_cleanup import is_thread_deleting
            if is_thread_deleting(conversation, initialized_read=True):
                raise ClientPlatformError("conversation_deleting")
            # The command already initialized thread metadata. Authority checks
            # inside the task writer transaction must not initialize either DB.
            with closing(sqlite3.connect(threads.DB_PATH)) as conn:
                if not conn.execute("SELECT 1 FROM thread_meta WHERE thread_id=?", (conversation,)).fetchone():
                    raise ClientPlatformError("conversation_deleting")

    def record_commit(conn: sqlite3.Connection, saved_run: str) -> None:
        authority()
        if saved_run != run_id:
            raise ClientPlatformError("action_denied")
        receipt = {**progress, "task_run_reserved": True}
        changed = conn.execute(
            "UPDATE client_commands SET result_json=? WHERE owner_id=? AND key=? AND status='admitting'",
            (json.dumps(receipt, separators=(",", ":")), owner_id, key),
        ).rowcount
        if changed != 1:
            raise ClientPlatformError("operation_uncertain")

    try:
        if stopping:
            current = task_execution.get_task_run(task_id, run_id)
            progress["conversation_id"] = current.conversation_id
            authority()
            # Repeating an exact stop is safe; only the existing run owner may
            # cancel its producers and report whether cleanup really completed.
            stopped = task_execution.stop_task_run(task_id, run_id, validate=authority)
            progress.update(status="completed", task_stop_requested=stopped.stop_requested,
                            task_run_quiesced=stopped.quiesced)
        elif retrying:
            if not previous or not previous.get("task_run_reserved"):
                raise ClientPlatformError("operation_uncertain")
            current = task_execution.get_task_run(task_id, run_id)
            progress.update(conversation_id=current.conversation_id, task_run_reserved=True)
            authority()
            # A reserved run may have crashed before or after its first effect.
            # Return its known identity; never dispatch it again.
            progress.update(status="partial", code="task_run_unconfirmed")
        else:
            tools = enabled_tools()
            review = task_execution.get_task_run_review(task_id, enabled_tool_names=tools)
            if review.task_revision != payload["task_revision"]:
                raise task_execution.TaskExecutionError("task_revision_conflict")
            if review.policy_revision != payload["policy_revision"]:
                raise task_execution.TaskExecutionError("task_policy_revision_conflict")
            # Every run is a turn in the workflow's one conversation (F31).
            from row_bot import tasks
            workflow = tasks.get_task(task_id)
            if workflow is None:
                raise task_execution.TaskExecutionError("task_not_found")
            conversation = tasks._prepare_task_thread(workflow)
            # A first run records the conversation it just made; the review still stands.
            task_revision = task_execution.revision_with_conversation(
                task_id, reviewed_revision=payload["task_revision"],
                reviewed_thread_id=workflow.get("persistent_thread_id"))
            if admissions.deletion_state(conversation) != "active":
                raise ClientPlatformError("task_run_target_conflict")
            if not threads._thread_exists(conversation):  # A reminder has no conversation of its own.
                threads.create_thread("Workflow run", thread_id=conversation, seed_default_skills=False)
            progress["conversation_id"] = conversation
            admissions.command_progress(owner_id, key, progress)
            validate()
            authority()
            task_execution.start_reviewed_task_run(task_id,
                expected_task_revision=task_revision, expected_policy_revision=payload["policy_revision"],
                stable_run_id=run_id, conversation_id=conversation, enabled_tool_names=tools,
                validate=authority, record_commit=record_commit, refresh_enabled_tool_names=enabled_tools)
            progress.update(status="completed", task_run_reserved=True)
        authority()
        return admissions.complete_command(owner_id, key, progress)
    except Exception as exc:
        saved = admissions.receipt(owner_id, command["command_id"])
        if saved and saved.get("task_run_reserved"):
            result = {**saved, "status": "partial", "code": "task_run_unconfirmed"}
            admissions.command_progress(owner_id, key, result)
            return result
        if isinstance(exc, task_execution.TaskExecutionError):
            if exc.committed:
                result = {**progress, "status": "partial", "code": exc.code}
                admissions.command_progress(owner_id, key, result)
                return result
            admissions.reject_command(owner_id, key, exc.code)
            raise ClientPlatformError(exc.code) from exc
        if isinstance(exc, ClientPlatformError) and exc.code != "operation_uncertain":
            admissions.reject_command(owner_id, key, exc.code, exc.current_revision)
        raise
