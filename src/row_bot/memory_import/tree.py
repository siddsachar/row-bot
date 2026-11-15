"""Picking the current branch of a message tree."""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def current_branch(parents: Mapping[str, str | None], times: Mapping[str, float | None],
                   order: Sequence[str], leaf: str | None = None) -> list[str]:
    """Node ids from the root to ``leaf``, or to the newest leaf when ``leaf`` is unknown.

    ``parents`` maps each node to its parent; a parent that is not a node makes
    the node a root. A cycle ends the walk.
    """
    if not parents:
        return []
    if leaf not in parents:
        has_child = {parent for parent in parents.values() if parent in parents}
        position = {node: index for index, node in enumerate(order)}
        leaves = [node for node in parents if node not in has_child]
        if not leaves:  # every node is in a cycle
            leaves = list(parents)
        leaf = max(leaves, key=lambda node: (
            times.get(node) if times.get(node) is not None else float("-inf"),
            position.get(node, -1),
        ))
    path: list[str] = []
    seen: set[str] = set()
    node: str | None = leaf
    while node is not None and node in parents and node not in seen:
        seen.add(node)
        path.append(node)
        node = parents[node]
    path.reverse()
    return path
