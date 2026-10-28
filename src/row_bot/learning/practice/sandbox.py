"""Practice runs in throwaway places (L4b; D17, O21).

A practice task runs the way a sample task does (``evaluation.compare``): in a fresh sandbox folder that its files
are seeded into, with the packs' fake apps, at the budget every task gets, and its own check decides. When the
evaluation worker starts it runs through Row-Bot's real runtime (``changes.Tester``'s runner: the person's own
tools behind a throwaway workspace, the network off for code), else in-process. Nothing it does reaches the
person's files, accounts or memory.

**Coding tasks need a container** (O21): without Docker or Podman they are left out and the result says so
(:data:`CODING_NOTE`).

A passing task run through the real runtime brings back its last request and reply exactly as Row-Bot sent them
(the worker returns that only for practice tasks): the training data's practice slice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from row_bot.learning.packs import PackTask

logger = logging.getLogger(__name__)

CODING_NOTE = "Coding practice needs Docker or Podman"


@dataclass
class Ran:
    """A practice run, in counts, with each task's case and the passing tasks' turns (by task id).
    ``state``: done · stopped (the person came back) · limit (the month's limit)."""

    state: str = "done"
    cases: list[Any] = field(default_factory=list)
    turns: dict[str, dict[str, Any]] = field(default_factory=dict)
    coding_left_out: int = 0
    tokens: int = 0
    local_tokens: int = 0

    @property
    def ran(self) -> int:
        return sum(1 for case in self.cases if case.counted)

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)


def runnable(tasks: Sequence[PackTask], *, container: bool) -> tuple[list[PackTask], int]:
    """The tasks that may run here, and how many coding tasks were left out for want of a container (O21)."""

    kept = [task for task in tasks if container or task.verifier.kind != "code_tests"]
    return kept, len(tasks) - len(kept)


class _Stopped(Exception):
    pass


def run(tasks: Sequence[PackTask], arm: Any, *, container: bool, client: Any = None, runner: Any = None,
        meter: Any = None, should_stop: Callable[[], bool] = lambda: False, budget: Any = None) -> Ran:
    """Run each practice task once on ``arm`` (the assistant as it is now), stopping before the next task when
    ``should_stop`` says so. Nothing is saved here."""

    from row_bot.learning import evaluation

    chosen, left_out = runnable(tasks, container=container)
    found = Ran(coding_left_out=left_out)
    if not chosen:
        return found

    def counted(_arm: str, case: Any) -> None:
        found.cases.append(case)
        found.tokens += int(case.tokens)
        found.local_tokens += int(case.local_tokens)
        if case.passed and case.turn:
            found.turns[case.task_id] = dict(case.turn)
        if should_stop():
            raise _Stopped

    try:
        result = evaluation.compare([arm], tasks=chosen, client=client, runner=runner, meter=meter, save=False,
                                    suite="practice", on_case=counted,
                                    budget=budget if budget is not None else evaluation.Budget())
    except _Stopped:
        found.state = "stopped"
        return found
    if result.stopped:
        found.state = "limit"
    return found


__all__ = ["CODING_NOTE", "Ran", "run", "runnable"]
