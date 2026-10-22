"""Held-out corrections (O87): every 2nd confirmed correction becomes a hidden problem.

The audit vault holds only decisions nobody corrected (O19's eligibility), so "lessons vs none"
on hidden real work would be tested only where the main model was already right. From the first
build that drafts or trains on corrections, **every 2nd confirmed correction from exact capture**,
in time order, is held out: its decision gets the ``held_out`` mark that datasets, drafting and
practice already leave out (:meth:`LearningStore.held_out_ids`), so nothing ever trains on it, and
the ``held_out_correction`` mark that says the vault should hold it with its corrected action as
the reference (L3a stores and tests it; :func:`waiting_for_vault` is that seam).

- **Confirmed correction**: a Teach (or, from L3a, a next-message correction) linked to the
  decision it corrects, not withdrawn.
- **Exact capture** (O89): the decision's fidelity is ``exact`` or ``redacted``. Imported past
  chats (``partial``) are never held out, never hidden, and don't count towards "every 2nd".
- **Time order, append-only**: corrections newer than the last one considered are walked in the
  order they were made; the decisions counted and that horizon are kept in ``learning.db``, so a
  correction once held stays held, a decision corrected again later is never counted twice (its
  redo may have trained meanwhile), and one trained on is never hidden later. These rules are
  fixed points (E4), like the vault's every 4th decision.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from row_bot.learning.contracts import Decision, Episode, Signal
from row_bot.learning.store import MARK_HELD_OUT, MARK_HELD_OUT_CORRECTION, LearningStore

logger = logging.getLogger(__name__)

HOLD_EVERY = 2
CORRECTION_KINDS = frozenset({"teach", "next_message_correction"})
EXACT = frozenset({"exact", "redacted"})
_HORIZON = "held_out_corrections.horizon"
_COUNTED = "held_out_corrections.counted"  # the decisions counted so far (ids), in order


@dataclass(frozen=True)
class HoldReport:
    """What one pass did (counts only)."""

    considered: int
    counted: int
    held: tuple[str, ...]  # the corrected decisions newly held out


def _confirmed(signals: Iterable[object], horizon: float) -> list[Signal]:
    found = [item for item in signals if isinstance(item, Signal) and item.kind in CORRECTION_KINDS
             and item.decision_id and item.withdrawn_at is None and item.created_at > horizon]
    return sorted(found, key=lambda item: (item.created_at, item.id))


def mark_corrections(store: LearningStore) -> HoldReport:
    """Hold out every 2nd new confirmed correction from exact capture, in time order."""

    if not store.exists():
        return HoldReport(0, 0, ())
    horizon = float(store.meta_value(_HORIZON, "0") or 0)
    try:
        decided: list[str] = [str(item) for item in json.loads(store.meta_value(_COUNTED, "[]"))]
    except ValueError:
        decided = []
    fresh = _confirmed(store.find("signal", limit=100_000), horizon)
    if not fresh:
        return HoldReport(0, 0, ())
    held: list[Decision] = []
    counted = 0
    known = set(decided)
    for signal in fresh:
        decision = store.get("decision", str(signal.decision_id))
        if not isinstance(decision, Decision) or decision.id in known:
            continue  # gone (purged), or corrected again: one problem, counted once
        if decision.fidelity not in EXACT:
            continue  # imported (partial): never hidden, not counted
        episode = store.get("episode", decision.episode_id)
        if isinstance(episode, Episode) and episode.historical:
            continue
        known.add(decision.id)
        decided.append(decision.id)
        counted += 1
        if len(decided) % HOLD_EVERY == 0:
            held.append(decision)
    items = [("decision", item.id, item.conversation_id) for item in held]
    if items:
        store.add_marks(items, MARK_HELD_OUT)
        store.add_marks(items, MARK_HELD_OUT_CORRECTION)
    store.set_meta_values({_HORIZON: repr(fresh[-1].created_at), _COUNTED: json.dumps(decided)})
    if held:
        logger.info("Learning held out %d corrected decision(s) as hidden problems", len(held))
    return HoldReport(len(fresh), counted, tuple(item.id for item in held))


def waiting_for_vault(store: LearningStore) -> set[str]:
    """Corrected decisions held out that the vault should hold (L3a reads this, stores each with its
    corrected action as the reference, and keeps them out of everything else as now)."""

    return store.held_out_correction_ids()


__all__ = ["CORRECTION_KINDS", "HOLD_EVERY", "HoldReport", "mark_corrections", "waiting_for_vault"]
