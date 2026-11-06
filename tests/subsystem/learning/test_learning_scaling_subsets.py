"""The scaling study's nested subsets (PREREG-SCALING §3, SA1): exactly N training targets, nested, stratified by
person and drawn once from a seed."""

from __future__ import annotations

import pytest

from tests.e2e.learning.scaling import subsets

pytestmark = [pytest.mark.subsystem]

POOL = {"idris": [f"i{n:03d}" for n in range(180)], "mara": [f"m{n:03d}" for n in range(120)],
        "wren": [f"w{n:03d}" for n in range(60)]}
#: Each row's training targets: 1 to 4 (a turn's tool calls and its answer).
TARGETS = {row: 1 + sum(map(ord, row)) % 4 for rows in POOL.values() for row in rows}


def _held(subset: subsets.Subset, targets: dict[str, int] = TARGETS) -> int:
    return sum(subset.trim[1] if subset.trim and subset.trim[0] == row else targets[row] for row in subset.rows)


def test_each_subset_holds_exactly_n_targets_and_every_smaller_one():
    drawn = subsets.draw(POOL, TARGETS, (100, 300, 600), seed=20261106)

    for subset in drawn:
        assert _held(subset) == subset.targets == subset.n
    for smaller, larger in zip(drawn, drawn[1:]):
        assert larger.rows[:len(smaller.rows)] == smaller.rows
        if smaller.trim:  # trimmed in the smaller subset only: the larger one holds that row whole
            assert smaller.trim[0] in larger.rows and (larger.trim is None or larger.trim[0] != smaller.trim[0])
            assert 0 < smaller.trim[1] < TARGETS[smaller.trim[0]]
    assert sum(1 for subset in drawn if subset.trim) >= 1


def test_a_trimmed_row_keeps_its_later_targets_as_context_and_leaves_the_pool_row_whole():
    row = {"messages": [{"role": "user", "content": "Total the donations"},
                        {"role": "assistant", "content": "", "weight": 1, "tool_calls": [{"id": "a"}]},
                        {"role": "tool", "content": "rows"},
                        {"role": "assistant", "content": "", "weight": 1, "tool_calls": [{"id": "b"}]},
                        {"role": "tool", "content": "done"},
                        {"role": "assistant", "content": "The total is 412.50.", "weight": 1}]}

    kept = subsets.trimmed(row, 2)

    assert subsets.targets_of(kept) == 2 and subsets.targets_of(row) == 3
    assert [message.get("weight") for message in kept["messages"]] == [None, 1, None, 1, None, 0]
    assert [message["content"] for message in kept["messages"]] == [message["content"] for message in row["messages"]]


def test_the_draw_is_stratified_by_person_and_the_seeds_own():
    drawn = subsets.draw(POOL, TARGETS, (100, 300, 600), seed=20261106)

    share = {person: len(ids) / 360 for person, ids in POOL.items()}
    for subset in drawn:
        for person, ids in POOL.items():
            taken = sum(1 for row in subset.rows if row in set(ids))
            assert abs(taken - share[person] * len(subset.rows)) <= 1, (subset.n, person, taken)
    again = subsets.draw(POOL, TARGETS, (100, 300, 600), seed=20261106)
    other = subsets.draw(POOL, TARGETS, (100, 300, 600), seed=1)
    assert [item.rows for item in drawn] == [item.rows for item in again]
    assert drawn[0].rows != other[0].rows


def test_only_the_largest_n_may_be_the_whole_pool():
    small = {"idris": ["i1", "i2", "i3"], "mara": ["m1", "m2"]}
    targets = {"i1": 3, "i2": 2, "i3": 1, "m1": 2, "m2": 3}

    drawn = subsets.draw(small, targets, (4, 8, 3000), seed=7)
    assert drawn[-1].whole_pool and drawn[-1].targets == 11 and len(drawn[-1].rows) == 5
    assert not drawn[1].whole_pool and _held(drawn[1], targets) == 8

    with pytest.raises(subsets.SubsetError, match="needs an amendment"):
        subsets.draw(small, targets, (4, 1000, 3000), seed=7)
