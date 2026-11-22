"""The audit hash chain: each link's hash covers the previous link's hash and the event's canonical bytes.

``chain_hash(prev, event) = SHA-256(lp("row-bot/audit/v1") ‖ lp(prev) ‖ lp(JCS(event)))``, where ``lp(x)`` is
``x`` prefixed with its length as 8 bytes big-endian and ``prev`` is the previous hash's 32 raw bytes. A chain
starts from ``genesis_hash(chain_id)``, so links can't be spliced between chains (an org's server chain, a
device's chain).

Hash the event as it is stored: redact first, then chain. ``verify_chain`` reports the first broken link, so an
edit, a reorder, a deletion or an insertion is found where it happened (a link rewritten with a fresh hash shows
as a break at the link after it). Cutting links off the end, or rewriting the whole tail, is only visible
against a head kept elsewhere (the SIEM copy, a signed checkpoint): pass it as ``expected_head``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .event import AuditEvent, InvalidAuditEvent, canonical_bytes, from_dict, to_dict
from .jcs import MAX_SAFE_INTEGER
from .vocabulary import HASH_PATTERN, ID_PATTERN

DOMAIN = b"row-bot/audit/v1"
_HASH = re.compile(HASH_PATTERN)
_ID = re.compile(ID_PATTERN)

BreakReason = Literal["invalid", "prev_hash", "seq", "hash", "head"]


def _lp(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data


def _hash_bytes(value: object, path: str) -> bytes:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise InvalidAuditEvent(f"{path}: must be a SHA-256 hash in lowercase hex")
    return bytes.fromhex(value)


def genesis_hash(chain_id: str) -> str:
    """The hash a chain named ``chain_id`` starts from (its first link's ``prev_hash``)."""
    if not isinstance(chain_id, str) or not _ID.fullmatch(chain_id):
        raise InvalidAuditEvent("chain_id: has the wrong form")
    return hashlib.sha256(_lp(DOMAIN + b"/genesis") + _lp(chain_id.encode("utf-8"))).hexdigest()


def chain_hash(prev_hash: str, event: AuditEvent) -> str:
    """The hash of the link holding ``event`` after a link whose hash is ``prev_hash``."""
    prev = _hash_bytes(prev_hash, "prev_hash")
    return hashlib.sha256(_lp(DOMAIN) + _lp(prev) + _lp(canonical_bytes(event))).hexdigest()


@dataclass(frozen=True)
class ChainLink:
    """One stored link: its position, the previous link's hash, its own hash and the event."""

    seq: int
    prev_hash: str
    hash: str
    event: AuditEvent

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or not 0 <= self.seq <= MAX_SAFE_INTEGER:
            raise InvalidAuditEvent("seq: must be a non-negative integer")
        _hash_bytes(self.prev_hash, "prev_hash")
        _hash_bytes(self.hash, "hash")
        if not isinstance(self.event, AuditEvent):
            raise InvalidAuditEvent("event: must be an AuditEvent")

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "prev_hash": self.prev_hash, "hash": self.hash, "event": to_dict(self.event)}

    @classmethod
    def from_dict(cls, data: object) -> ChainLink:
        if not isinstance(data, Mapping):
            raise InvalidAuditEvent("link: must be an object")
        if unknown := sorted(str(key) for key in data if key not in ("seq", "prev_hash", "hash", "event")):
            raise InvalidAuditEvent(f"link: unknown field {unknown[0]!r}")
        if missing := [key for key in ("seq", "prev_hash", "hash", "event") if key not in data]:
            raise InvalidAuditEvent(f"link: missing field {missing[0]!r}")
        return cls(data["seq"], data["prev_hash"], data["hash"], from_dict(data["event"]))


def make_link(prev_hash: str, seq: int, event: AuditEvent) -> ChainLink:
    """The link that appends ``event`` at position ``seq`` after a link whose hash is ``prev_hash``."""
    return ChainLink(seq, prev_hash, chain_hash(prev_hash, event), event)


@dataclass(frozen=True)
class ChainReport:
    """What ``verify_chain`` found: ``ok``, how many links checked out, the last good hash (``head``), and for a
    broken chain the position of the first broken link and why."""

    ok: bool
    checked: int
    head: str
    broken_at: int | None = None
    reason: BreakReason | None = None


def verify_chain(links: Iterable[ChainLink | Mapping[str, Any]], *, start_hash: str, start_seq: int = 0,
                 expected_head: str | None = None) -> ChainReport:
    """Check links in stored order from ``start_hash`` (``genesis_hash(chain_id)`` for a whole chain, or a
    trusted checkpoint) and ``start_seq``. Links may be ``ChainLink`` or their JSON. Stops at the first broken one:
    ``invalid`` (not a valid link), ``prev_hash`` (doesn't follow the link before: a deletion, insertion or
    reorder), ``seq`` (out of position), ``hash`` (its event or hash was changed), or ``head`` (all links are
    sound but the last isn't ``expected_head``: links were cut off the end or the tail was rewritten)."""
    _hash_bytes(start_hash, "start_hash")
    prev, checked = start_hash, 0
    for offset, raw in enumerate(links):
        position = start_seq + offset
        try:
            link = raw if isinstance(raw, ChainLink) else ChainLink.from_dict(raw)
        except InvalidAuditEvent:
            return ChainReport(False, checked, prev, position, "invalid")
        if link.prev_hash != prev:
            return ChainReport(False, checked, prev, position, "prev_hash")
        if link.seq != position:
            return ChainReport(False, checked, prev, position, "seq")
        try:
            sound = chain_hash(link.prev_hash, link.event) == link.hash
        except InvalidAuditEvent:  # An event forced into a bad state after it was built.
            return ChainReport(False, checked, prev, position, "invalid")
        if not sound:
            return ChainReport(False, checked, prev, position, "hash")
        prev, checked = link.hash, checked + 1
    if expected_head is not None and prev != expected_head:
        return ChainReport(False, checked, prev, start_seq + checked, "head")
    return ChainReport(True, checked, prev)
