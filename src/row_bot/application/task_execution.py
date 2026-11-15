"""Reviewed task execution and bounded history using existing task/run owners."""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
from typing import Callable, Sequence

from row_bot import tasks
from row_bot.agent_profiles import AgentProfileError


class TaskExecutionError(ValueError):
    def __init__(self, code: str, *, run_id: str | None = None, committed: bool = False):
        self.code, self.run_id, self.committed = code, run_id, committed
        super().__init__(code)


@dataclass(frozen=True)
class TaskRunReview:
    task_id: str
    task_revision: str
    policy_revision: str
    agent_profile_id: str
    approval_mode: str
    notify_only: bool
    steps_total: int
    conversation_id: str | None


@dataclass(frozen=True)
class TaskRunAction:
    title: str
    result: str
    link: str | None = None


@dataclass(frozen=True)
class TaskRunSummary:
    id: str
    task_id: str
    conversation_id: str
    status: str
    started_at: str
    finished_at: str | None
    steps_total: int
    steps_done: int
    # The code folder the run worked in (N9), by id and by name as it was then.
    workspace_id: str | None = None
    workspace_name: str | None = None
    # The scheduled time it missed while Row-Bot was closed, when it ran once on start instead.
    late_for: str | None = None
    # A plain sentence about how it ended, e.g. "Not approved in time".
    notice: str | None = None
    # The one thing the person can do about it: "choose_folder" when its code folder was removed.
    fix: str | None = None
    # What it did outside Row-Bot, from its tools' results: posts, emails, drafts (F31).
    actions: tuple[TaskRunAction, ...] = ()
    # The approval card a waiting run waits on: the person reviews and answers it there.
    approval_id: str | None = None


@dataclass(frozen=True)
class TaskRunPage:
    task_id: str
    revision: str
    items: tuple[TaskRunSummary, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class TaskRunResult:
    run: TaskRunSummary
    replayed: bool


@dataclass(frozen=True)
class TaskStopResult:
    run: TaskRunSummary
    stop_requested: bool
    quiesced: bool


def _identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise TaskExecutionError("invalid_task_identity")


def _revision(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise TaskExecutionError("invalid_task_revision")


def _tools(names: Sequence[str]) -> None:
    if len(names) > 4096 or any(not isinstance(name, str) or not 1 <= len(name) <= 128 for name in names):
        raise TaskExecutionError("task_policy_unavailable")


def get_task_run_review(task_id: str, *, enabled_tool_names: Sequence[str]) -> TaskRunReview:
    _identity(task_id)
    _tools(enabled_tool_names)
    try:
        task, revision, policy = tasks.capture_task_run_review(task_id, enabled_tool_names)
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code) from exc
    except AgentProfileError as exc:
        raise TaskExecutionError("task_policy_unavailable") from exc
    return TaskRunReview(
        task_id, revision, tasks._reviewed_policy_revision(policy), policy["agent_profile_id"],
        policy["approval_mode"], bool(task.get("notify_only")),
        0 if task.get("notify_only") else len(task["prompts"]), task.get("persistent_thread_id"),
    )


def revision_with_conversation(task_id: str, *, reviewed_revision: str, reviewed_thread_id: str | None) -> str:
    """The reviewed revision, carried over the conversation a first run just gave the workflow."""
    _identity(task_id)
    _revision(reviewed_revision)
    try:
        return tasks.reviewed_revision_with_conversation(
            task_id, reviewed_revision=reviewed_revision, reviewed_thread_id=reviewed_thread_id)
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code) from exc


def _summary(row: dict) -> TaskRunSummary:
    for key in ("id", "task_id", "thread_id"):
        _identity(row[key])
    if any(type(row[key]) is not int or row[key] < 0 for key in ("steps_total", "steps_done")):
        raise TaskExecutionError("task_run_metadata_unavailable")
    try:
        saved = json.loads(row.get("actions_json") or "[]")
    except (TypeError, ValueError):
        saved = []
    actions = tuple(
        TaskRunAction(str(item.get("title") or "")[:256] or "Action", str(item.get("result") or "")[:512],
                      str(item.get("link") or "")[:2048] or None)
        for item in (saved if isinstance(saved, list) else [])[:16] if isinstance(item, dict))
    waiting = (row["status"] or "") == "waiting"
    return TaskRunSummary(row["id"], row["task_id"], row["thread_id"], row["status"] or "unknown",
                          row["started_at"] or "", row["finished_at"], row["steps_total"], row["steps_done"],
                          row.get("workspace_id") or None, row.get("workspace_name") or None,
                          row.get("late_for") or None, row.get("notice") or None,
                          row.get("fix") if row.get("fix") == "choose_folder" else None,
                          actions, tasks.pending_approval_id(row["thread_id"]) if waiting else None)


def get_task_run(task_id: str, run_id: str) -> TaskRunSummary:
    _identity(task_id)
    _identity(run_id)
    row = tasks.read_task_run(run_id)
    if row is None or row["task_id"] != task_id:
        raise TaskExecutionError("task_run_not_found")
    return _summary(row)


def start_reviewed_task_run(
    task_id: str, *, expected_task_revision: str, expected_policy_revision: str,
    stable_run_id: str, conversation_id: str, enabled_tool_names: Sequence[str],
    validate: Callable[[], None], record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
    refresh_enabled_tool_names: Callable[[], Sequence[str]] | None = None,
) -> TaskRunResult:
    for identity in (task_id, stable_run_id, conversation_id):
        _identity(identity)
    _revision(expected_task_revision)
    _revision(expected_policy_revision)
    _tools(enabled_tool_names)
    validate()
    try:
        capture = tasks.claim_reviewed_task_run(
            task_id, conversation_id, stable_run_id, expected_task_revision=expected_task_revision,
            expected_policy_revision=expected_policy_revision, enabled_tool_names=enabled_tool_names,
            validate=validate, record_commit=record_commit,
            refresh_enabled_tool_names=refresh_enabled_tool_names,
        )
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code, run_id=stable_run_id, committed=exc.committed) from exc
    except AgentProfileError as exc:
        raise TaskExecutionError("task_policy_unavailable") from exc
    if capture is None:
        validate()
        return TaskRunResult(get_task_run(task_id, stable_run_id), replayed=True)
    try:
        validate()
        tasks.run_task_background(
            task_id, conversation_id, list(enabled_tool_names),
            reviewed_capture=capture, validate=validate,
        )
        return TaskRunResult(get_task_run(task_id, stable_run_id), replayed=False)
    except Exception as exc:
        # The reservation is durable. Neither a missing completion response
        # nor an interrupted dispatch authorizes another workflow execution.
        raise TaskExecutionError("task_run_unconfirmed", run_id=stable_run_id, committed=True) from exc


def list_task_runs(task_id: str, *, cursor: str | None = None, limit: int = 50) -> TaskRunPage:
    _identity(task_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise TaskExecutionError("invalid_task_query")
    offset, expected = 0, None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,1024}", cursor):
                raise ValueError
            parsed = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(parsed, list) or len(parsed) != 4 or parsed[0] != task_id or parsed[1] != limit:
                raise ValueError
            expected, offset = parsed[2:]
            if type(offset) is not int or not 0 < offset < 10**12:
                raise ValueError
            _revision(expected)
        except (ValueError, TypeError, UnicodeError):
            raise TaskExecutionError("cursor_expired") from None
    digest, items, total = hashlib.sha256(), [], 0
    rows = tasks.iter_task_run_snapshot(task_id)
    try:
        for row in rows:
            summary = _summary(row)
            digest.update(json.dumps(asdict(summary), sort_keys=True).encode())
            digest.update(b"\n")
            if offset <= total < offset + limit:
                items.append(summary)
            total += 1
    finally:
        rows.close()
    revision = digest.hexdigest()
    if expected is not None and (expected != revision or offset >= total):
        raise TaskExecutionError("cursor_expired")
    next_cursor = None if offset + len(items) >= total else base64.urlsafe_b64encode(
        json.dumps([task_id, limit, revision, offset + len(items)]).encode()
    ).decode()
    return TaskRunPage(task_id, revision, tuple(items), next_cursor, total)


def stop_task_run(task_id: str, run_id: str, *, validate: Callable[[], None]) -> TaskStopResult:
    _identity(task_id)
    _identity(run_id)
    validate()
    try:
        requested, quiesced = tasks.stop_reviewed_task_run(task_id, run_id, validate=validate)
        return TaskStopResult(get_task_run(task_id, run_id), requested, quiesced)
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code, run_id=run_id, committed=exc.committed) from exc
