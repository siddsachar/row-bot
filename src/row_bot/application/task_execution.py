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
class TaskRunSummary:
    id: str
    task_id: str
    conversation_id: str
    status: str
    started_at: str
    finished_at: str | None
    steps_total: int
    steps_done: int


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
class TaskApprovalReview:
    id: str
    task_id: str
    run_id: str
    revision: str
    message: str
    requested_at: str
    expires_at: str | None
    approval_mode: str
    message_truncated: bool
    response_available: bool


@dataclass(frozen=True)
class TaskApprovalPage:
    items: tuple[TaskApprovalReview, ...]
    total: int
    revision: str
    next_cursor: str | None


@dataclass(frozen=True)
class TaskApprovalResult:
    approval_id: str
    decision: str
    run: TaskRunSummary


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
        0 if task.get("notify_only") else len(task["steps"]), task.get("persistent_thread_id"),
    )


def _summary(row: dict) -> TaskRunSummary:
    for key in ("id", "task_id", "thread_id"):
        _identity(row[key])
    if any(type(row[key]) is not int or row[key] < 0 for key in ("steps_total", "steps_done")):
        raise TaskExecutionError("task_run_metadata_unavailable")
    return TaskRunSummary(row["id"], row["task_id"], row["thread_id"], row["status"] or "unknown",
                          row["started_at"] or "", row["finished_at"], row["steps_total"], row["steps_done"])


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
            task_id, conversation_id, list(enabled_tool_names), notification=False,
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


def list_task_approvals(task_id: str, run_id: str, *, cursor: str | None = None, limit: int = 8) -> TaskApprovalPage:
    _identity(task_id)
    _identity(run_id)
    if type(limit) is not int or not 1 <= limit <= 8:
        raise TaskExecutionError("invalid_task_query")
    expected, offset = None, 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,1024}", cursor):
                raise ValueError
            parsed = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(parsed, list) or len(parsed) != 5 or parsed[:3] != [task_id, run_id, limit]:
                raise ValueError
            expected, offset = parsed[3:]
            _revision(expected)
            if type(offset) is not int or not 0 < offset < 10**12:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise TaskExecutionError("cursor_expired") from None
    items, total, size, full = [], 0, 4096, False  # Reserve envelope/cursor bytes.
    digest = hashlib.sha256()
    from row_bot.runtime.executions import generation_registry
    rows = tasks.iter_task_approval_snapshot(task_id, run_id)
    try:
        for approval, state, revision in rows:
            _identity(approval["id"])
            digest.update(revision.encode())
            if total >= offset and not full:
                capture = state["config"].get("_reviewed_task_run")
                message = approval.get("message") or ""
                truncated = len(message) > 16384
                draining = any(h.domain == "workflow" and h.domain_id == run_id
                               for h in generation_registry.active(state["thread_id"]))
                expired = bool(approval.get("timeout_at") and approval["timeout_at"] <= tasks.datetime.now().isoformat())
                item = TaskApprovalReview(
                    approval["id"], task_id, run_id, revision, message[:16384],
                    str(approval.get("requested_at") or "")[:80],
                    str(approval["timeout_at"])[:80] if approval.get("timeout_at") else None,
                    str(capture["policy"]["approval_mode"]) if capture else "unknown",
                    truncated, bool(capture and not truncated and not draining and not expired and state["status"] == "paused"
                                    and state.get("resume_token") == approval.get("resume_token")
                                    and approval.get("resume_kind") in {None, "", "workflow"}),
                )
                item_size = len(json.dumps(asdict(item), ensure_ascii=True).encode()) + 2
                if len(items) == limit or size + item_size > 240 * 1024:
                    full = True
                else:
                    items.append(item)
                    size += item_size
            total += 1
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code) from exc
    finally:
        rows.close()
    revision = digest.hexdigest()
    if expected is not None and (expected != revision or offset >= total):
        raise TaskExecutionError("cursor_expired")
    if total > offset and not items:
        raise TaskExecutionError("task_approval_review_incomplete")
    next_cursor = None if offset + len(items) >= total else base64.urlsafe_b64encode(
        json.dumps([task_id, run_id, limit, revision, offset + len(items)]).encode()
    ).decode()
    return TaskApprovalPage(tuple(items), total, revision, next_cursor)


def respond_task_approval(
    task_id: str, run_id: str, approval_id: str, *, expected_revision: str, approved: bool,
    validate: Callable[[], None], record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
) -> TaskApprovalResult:
    for identity in (task_id, run_id, approval_id):
        _identity(identity)
    _revision(expected_revision)
    if type(approved) is not bool:
        raise TaskExecutionError("invalid_task_approval")
    validate()
    try:
        approval, state = tasks.claim_reviewed_task_approval(
            task_id, run_id, approval_id, expected_revision=expected_revision, approved=approved,
            validate=validate, record_commit=record_commit,
        )
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code) from exc
    try:
        tasks.resume_reviewed_task_approval(approval, state, approved=approved, validate=validate)
        return TaskApprovalResult(approval_id, "approved" if approved else "denied", get_task_run(task_id, run_id))
    except Exception as exc:
        raise TaskExecutionError("task_approval_unconfirmed", run_id=run_id, committed=True) from exc


def stop_task_run(task_id: str, run_id: str, *, validate: Callable[[], None]) -> TaskStopResult:
    _identity(task_id)
    _identity(run_id)
    validate()
    try:
        requested, quiesced = tasks.stop_reviewed_task_run(task_id, run_id, validate=validate)
        return TaskStopResult(get_task_run(task_id, run_id), requested, quiesced)
    except tasks.TaskMutationError as exc:
        raise TaskExecutionError(exc.code, run_id=run_id, committed=exc.committed) from exc
