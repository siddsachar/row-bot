"""Learning's built-in task packs (D30): synthetic tasks with executable verifiers.

Four packs, one per kind of work: **document lookups**, **structured transforms**,
**small coding tasks** and **tool workflows** (fake apps). Every task is synthetic and
checked in, and has

- a prompt and the files and fake-app state it starts from;
- a **verifier** (:class:`Verifier`): an executable check of the answer, a file, the fake
  apps' final state, or tests run in a sandbox with the network off;
- a **reference** solution that passes and a **decoy** (a plausible mistake) that fails,
  so every verifier is itself tested.

Each pack has a **visible split** (here, for testing changes and for practice) and a
**hidden split** (``packs.hidden``, sealed: only the audit vault reads it, E10).

Importing this package reads no file and loads nothing else from Row-Bot.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

PACK_FORMAT = "row_bot.learning.pack_task"
PACK_FORMAT_VERSION = 1

Split = Literal["visible", "hidden"]
VerifierKind = Literal["answer", "json_answer", "file", "app_state", "code_tests"]
VERIFIER_KINDS: tuple[str, ...] = ("answer", "json_answer", "file", "app_state", "code_tests")
ANSWER_MATCHES = ("exact", "contains", "number", "any_of")
FILE_MATCHES = ("text", "lines", "json", "csv", "csv_rows")
TOOLSETS = ("files", "calendar", "mail", "issues")

# The four packs (D30): name -> the kind of work people see.
PACKS: dict[str, str] = {
    "document_lookups": "Document lookups",
    "structured_transforms": "Structured transforms",
    "small_coding": "Small coding tasks",
    "tool_workflows": "Tool workflows",
}


class PackError(ValueError):
    """A pack task that is not well formed."""


@dataclass(frozen=True)
class Verifier:
    """How a task's result is checked.

    - ``answer``: the final answer, ``match`` = exact | contains | number | any_of
      (``expected`` a string, a number, or a list of acceptable strings).
    - ``json_answer``: the answer parses as JSON equal to ``expected``.
    - ``file``: the workspace file ``path`` equals ``expected``, ``match`` = text | lines |
      json | csv | csv_rows (rows in any order).
    - ``app_state``: ``expected`` is a list of checks on the fake apps' final state, each
      ``{"in": "calendar.events", "where": {...}, "count": n}`` (``where`` values match
      case-insensitively; ``{"contains": "x"}`` matches a substring, a list matches a
      list in any order).
    - ``code_tests``: ``expected`` is a unittest file run against the workspace in a
      sandbox with the network off; ``path`` is the module the task asks for.
    """

    kind: VerifierKind
    expected: Any = None
    match: str = "exact"
    path: str = ""
    tolerance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "expected": self.expected, "match": self.match, "path": self.path,
                "tolerance": self.tolerance}


@dataclass(frozen=True)
class Solution:
    """One way to finish a task: the final answer, the files written, the fake-app calls made."""

    answer: str = ""
    files: Mapping[str, str] = field(default_factory=dict)
    calls: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"answer": self.answer, "files": dict(self.files), "calls": [dict(call) for call in self.calls]}


@dataclass(frozen=True)
class PackTask:
    """One task of a pack."""

    id: str
    pack: str
    split: Split
    prompt: str
    verifier: Verifier
    reference: Solution
    decoy: Solution
    files: Mapping[str, str] = field(default_factory=dict)
    apps: Mapping[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] = ("files",)
    max_steps: int = 8

    @property
    def kind_of_work(self) -> str:
        return PACKS.get(self.pack, self.pack)

    def text(self) -> str:
        """The task as one text (prompt and starting files): what novelty is judged on."""

        parts = [self.prompt]
        parts.extend(f"{name}\n{content}" for name, content in sorted(self.files.items()))
        if self.apps:
            parts.append(json.dumps(self.apps, sort_keys=True))
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """The task's canonical form (a fixture's input blob holds it)."""

        return {
            "format": PACK_FORMAT, "version": PACK_FORMAT_VERSION, "id": self.id, "pack": self.pack,
            "split": self.split, "prompt": self.prompt, "files": dict(self.files), "apps": dict(self.apps),
            "tools": list(self.tools), "max_steps": self.max_steps, "verifier": self.verifier.to_dict(),
            "reference": self.reference.to_dict(), "decoy": self.decoy.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackTask:
        if data.get("format") != PACK_FORMAT or data.get("version") != PACK_FORMAT_VERSION:
            raise PackError("not a pack task this app reads")
        verifier = dict(data["verifier"])

        def solution(raw: Mapping[str, Any]) -> Solution:
            return Solution(str(raw.get("answer") or ""), dict(raw.get("files") or {}),
                            tuple(dict(call) for call in raw.get("calls") or ()))

        task = cls(
            id=str(data["id"]), pack=str(data["pack"]), split=data["split"], prompt=str(data["prompt"]),
            verifier=Verifier(verifier["kind"], verifier.get("expected"), str(verifier.get("match") or "exact"),
                              str(verifier.get("path") or ""), float(verifier.get("tolerance") or 0.0)),
            reference=solution(data["reference"]), decoy=solution(data["decoy"]),
            files=dict(data.get("files") or {}), apps=dict(data.get("apps") or {}),
            tools=tuple(data.get("tools") or ("files",)), max_steps=int(data.get("max_steps") or 8),
        )
        validate(task)
        return task


def validate(task: PackTask) -> PackTask:
    """Refuse a task whose shape the evaluator can't run or check."""

    if not task.id or not task.prompt.strip():
        raise PackError("a task has an id and a prompt")
    if task.pack not in PACKS:
        raise PackError(f"{task.id}: unknown pack {task.pack!r}")
    if task.split not in ("visible", "hidden"):
        raise PackError(f"{task.id}: unknown split {task.split!r}")
    if task.verifier.kind not in VERIFIER_KINDS:
        raise PackError(f"{task.id}: unknown verifier {task.verifier.kind!r}")
    if any(tool not in TOOLSETS for tool in task.tools) or not task.tools:
        raise PackError(f"{task.id}: unknown tools {task.tools!r}")
    if task.verifier.kind == "answer" and task.verifier.match not in ANSWER_MATCHES:
        raise PackError(f"{task.id}: unknown answer match {task.verifier.match!r}")
    if task.verifier.kind == "file" and (task.verifier.match not in FILE_MATCHES or not task.verifier.path):
        raise PackError(f"{task.id}: a file check names its file and match")
    if task.verifier.kind == "code_tests" and not (task.verifier.path and isinstance(task.verifier.expected, str)):
        raise PackError(f"{task.id}: code tests name the module and hold the tests")
    if task.verifier.kind == "app_state" and not isinstance(task.verifier.expected, list):
        raise PackError(f"{task.id}: app checks are a list")
    if not 1 <= task.max_steps <= 32:
        raise PackError(f"{task.id}: 1 to 32 steps")
    for name in task.files:
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise PackError(f"{task.id}: {name!r} is not a workspace path")
    return task


def _load(pack: str) -> tuple[PackTask, ...]:
    from importlib import import_module

    module = import_module(f"row_bot.learning.packs.{pack}")
    return tuple(validate(task) for task in module.TASKS)


def visible_tasks(packs: Iterable[str] | None = None) -> list[PackTask]:
    """The visible split of the named packs (all four by default), in pack order."""

    names = list(PACKS) if packs is None else [name for name in packs]
    unknown = [name for name in names if name not in PACKS]
    if unknown:
        raise PackError(f"unknown packs {unknown}")
    found: list[PackTask] = []
    for name in names:
        found.extend(task for task in _load(name) if task.split == "visible")
    return found


def task_by_id(task_id: str) -> PackTask:
    """A visible task by its id."""

    for task in visible_tasks():
        if task.id == task_id:
            return task
    raise KeyError(task_id)


__all__ = [
    "ANSWER_MATCHES",
    "FILE_MATCHES",
    "PACKS",
    "PACK_FORMAT",
    "PackError",
    "PackTask",
    "Solution",
    "TOOLSETS",
    "VERIFIER_KINDS",
    "Verifier",
    "task_by_id",
    "validate",
    "visible_tasks",
]
