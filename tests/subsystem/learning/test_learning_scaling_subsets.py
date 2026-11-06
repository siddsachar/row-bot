"""The scaling study's nested subsets (PREREG-SCALING §3): nested, stratified by person, drawn once from a seed."""

from __future__ import annotations

import pytest

from tests.e2e.learning.scaling import subsets

pytestmark = [pytest.mark.subsystem]

POOL = {"idris": [f"i{n:03d}" for n in range(180)], "mara": [f"m{n:03d}" for n in range(120)],
        "wren": [f"w{n:03d}" for n in range(60)]}


def test_each_subset_holds_every_smaller_one_and_the_pools_mix_of_people():
    drawn = subsets.draw(POOL, (30, 90, 180, 360), seed=20261106)

    for smaller, larger in zip(drawn, drawn[1:]):
        assert set(smaller.rows) < set(larger.rows)
        assert larger.rows[:len(smaller.rows)] == smaller.rows
    share = {person: len(ids) / 360 for person, ids in POOL.items()}
    for subset in drawn:
        for person, ids in POOL.items():
            taken = sum(1 for row in subset.rows if row in set(ids))
            assert abs(taken - share[person] * subset.n) <= 1, (subset.n, person, taken)
    assert len(set(drawn[-1].rows)) == 360


def test_the_draw_is_the_seeds_own():
    first = subsets.draw(POOL, (30, 90), seed=20261106)
    again = subsets.draw(POOL, (30, 90), seed=20261106)
    other = subsets.draw(POOL, (30, 90), seed=1)

    assert [item.rows for item in first] == [item.rows for item in again]
    assert first[0].rows != other[0].rows


def test_only_the_largest_n_may_be_the_whole_pool():
    small = {"idris": ["i1", "i2", "i3"], "mara": ["m1", "m2"]}

    drawn = subsets.draw(small, (2, 4, 3000), seed=7)
    assert drawn[-1].whole_pool and len(drawn[-1].rows) == 5
    assert not drawn[1].whole_pool and len(drawn[1].rows) == 4

    with pytest.raises(subsets.SubsetError, match="needs an amendment"):
        subsets.draw(small, (2, 1000, 3000), seed=7)
