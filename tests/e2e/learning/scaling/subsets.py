"""The nested subsets of the teacher pool (§3): N = 100, 300, 1,000 and 3,000 rows, stratified by person.

Drawn once from one seed. Each person's rows are put in a seeded order; then one sequence takes, at each step, the
next row of the person whose share of the sequence is furthest below their share of the pool (Sainte-Laguë's
divisor rule: the person with the largest ``pool rows / (2 * taken + 1)``, ties by name). Subset N is the first N
rows of that sequence, so 100 ⊂ 300 ⊂ 1,000 ⊂ 3,000 by construction, and every subset is as close to the pool's
mix of people as whole rows allow (a divisor rule never takes a row back as N grows).

Only the largest N may exceed the pool (§3: then it is the whole pool, stated); a smaller N the pool can't reach is
refused, because the pre-registration has no rule for it.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class SubsetError(RuntimeError):
    """A subset the pre-registration doesn't allow."""


@dataclass(frozen=True)
class Subset:
    n: int  # as designed
    rows: tuple[str, ...]  # row ids, in draw order
    whole_pool: bool = False

    def to_dict(self, by_person: Mapping[str, str]) -> dict[str, Any]:
        people: dict[str, int] = {}
        for row in self.rows:
            people[by_person[row]] = people.get(by_person[row], 0) + 1
        return {"n": self.n, "rows": len(self.rows), "whole_pool": self.whole_pool, "by_person": people,
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
        person = max(open_people, key=lambda name: (len(order[name]) / (2 * taken[name] + 1), -sorted(order).index(name)))
        found.append(order[person][taken[person]])
        taken[person] += 1


def draw(rows: Mapping[str, Sequence[str]], sizes: Sequence[int], seed: int) -> list[Subset]:
    """The nested subsets of ``sizes`` (see the module's docstring)."""

    ordered = sequence(rows, seed)
    largest = max(sizes)
    found = []
    for n in sorted(sizes):
        if n > len(ordered) and n != largest:
            raise SubsetError(f"the pool holds {len(ordered):,} rows, fewer than N = {n:,}; §3 covers only the "
                              "largest N, so this subset needs an amendment (AMENDMENTS-SCALING.md)")
        found.append(Subset(n, tuple(ordered[:n]), whole_pool=n > len(ordered)))
    return found


__all__ = ["Subset", "SubsetError", "draw", "sequence"]
