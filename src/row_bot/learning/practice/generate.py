"""Practice tasks Row-Bot makes up from its own mistakes (L4b; D17, O18, O21; pre-registration §3.3).

For one issue (``diagnosis``: a kind of work and what went wrong in it, with the person's evidence), one
``learning_teacher`` call asks the model for a few **made-up** tasks of the same kind in the task packs' shape: a
prompt, the files or fake-app records it starts from, an executable check, a right solution and a plausible wrong
one that makes the same mistake. The data is synthetic: the model is told to invent every name, number and record.
A task is kept only when it is

- **verifiable**: its check passes its right solution and fails its wrong one, played in a throwaway sandbox
  (``packs.verify``). A coding task's check runs code, which needs a container (O21): without Docker or Podman no
  coding practice is made, and the result says so;
- **novel**: the audit vault says it isn't close to any hidden problem (``evaluation.is_novel``: a yes or no, and
  the vault logs the look). This module can't read the vault (``sealing``);
- **new**: not the twin of a sample task or of a practice task made before.

What the model is shown is ``diagnosis.describe``'s view of the issue, made from evidence that already leaves out
everything held for testing (E10: the held-out decisions and the turns after held-out corrections, O18, O87), and
one visible sample task of the kind, as the format. Nothing here is saved: the night (``practice.practise``) keeps
what it made; a strategy trial's arm keeps its own to itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from row_bot.learning.packs import PACK_FORMAT, PACK_FORMAT_VERSION, PACKS, PackError, PackTask

logger = logging.getLogger(__name__)

PURPOSE = "learning_teacher"
#: Practice tasks one call may make (a learning method asks for up to its ``practice.made``).
MAX_TASKS = 6
#: How much of the model's reply is read.
REPLY_LIMIT = 200_000
CODING = "small_coding"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_OWN_KEYS = ("format", "version", "id", "split", "pack")

SYSTEM = """You make practice tasks for Row-Bot, an AI assistant, from a kind of mistake it made.

Each task is made up. Invent every name, number, date, document and record: never copy the person's words, names, \
files or data from the examples you are shown. Make each task about the same kind of mistake, so that an assistant \
that has learned to avoid it passes and one that hasn't fails.

Each task has a prompt, the files or app records it starts from, a check, the right solution ("reference") and a \
plausible wrong solution ("decoy") that makes the same mistake. The check must pass the reference and fail the decoy.

Answer with a JSON list of tasks in exactly the sample task's format (its keys, its kinds of check, its tools), \
without the keys "format", "version", "id", "split" and "pack". Nothing else."""


@dataclass
class Made:
    """What one issue's practice call came to. ``state``: done · no_call (nothing asked) · not_a_task_kind (its
    kind of work has no task shape) · needs_container (coding without Docker or Podman) · limit · unavailable."""

    issue: str
    kind: str
    state: str = "done"
    asked: int = 0
    kept: list[PackTask] = field(default_factory=list)
    unreadable: int = 0
    unverifiable: int = 0
    repeats: int = 0
    not_novel: int = 0
    tokens: int = 0
    local: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"issue": self.issue, "kind": self.kind, "state": self.state, "asked": self.asked,
                "kept": len(self.kept), "unreadable": self.unreadable, "unverifiable": self.unverifiable,
                "repeats": self.repeats, "not_novel": self.not_novel, "tokens": self.tokens}


def sample(kind: str) -> PackTask:
    """The visible sample task the model is shown as the format for ``kind``."""

    from row_bot.learning.packs import visible_tasks

    return visible_tasks([kind])[0]


def _shape(task: PackTask) -> dict[str, Any]:
    return {key: value for key, value in task.to_dict().items() if key not in _OWN_KEYS}


def fingerprint(task: PackTask) -> str:
    """What makes two tasks twins: the same text (prompt, files, apps)."""

    return hashlib.sha256(" ".join(task.text().lower().split()).encode("utf-8")).hexdigest()


def _candidates(reply: str) -> list[Any]:
    text = str(reply or "")[:REPLY_LIMIT].strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = min((index for index in (text.find("["), text.find("{")) if index >= 0), default=-1)
    if start < 0:
        return []
    try:
        found, _end = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return []
    if isinstance(found, Mapping):
        found = found.get("tasks", [found])
    return list(found) if isinstance(found, list) else []


def task_from(raw: Any, kind: str) -> PackTask:
    """A practice task from one item of the model's answer, in the packs' shape and checked by it; raises
    :class:`PackError` when it isn't one."""

    if not isinstance(raw, Mapping):
        raise PackError("not a task")
    body = {key: value for key, value in raw.items() if key not in _OWN_KEYS}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    data = {**body, "format": PACK_FORMAT, "version": PACK_FORMAT_VERSION, "id": f"practice_{digest.hexdigest()[:16]}",
            "split": "practice", "pack": kind}
    try:
        task = PackTask.from_dict(data)
    except (KeyError, TypeError, ValueError) as error:
        raise PackError("not a task") from error
    if task.verifier.kind == "code_tests" and kind != CODING:
        raise PackError("only coding tasks run code")
    return task


def verifiable(task: PackTask, *, container: bool) -> bool:
    """Its check passes its right solution and fails its wrong one (code only in a container, O21)."""

    from row_bot.learning.packs.verify import solve, verify
    from row_bot.learning.sandbox import SandboxError

    if task.verifier.kind == "code_tests" and not container:
        return False
    backend = "container" if task.verifier.kind == "code_tests" else "auto"
    try:
        right = verify(task, solve(task, task.reference), backend=backend)
        wrong = verify(task, solve(task, task.decoy), backend=backend)
    except (SandboxError, PackError, KeyError, TypeError, ValueError, OSError):
        return False
    return bool(right.passed and not wrong.passed)


def _container() -> bool:
    from row_bot.learning.sandbox import container_runtime

    try:
        return container_runtime() is not None
    except Exception:  # noqa: BLE001 - no container: no coding practice
        return False


def make(store: Any, issue: Any, *, client: Any, count: int, now: float | None = None, meter: Any = None,
         container: Callable[[], bool] | None = None, known: Iterable[str] = (), purpose: str = PURPOSE,
         method: Any = None, book: Any = None, asked_by: str = "practice") -> Made:
    """Up to ``count`` practice tasks for ``issue`` (one call): the verifiable, novel and new ones are kept.
    ``known`` holds fingerprints of practice tasks made before (none is made again); ``meter`` is the month's (or a
    trial arm's) allowance."""

    from row_bot.learning import corrections, diagnosis, evaluation
    from row_bot.learning.calls import ClaudePlanRefused, NoModel, estimate_tokens, text_of

    made = Made(issue.key, issue.kind)
    count = max(0, min(int(count), MAX_TASKS))
    if issue.kind not in PACKS:
        made.state = "not_a_task_kind"
        return made
    has_container = (container or _container)()
    if issue.kind == CODING and not has_container:
        made.state = "needs_container"
        return made
    if count == 0:
        made.state = "no_call"
        return made
    from langchain_core.messages import HumanMessage, SystemMessage

    moment = float(now if now is not None else store.now())
    shown = diagnosis.describe(store, book or corrections.Book(store), issue, now=moment, method=method)
    template = sample(issue.kind)
    messages = [SystemMessage(content=SYSTEM), HumanMessage(content=(
        f"{shown}\n\nThe sample task (its format, not its topic):\n```json\n"
        f"{json.dumps(_shape(template), ensure_ascii=False, indent=1)}\n```\n\nWrite {count} new practice "
        f"{'task' if count == 1 else 'tasks'}."))]
    made.local = bool(client.is_local(None))
    if meter is not None and not meter.allows(estimate_tokens(messages), local=made.local):
        made.state = "limit"
        return made
    try:
        reply = client.invoke(messages, tools=None, model_ref=None, purpose=purpose)
    except (ClaudePlanRefused, NoModel):
        made.state = "unavailable"
        return made
    except Exception as error:  # noqa: BLE001 - no practice tonight, said plainly
        logger.info("Learning couldn't make practice tasks (%s)", type(error).__name__)
        made.state = "unavailable"
        return made
    if meter is not None:
        meter.charge(reply.tokens, local=reply.local)
    made.tokens = int(reply.tokens)
    seen = set(known) | {fingerprint(task) for task in _visible(issue.kind)}
    for raw in _candidates(text_of(reply.message))[:count]:
        made.asked += 1
        try:
            task = task_from(raw, issue.kind)
        except PackError:
            made.unreadable += 1
            continue
        mark = fingerprint(task)
        if mark in seen:
            made.repeats += 1
            continue
        if not verifiable(task, container=has_container):
            made.unverifiable += 1
            continue
        if not evaluation.is_novel(task, asked_by=asked_by, store=store):
            made.not_novel += 1
            continue
        seen.add(mark)
        made.kept.append(task)
    return made


def _visible(kind: str) -> list[PackTask]:
    from row_bot.learning.packs import visible_tasks

    return visible_tasks([kind])


__all__ = ["CODING", "MAX_TASKS", "Made", "PURPOSE", "SYSTEM", "fingerprint", "make", "sample", "task_from",
           "verifiable"]
