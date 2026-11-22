"""The ``Brain`` protocol and the access rules every implementation enforces itself.

One contract, two stores: the person's SQLite brain on the device and the org server's Postgres brain. The
reusable suite in ``tests/contracts/brain/suite.py`` is the executable form of the semantics written here.

Access (enforced inside the implementation, never left to callers):

* A personal or work space is read and written only by its person (``principal.id == space.space_id``).
* A team or company space is read by principals in one of its ``SpaceAccess.readers`` (or ``publishers``)
  groups. An item's non-empty ``audience`` (often the upstream system's ACL) narrows that, for everyone,
  publishers included, to principals in one of those groups: the brain never shows what the source would
  deny.
* Team and company content arrives through promotion. Directly, only a principal in one of the space's
  ``publishers`` groups may write there, and may supersede or forget only an item they can also read.
  A publisher may write an item whose audience they are not in (a connector does), and then can't read it
  back; so ``write`` and ``supersede`` return only the new item's ref, whoever writes.
* Anything else raises ``AccessDenied``, with the same message whether or not the item or space exists.
  A shared space with no ``SpaceAccess`` is readable by nobody.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from .errors import InvalidRecord
from .types import ChangePage, Item, ItemRef, Layer, Principal, RecallHit, RecallQuery, Space, SpaceAccess

Clock = Callable[[], datetime]


def can_read_space(principal: Principal, space: Space, access: SpaceAccess | None) -> bool:
    """Whether ``principal`` may read ``space`` at all (``access`` is the space's list, if it is shared)."""
    if not space.shared:
        return principal.id == space.space_id
    return access is not None and bool(principal.groups & (access.readers | access.publishers))


def can_write_space(principal: Principal, space: Space, access: SpaceAccess | None) -> bool:
    """Whether ``principal`` may write, supersede or forget directly in ``space``."""
    if not space.shared:
        return principal.id == space.space_id
    return access is not None and bool(principal.groups & access.publishers)


def can_read_item(principal: Principal, space: Space, audience: frozenset[str], access: SpaceAccess | None) -> bool:
    """Whether ``principal`` may read an item of ``space`` with this ``audience``; publishing grants nothing."""
    if not can_read_space(principal, space, access):
        return False
    return not audience or bool(principal.groups & audience)


def recall_label(space: Space, access: SpaceAccess | None) -> str:
    """The short label recall shows for an item of ``space``, such as "From your notes"."""
    if space.layer is Layer.PERSONAL:
        return "From your notes"
    if space.layer is Layer.WORK:
        return "From your work notes"
    if access is None or access.space != space:
        raise InvalidRecord("recall_label: a team or company space is labelled by its access list")
    return f"From {access.title}"


class Brain(Protocol):
    """A store of layered items. Every call checks access first (see the module docstring)."""

    def write(self, principal: Principal, item: Item) -> ItemRef:
        """Record a new item and return its ref, never the stored item.

        ``item`` must not be recorded yet: an empty ``id`` and no ``recorded_at`` or supersession. The brain
        mints the id and stamps ``recorded_at`` with its clock; ``get`` returns the stored item to whoever may
        read it. A relation's endpoints must be facts of the same space that the writer can read (an
        unreadable endpoint is refused exactly like a missing one). Lineage refs are not resolved: a ref to
        nothing is inert. Raises ``AccessDenied`` or ``InvalidRecord``.
        """
        ...

    def supersede(self, principal: Principal, old: ItemRef, new: Item) -> ItemRef:
        """Record ``new`` as the replacement of ``old`` and return the new item's ref.

        The caller must be able to read ``old`` and write its space. ``new`` is a new item of the same kind
        and space (a skill keeps its name). ``old`` gains ``superseded_by`` and ``superseded_at`` (the brain's
        clock) and stays readable as history. Raises ``AccessDenied``, ``InvalidRecord``, or ``Conflict``
        when ``old`` is already superseded.
        """
        ...

    def get(self, principal: Principal, ref: ItemRef) -> Item:
        """The item as stored now, superseded or not. A forgotten, missing or unreadable item raises
        ``AccessDenied``."""
        ...

    def recall(self, query: RecallQuery) -> tuple[RecallHit, ...]:
        """Items matching ``query.text``, best first, at most ``query.limit``.

        Searches ``query.spaces`` (each must be readable, or ``AccessDenied``) or else every space the
        principal may read, and only items they may read. At time ``t`` (``query.as_of``, or now) an item is
        shown when it was recorded by ``t`` and, unless ``include_superseded``, was not superseded by ``t``
        and is valid at ``t`` (``valid_from <= t < valid_to``). A hit's item is shown as it was at ``t``: a
        supersession recorded after ``t`` is not on it. Ordering is by score, then the most recently
        recorded, then id; an item that holds every query term ranks above one that holds only some.
        """
        ...

    def forget(self, principal: Principal, ref: ItemRef, reason: str) -> tuple[ItemRef, ...]:
        """Retract ``ref`` and, transitively, every item derived from it (lineage) and every relation that
        has a retracted fact as an endpoint, in any space.

        Retracted items are gone: ``get`` and ``recall`` no longer return them, even with ``as_of`` or
        ``include_superseded``, and their earlier upsert events leave the change feed, which gains a
        ``retract`` event for each. ``reason`` (1 to 500 characters) is for the implementation's audit log.
        Returns the retracted refs the caller could read, ``ref`` first. The caller must be able to read
        ``ref`` and write its space; raises ``AccessDenied`` or ``InvalidRecord``.
        """
        ...

    def changes(self, principal: Principal, space: Space, after_seq: int = 0, limit: int = 100) -> ChangePage:
        """Up to ``limit`` (1 to 1,000) of ``space``'s events with ``seq > after_seq``, oldest first.

        Only events about items the principal may read now are returned; ``next_seq`` still moves past the
        ones left out. A principal who can't read the space gets ``AccessDenied``. Visibility is decided at
        each call, so a client whose groups change replays the feed from 0 into a fresh cache.
        """
        ...
