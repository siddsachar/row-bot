"""Mirroring a file's upstream permissions onto its document's readers (``brain_documents.readers``, migration 0011).

A reader is ``e:<email>`` (a person, by the email Row-Bot has for them, lower case: one stored only when the identity
provider verified it) or ``g:<group>`` (a Row-Bot group, as ``group_map`` names it). Each upstream permission that
lets someone read the file (a ``Grant``) maps, in this order:

1. its key in the connector's ``principal_map`` (set by an admin): that Row-Bot group;
2. a person with an email: that email;
3. a group: the Row-Bot group ``group_map`` maps its identifier to (an Entra group's object id; a Google group's
   email, compared without case); with no ``group_map``, a Microsoft group maps to its object id itself when people
   sign in with Entra (whose groups claim holds those ids, which is then what Row-Bot stores);
4. anything else (a sharing link for the organisation or anyone, a Google domain or "anyone", a SharePoint group, a
   group nothing maps, a person with no email) can't be mapped.

While any grant of a file can't be mapped, the file is read by admins only (an empty list), never by everyone: the
connector counts such files and names what couldn't be mapped, so an admin can map it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from row_bot_org.identity.config import IdpConfig

MAX_READERS = 1000
TOO_MANY = {"key": "too-many-readers", "name": f"More than {MAX_READERS:,} people and groups"}
PERSON, GROUP, OTHER = "person", "group", "other"


@dataclass(frozen=True)
class Grant:
    """One upstream permission that lets someone read a file."""

    kind: str
    # Stable upstream key, which principal_map may name: user:<id>, microsoft-group:<id>, google-group:<email>,
    # sharepoint-group:<id>, link:<scope>, google-domain:<domain>, google-anyone.
    key: str
    # What admins see in the connector's warning.
    name: str
    email: str | None = None
    # A group's identifier at the identity provider (an Entra object id, a Google group's email).
    group: str | None = None


@dataclass(frozen=True)
class Mirrored:
    readers: tuple[str, ...]
    # [{"key", "name"}] of the grants nothing maps; non-empty means the file is admins only.
    unmapped: tuple[dict[str, str], ...]


def _group(grant: Grant, provider: str, idp: IdpConfig | None) -> str | None:
    if grant.group is None or idp is None:
        return None
    if idp.group_map:
        if provider == "google":
            wanted = grant.group.casefold()
            return next((target for source, target in idp.group_map if source.casefold() == wanted), None)
        return dict(idp.group_map).get(grant.group)
    return grant.group if provider == "microsoft" and idp.kind == "entra" else None


def mirror(grants: Sequence[Grant], *, provider: str, idp: IdpConfig | None,
           principal_map: Mapping[str, str]) -> Mirrored:
    """The readers ``grants`` give a file of a ``provider`` (``microsoft`` or ``google``) connector."""
    readers: set[str] = set()
    unmapped: dict[str, dict[str, str]] = {}
    for grant in grants:
        mapped = principal_map.get(grant.key)
        if mapped is not None:
            readers.add("g:" + mapped)
        elif grant.kind == PERSON and grant.email:
            readers.add("e:" + grant.email.strip().lower())
        elif grant.kind == GROUP and (group := _group(grant, provider, idp)) is not None:
            readers.add("g:" + group)
        else:
            unmapped.setdefault(grant.key, {"key": grant.key, "name": grant.name[:200]})
    if len(readers) > MAX_READERS:
        unmapped.setdefault(TOO_MANY["key"], dict(TOO_MANY))
    if unmapped:
        return Mirrored((), tuple(unmapped[key] for key in sorted(unmapped)))
    return Mirrored(tuple(sorted(readers)), ())


__all__ = ["GROUP", "MAX_READERS", "OTHER", "PERSON", "Grant", "Mirrored", "mirror"]
