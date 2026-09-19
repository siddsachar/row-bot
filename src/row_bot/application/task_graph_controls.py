"""Bounded semantic editing of the canonical saved workflow graph."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
import re
import sqlite3
from typing import Callable

from row_bot import tasks


class TaskGraphError(ValueError):
    def __init__(self, code: str, *, task_id: str = "", committed: bool = False):
        self.code = code
        self.task_id = task_id
        self.committed = committed
        super().__init__(code)


@dataclass(frozen=True)
class TaskGraphFields:
    prompt: str | None = None
    condition: str | None = None
    message: str | None = None
    next: str | None = None
    if_true: str | None = None
    if_false: str | None = None
    if_approved: str | None = None
    if_denied: str | None = None
    on_error: str | None = None
    task_id: str | None = None
    channel: str | None = None
    objective: str | None = None
    profile: str | None = None
    developer_workspace_id: str | None = None
    editing_safety: str | None = None
    return_mode: str | None = None
    context: str | None = None
    max_retries: int | None = None
    retry_delay_seconds: int | None = None
    timeout_minutes: int | None = None
    timeout_seconds: int | None = None
    pass_output: bool | None = None
    run_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class TaskGraphStepEdit:
    id: str
    type: str
    fields: TaskGraphFields


@dataclass(frozen=True)
class TaskGraphStep(TaskGraphStepEdit):
    editable: bool
    retained_fields: bool


@dataclass(frozen=True)
class TaskGraphSnapshot:
    task_id: str
    revision: str
    steps: tuple[TaskGraphStep, ...]
    notify_only: bool


_FIELDS = {
    "prompt": {"prompt", "next", "on_error", "max_retries", "retry_delay_seconds"},
    "condition": {"condition", "if_true", "if_false"},
    "approval": {"message", "timeout_minutes", "if_approved", "if_denied"},
    "subtask": {"task_id", "pass_output", "on_error", "next"},
    "notify": {"message", "channel", "next"},
    "delegate_agent": {"objective", "profile", "developer_workspace_id", "editing_safety",
                       "return_mode", "context", "timeout_seconds", "on_error", "next"},
    "wait_for_agents": {"run_ids", "timeout_seconds", "on_error", "next"},
}
_REFS = {"next", "if_true", "if_false", "if_approved", "if_denied"}
_NUMBERS = {"max_retries": (1, 10), "retry_delay_seconds": (0, 300),
            "timeout_minutes": (0, 1440), "timeout_seconds": (1, 7200)}


def _fail(code: str = "invalid_task_graph") -> None:
    raise TaskGraphError(code)


def _identity(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        _fail()


def _budget(value: object) -> None:
    if len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()) > 240 * 1024:
        _fail("task_graph_too_large")


def _project(step: dict) -> TaskGraphStep:
    identity, kind = step.get("id"), step.get("type", "prompt")
    _identity(identity)
    _identity(kind)
    allowed = _FIELDS.get(kind, set())
    values = {name: step[name] for name in allowed if name in step}
    if kind == "delegate_agent":
        values.setdefault("objective", step.get("prompt", ""))
        values.setdefault("profile", step.get("agent_profile_id", "worker"))
        values.setdefault("editing_safety", "worktree" if step.get("use_worktree")
                          else step.get("workspace_mode", "profile_default"))
        values.setdefault("return_mode", "wait" if step.get("wait", True) else "background")
    if "run_ids" in values and values["run_ids"] is not None:
        raw = values["run_ids"]
        values["run_ids"] = tuple(item.strip() for item in raw.split(",") if item.strip()) if isinstance(raw, str) else tuple(raw)
    fields = TaskGraphFields(**values)
    _validate_fields(kind, fields, semantic=False)
    return TaskGraphStep(identity, kind, fields, kind in _FIELDS,
                         bool(set(step) - allowed - {"id", "type"}))


def _capture(task_id: str) -> tuple[dict, str]:
    _identity(task_id)
    try:
        saved = tasks.read_task_for_edit(task_id)
    except tasks.TaskMutationError as exc:
        raise TaskGraphError(exc.code, task_id=task_id) from exc
    if saved is None:
        raise TaskGraphError("task_not_found", task_id=task_id)
    return saved


def _snapshot(task_id: str, task: dict, revision: str) -> TaskGraphSnapshot:
    raw = task.get("steps") or []
    if len(raw) > 100 or any(not isinstance(step, dict) for step in raw):
        _fail("task_graph_too_large")
    try:
        projected = tuple(_project(step) for step in raw)
    except (TypeError, KeyError, ValueError) as exc:
        raise TaskGraphError("task_graph_unavailable", task_id=task_id) from exc
    if len({step.id for step in projected}) != len(projected):
        _fail("task_graph_unavailable")
    result = TaskGraphSnapshot(task_id, revision, projected, bool(task.get("notify_only")))
    _budget(asdict(result))
    return result


def get_task_graph(task_id: str) -> TaskGraphSnapshot:
    task, revision = _capture(task_id)
    return _snapshot(task_id, task, revision)


def _condition(value: str, depth: int = 0) -> None:
    """Check syntax only: never evaluate a saved LLM or regular expression."""
    if depth > 16:
        _fail()
    if value in {"true", "false", "empty", "not_empty"}:
        return
    operator, separator, argument = value.partition(":")
    if not separator:
        _fail()
    if operator in {"contains", "not_contains", "equals", "llm"}:
        return
    if operator == "matches":
        try:
            re.compile(argument)
        except re.error:
            _fail()
        return
    if operator in {"gt", "lt", "gte", "lte", "length_gt", "length_lt"}:
        try:
            number = int(argument) if operator.startswith("length_") else float(argument)
            if not math.isfinite(number):
                _fail()
        except ValueError:
            _fail()
        return
    if operator == "json":
        path, separator, child = argument.partition(":")
        if not separator or not path:
            _fail()
        _condition(child, depth + 1)
        return
    if operator in {"and", "or"} and argument.startswith("[") and argument.endswith("]"):
        children = tasks._split_compound(argument[1:-1])
        if not children or len(children) > 100:
            _fail()
        for child in children:
            _condition(child, depth + 1)
        return
    _fail()


def _validate_fields(kind: str, fields: TaskGraphFields, *, semantic: bool) -> None:
    if not isinstance(fields, TaskGraphFields):
        _fail()
    for name, value in asdict(fields).items():
        if value is None:
            continue
        if name not in _FIELDS.get(kind, set()):
            _fail()
        if name in _NUMBERS:
            low, high = _NUMBERS[name]
            if type(value) is not int or not low <= value <= high:
                _fail()
        elif name == "pass_output":
            if type(value) is not bool:
                _fail()
        elif name == "run_ids":
            if not isinstance(value, tuple) or len(value) > 100:
                _fail()
            for identity in value:
                _identity(identity)
        elif not isinstance(value, str) or len(value) > 16384 or "\x00" in value:
            _fail()
    if not semantic:
        return
    required = {"prompt": "prompt", "condition": "condition", "approval": "message",
                "notify": "message", "delegate_agent": "objective", "subtask": "task_id"}
    if kind in required and not (getattr(fields, required[kind]) or "").strip():
        _fail()
    if fields.on_error not in {None, "stop", "skip"}:
        _fail()
    if fields.editing_safety not in {None, "profile_default", "read_only", "single_writer", "worktree"}:
        _fail()
    if fields.return_mode not in {None, "wait", "background"}:
        _fail()
    if fields.editing_safety == "worktree" and not fields.developer_workspace_id:
        _fail()
    for name in ("task_id", "profile", "developer_workspace_id", "channel"):
        if getattr(fields, name):
            if name == "profile":
                if not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", fields.profile):
                    _fail()
            else:
                _identity(getattr(fields, name))
    if kind == "condition":
        _condition((fields.condition or "").strip())
        if not fields.if_true and not fields.if_false:
            _fail()


def update_saved_task_graph(
    task_id: str, steps: tuple[TaskGraphStepEdit, ...], *, expected_revision: str,
    validate: Callable[[], None],
    record_commit: Callable[[sqlite3.Connection, str], None] | None = None,
) -> TaskGraphSnapshot:
    if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
        _fail()
    if not isinstance(steps, tuple) or not 1 <= len(steps) <= 100:
        _fail()
    task, revision = _capture(task_id)
    if revision != expected_revision:
        _fail("task_revision_conflict")
    before = _snapshot(task_id, task, revision)
    originals = {step["id"]: step for step in task.get("steps") or []}
    projected = {step.id: step for step in before.steps}
    identities: set[str] = set()
    merged = []
    for edit in steps:
        if not isinstance(edit, TaskGraphStepEdit):
            _fail()
        _identity(edit.id)
        if edit.id == "end" or edit.id in identities:
            _fail()
        identities.add(edit.id)
        previous = projected.get(edit.id)
        if edit.type not in _FIELDS:
            if previous is None or previous.type != edit.type or previous.fields != edit.fields:
                _fail()
            merged.append(deepcopy(originals[edit.id]))
            continue
        _validate_fields(edit.type, edit.fields, semantic=True)
        item = deepcopy(originals.get(edit.id, {"id": edit.id}))
        # Only explicitly changed public fields are rewritten. Hidden provider,
        # profile migration and future metadata remain byte-for-byte values.
        if previous is None or previous.type != edit.type:
            for name in TaskGraphFields.__dataclass_fields__:
                item.pop(name, None)
        for name in _FIELDS[edit.type]:
            value = getattr(edit.fields, name)
            if previous and previous.type == edit.type and value == getattr(previous.fields, name):
                continue
            if value is None:
                item.pop(name, None)
            else:
                item[name] = list(value) if isinstance(value, tuple) else value
        item["type"] = edit.type
        if edit.type == "delegate_agent" and (previous is None or previous.fields != edit.fields):
            if edit.fields.editing_safety is not None:
                item["use_worktree"] = edit.fields.editing_safety == "worktree"
                item.pop("workspace_mode", None)
                if edit.fields.editing_safety != "profile_default":
                    item["workspace_mode"] = edit.fields.editing_safety
            if edit.fields.return_mode is not None:
                item["wait"] = edit.fields.return_mode == "wait"
        merged.append(item)
    for item in merged:
        for field in _REFS:
            target = item.get(field)
            if target and (target not in identities | {"end"} or target == item["id"]):
                _fail("task_graph_invalid_reference")
        for field in ("prompt", "message", "objective", "context"):
            value = item.get(field, "")
            if not isinstance(value, str):
                continue  # Unexposed metadata on retained unknown types is not executable here.
            for target in re.findall(r"\{\{\s*step\.([A-Za-z0-9_-]+)\.output\s*\}\}", value):
                if target not in identities:
                    _fail("task_graph_invalid_reference")
    # Bound the complete returned projection before any write, not a truncated
    # subset that could erase unseen steps on the next save.
    _snapshot(task_id, {**task, "steps": merged}, revision)
    try:
        tasks.update_task_graph(task_id, expected_revision=revision, steps=merged,
                                validate=validate, record_commit=record_commit)
    except tasks.TaskMutationError as exc:
        raise TaskGraphError(exc.code, task_id=task_id, committed=exc.committed) from exc
    try:
        return get_task_graph(task_id)
    except Exception as exc:
        raise TaskGraphError("task_graph_read_unconfirmed", task_id=task_id, committed=True) from exc
