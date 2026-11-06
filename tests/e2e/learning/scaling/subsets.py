"""The nested subsets of the teacher pool (§3 with SA1): N = 100, 300, 1,000 and 3,000 training targets, stratified
by person.

Drawn once from one seed. Each person's rows are put in a seeded order; then one sequence takes, at each step, the
next row of the person whose share of the sequence is furthest below their share of the pool's rows (Sainte-Laguë's
divisor rule: the person with the largest ``pool rows / (2 * taken + 1)``, ties by name). A divisor rule never takes
a row back as the sequence grows, and the sequence is the same for every N.

**N counts training targets** (SA1: the teacher's messages a row trains on, weight 1). Subset N is the shortest prefix
of the sequence holding at least N targets; in its last row the targets past N stay in as context (weight 0), so the
subset holds exactly N. That trim belongs to the subset alone (:attr:`Subset.trim`): a larger subset holds the same
row whole. So 100 ⊂ 300 ⊂ 1,000 ⊂ 3,000 by construction.

Only the largest N may exceed the pool (§3: then it is the whole pool, stated); a smaller N the pool can't reach is
refused, because the pre-registration has no rule for it.
"""

from __future__ import annotations

import copy
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class SubsetError(RuntimeError):
    """A subset the pre-registration doesn't allow."""


def targets_of(row: Mapping[str, Any]) -> int:
    """A row's training targets: its assistant messages that train (weight 1)."""

    return sum(1 for message in row.get("messages") or () if message.get("role") == "assistant" and message.get("weight"))


def trimmed(row: Mapping[str, Any], keep: int) -> dict[str, Any]:
    """A copy of ``row`` whose targets after the first ``keep`` stay in as context (weight 0)."""

    found = copy.deepcopy(dict(row))
    kept = 0
    for message in found.get("messages") or ():
        if message.get("role") == "assistant" and message.get("weight"):
            kept += 1
            if kept > keep:
                message["weight"] = 0
    return found


@dataclass(frozen=True)
class Subset:
    n: int  # as designed, in training targets
    rows: tuple[str, ...]  # row ids, in draw order
    targets: int  # the targets it holds (N, or the whole pool's)
    #: The last row's id and how many of its targets train in this subset (None: every row whole).
    trim: tuple[str, int] | None = None
    whole_pool: bool = False

    def to_dict(self, by_person: Mapping[str, str], targets: Mapping[str, int]) -> dict[str, Any]:
        people: dict[str, dict[str, int]] = {}
        for row in self.rows:
            entry = people.setdefault(by_person[row], {"rows": 0, "targets": 0})
            entry["rows"] += 1
            entry["targets"] += self.trim[1] if self.trim and self.trim[0] == row else targets[row]
        return {"n": self.n, "rows": len(self.rows), "targets": self.targets, "whole_pool": self.whole_pool,
                "trim": {"id": self.trim[0], "targets": self.trim[1]} if self.trim else None, "by_person": people,
                "ids": list(self.rows)}


def sequence(rows: Mapping[str, Sequence[str]], seed: int | str) -> list[str]:
    """Every row id once, in the stratified, seeded order the subsets are prefixes of (``rows``: person → ids)."""

    order = {person: sorted(ids) for person, ids in rows.items() if ids}
    for person, ids in order.items():
        random.Random(f"{seed}/{person}").shuffle(ids)
    taken = {person: 0 for person in order}
    found: list[str] = []
    while True:
        open_people = [person for person in sorted(order) if taken[person] < len(order[person])]
        if not open_people:
            return found
        person = max(open_people,
                     key=lambda name: (len(order[name]) / (2 * taken[name] + 1), -sorted(order).index(name)))
        found.append(order[person][taken[person]])
        taken[person] += 1


def draw(rows: Mapping[str, Sequence[str]], targets: Mapping[str, int], sizes: Sequence[int],
         seed: int) -> list[Subset]:
    """The nested subsets of ``sizes`` training targets (see the module's docstring); ``targets``: row id → its
    training targets."""

    ordered = sequence(rows, seed)
    total = sum(targets[row] for row in ordered)
    largest = max(sizes)
    found = []
    for n in sorted(sizes):
        if n > total:
            if n != largest:
                raise SubsetError(f"the pool holds {total:,} training targets, fewer than N = {n:,}; §3 covers only "
                                  "the largest N, so this subset needs an amendment (AMENDMENTS-SCALING.md)")
            found.append(Subset(n, tuple(ordered), total, whole_pool=True))
            continue
        held = 0
        for index, row in enumerate(ordered):
            held += targets[row]
            if held >= n:
                keep = targets[row] - (held - n)
                trim = (row, keep) if keep < targets[row] else None
                found.append(Subset(n, tuple(ordered[:index + 1]), n, trim))
                break
    return found


__all__ = ["Subset", "SubsetError", "draw", "sequence", "targets_of", "trimmed"]
