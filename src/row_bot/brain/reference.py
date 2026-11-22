"""``ReferenceBrain``: the in-memory, deterministic implementation of the ``Brain`` protocol.

It is the executable reading of the contract, used by the contract suite and by tests that need a brain.
Ids are minted from a counter, the clock is injectable, and recall ranks by plain term matching.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Annotated

from .errors import AccessDenied, Conflict, InvalidRecord
from .protocol import Clock, can_read_item, can_read_space, can_write_space, recall_label
from .serialization import Bounds, check_value
from .types import (
    ITEM_TYPES, ChangeEvent, ChangeOp, ChangePage, Fact, Item, ItemRef, Layer, Principal, RecallHit,
    RecallQuery, Relation, Skill, SourceDoc, Space, SpaceAccess,
)

_Reason = Annotated[str, Bounds(min_length=1, max_length=500)]
_AfterSeq = Annotated[int, Bounds(minimum=0)]
_PageLimit = Annotated[int, Bounds(minimum=1, maximum=1_000)]
_ITEM_CLASSES = tuple(ITEM_TYPES.values())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def terms(text: str) -> list[str]:
    """The distinct lower-cased words of ``text``, in order."""
    return list(dict.fromkeys(re.findall(r"\w+", text.casefold())))


def _searchable(item: Item) -> tuple[str, str]:
    """An item's title-like text and the rest of its text."""
    if isinstance(item, Fact):
        return item.subject, f"{item.entity_type} {item.text}"
    if isinstance(item, Skill):
        return f"{item.name} {item.description}", item.body
    if isinstance(item, SourceDoc):
        return item.title, item.uri
    return item.relation, ""


def lexical_score(query_terms: list[str], item: Item) -> float:
    """In [0, 1]: each query term scores 2 in the title, 1 elsewhere, over twice the number of terms."""
    if not query_terms:
        return 0.0
    title, rest = _searchable(item)
    title_terms = set(terms(title))
    all_terms = title_terms | set(terms(rest))
    points = sum(2 if term in title_terms else 1 if term in all_terms else 0 for term in query_terms)
    return points / (2 * len(query_terms))


class ReferenceBrain:
    """An in-memory ``Brain``. ``spaces`` are the team and company access lists; ``clock`` returns UTC now."""

    def __init__(self, spaces: Iterable[SpaceAccess] = (), *, clock: Clock = _utc_now) -> None:
        self._access: dict[Space, SpaceAccess] = {}
        for access in spaces:
            if not isinstance(access, SpaceAccess) or access.space in self._access:
                raise InvalidRecord("ReferenceBrain: spaces must be distinct SpaceAccess records")
            self._access[access.space] = access
        self._clock = clock
        self._items: dict[ItemRef, Item] = {}
        self._log: dict[Space, list[ChangeEvent]] = {}
        self._seq: dict[Space, int] = {}
        # A retracted item's space and audience, so its retract event is shown to whom the item was.
        self._tombstones: dict[ItemRef, tuple[Space, frozenset[str]]] = {}
        self._minted = 0

    # ── Protocol ────────────────────────────────────────────────────────────

    def write(self, principal: Principal, item: Item) -> ItemRef:
        self._check_principal(principal)
        if not isinstance(item, _ITEM_CLASSES):
            raise InvalidRecord("an item is required")
        # Access first: nothing about the space's content may show in an error to someone who can't write it.
        if not can_write_space(principal, item.space, self._access.get(item.space)):
            raise AccessDenied()
        self._check_new(principal, item)
        now = self._now()
        stored = self._record(item, now)
        self._emit(ChangeOp.UPSERT, stored, now)
        return stored.ref

    def supersede(self, principal: Principal, old: ItemRef, new: Item) -> ItemRef:
        self._check_principal(principal)
        previous = self._readable(principal, old)
        if not can_write_space(principal, previous.space, self._access.get(previous.space)):
            raise AccessDenied()
        if type(new) is not type(previous) or new.space != previous.space:
            raise InvalidRecord("supersede: the new item must be of the same kind and space")
        self._check_new(principal, new)
        if isinstance(new, Skill) and new.name != previous.name:
            raise InvalidRecord("supersede: a new version of a skill keeps its name")
        if previous.validity.superseded_at is not None:
            raise Conflict("supersede: the item is already superseded")
        now = self._now()
        stored = self._record(new, now)
        replaced = dataclasses.replace(previous, validity=dataclasses.replace(
            previous.validity, superseded_by=stored.ref, superseded_at=now))
        self._items[replaced.ref] = replaced
        self._emit(ChangeOp.UPSERT, stored, now)
        self._emit(ChangeOp.UPSERT, replaced, now)
        return stored.ref

    def get(self, principal: Principal, ref: ItemRef) -> Item:
        self._check_principal(principal)
        return self._readable(principal, ref)

    def recall(self, query: RecallQuery) -> tuple[RecallHit, ...]:
        if not isinstance(query, RecallQuery):
            raise InvalidRecord("recall: takes a RecallQuery")
        principal = query.principal
        if query.spaces is not None:
            if not all(can_read_space(principal, space, self._access.get(space)) for space in query.spaces):
                raise AccessDenied()
            spaces = set(query.spaces)
        else:
            spaces = {Space(Layer.PERSONAL, principal.id), Space(Layer.WORK, principal.id)}
            spaces |= {space for space, access in self._access.items() if can_read_space(principal, space, access)}
        at = query.as_of or self._now()
        query_terms = terms(query.text)
        scored = []
        for item in self._items.values():
            if item.space not in spaces or (query.kinds is not None and item.KIND not in query.kinds):
                continue
            if not self._can_read(principal, item) or not _shown_at(item, at, query.include_superseded):
                continue
            if (score := lexical_score(query_terms, item)) > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].validity.recorded_at.timestamp(), pair[1].id))
        return tuple(self._hit(_as_at(item, at), score) for score, item in scored[:query.limit])

    def forget(self, principal: Principal, ref: ItemRef, reason: str) -> tuple[ItemRef, ...]:
        self._check_principal(principal)
        check_value(reason, _Reason, "forget.reason")
        root = self._readable(principal, ref)
        if not can_write_space(principal, root.space, self._access.get(root.space)):
            raise AccessDenied()
        order = [root.ref]
        index = 0
        while index < len(order):
            current = order[index]
            index += 1
            for item in sorted(self._items.values(), key=lambda candidate: candidate.id):
                if item.ref not in order and _depends_on(item, current):
                    order.append(item.ref)
        now = self._now()
        reported = []
        for retracted in order:
            item = self._items.pop(retracted)
            if self._can_read(principal, item):
                reported.append(retracted)
            self._tombstones[retracted] = (item.space, item.audience)
            # Forgotten content leaves the feed too: only the retract event remains.
            self._log[item.space] = [event for event in self._log.get(item.space, []) if event.ref != retracted]
            self._append(item.space, ChangeOp.RETRACT, retracted, None, now)
        return tuple(reported)

    def changes(self, principal: Principal, space: Space, after_seq: int = 0, limit: int = 100) -> ChangePage:
        self._check_principal(principal)
        check_value(space, Space, "changes.space")
        check_value(after_seq, _AfterSeq, "changes.after_seq")
        check_value(limit, _PageLimit, "changes.limit")
        access = self._access.get(space)
        if not can_read_space(principal, space, access):
            raise AccessDenied()
        events: list[ChangeEvent] = []
        next_seq = after_seq
        has_more = False
        for event in self._log.get(space, []):
            if event.seq <= after_seq:
                continue
            if len(events) == limit:
                has_more = True
                break
            next_seq = event.seq
            audience = event.item.audience if event.item is not None else self._tombstones[event.ref][1]
            if can_read_item(principal, space, audience, access):
                events.append(event)
        return ChangePage(space=space, events=tuple(events), next_seq=next_seq, has_more=has_more)

    # ── Internals ───────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return check_value(self._clock(), datetime, "clock")

    @staticmethod
    def _check_principal(principal: Principal) -> None:
        if not isinstance(principal, Principal):
            raise InvalidRecord("a Principal is required")

    def _check_new(self, principal: Principal, item: Item) -> None:
        """A new item for a space ``principal`` may write."""
        if item.id or item.validity.recorded_at is not None or item.validity.superseded_by is not None:
            raise InvalidRecord("a new item has no id, recorded_at or supersession: the brain records those")
        if isinstance(item, Relation):
            for end in (item.source, item.target):
                other = self._items.get(end)
                # An endpoint the writer can't read is refused exactly like a missing one.
                if other is None or other.space != item.space or not self._can_read(principal, other):
                    raise InvalidRecord("Relation: source and target must be facts of the same space")

    def _can_read(self, principal: Principal, item: Item) -> bool:
        return can_read_item(principal, item.space, item.audience, self._access.get(item.space))

    def _readable(self, principal: Principal, ref: ItemRef) -> Item:
        check_value(ref, ItemRef, "ref")
        item = self._items.get(ref)
        if item is None or not self._can_read(principal, item):
            raise AccessDenied()
        return item

    def _record(self, item: Item, now: datetime) -> Item:
        self._minted += 1
        stored = dataclasses.replace(item, id=f"{item.KIND.value}-{self._minted:08d}",
                                     validity=dataclasses.replace(item.validity, recorded_at=now))
        self._items[stored.ref] = stored
        return stored

    def _emit(self, op: ChangeOp, item: Item, now: datetime) -> None:
        self._append(item.space, op, item.ref, item, now)

    def _append(self, space: Space, op: ChangeOp, ref: ItemRef, item: Item | None, now: datetime) -> None:
        self._seq[space] = self._seq.get(space, 0) + 1
        self._log.setdefault(space, []).append(ChangeEvent(seq=self._seq[space], op=op, ref=ref, at=now, item=item))

    def _hit(self, item: Item, score: float) -> RecallHit:
        is_fact = isinstance(item, Fact)
        return RecallHit(
            item=item, score=score, layer=item.space.layer, owner=item.space.owner,
            label=recall_label(item.space, self._access.get(item.space)),
            owner_principal=item.owner_principal if is_fact else None,
            verified_at=item.verified_at if is_fact else None,
        )


def _depends_on(item: Item, ref: ItemRef) -> bool:
    """Whether retracting ``ref`` retracts ``item``: derived from it, or a relation with it as an endpoint."""
    if ref in item.lineage.derived_from:
        return True
    return isinstance(item, Relation) and ref in (item.source, item.target)


def _shown_at(item: Item, at: datetime, include_superseded: bool) -> bool:
    validity = item.validity
    if validity.recorded_at > at:
        return False
    if include_superseded:
        return True
    if validity.superseded_at is not None and validity.superseded_at <= at:
        return False
    return validity.valid_from <= at and (validity.valid_to is None or at < validity.valid_to)


def _as_at(item: Item, at: datetime) -> Item:
    """The item as it was at ``at``: a supersession recorded later is not on it yet."""
    validity = item.validity
    if validity.superseded_at is None or validity.superseded_at <= at:
        return item
    return dataclasses.replace(item, validity=dataclasses.replace(validity, superseded_by=None, superseded_at=None))
